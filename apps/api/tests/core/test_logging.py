"""Structured logging with a field allowlist and redaction (SEC-008, NFR-OBS-001, invariant 5).

Seeded values below are synthetic. No real student data (invariant 11).
"""

from __future__ import annotations

import io
import json
import logging
import uuid
from collections.abc import Iterator
from datetime import datetime
from typing import Any

import pytest
from opentelemetry.sdk.trace import TracerProvider
from pydantic import SecretStr

from app.core import logging as core_logging
from app.core.config import Environment, Settings
from app.core.logging import (
    ALLOWED_FIELDS,
    INVALID_EVENT,
    bind_context,
    bind_task_context,
    clear_context,
    get_context,
    get_logger,
    request_scope,
    reset_context,
    setup_logging,
)
from app.core.redaction import verhoeff_check_digit

SYNTHETIC_NAME = "Kommineni Venkata Sai"
SYNTHETIC_TELUGU_NAME = "వెంకట సాయి"
SYNTHETIC_DOB = "2012-03-14"
SYNTHETIC_PHONE = "9876543210"
SYNTHETIC_EMAIL = "kommineni.sai@example.org"
_BODY = "73920184556"
SYNTHETIC_AADHAAR = _BODY + verhoeff_check_digit(_BODY)
SEEDED = [SYNTHETIC_NAME, SYNTHETIC_TELUGU_NAME, SYNTHETIC_DOB, SYNTHETIC_PHONE, SYNTHETIC_EMAIL]

DOC_EXAMPLE_KEYS = {
    "ts",
    "level",
    "service",
    "version",
    "env",
    "request_id",
    "trace_id",
    "tenant_id",
    "user_id",
    "event",
    "route",
    "status",
    "duration_ms",
}


def _settings(env: Environment = Environment.CI, level: str = "INFO") -> Settings:
    if env in (Environment.STAGING, Environment.PROD):
        return Settings(
            env=env,
            log_level=level,
            version="2026.10.1",
            key_wrapper="kms",
            database_url=SecretStr(
                "postgresql+psycopg://sos_app:x@db/schoolos?sslmode=verify-full"
            ),
            platform_database_url=SecretStr(
                "postgresql+psycopg://sos_platform:x@db/schoolos?sslmode=verify-full"
            ),
            service_token_key=SecretStr("k" * 48),
        )
    return Settings(env=env, log_level=level, version="2026.10.1")


class Captured:
    def __init__(self) -> None:
        self.stream = io.StringIO()

    @property
    def text(self) -> str:
        return self.stream.getvalue()

    def lines(self) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self.text.splitlines() if line.strip()]

    def one(self) -> dict[str, Any]:
        lines = self.lines()
        assert len(lines) == 1, lines
        return lines[0]


@pytest.fixture
def capture() -> Iterator[Captured]:
    cap = Captured()
    setup_logging(_settings(), stream=cap.stream)
    clear_context()
    yield cap
    clear_context()
    # Back to stdout: later tests (any module, any order) must not log into this buffer.
    setup_logging(_settings())


def _assert_no_pii(text: str) -> None:
    for value in SEEDED:
        assert value not in text
    assert SYNTHETIC_AADHAAR not in text
    assert SYNTHETIC_AADHAAR[:8] not in text


# --- Shape --------------------------------------------------------------------------------


def test_NFR_OBS_001_json_line_shape_matches_docs_example(capture: Captured) -> None:
    tenant, user = str(uuid.uuid4()), str(uuid.uuid4())
    bind_context(request_id="req_0123456789abcdef", tenant_id=tenant, user_id=user)
    tracer = TracerProvider().get_tracer("test")
    with tracer.start_as_current_span("unit") as span:
        get_logger("app.changes").info(
            "change_request.approved",
            route="POST /api/v1/change-requests/{id}/approve",
            method="POST",
            status=200,
            duration_ms=84,
        )
        ctx = span.get_span_context()
    line = capture.one()
    assert set(line) >= DOC_EXAMPLE_KEYS
    assert set(line) <= set(ALLOWED_FIELDS) | {"dropped_fields"}
    assert line["level"] == "INFO"
    assert line["service"] == "api"
    assert line["version"] == "2026.10.1"
    assert line["env"] == "ci"
    assert line["event"] == "change_request.approved"
    assert line["tenant_id"] == tenant
    assert line["user_id"] == user
    assert line["request_id"] == "req_0123456789abcdef"
    assert line["trace_id"] == format(ctx.trace_id, "032x")
    assert line["span_id"] == format(ctx.span_id, "016x")
    assert line["logger"] == "app.changes"
    assert line["status"] == 200
    assert line["ts"].endswith("Z")
    assert datetime.fromisoformat(line["ts"]).utcoffset() is not None
    assert "dropped_fields" not in line


