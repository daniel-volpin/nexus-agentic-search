from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, TypedDict, cast

import httpx

from nexus.crawl import wrap_untrusted

from .budget import DailyBudgetStore
from .config import LLMConfig
from .exceptions import BudgetExceeded, InputTooLarge, LLMUnavailable, SynthesisToolsDisabled
from .redaction import _redact_secrets
from .telemetry import LLMTelemetrySink
from .types import (
    CompletionResult,
    FinishReason,
    LiteLLMBackend,
    Message,
    ProviderResponse,
    StreamChunk,
    ToolCall,
    ToolSpec,
)

_PROVIDER_ENV_VARS = {
    "openai": ("OPENAI_API_KEY",),
    "anthropic": ("ANTHROPIC_API_KEY",),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
}

FinishReason = Literal["stop", "length", "tool_calls", "content_filter", "error"]


class LMStudioAvailability(TypedDict):
    available: bool
    api_base: str


@dataclass(frozen=True)
class _ResolvedModel:
    configured_model_id: str
    backend_model: str
    request_kwargs: dict[str, object] = field(default_factory=dict)


LMStudioProbe = Callable[[str], Awaitable[LMStudioAvailability]]


class LiteLLMClient:
    def __init__(
        self,
        *,
        config: LLMConfig,
        backend: LiteLLMBackend | None = None,
        model_availability_probe: LMStudioProbe | None = None,
        budget_db_path: str | Path = ":memory:",
        logger: logging.Logger | None = None,
        telemetry: LLMTelemetrySink | None = None,
    ) -> None:
        self._config = config
        self._backend = backend or _RuntimeLiteLLMBackend()
        self._model_availability_probe = model_availability_probe or self._probe_lmstudio_model
        self._budget = DailyBudgetStore(budget_db_path)
        self._logger = logger or logging.getLogger(__name__)
        self._telemetry = telemetry
        self._lmstudio_cache: dict[str, LMStudioAvailability] = {}

    async def complete(
        self,
        role: str,
        messages: list[Message],
        max_output_tokens: int,
        temperature: float = 0.0,
        tools: list[ToolSpec] | None = None,
    ) -> CompletionResult:
        started_at = time.perf_counter()
        if role == "synthesis" and tools:
            # Tool calling is disabled for synthesis at the API parameter,
            # not just by prompt: defense in depth against prompt-injection
            # in crawled content coercing a tool call. The orchestrator
            # already passes tools=None; this is the boundary backstop.
            raise SynthesisToolsDisabled("tools=None is required for role='synthesis'")
        role_config = self._config.roles[role]
        self._ensure_budget(role)
        input_tokens = self.count_tokens(role, messages)
        if input_tokens > role_config.max_input_tokens:
            raise InputTooLarge(
                f"input tokens {input_tokens} exceed limit {role_config.max_input_tokens}"
            )

        providers = await self._providers_for_role(role)
        errors: list[str] = []
        for index, resolved in enumerate(providers):
            try:
                response = await self._backend.acompletion(
                    model=resolved.backend_model,
                    messages=messages,
                    max_tokens=min(max_output_tokens, role_config.max_output_tokens),
                    temperature=temperature,
                    tools=tools,
                    **resolved.request_kwargs,
                )
                if not isinstance(response, ProviderResponse):
                    raise TypeError("streaming response returned for non-streaming completion")
                response = await self._repair_tool_calls_if_needed(
                    role=role,
                    model_id=resolved.backend_model,
                    response=response,
                    messages=messages,
                    max_output_tokens=max_output_tokens,
                    temperature=temperature,
                    request_kwargs=resolved.request_kwargs,
                )
                total_spend = self._budget.add_spend(role, response.cost_usd)
                cost_authoritative = self._is_cost_authoritative()
                result = CompletionResult(
                    text=response.text,
                    finish_reason=_normalize_finish_reason(response.finish_reason) or "error",
                    input_tokens=response.input_tokens,
                    output_tokens=response.output_tokens,
                    cost_usd=response.cost_usd,
                    tool_calls=response.tool_calls,
                    model_id=response.model or resolved.configured_model_id,
                    role=role,
                    fallback_used=index > 0,
                    model_drift=not _model_ids_match(
                        expected=resolved.configured_model_id,
                        actual=response.model or resolved.configured_model_id,
                    ),
                    cost_authoritative=cost_authoritative,
                )
                if result.model_drift:
                    self._logger.warning(
                        _redact_secrets(
                            "model drift detected "
                            f"role={role} expected={resolved.configured_model_id} actual={result.model_id}"
                        )
                    )
                if not cost_authoritative:
                    self._logger.warning(
                        _redact_secrets(
                            f"pricing table drift detected configured={self._config.pricing_table_version} runtime={self._backend.pricing_table_version}"
                        )
                    )
                if total_spend >= self._config.daily_usd_budget * self._config.soft_budget_fraction:
                    self._logger.warning(
                        _redact_secrets(f"soft budget threshold reached for role={role}")
                    )
                if total_spend >= self._config.daily_usd_budget:
                    self._logger.warning(
                        _redact_secrets(f"daily llm budget exhausted for role={role}")
                    )
                self._record_success_telemetry(result=result, latency_ms=_latency_ms(started_at))
                return result
            except Exception as exc:
                errors.append(_redact_secrets(str(exc)))

        self._record_error_telemetry(
            role=role, reason="all_providers_failed", latency_ms=_latency_ms(started_at)
        )
        raise LLMUnavailable("; ".join(errors) if errors else "no providers available")

    async def stream_complete(
        self,
        role: str,
        messages: list[Message],
        max_output_tokens: int,
        temperature: float = 0.0,
    ) -> AsyncIterator[StreamChunk]:
        started_at = time.perf_counter()
        role_config = self._config.roles[role]
        self._ensure_budget(role)
        input_tokens = self.count_tokens(role, messages)
        if input_tokens > role_config.max_input_tokens:
            raise InputTooLarge(
                f"input tokens {input_tokens} exceed limit {role_config.max_input_tokens}"
            )

        providers = await self._providers_for_role(role)
        errors: list[str] = []
        for index, resolved in enumerate(providers):
            try:
                stream = await self._backend.acompletion(
                    model=resolved.backend_model,
                    messages=messages,
                    max_tokens=min(max_output_tokens, role_config.max_output_tokens),
                    temperature=temperature,
                    stream=True,
                    stream_options={"include_usage": True},
                    **resolved.request_kwargs,
                )
                if isinstance(stream, ProviderResponse):
                    raise TypeError("non-streaming response returned for stream completion")

                input_count = 0
                output_count = 0
                cost_usd = 0.0
                resolved_model = resolved.configured_model_id
                finish_reason = None
                async for raw_chunk in stream:
                    choice = (raw_chunk.get("choices") or [{}])[0]
                    delta = choice.get("delta") or {}
                    text_delta = delta.get("content", "")
                    finish_reason = _normalize_finish_reason(choice.get("finish_reason"))
                    usage = raw_chunk.get("usage") or {}
                    if usage:
                        input_count = int(usage.get("prompt_tokens") or 0)
                        output_count = int(usage.get("completion_tokens") or 0)
                    if raw_chunk.get("_response_cost") is not None:
                        cost_usd = float(raw_chunk["_response_cost"])
                    if raw_chunk.get("model"):
                        resolved_model = str(raw_chunk["model"])
                    yield StreamChunk(
                        text_delta=text_delta,
                        finish_reason=finish_reason,
                        input_tokens=input_count,
                        output_tokens=output_count,
                        cost_usd=cost_usd,
                        model_id=resolved_model,
                        role=role,
                        fallback_used=index > 0,
                        model_drift=not _model_ids_match(
                            expected=resolved.configured_model_id,
                            actual=resolved_model,
                        ),
                    )

                self._budget.add_spend(role, cost_usd)
                result = CompletionResult(
                    text="",
                    finish_reason=finish_reason or "error",
                    input_tokens=input_count,
                    output_tokens=output_count,
                    cost_usd=cost_usd,
                    tool_calls=[],
                    model_id=resolved_model,
                    role=role,
                    fallback_used=index > 0,
                    model_drift=not _model_ids_match(
                        expected=resolved.configured_model_id,
                        actual=resolved_model,
                    ),
                    cost_authoritative=self._is_cost_authoritative(),
                )
                self._record_success_telemetry(result=result, latency_ms=_latency_ms(started_at))
                return
            except Exception as exc:
                errors.append(_redact_secrets(str(exc)))

        self._record_error_telemetry(
            role=role, reason="all_providers_failed", latency_ms=_latency_ms(started_at)
        )
        raise LLMUnavailable("; ".join(errors) if errors else "no providers available")

    def count_tokens(self, role: str, messages: list[Message]) -> int:
        model_id = self._token_counter_model(role)
        return int(self._backend.token_counter(model=model_id, messages=messages))

    def budget_remaining_usd(self, role: str) -> float:
        return max(0.0, self._config.daily_usd_budget - self._budget.get_spend(role))

    @staticmethod
    def wrap_untrusted(url: str, content_hash: str, body: str) -> str:
        return wrap_untrusted(url, content_hash, body)

    def _ensure_budget(self, role: str) -> None:
        if self._budget.get_spend(role) >= self._config.daily_usd_budget:
            self._record_error_telemetry(role=role, reason="budget_exhausted", latency_ms=0)
            raise BudgetExceeded(f"daily budget exhausted for role={role}")

    async def _providers_for_role(self, role: str) -> list[_ResolvedModel]:
        role_config = self._config.roles[role]
        available: list[_ResolvedModel] = []
        for model_id in [role_config.primary, *role_config.fallback]:
            resolved = await self._resolve_model(model_id)
            if resolved is not None:
                available.append(resolved)
        if not available:
            raise LLMUnavailable(f"no configured providers available for role={role}")
        return available

    async def _resolve_model(self, model_id: str) -> _ResolvedModel | None:
        provider, _, raw_model = model_id.partition("/")
        if provider == "lmstudio":
            status = await self._lmstudio_status(model_id)
            if not status["available"]:
                return None
            return _ResolvedModel(
                configured_model_id=model_id,
                backend_model=f"openai/{raw_model}",
                request_kwargs={"api_base": status["api_base"], "api_key": "lm-studio"},
            )
        if not self._provider_is_configured(model_id):
            return None
        return _ResolvedModel(configured_model_id=model_id, backend_model=model_id)

    async def _lmstudio_status(self, model_id: str) -> LMStudioAvailability:
        cached = self._lmstudio_cache.get(model_id)
        if cached is not None:
            return cached
        status = await self._model_availability_probe(model_id)
        self._lmstudio_cache[model_id] = status
        return status

    def _provider_is_configured(self, model_id: str) -> bool:
        if not isinstance(self._backend, _RuntimeLiteLLMBackend):
            return True
        provider = model_id.split("/", 1)[0]
        if provider == "vertex_ai":
            # LiteLLM Vertex requires project + region. Auth comes from
            # GOOGLE_APPLICATION_CREDENTIALS or ADC (e.g. gcloud auth app-default login).
            return bool(os.getenv("VERTEX_PROJECT")) and bool(os.getenv("VERTEX_LOCATION"))
        env_names = _PROVIDER_ENV_VARS.get(provider)
        if env_names is None:
            return True
        return any(os.getenv(name) for name in env_names)

    def _is_cost_authoritative(self) -> bool:
        return self._backend.pricing_table_version == self._config.pricing_table_version

    def _record_success_telemetry(self, *, result: CompletionResult, latency_ms: int) -> None:
        if self._telemetry is None:
            return
        self._telemetry.record_span(
            f"llm.{result.role}",
            {
                "role": result.role,
                "model_id_resolved": result.model_id,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "cost_usd": result.cost_usd,
                "latency_ms": latency_ms,
                "finish_reason": result.finish_reason,
                "fallback_used": result.fallback_used,
                "model_drift": result.model_drift,
            },
        )
        self._telemetry.increment_counter(
            "llm_input_tokens_total",
            result.input_tokens,
            {"role": result.role, "model": result.model_id},
        )
        self._telemetry.increment_counter(
            "llm_output_tokens_total",
            result.output_tokens,
            {"role": result.role, "model": result.model_id},
        )
        self._telemetry.increment_counter(
            "llm_cost_usd_total", result.cost_usd, {"role": result.role}
        )
        self._telemetry.observe_histogram("llm_latency_ms", latency_ms, {"role": result.role})
        self._telemetry.set_gauge(
            "llm_budget_remaining_usd",
            self.budget_remaining_usd(result.role),
            {"role": result.role},
        )

    def _record_error_telemetry(self, *, role: str, reason: str, latency_ms: int) -> None:
        if self._telemetry is None:
            return
        self._telemetry.increment_counter("llm_errors_total", 1, {"role": role, "reason": reason})
        self._telemetry.observe_histogram("llm_latency_ms", latency_ms, {"role": role})

    async def _repair_tool_calls_if_needed(
        self,
        *,
        role: str,
        model_id: str,
        response: ProviderResponse,
        messages: list[Message],
        max_output_tokens: int,
        temperature: float,
        request_kwargs: dict[str, object],
    ) -> ProviderResponse:
        if not _has_malformed_tool_call(response.tool_calls):
            return response

        repair_messages: list[Message] = [
            {
                "role": "system",
                "content": "Retry the previous response. Every tool call arguments field must be valid JSON with no trailing text.",
            },
            *messages,
        ]
        retried = await self._backend.acompletion(
            model=model_id,
            messages=repair_messages,
            max_tokens=min(max_output_tokens, self._config.roles[role].max_output_tokens),
            temperature=temperature,
            tools=None,
            **request_kwargs,
        )
        if not isinstance(retried, ProviderResponse):
            raise TypeError("streaming response returned for non-streaming completion")
        if _has_malformed_tool_call(retried.tool_calls):
            return ProviderResponse(
                text=retried.text,
                finish_reason="error",
                input_tokens=retried.input_tokens,
                output_tokens=retried.output_tokens,
                cost_usd=retried.cost_usd,
                tool_calls=[],
                model=retried.model or model_id,
            )
        return retried

    def _token_counter_model(self, role: str) -> str:
        configured = [self._config.roles[role].primary, *self._config.roles[role].fallback]
        for model_id in configured:
            if not model_id.startswith("lmstudio/"):
                return model_id
        return configured[0]

    async def _probe_lmstudio_model(self, model_id: str) -> LMStudioAvailability:
        _, _, raw_model = model_id.partition("/")
        api_base = _lmstudio_api_base()
        if not api_base:
            return {"available": False, "api_base": ""}

        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                response = await client.get(f"{api_base}/models")
                response.raise_for_status()
        except httpx.HTTPError:
            return {"available": False, "api_base": api_base}

        payload = response.json()
        data = payload.get("data", [])
        available_models = {
            str(item.get("id", ""))
            for item in data
            if isinstance(item, dict)
        }
        return {"available": raw_model in available_models, "api_base": api_base}


