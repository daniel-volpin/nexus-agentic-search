"""Regression for the duplicate-Server-header bug found by the live smoke.

The unit transport tests use Starlette's TestClient, which does not add
uvicorn's protocol-level ``Server: uvicorn`` header — so they passed
while a real uvicorn server emitted BOTH ``uvicorn`` and our ``nexus``
header. These tests pin the fix at two levels: the shared uvicorn config
flag, and a real server boot.
"""

from __future__ import annotations

import socket
import threading
import time
from contextlib import closing

import httpx
import pytest
import uvicorn

from nexus.http import HTTPConfig, create_app
from nexus.main import _uvicorn_config

_TOKEN = "t" * 32


def _free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class _StaticOrchestrator:
    async def search(self, _req):
        if False:
            yield  # pragma: no cover


def _app():
    return create_app(
        orchestrator=_StaticOrchestrator(),
        llm_config_roles={},
        config=HTTPConfig(token=_TOKEN),
    )


def test_uvicorn_config_suppresses_server_header() -> None:
    """The shared config must disable uvicorn's own Server header."""
    cfg = _uvicorn_config(_app(), "127.0.0.1", _free_port())
    assert cfg.server_header is False


def test_real_server_emits_only_nexus_server_header() -> None:
    """Boot a real uvicorn server (not TestClient) and confirm the only
    Server header is ``nexus`` — no leaked ``uvicorn``."""
    port = _free_port()
    server = uvicorn.Server(_uvicorn_config(_app(), "127.0.0.1", port))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.time() + 10
        while not server.started and time.time() < deadline:
            time.sleep(0.05)
        assert server.started, "uvicorn did not start in time"

        resp = httpx.get(f"http://127.0.0.1:{port}/v1/health", timeout=5)
        assert resp.status_code == 200
        server_header = resp.headers.get("server", "")
        assert server_header == "nexus", f"unexpected Server header: {server_header!r}"
        assert "uvicorn" not in server_header.lower()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


@pytest.mark.security
def test_real_server_error_body_has_no_traceback() -> None:
    """On a real server, an unauthenticated request returns the strict
    error body and no framework/traceback leakage."""
    port = _free_port()
    server = uvicorn.Server(_uvicorn_config(_app(), "127.0.0.1", port))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.time() + 10
        while not server.started and time.time() < deadline:
            time.sleep(0.05)
        assert server.started
        resp = httpx.post(f"http://127.0.0.1:{port}/v1/search", json={"query": "x"}, timeout=5)
        assert resp.status_code == 401
        assert resp.json() == {"error": "unauthorized"}
    finally:
        server.should_exit = True
        thread.join(timeout=5)