def test_NFR_OBS_001_one_json_object_per_line(capture: Captured) -> None:
    log = get_logger("app.test")
    log.info("a.one")
    log.warning("a.two")
    lines = capture.lines()
    assert [ln["event"] for ln in lines] == ["a.one", "a.two"]
    assert lines[1]["level"] == "WARN"


def test_NFR_OBS_001_stdout_is_default(capsys: pytest.CaptureFixture[str]) -> None:
    setup_logging(_settings())
    get_logger("app.test").info("stdout.works")
    out = capsys.readouterr().out
    assert json.loads(out.strip().splitlines()[-1])["event"] == "stdout.works"


# --- Allowlist and PII ----------------------------------------------------------------------


def test_SEC_008_unknown_fields_are_dropped_and_counted(capture: Captured) -> None:
    get_logger("app.students").info(
        "student.created",
        student_name=SYNTHETIC_NAME,
        dob=SYNTHETIC_DOB,
        resource_type="student",
    )
    line = capture.one()
    assert line["resource_type"] == "student"
    assert "student_name" not in line
    assert "dob" not in line
    assert line["dropped_fields"] == 2
    _assert_no_pii(capture.text)


def test_SEC_008_sheet_editor_fields_are_dropped(capture: Captured) -> None:
    """FR-IMP-008, FR-DOC-010: cell values, headers and edit lists of the sheet editor never
    reach a log line, even if a caller passes them by mistake; only the counts stay."""
    get_logger("app.imports").info(
        "imports.sheet.edited",
        value=SYNTHETIC_NAME,
        old_value=SYNTHETIC_TELUGU_NAME,
        new_value=SYNTHETIC_AADHAAR,
        header="Student name",
        cells=[{"column": 1, "value": SYNTHETIC_NAME}],
        edits={"B3": SYNTHETIC_PHONE},
        resource_type="import_batch",
        count=1,
    )
    line = capture.one()
    for field in ("value", "old_value", "new_value", "header", "cells", "edits"):
        assert field not in line
    assert line["count"] == 1
    assert line["dropped_fields"] == 6
    _assert_no_pii(capture.text)


def test_SEC_008_non_scalar_values_in_allowed_fields_are_dropped(capture: Captured) -> None:
    get_logger("app.x").info("x.done", resource_id={"name": SYNTHETIC_NAME}, count=3)
    line = capture.one()
    assert "resource_id" not in line
    assert line["count"] == 3
    assert line["dropped_fields"] == 1
    _assert_no_pii(capture.text)


def test_SEC_008_seeded_pii_never_reaches_output(capture: Captured) -> None:
    log = get_logger("app.students")
    log.info(
        "student.updated",
        name=SYNTHETIC_NAME,
        telugu_name=SYNTHETIC_TELUGU_NAME,
        dob=SYNTHETIC_DOB,
        phone=SYNTHETIC_PHONE,
        aadhaar=SYNTHETIC_AADHAAR,
        email=SYNTHETIC_EMAIL,
    )
    # Allowed fields that accidentally carry PII are redacted.
    log.info("student.lookup", resource_id=f"{SYNTHETIC_PHONE}", action=SYNTHETIC_EMAIL)
    log.info("student.lookup", outcome=f"aadhaar {SYNTHETIC_AADHAAR}")
    # Free text in the event is rejected.
    log.info(f"Created {SYNTHETIC_NAME} {SYNTHETIC_TELUGU_NAME} born {SYNTHETIC_DOB}")
    log.info("x", positional="ignored")
    _assert_no_pii(capture.text)
    lines = capture.lines()
    assert lines[1]["resource_id"] == "XXXXXX3210"
    assert lines[1]["action"] == "[redacted-email]"
    assert lines[2]["outcome"] == f"aadhaar XXXX XXXX {SYNTHETIC_AADHAAR[-4:]}"
    assert lines[3]["event"] == INVALID_EVENT


