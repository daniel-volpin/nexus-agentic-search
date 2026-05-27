"""Structured logging (Spec 11).

Logs are JSON-by-default, written to stdout. Every record carries the
``request_id`` (from :mod:`nexus.telemetry`) and passes through the
secret-redaction filter (Spec 10).

Bootstrapping
-------------
Call :func:`setup_logging` once at process start. Both the stdlib root
logger and structlog are configured. Modules then use either:

- ``logging.getLogger(__name__)`` (existing modules — already in use)
- ``nexus.logging.get_logger(__name__)`` (new modules — preferred)

The two paths converge: the stdlib root has the redaction filter
installed, and structlog's renderer chain calls into the same
redaction primitive. Migration is per-module and not part of this PR.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

from nexus.llm.redaction import _SECRET_PATTERNS, _redact_secrets  # noqa: F401  re-exported
from nexus.telemetry import get_request_id


class SecretRedactingFilter(logging.Filter):
    """Stdlib logging filter that redacts known secret patterns from
    ``record.msg`` and any positional args. Fail-closed: if the filter
    cannot process the record, the original text is replaced with a
    placeholder so a malformed record cannot silently leak.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            # Coerce to str so non-string msg objects (lazy formatters,
            # exception payloads) still pass through the redactor and so
            # a str() that raises is caught here, not downstream.
            record.msg = _redact_secrets(str(record.msg))
            if record.args:
                record.args = tuple(
                    _redact_secrets(a) if isinstance(a, str) else a for a in record.args
                )
        except Exception:  # fail-closed
            record.msg = "[REDACTION_FILTER_FAILED]"
            record.args = ()
        return True


def _redaction_processor(_logger: Any, _method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """structlog processor — redact every string field in the event dict."""
    for key, value in list(event_dict.items()):
        if isinstance(value, str):
            event_dict[key] = _redact_secrets(value)
    return event_dict


def _request_id_processor(_logger: Any, _method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """structlog processor — attach the current request id if any."""
    rid = get_request_id()
    if rid is not None and "request_id" not in event_dict:
        event_dict["request_id"] = rid
    return event_dict


def setup_logging(
    *,
    level: str = "INFO",
    json_format: bool = True,
) -> None:
    """Configure stdlib + structlog. Idempotent."""
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    # Stdlib root: stdout handler with secret redaction.
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(SecretRedactingFilter())
    handler.setLevel(numeric_level)

    root = logging.getLogger()
    # Replace existing handlers to make setup idempotent.
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(numeric_level)

    # Structlog: JSON output (or dev console), redaction + request_id + timestamp.
    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        _request_id_processor,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _redaction_processor,
    ]
    if json_format:
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer(colors=True))

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        cache_logger_on_first_use=False,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a structlog logger bound to ``name``.

    Safe to call before :func:`setup_logging`; structlog's defaults apply
    until configured.
    """
    return structlog.get_logger(name)


__all__ = [
    "SecretRedactingFilter",
    "get_logger",
    "setup_logging",
]
