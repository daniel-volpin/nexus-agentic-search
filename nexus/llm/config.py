from __future__ import annotations

import os
import tomllib
from pathlib import Path

from pydantic import BaseModel, Field, field_validator, model_validator


class LLMRoleConfig(BaseModel):
    primary: str
    fallback: list[str] = Field(default_factory=list)
    max_input_tokens: int = Field(ge=1)
    max_output_tokens: int = Field(ge=1)

    @field_validator("primary")
    @classmethod
    def validate_primary(cls, value: str) -> str:
        _validate_model_id(value)
        return value

    @field_validator("fallback")
    @classmethod
    def validate_fallback(cls, value: list[str]) -> list[str]:
        for item in value:
            _validate_model_id(item)
        return value


class LLMConfig(BaseModel):
    roles: dict[str, LLMRoleConfig]
    daily_usd_budget: float = Field(default=10.0, gt=0.0)
    soft_budget_fraction: float = Field(default=0.8, gt=0.0, le=1.0)
    pricing_table_version: str = "unknown"

    @model_validator(mode="after")
    def validate_roles(self) -> LLMConfig:
        if not self.roles:
            raise ValueError("at least one role must be configured")
        return self

    @classmethod
    def from_toml(cls, content: str) -> LLMConfig:
        payload = tomllib.loads(content)
        role_section = payload.pop("role", {})
        payload["roles"] = role_section
        if os.getenv("DAILY_USD_BUDGET"):
            payload["daily_usd_budget"] = float(os.environ["DAILY_USD_BUDGET"])
        return cls.model_validate(payload)

    @classmethod
    def from_file(cls, path: str | Path) -> LLMConfig:
        return cls.from_toml(Path(path).read_text())


def _validate_model_id(value: str) -> None:
    provider, separator, model = value.partition("/")
    if not provider or not separator or not model:
        raise ValueError(f"model id must include a provider prefix: {value}")
    if "-" not in model:
        raise ValueError(f"model id must be pinned to a dated or versioned identifier: {value}")
    tail = model.rsplit("-", 1)[-1]
    if tail.isdigit() and len(tail) >= 3:
        return
    segments = model.split("-")
    if len(segments) >= 3 and all(part.isdigit() for part in segments[-3:]):
        return
    raise ValueError(f"model id must be pinned to a dated or versioned identifier: {value}")
