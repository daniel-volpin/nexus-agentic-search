from __future__ import annotations

import json
from typing import Any

from nexus.search.types import SearchRequest

from .types import MCPConfig


def validate_input(payload: dict, *, config: MCPConfig) -> SearchRequest:
    encoded = json.dumps(payload).encode("utf-8")
    if len(encoded) > config.input_json_max_bytes:
        raise ValueError("input_too_large")
    return SearchRequest.model_validate(payload)


def truncate_answer_payload(payload: dict, *, config: MCPConfig) -> dict:
    out = dict(payload)
    degraded = bool(out.get("degraded", False))

    answer_text = str(out.get("answer_text", ""))
    if len(answer_text) > config.answer_text_max_bytes:
        out["answer_text"] = answer_text[: config.answer_text_max_bytes]
        degraded = True

    citations = list(out.get("citations", []))
    if len(citations) > config.max_citations:
        out["citations"] = citations[: config.max_citations]
        degraded = True

    documents = list(out.get("documents", []))
    if len(documents) > config.max_documents:
        out["documents"] = documents[: config.max_documents]
        degraded = True

    out["degraded"] = degraded
    return out


def validate_output(payload: dict[str, Any]) -> bool:
    required = {
        "answer_text": str,
        "citations": list,
        "rejected_citations": list,
        "documents": list,
        "cost_usd": (int, float),
        "tokens_in": int,
        "tokens_out": int,
        "latency_ms": int,
        "degraded": bool,
        "ungrounded": bool,
    }
    for key, expected_type in required.items():
        if key not in payload or not isinstance(payload[key], expected_type):
            return False
    return True