@pytest.mark.parametrize(
    "event",
    ["Student created", "student created", "student.Created", "student-created", "", "a" * 101],
)
def test_SEC_008_event_must_be_snake_or_dot_case(capture: Captured, event: str) -> None:
    get_logger("app.x").info(event)
    assert capture.one()["event"] == INVALID_EVENT


def test_SEC_008_event_digits_are_still_redacted(capture: Captured) -> None:
    get_logger("app.x").info(f"student.{SYNTHETIC_PHONE}")
    assert capture.one()["event"] == "student.XXXXXX3210"


def test_SEC_008_large_integers_are_redacted(capture: Captured) -> None:
    get_logger("app.x").info("x.count", count=int(SYNTHETIC_PHONE), status=200)
    line = capture.one()
    assert line["count"] == "XXXXXX3210"
    assert line["status"] == 200


def test_SEC_008_uuid_identifiers_are_kept_verbatim(capture: Captured) -> None:
    # An all-digit UUID section must not be mangled by phone/Aadhaar masking.
    rid = uuid.UUID("98765432-1012-7345-8678-901234567890")
    get_logger("app.x").info("x.read", resource_id=rid, resource_type="student")
    assert capture.one()["resource_id"] == str(rid)


# --- Levels ---------------------------------------------------------------------------------


def test_NFR_OBS_001_level_filtering_from_settings() -> None:
    cap = Captured()
    setup_logging(_settings(level="WARNING"), stream=cap.stream)
    log = get_logger("app.x")
    log.info("x.info")
    log.debug("x.debug")
    log.warning("x.warn")
    log.error("x.error")
    assert [ln["level"] for ln in cap.lines()] == ["WARN", "ERROR"]


def test_NFR_OBS_001_debug_is_disabled_in_prod() -> None:
    cap = Captured()
    setup_logging(_settings(env=Environment.PROD, level="DEBUG"), stream=cap.stream)
    get_logger("app.x").debug("x.debug")
    get_logger("app.x").info("x.info")
    assert [ln["event"] for ln in cap.lines()] == ["x.info"]


# --- Exceptions -----------------------------------------------------------------------------


def _raise_with_pii() -> None:
    raise ValueError(f"bad row for {SYNTHETIC_NAME} {SYNTHETIC_AADHAAR}")


def test_SEC_008_exceptions_log_type_only_outside_local(capture: Captured) -> None:
    try:
        _raise_with_pii()
    except ValueError:
        get_logger("app.x").exception("import.row_failed")
    line = capture.one()
    assert line["level"] == "ERROR"
    assert line["error_type"] == "ValueError"
    assert "traceback" not in line
    assert "Traceback" not in capture.text
    _assert_no_pii(capture.text)


def test_SEC_008_local_tracebacks_are_redacted() -> None:
    cap = Captured()
    setup_logging(_settings(env=Environment.LOCAL), stream=cap.stream)
    try:
        _raise_with_pii()
    except ValueError:
        get_logger("app.x").exception("import.row_failed")
    line = cap.one()
    assert line["error_type"] == "ValueError"
    assert "Traceback" in line["traceback"]
    assert SYNTHETIC_AADHAAR not in cap.text


# --- Standard library routing ---------------------------------------------------------------


def test_SEC_008_stdlib_logs_use_the_same_pipeline(capture: Captured) -> None:
    std = logging.getLogger("uvicorn.error")
    std.warning("Worker for %s failed", SYNTHETIC_NAME, extra={"student": SYNTHETIC_NAME})
    line = capture.one()
    assert line["logger"] == "uvicorn.error"
    assert line["level"] == "WARN"
    # Arguments are never interpolated; only the constant template is kept.
    assert line["event"] == "Worker for %s failed"
    assert line["service"] == "api"
    _assert_no_pii(capture.text)


def test_SEC_008_stdlib_non_string_messages_are_not_rendered(capture: Captured) -> None:
    logging.getLogger("celery.app.trace").error(ValueError(SYNTHETIC_NAME))
    assert capture.one()["event"] == "log.unstructured"
    _assert_no_pii(capture.text)