class _RuntimeLiteLLMBackend:
    def __init__(self) -> None:
        try:
            import litellm
        except ImportError as exc:  # pragma: no cover - exercised only when dependency missing
            raise RuntimeError("litellm is required for the runtime backend") from exc

        litellm.set_verbose = False
        self._litellm = litellm

    @property
    def pricing_table_version(self) -> str:
        return getattr(self._litellm, "__version__", "unknown")

    async def acompletion(self, **kwargs: object) -> ProviderResponse | AsyncIterator[dict]:
        response = await self._litellm.acompletion(**kwargs)
        if kwargs.get("stream"):
            return response

        choice = response.choices[0]
        message = choice.message
        usage = getattr(response, "usage", None)
        tool_calls = [
            _coerce_tool_call(item) for item in (getattr(message, "tool_calls", None) or [])
        ]
        return ProviderResponse(
            text=message.content or "",
            finish_reason=choice.finish_reason or "error",
            input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
            cost_usd=_coerce_response_cost(getattr(response, "_hidden_params", {})),
            tool_calls=tool_calls,
            model=getattr(response, "model", "") or str(kwargs.get("model") or ""),
        )

    def token_counter(self, *, model: str, messages: list[Message]) -> int:
        return int(self._litellm.token_counter(model=model, messages=messages))


