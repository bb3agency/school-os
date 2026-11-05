"""RFC 9457 problem+json mapping (docs/09 §3)."""

from __future__ import annotations

import io
import json
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy.exc import DataError as SADataError
from sqlalchemy.exc import IntegrityError as SAIntegrityError

from app.core.config import Settings
from app.core.errors import Conflict, NotFound, StepUpRequired, install_error_handlers
from app.core.logging import ALLOWED_FIELDS, clear_context, setup_logging
from app.core.middleware import install_middleware


class Payload(BaseModel):
    dob: str
    count: int


def _app() -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/missing")
    def missing() -> None:
        raise NotFound()

    @app.get("/stepup")
    def stepup() -> None:
        raise StepUpRequired()

    @app.post("/validate")
    def validate(p: Payload) -> Payload:
        return p

    @app.post("/nul")
    def nul() -> None:
        orig = psycopg.DataError("PostgreSQL text fields cannot contain NUL (0x00) bytes")
        raise SADataError("INSERT INTO sis.x VALUES (%(v)s)", {"v": "a\x00b"}, orig)

    @app.post("/nul-raw")
    def nul_raw() -> None:
        raise psycopg.DataError("PostgreSQL text fields cannot contain NUL (0x00) bytes")

    @app.post("/other-data-error")
    def other_data_error() -> None:
        orig = psycopg.DataError("numeric field overflow")
        raise SADataError("UPDATE x SET n = %(n)s", {"n": 1}, orig)

    @app.post("/too-big-number")
    def out_of_range() -> None:
        orig = psycopg.errors.NumericValueOutOfRange("numeric field overflow")
        raise SADataError("INSERT INTO platform.x VALUES (%(n)s)", {"n": 10**20}, orig)

    @app.post("/too-big-date")
    def date_overflow() -> None:
        raise psycopg.errors.DatetimeFieldOverflow("date out of range")

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("secret internals: SELECT * FROM sis.students")

    return app


def test_not_found_is_problem_json() -> None:
    res = TestClient(_app()).get("/missing")
    assert res.status_code == 404
    assert res.headers["content-type"].startswith("application/problem+json")
    body = res.json()
    assert body["code"] == "not_found"
    assert body["status"] == 404
    assert body["instance"] == "/missing"


def test_step_up_is_428() -> None:
    res = TestClient(_app()).get("/stepup")
    assert res.status_code == 428
    assert res.json()["code"] == "step_up_required"


def test_validation_errors_never_echo_submitted_values() -> None:
    res = TestClient(_app()).post("/validate", json={"dob": "2012-03-14", "count": "Ravi Kumar"})
    assert res.status_code == 422
    assert "Ravi Kumar" not in res.text
    assert res.json()["errors"][0]["field"] == "count"


def test_unhandled_errors_hide_internals() -> None:
    res = TestClient(_app(), raise_server_exceptions=False).get("/boom")
    assert res.status_code == 500
    assert "SELECT" not in res.text
    assert "secret" not in res.text


def test_SEC_010_a_nul_character_is_a_422_not_a_500() -> None:
    """A NUL (U+0000) in any text that reaches PostgreSQL is refused by the driver. Many free-text
    fields accept it (audit 2026-10-06 R-12), so the refusal is mapped centrally to a 422 that
    says how to fix it, without echoing the value or the SQL."""
    client = TestClient(_app(), raise_server_exceptions=False)
    for path in ("/nul", "/nul-raw"):
        res = client.post(path)
        assert res.status_code == 422, path
        body = res.json()
        assert body["code"] == "invalid_characters"
        assert "INSERT" not in res.text
        assert "0x00" not in res.text


def test_other_driver_data_errors_still_hide_internals() -> None:
    res = TestClient(_app(), raise_server_exceptions=False).post("/other-data-error")
    assert res.status_code == 500
    assert "overflow" not in res.text
    assert "UPDATE" not in res.text


def test_R_13_a_number_or_date_out_of_range_is_a_422_not_a_500() -> None:
    """A value PostgreSQL cannot hold (SQLSTATE 22003/22008), such as an invoice amount past
    Numeric(14, 2), was a 500 (audit 2026-10-06 R-13). It is a 422 without the SQL or value."""
    client = TestClient(_app(), raise_server_exceptions=False)
    for path in ("/too-big-number", "/too-big-date"):
        res = client.post(path)
        assert res.status_code == 422, path
        assert res.headers["content-type"].startswith("application/problem+json")
        assert res.json()["code"] == "value_out_of_range"
        assert "INSERT" not in res.text
        assert "overflow" not in res.text


# --- D55: a CHECK refusal the input checks missed (SQLSTATE 23514) ---------------------------

_CONSTRAINT = "things_grade_range"
_SECRET_VALUE = "Kommineni Venkata Sai"  # synthetic; must never reach the body or the logs


class _Diag:
    def __init__(self, constraint: str | None) -> None:
        self.constraint_name = constraint
        self.table_name = "things"
        self.schema_name = "sis"


class _CheckViolation(psycopg.errors.CheckViolation):
    """A driver CheckViolation carrying a constraint name (``diag`` is read-only on the real
    class; ``test_check_violation_db.py`` uses PostgreSQL itself)."""

    @property
    def diag(self) -> _Diag:  # type: ignore[override]
        return _Diag(_CONSTRAINT)


