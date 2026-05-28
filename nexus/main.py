"""Process entrypoint.

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

Search wiring: the orchestrator gets a real
``DefaultSearchClient`` — Brave primary with SearXNG (google +
duckduckgo, breaker-guarded) fallback. With no Brave key the router
uses SearXNG only; if neither is reachable a search raises
``SearchUnavailable`` rather than returning empty results. Crawl uses
the real ``CrawlClient`` behind the SSRF guard. The LLM gateway,
cache, telemetry, transports, auth, and selftest are all real.
"""

from __future__ import annotations

import asyncio
import logging
import signal
from collections.abc import Awaitable
from typing import Any

import uvicorn

from nexus.cache import namespaces as cache_ns
from nexus.cache import setup_cache, shutdown_cache
from nexus.config import Config, load_config
from nexus.crawl import CrawlClient
from nexus.crawl.ssrf import SSRFGuard
from nexus.http import create_app as create_http_app
from nexus.llm import LiteLLMClient
from nexus.logging import setup_logging
from nexus.mcp.server import MCPTransport, create_streamable_http_app
from nexus.orchestrator.service import Orchestrator
from nexus.search import BraveProvider, DefaultSearchClient, SearXNGProvider
from nexus.security import run_selftest
from nexus.telemetry import PrometheusTelemetrySink, setup_telemetry

logger = logging.getLogger(__name__)


def _build_search_client(config: Config) -> DefaultSearchClient:
    """Brave-first router with SearXNG fallback.

    Both providers are real. If no Brave key is configured the router
    transparently uses SearXNG (google + duckduckgo, breaker-guarded).
    If neither is reachable a search raises ``SearchUnavailable`` — no
    silent empty-result placeholder.
    """
    brave = BraveProvider(api_key=config.env.brave_api_key.get_secret_value())
    searxng = SearXNGProvider(
        base_url=config.searxng_base_url,
        engines=config.searxng_engines,
    )
    if not brave.enabled:
        logger.warning(
            "brave_not_configured_using_searxng_only",
            extra={"engines": list(config.searxng_engines)},
        )
    # cache_ns.SEARCH_BRAVE is populated by setup_cache() (called before
    # this runs); it's the shared search-response cache (provider-agnostic
    # merged responses). None when the cache is disabled.
    return DefaultSearchClient(brave=brave, searxng=searxng, cache=cache_ns.SEARCH_BRAVE)


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

    # One sink for the whole process: forwards orchestrator + LLM metrics
    # to the Prometheus instruments scraped on the metrics port.
    telemetry_sink = PrometheusTelemetrySink()
    llm_client = LiteLLMClient(config=config.llm, telemetry=telemetry_sink)

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

    http_server, http_task = _start_http(config, llm_client, telemetry_sink)
    mcp_task = _start_mcp(config, llm_client, telemetry_sink)

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


def _build_orchestrator(
    config: Config, llm_client: LiteLLMClient, telemetry_sink: PrometheusTelemetrySink
) -> Orchestrator:
    """Wire the real search router + crawl client + LLM gateway, with the
    disk caches (populated by setup_cache) injected."""
    return Orchestrator(
        search_client=_build_search_client(config),
        crawl_client=CrawlClient(ssrf_guard=SSRFGuard(), cache=cache_ns.CRAWL_DOCUMENT),
        llm_client=llm_client,
        telemetry=telemetry_sink,
    )


def _start_http(
    config: Config, llm_client: LiteLLMClient, telemetry_sink: PrometheusTelemetrySink
) -> tuple[uvicorn.Server, asyncio.Task[None]]:
    app = create_http_app(
        orchestrator=_build_orchestrator(config, llm_client, telemetry_sink),
        llm_config_roles=dict(config.llm.roles),
        config=config.http,
    )
    server = uvicorn.Server(_uvicorn_config(app, config.bind_host, config.http_port))
    task = asyncio.create_task(server.serve(), name="http_server")
    return server, task


def _start_mcp(
    config: Config, llm_client: LiteLLMClient, telemetry_sink: PrometheusTelemetrySink
) -> asyncio.Task[None]:
    """MCP server. Runs on its own uvicorn instance via the
    streamable-http app FastMCP provides. Falls back to a no-op task
    in environments where fastmcp's HTTP server is not importable
    (so tests can run without it)."""
    transport = MCPTransport(
        orchestrator=_build_orchestrator(config, llm_client, telemetry_sink),
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

    server = uvicorn.Server(_uvicorn_config(app, config.bind_host, config.mcp_port))
    return asyncio.create_task(server.serve(), name="mcp_server")


def _uvicorn_config(app: Any, host: str, port: int) -> uvicorn.Config:
    """Shared uvicorn config. ``server_header=False`` suppresses uvicorn's
    own ``Server: uvicorn`` header so our middleware's ``Server: nexus`` is
    the only one (no framework disclosure / duplicate header). We own
    logging, so uvicorn's log config and access log are off."""
    return uvicorn.Config(
        app=app,
        host=host,
        port=port,
        log_config=None,
        access_log=False,
        lifespan="off",
        server_header=False,
    )


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
