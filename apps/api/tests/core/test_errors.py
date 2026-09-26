"""RFC 9457 problem+json mapping (docs/09 §3)."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

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
