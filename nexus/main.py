"""Process entrypoint (Spec 12 §Container image).

Boot sequence (deliberate order):

1. Load env config (fail-closed on missing tokens / bad SearXNG engines).
2. Configure structured logging + secret redaction.
3. Initialise OpenTelemetry + start the Prometheus metrics server.
4. Open the disk cache namespaces.
5. Run the security self-test; FAIL-CLOSED on any critical regression.
6. Construct LLM gateway, orchestrator, transports.
7. Start HTTP + MCP servers concurrently.
8. Wait for shutdown signal; tear down cleanly.

Each step has a single failure mode and a clear log line. The selftest
is the last gate before the service accepts traffic.

NOTE — placeholder search/crawl wiring
======================================
Spec 01 ``BraveSearchClient`` and ``SearXNGProvider`` are still
scaffolding (per their module docstrings). This entrypoint wires a
``_PlaceholderSearchClient`` that returns an empty SearchResponse and
logs a single CRITICAL line at startup so the operator knows the
service is up but search is dead until that hardening lands. The
container itself, transports, auth, telemetry, cache, and selftest
are all real.
"""

from __future__ import annotations

import asyncio
import logging
import signal
from collections.abc import Awaitable

import uvicorn

from nexus.cache import setup_cache, shutdown_cache
from nexus.config import Config, load_config
from nexus.crawl import CrawlClient
from nexus.crawl.ssrf import SSRFGuard
from nexus.http import create_app as create_http_app
from nexus.llm import LiteLLMClient
from nexus.logging import setup_logging
from nexus.mcp.server import MCPTransport, create_streamable_http_app
from nexus.orchestrator.service import Orchestrator
from nexus.search import SearchRequest, SearchResponse
from nexus.security import run_selftest
from nexus.telemetry import setup_telemetry

logger = logging.getLogger(__name__)


class _PlaceholderSearchClient:
    """Stand-in until Spec 01 BraveSearchClient lands.

    Returns an empty SearchResponse for every query. The orchestrator
    handles zero-result responses gracefully (yields an `ungrounded`
    answer event). Operators see one CRITICAL log line at construction
    so the deployment is loud about the missing piece.
    """

    def __init__(self) -> None:
        logger.critical(
            "search_provider_placeholder_in_use",
            extra={
                "remediation": (
                    "Spec 01 BraveSearchClient is a stub; this deployment "
                    "returns empty results until that component is hardened."
                )
            },
        )

    async def search(self, req: SearchRequest) -> SearchResponse:
        return SearchResponse(
            results=[],
            provider="placeholder",
            query_sent=req.query,
            latency_ms=0,
        )


async def amain() -> int:
    """Async entrypoint. Returns process exit code."""
    try:
        config = load_config()
    except (RuntimeError, ValueError) as exc:
        # Logging isn't set up yet — go to stderr direct.
        import sys

        print(f"[fatal] config: {exc}", file=sys.stderr)
        return 78  # EX_CONFIG

    setup_logging(level=config.log_level, json_format=config.json_logs)
    setup_telemetry(
        service_name="nexus-agentic-search",
        service_version=_version(),
        metrics_port=config.metrics_port,
        metrics_bind_host=config.bind_host,
    )
    logger.info("startup_begin")

    setup_cache(root=config.cache_root, total_size_gb=config.cache_total_size_gb)

    llm_client = LiteLLMClient(config=config.llm)

    selftest_report = await run_selftest(llm_client=llm_client)
    if selftest_report.critical_failures:
        logger.critical(
            "selftest_failed_closed",
            extra={"failures": list(selftest_report.failures)},
        )
        shutdown_cache()
        return 70  # EX_SOFTWARE
    if not selftest_report.all_ok:
        logger.warning(
            "selftest_noncritical_failures",
            extra={"failures": list(selftest_report.failures)},
        )

    http_server, http_task = _start_http(config, llm_client)
    mcp_task = _start_mcp(config, llm_client)

    logger.info(
        "service_ready",
        extra={
            "http_port": config.http_port,
            "mcp_port": config.mcp_port,
            "metrics_port": config.metrics_port,
        },
    )

    stop_event = asyncio.Event()
    _install_signal_handlers(stop_event)

    try:
        await stop_event.wait()
    finally:
        logger.info("shutdown_begin")
        http_server.should_exit = True
        for task in (http_task, mcp_task):
            task.cancel()
        await asyncio.gather(*_safe_awaits(http_task, mcp_task), return_exceptions=True)
        shutdown_cache()
        logger.info("shutdown_complete")

    return 0


# ---------- transport bootstrap ----------


def _build_orchestrator(llm_client: LiteLLMClient) -> Orchestrator:
    """Construct an Orchestrator with placeholder search +
    real crawl + real llm. See module docstring for the placeholder
    caveat."""
    return Orchestrator(
        search_client=_PlaceholderSearchClient(),
        crawl_client=CrawlClient(ssrf_guard=SSRFGuard()),
        llm_client=llm_client,
    )


def _start_http(
    config: Config, llm_client: LiteLLMClient
) -> tuple[uvicorn.Server, asyncio.Task[None]]:
    app = create_http_app(
        orchestrator=_build_orchestrator(llm_client),
        llm_config_roles=dict(config.llm.roles),
        config=config.http,
    )
    server = uvicorn.Server(
        uvicorn.Config(
            app=app,
            host=config.bind_host,
            port=config.http_port,
            log_config=None,  # we own logging
            access_log=False,
            lifespan="off",
        )
    )
    task = asyncio.create_task(server.serve(), name="http_server")
    return server, task


def _start_mcp(config: Config, llm_client: LiteLLMClient) -> asyncio.Task[None]:
    """MCP server. Runs on its own uvicorn instance via the
    streamable-http app FastMCP provides. Falls back to a no-op task
    in environments where fastmcp's HTTP server is not importable
    (so tests can run without it)."""
    transport = MCPTransport(
        orchestrator=_build_orchestrator(llm_client),
        llm_config_roles=dict(config.llm.roles),
        config=config.mcp,
    )
    try:
        app = create_streamable_http_app(transport=transport)
    except RuntimeError as exc:  # fastmcp not available
        logger.warning("mcp_server_unavailable", extra={"reason": str(exc)})

        async def _noop() -> None:
            await asyncio.Event().wait()

        return asyncio.create_task(_noop(), name="mcp_noop")

    server = uvicorn.Server(
        uvicorn.Config(
            app=app,
            host=config.bind_host,
            port=config.mcp_port,
            log_config=None,
            access_log=False,
            lifespan="off",
        )
    )
    return asyncio.create_task(server.serve(), name="mcp_server")


# ---------- shutdown plumbing ----------


def _install_signal_handlers(stop_event: asyncio.Event) -> None:
    import contextlib

    loop = asyncio.get_running_loop()
    # NotImplementedError on Windows / certain test harnesses — fall back
    # to the default signal handler.
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop_event.set)


def _safe_awaits(*tasks: asyncio.Task[None]) -> tuple[Awaitable[None], ...]:
    return tuple(t for t in tasks if not t.done())


def _version() -> str:
    from nexus import __version__

    return __version__


def main() -> None:
    raise SystemExit(asyncio.run(amain()))


if __name__ == "__main__":
    main()
