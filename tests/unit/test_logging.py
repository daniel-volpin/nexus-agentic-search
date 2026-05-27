"""Tests for structured logging + redaction integration (Spec 11)."""

from __future__ import annotations

import asyncio
import json
import logging

import pytest

from nexus import telemetry
from nexus.logging import SecretRedactingFilter, get_logger, setup_logging


@pytest.fixture(autouse=True)
def _reset_request_id() -> None:
    yield
    telemetry.request_id_var.set(None)


# ---------- stdlib SecretRedactingFilter ----------


def test_filter_redacts_msg() -> None:
    rec = logging.LogRecord(
        name="t",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="leaked: sk-1234567890abcdefghijABCDEFGHIJ",
        args=(),
        exc_info=None,
    )
    SecretRedactingFilter().filter(rec)
    assert "[REDACTED]" in str(rec.msg)
    assert "sk-1234567890" not in str(rec.msg)


def test_filter_redacts_string_args() -> None:
    rec = logging.LogRecord(
        name="t",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="key=%s",
        args=("sk-1234567890abcdefghijABCDEFGHIJ",),
        exc_info=None,
    )
    SecretRedactingFilter().filter(rec)
    rendered = rec.msg % rec.args  # type: ignore[arg-type]
    assert "[REDACTED]" in rendered


def test_filter_passes_non_string_args_through() -> None:
    rec = logging.LogRecord(
        name="t",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="n=%d",
        args=(42,),
        exc_info=None,
    )
    SecretRedactingFilter().filter(rec)
    assert rec.args == (42,)


def test_filter_fails_closed_on_bad_msg() -> None:
    class Bad:
        def __str__(self) -> str:
            raise RuntimeError("boom")

    rec = logging.LogRecord(
        name="t",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg=Bad(),  # type: ignore[arg-type]
        args=(),
        exc_info=None,
    )
    SecretRedactingFilter().filter(rec)
    assert str(rec.msg) == "[REDACTION_FILTER_FAILED]"


# ---------- setup_logging end-to-end ----------


def test_stdlib_logger_writes_json_with_redaction(
    capsys: pytest.CaptureFixture[str],
) -> None:
    setup_logging(level="INFO", json_format=True)
    logging.getLogger("nexus.test").info(
        "user=%s key=%s", "alice", "sk-1234567890abcdefghijABCDEFGHIJ"
    )
    captured = capsys.readouterr().out
    # Stdlib emits its formatted string (single-line), filter has
    # already redacted the args.
    assert "[REDACTED]" in captured
    assert "sk-1234567890" not in captured


def test_structlog_emits_json_one_line_per_event(
    capsys: pytest.CaptureFixture[str],
) -> None:
    setup_logging(level="INFO", json_format=True)
    log = get_logger("nexus.test")
    log.info("hello", value=42)
    out = capsys.readouterr().out.strip().splitlines()[-1]
    record = json.loads(out)
    assert record["event"] == "hello"
    assert record["value"] == 42
    assert record["level"] == "info"
    assert "timestamp" in record


def test_structlog_redacts_string_fields(
    capsys: pytest.CaptureFixture[str],
) -> None:
    setup_logging(level="INFO", json_format=True)
    log = get_logger("nexus.test")
    log.info("event", key="sk-1234567890abcdefghijABCDEFGHIJ")
    out = capsys.readouterr().out.strip().splitlines()[-1]
    record = json.loads(out)
    assert record["key"] == "[REDACTED]"
    assert "sk-1234567890" not in out


def test_structlog_attaches_request_id(
    capsys: pytest.CaptureFixture[str],
) -> None:
    setup_logging(level="INFO", json_format=True)
    telemetry.bind_request_id("req-XYZ-12345")
    get_logger("nexus.test").info("happened")
    out = capsys.readouterr().out.strip().splitlines()[-1]
    record = json.loads(out)
    assert record["request_id"] == "req-XYZ-12345"


def test_request_id_absent_when_unbound(
    capsys: pytest.CaptureFixture[str],
) -> None:
    setup_logging(level="INFO", json_format=True)
    get_logger("nexus.test").info("happened")
    out = capsys.readouterr().out.strip().splitlines()[-1]
    record = json.loads(out)
    assert "request_id" not in record


def test_setup_logging_is_idempotent(
    capsys: pytest.CaptureFixture[str],
) -> None:
    setup_logging(level="INFO", json_format=True)
    setup_logging(level="INFO", json_format=True)
    # Logger still works and does not duplicate handler output.
    logging.getLogger("nexus.test").info("once")
    out = capsys.readouterr().out
    # Exactly one stdlib line, no duplicates.
    stdlib_lines = [line for line in out.splitlines() if "once" in line]
    assert len(stdlib_lines) == 1


async def test_request_id_propagates_across_await_points(
    capsys: pytest.CaptureFixture[str],
) -> None:
    setup_logging(level="INFO", json_format=True)
    log = get_logger("nexus.test")
    telemetry.bind_request_id("outer-rid")

    async def child() -> None:
        await asyncio.sleep(0)
        log.info("from-child")

    await child()
    out = capsys.readouterr().out.strip().splitlines()[-1]
    record = json.loads(out)
    assert record["request_id"] == "outer-rid"


def test_log_level_threshold_respected(capsys: pytest.CaptureFixture[str]) -> None:
    setup_logging(level="WARNING", json_format=True)
    log = get_logger("nexus.test")
    log.info("filtered")
    log.warning("kept")
    out = capsys.readouterr().out
    assert "filtered" not in out
    assert "kept" in out
