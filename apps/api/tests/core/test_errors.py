"""RFC 9457 problem+json mapping (docs/09 §3)."""

from __future__ import annotations

import psycopg
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy.exc import DataError as SADataError

from app.core.errors import NotFound, StepUpRequired, install_error_handlers


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