def _stack_app() -> FastAPI:
    """The production stack: error handlers plus the middleware (the request-context middleware
    used to answer 500 itself before the ``Exception`` handler's mappings could run)."""
    app = FastAPI()
    install_error_handlers(app)
    install_middleware(app, Settings(env="ci"))

    @app.post("/api/v1/check")
    def check() -> None:
        orig = _CheckViolation(f'new row violates check constraint "{_CONSTRAINT}"')
        raise SAIntegrityError(
            "INSERT INTO sis.things (name) VALUES (%(n)s)", {"n": _SECRET_VALUE}, orig
        )

    @app.post("/api/v1/check-raw")
    def check_raw() -> None:
        raise _CheckViolation(f'new row violates check constraint "{_CONSTRAINT}"')

    @app.post("/api/v1/unique")
    def unique() -> None:
        orig = psycopg.errors.UniqueViolation('duplicate key value violates "things_name_key"')
        raise SAIntegrityError("INSERT INTO sis.things VALUES (%(n)s)", {"n": _SECRET_VALUE}, orig)

    @app.post("/api/v1/fk")
    def fk() -> None:
        orig = psycopg.errors.ForeignKeyViolation("insert violates foreign key constraint")
        raise SAIntegrityError("INSERT INTO sis.things VALUES (%(n)s)", {"n": 1}, orig)

    @app.post("/api/v1/translated")
    def translated() -> None:
        # A module translator (e.g. platform.repository.translate_db_error) keeps precedence.
        raise Conflict("This already exists.", code="duplicate")

    @app.post("/api/v1/nul")
    def nul() -> None:
        orig = psycopg.DataError("PostgreSQL text fields cannot contain NUL (0x00) bytes")
        raise SADataError("INSERT INTO sis.x VALUES (%(v)s)", {"v": "a\x00b"}, orig)

    @app.post("/api/v1/too-big")
    def too_big() -> None:
        orig = psycopg.errors.NumericValueOutOfRange("numeric field overflow")
        raise SADataError("INSERT INTO platform.x VALUES (%(n)s)", {"n": 10**20}, orig)

    return app


@pytest.fixture
def log_lines() -> Iterator[io.StringIO]:
    stream = io.StringIO()
    setup_logging(Settings(env="ci"), stream=stream)
    clear_context()
    yield stream
    clear_context()
    setup_logging(Settings(env="ci"))


def _lines(stream: io.StringIO) -> list[dict[str, Any]]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]


def test_D55_a_check_violation_is_a_422_invalid_value(log_lines: io.StringIO) -> None:
    client = TestClient(_stack_app(), raise_server_exceptions=False)
    for path in ("/api/v1/check", "/api/v1/check-raw"):
        res = client.post(path)
        assert res.status_code == 422, (path, res.text)
        assert res.headers["content-type"].startswith("application/problem+json")
        body = res.json()
        assert body["code"] == "invalid_value"
        assert body["errors"] == [
            {"field": "body", "code": "invalid_value", "message_key": "errors.invalid_value"}
        ]
        # Neither the constraint, the table, the SQL nor the value reaches the caller.
        for leak in (_CONSTRAINT, "things", "INSERT", "sis.", _SECRET_VALUE, "constraint"):
            assert leak not in res.text, (path, leak)


def test_D55_the_check_violation_is_logged_at_warning_with_the_constraint_name_only(
    log_lines: io.StringIO,
) -> None:
    TestClient(_stack_app(), raise_server_exceptions=False).post("/api/v1/check")
    lines = _lines(log_lines)
    refusals = [line for line in lines if line["event"] == "db.check_violation"]
    assert len(refusals) == 1, lines
    line = refusals[0]
    assert line["level"] == "WARN"
    assert line["constraint"] == _CONSTRAINT
    assert line["error_code"] == "invalid_value"
    assert set(line) <= set(ALLOWED_FIELDS)
    text = log_lines.getvalue()
    for leak in (_SECRET_VALUE, "INSERT", "violates", "sis.things"):
        assert leak not in text, leak
    # The access line says 422 at INFO, not an ERROR-level 500.
    access = [entry for entry in lines if entry["event"] == "http.request"]
    assert access[-1]["status"] == 422
    assert access[-1]["level"] == "INFO"


def test_D55_unique_and_foreign_key_violations_without_a_translator_stay_500(
    log_lines: io.StringIO,
) -> None:
    client = TestClient(_stack_app(), raise_server_exceptions=False)
    for path in ("/api/v1/unique", "/api/v1/fk"):
        res = client.post(path)
        assert res.status_code == 500, (path, res.text)
        assert res.json()["code"] == "internal_error"
        assert _SECRET_VALUE not in res.text
    lines = _lines(log_lines)
    assert not [line for line in lines if line["event"] == "db.check_violation"]
    access = [line for line in lines if line["event"] == "http.request"]
    assert [line["level"] for line in access] == ["ERROR", "ERROR"]


def test_D55_a_module_translation_keeps_precedence() -> None:
    res = TestClient(_stack_app(), raise_server_exceptions=False).post("/api/v1/translated")
    assert res.status_code == 409
    assert res.json()["code"] == "duplicate"


def test_D55_nul_and_out_of_range_are_422_through_the_middleware_too() -> None:
    """Through the full middleware stack the ``Exception`` handler's 422 mappings never reached
    the caller (the request-context middleware answered 500 first). They now run as database
    error handlers inside the middleware."""
    client = TestClient(_stack_app(), raise_server_exceptions=False)
    res = client.post("/api/v1/nul")
    assert (res.status_code, res.json()["code"]) == (422, "invalid_characters")
    res = client.post("/api/v1/too-big")
    assert (res.status_code, res.json()["code"]) == (422, "value_out_of_range")