def _coerce_tool_call(item: object) -> ToolCall:
    identifier = getattr(item, "id", "")
    function = getattr(item, "function", None)
    name = getattr(function, "name", "")
    arguments = getattr(function, "arguments", {}) or {}
    if isinstance(arguments, str):
        arguments = {"raw": arguments}
    return {"id": str(identifier), "name": str(name), "arguments": dict(arguments)}


def _normalize_finish_reason(value: object) -> FinishReason | None:
    if value is None:
        return None
    lowered = str(value).lower()
    # Explicit per-literal returns so the result is typed as FinishReason,
    # not str; anything unrecognized collapses to "error".
    if lowered == "stop":
        return "stop"
    if lowered == "length":
        return "length"
    if lowered == "tool_calls":
        return "tool_calls"
    if lowered == "content_filter":
        return "content_filter"
    if lowered == "error":
        return "error"
    return "error"


def _has_malformed_tool_call(tool_calls: list[ToolCall]) -> bool:
    for tool_call in tool_calls:
        raw_value = tool_call.get("arguments", {}).get("raw")
        if not isinstance(raw_value, str):
            continue
        try:
            json.loads(raw_value)
        except json.JSONDecodeError:
            return True
    return False


def _latency_ms(started_at: float) -> int:
    return int((time.perf_counter() - started_at) * 1000)


def _lmstudio_api_base() -> str:
    base_url = os.getenv("LMSTUDIO_BASE_URL", "http://127.0.0.1:1234").rstrip("/")
    return f"{base_url}/v1"


def _model_ids_match(*, expected: str, actual: str) -> bool:
    if expected == actual:
        return True
    provider, _, model = expected.partition("/")
    if provider != "lmstudio":
        return False
    return actual in {model, f"openai/{model}"}


def _coerce_response_cost(hidden_params: object) -> float:
    if not isinstance(hidden_params, dict):
        return 0.0
    raw_cost = hidden_params.get("response_cost")
    if raw_cost is None:
        return 0.0
    return float(raw_cost)
