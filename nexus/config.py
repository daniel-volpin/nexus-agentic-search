from __future__ import annotations

from dataclasses import dataclass
import os


@dataclass(frozen=True)
class AppConfig:
    http_token: str
    mcp_token: str
    bind_host: str = "127.0.0.1"
    http_port: int = 8186
    mcp_path: str = "/mcp"

    def __post_init__(self) -> None:
        if not self.http_token:
            raise ValueError("http_token must not be empty")
        if not self.mcp_token:
            raise ValueError("mcp_token must not be empty")


def load_config() -> AppConfig:
    return AppConfig(
        http_token=os.environ.get("NEXUS_HTTP_TOKEN", ""),
        mcp_token=os.environ.get("NEXUS_MCP_TOKEN", ""),
        bind_host=os.environ.get("BIND_HOST", "127.0.0.1"),
        http_port=int(os.environ.get("BIND_PORT", "8186")),
        mcp_path=os.environ.get("NEXUS_MCP_PATH", "/mcp"),
    )
