from __future__ import annotations

from typing import Never

import uvicorn
from fastapi import FastAPI

from nexus.config import load_config
from nexus.http import HTTPConfig, create_app
from nexus.logging import configure_logging
from nexus.orchestrator import AnswerEvent


class _UnavailableOrchestrator:
    async def search(self, req) -> Never:
        raise RuntimeError("runtime dependencies not configured")
        yield AnswerEvent(stage="error", payload={})  # pragma: no cover


def build_app() -> FastAPI:
    configure_logging()
    config = load_config()
    return create_app(
        orchestrator=_UnavailableOrchestrator(),
        llm_config_roles={},
        config=HTTPConfig(token=config.http_token),
    )


def main() -> None:
    config = load_config()
    uvicorn.run(build_app(), host=config.bind_host, port=config.http_port)


if __name__ == "__main__":
    main()