def test_SEC_008_stdlib_exceptions_log_type_only(capture: Captured) -> None:
    try:
        _raise_with_pii()
    except ValueError:
        logging.getLogger("celery.worker").exception("Task failed")
    line = capture.one()
    assert line["error_type"] == "ValueError"
    _assert_no_pii(capture.text)


def test_SEC_008_uvicorn_access_log_is_disabled(capture: Captured) -> None:
    logging.getLogger("uvicorn.access").info('%s - "%s %s"', "10.0.0.1", "GET", "/x?q=1")
    assert capture.text == ""


def test_SEC_008_sql_echo_loggers_are_capped_at_warning(capture: Captured) -> None:
    assert not logging.getLogger("sqlalchemy.engine").isEnabledFor(logging.INFO)
    assert not logging.getLogger("sqlalchemy.engine.Engine").isEnabledFor(logging.INFO)


def test_NFR_OBS_001_setup_is_idempotent() -> None:
    cap = Captured()
    for _ in range(3):
        setup_logging(_settings(), stream=cap.stream)
    get_logger("app.x").info("x.once")
    assert len(cap.lines()) == 1
    ours = [h for h in logging.getLogger().handlers if getattr(h, "sos_handler", False)]
    assert len(ours) == 1


def test_NFR_OBS_001_service_override_for_worker() -> None:
    cap = Captured()
    setup_logging(_settings(), service="worker", stream=cap.stream)
    get_logger("app.x").info("x.worker")
    assert cap.one()["service"] == "worker"


# --- Context --------------------------------------------------------------------------------


def test_NFR_OBS_001_bind_context_rejects_unknown_keys() -> None:
    with pytest.raises(ValueError, match="student_name"):
        bind_context(student_name=SYNTHETIC_NAME)


def test_NFR_OBS_001_reset_context_restores_previous_values(capture: Captured) -> None:
    outer = bind_context(request_id="req_outer_123")
    inner = bind_context(request_id="req_inner_456")
    assert get_context()["request_id"] == "req_inner_456"
    reset_context(inner)
    assert get_context()["request_id"] == "req_outer_123"
    reset_context(outer)
    assert "request_id" not in get_context()


def test_NFR_OBS_001_request_scope_collects_late_bindings() -> None:
    tenant = str(uuid.uuid4())
    with request_scope() as fields:
        tokens = bind_context(tenant_id=tenant)
        reset_context(tokens)
    assert fields == {"tenant_id": tenant}


def test_NFR_OBS_001_task_context_from_kwargs(capture: Captured) -> None:
    job, tenant = uuid.uuid4(), uuid.uuid4()
    tokens = bind_task_context(
        task_name="dq.run",
        kwargs={"job_id": str(job), "tenant_id": str(tenant), "name": SYNTHETIC_NAME},
        queue="dq",
        attempt=1,
    )
    get_logger("app.dq").info("dq.run_started")
    reset_context(tokens)
    get_logger("app.dq").info("dq.after")
    first, second = capture.lines()
    assert first["job_id"] == str(job)
    assert first["tenant_id"] == str(tenant)
    assert first["task_name"] == "dq.run"
    assert first["queue"] == "dq"
    assert first["attempt"] == 1
    assert "job_id" not in second
    _assert_no_pii(capture.text)


def test_NFR_OBS_001_task_context_ignores_non_uuid_ids(capture: Captured) -> None:
    tokens = bind_task_context(
        task_name="dq.run", kwargs={"job_id": SYNTHETIC_NAME, "tenant_id": 5}, queue=None, attempt=0
    )
    try:
        get_logger("app.dq").info("dq.run_started")
    finally:
        reset_context(tokens)
    line = capture.one()
    assert "job_id" not in line
    assert "tenant_id" not in line
    _assert_no_pii(capture.text)


def test_NFR_OBS_001_get_logger_configures_lazily(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(core_logging, "_configured", False)
    calls: list[Settings] = []
    monkeypatch.setattr(core_logging, "setup_logging", lambda s, **_: calls.append(s))
    get_logger("app.x")
    assert len(calls) == 1
