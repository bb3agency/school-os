"""Request ID, access log, API security headers and body size limit.

SEC-008 (no PII in logs), SEC-010 (security headers, docs/07 §11), NFR-OBS-001 (request
correlation), docs/09 §2 (X-Request-Id), docs/07 §11 API4 (request size limits).
Seeded values are synthetic.
"""

from __future__ import annotations

import io
import json
import re
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.core.config import Settings
from app.core.errors import NotFound, install_error_handlers
from app.core.logging import bind_context, setup_logging
from app.core.middleware import (
    REQUEST_ID_HEADER,
    BodySizeLimitMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
    install_middleware,
    new_request_id,
)
from app.main import create_app

SYNTHETIC_NAME = "Kommineni Venkata Sai"
SYNTHETIC_TELUGU_NAME = "వెంకట సాయి"
SYNTHETIC_DOB = "2012-03-14"
SYNTHETIC_PHONE = "9876543210"
SEEDED = [SYNTHETIC_NAME, SYNTHETIC_TELUGU_NAME, SYNTHETIC_DOB, SYNTHETIC_PHONE]
TENANT = str(uuid.UUID("0192f3a4-5b6c-7d8e-9f01-23456789abcd"))
GENERATED_ID = re.compile(r"^req_[0-9a-f]{32}$")
SMALL_LIMIT = 1024


class StudentIn(BaseModel):
    name: str
    dob: str
    phone: str


def bind_tenant() -> None:
    """Stands in for the authz dependency, which binds tenant context (sync, in a thread)."""
    bind_context(tenant_id=TENANT)


def _app() -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)
    install_middleware(
        app, Settings(env="ci"), max_body_bytes=SMALL_LIMIT, max_multipart_bytes=4 * SMALL_LIMIT
    )

    @app.get("/api/v1/items/{item_id}")
    def get_item(item_id: str, request: Request) -> dict[str, Any]:
        return {"item_id": item_id, "request_id": request.state.request_id}

    @app.post("/api/v1/students", dependencies=[Depends(bind_tenant)])
    def create_student(body: StudentIn) -> dict[str, str]:
        return {"status": "created"}

    @app.post("/api/v1/raw")
    async def raw(request: Request) -> dict[str, int]:
        return {"size": len(await request.body())}

    @app.get("/api/v1/missing")
    def missing() -> None:
        raise NotFound()

    @app.get("/api/v1/boom")
    def boom() -> None:
        raise RuntimeError(f"internal detail for {SYNTHETIC_NAME}")

    @app.get("/api/v1/cached")
    def cached() -> Any:
        from fastapi.responses import JSONResponse

        return JSONResponse({"ok": True}, headers={"Cache-Control": "private, max-age=60"})

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


class Logs:
    def __init__(self) -> None:
        self.stream = io.StringIO()

    @property
    def text(self) -> str:
        return self.stream.getvalue()

    def events(self, name: str = "http.request") -> list[dict[str, Any]]:
        lines = [json.loads(x) for x in self.text.splitlines() if x.strip()]
        return [x for x in lines if x.get("event") == name]


@pytest.fixture
def logs() -> Logs:
    captured = Logs()
    setup_logging(Settings(env="ci"), stream=captured.stream)
    return captured


@pytest.fixture
def client(logs: Logs) -> TestClient:
    return TestClient(_app(), raise_server_exceptions=False)


def _assert_no_pii(text: str) -> None:
    for value in SEEDED:
        assert value not in text


# --- Request ID -----------------------------------------------------------------------------


def test_NFR_OBS_001_request_id_is_generated_and_echoed(client: TestClient) -> None:
    res = client.get("/api/v1/items/abc")
    rid = res.headers[REQUEST_ID_HEADER]
    assert GENERATED_ID.match(rid)
    assert res.json()["request_id"] == rid


def test_NFR_OBS_001_generated_ids_are_unique() -> None:
    assert len({new_request_id() for _ in range(100)}) == 100


def test_NFR_OBS_001_valid_incoming_request_id_is_kept(client: TestClient) -> None:
    res = client.get("/api/v1/items/abc", headers={REQUEST_ID_HEADER: "bff_01J8ZQ-abc_123"})
    assert res.headers[REQUEST_ID_HEADER] == "bff_01J8ZQ-abc_123"
    assert res.json()["request_id"] == "bff_01J8ZQ-abc_123"


@pytest.mark.parametrize(
    "incoming",
    ["short", "x" * 65, "has space inside", "semi;colon", "ఆధార్ఆధార్ఆధార్", "a\nb-cdefgh"],
)
def test_NFR_OBS_001_invalid_incoming_request_id_is_replaced(
    client: TestClient, incoming: str
) -> None:
    res = client.get("/api/v1/items/abc", headers={REQUEST_ID_HEADER: incoming.encode()})
    assert GENERATED_ID.match(res.headers[REQUEST_ID_HEADER])


# --- Access log -----------------------------------------------------------------------------


def test_NFR_OBS_001_one_access_line_per_request(client: TestClient, logs: Logs) -> None:
    res = client.get(
        f"/api/v1/items/{SYNTHETIC_PHONE}?name={SYNTHETIC_NAME}",
        headers={"Authorization": "Bearer secret-token-value", "User-Agent": "Agent/1.0"},
    )
    lines = logs.events()
    assert len(lines) == 1
    line = lines[0]
    assert line["route"] == "GET /api/v1/items/{item_id}"
    assert line["method"] == "GET"
    assert line["status"] == 200
    assert isinstance(line["duration_ms"], int)
    assert line["request_id"] == res.headers[REQUEST_ID_HEADER]
    assert line["level"] == "INFO"
    for leaked in ("secret-token-value", "Agent/1.0", "name=", "/api/v1/items/98"):
        assert leaked not in logs.text
    _assert_no_pii(logs.text)


def test_SEC_008_request_bodies_never_reach_logs(client: TestClient, logs: Logs) -> None:
    res = client.post(
        "/api/v1/students",
        json={"name": SYNTHETIC_NAME, "dob": SYNTHETIC_DOB, "phone": SYNTHETIC_PHONE},
    )
    assert res.status_code == 200
    bad = client.post("/api/v1/students", json={"name": SYNTHETIC_TELUGU_NAME, "dob": 5})
    assert bad.status_code == 422
    assert [ln["status"] for ln in logs.events()] == [200, 422]
    _assert_no_pii(logs.text)


def test_NFR_OBS_001_access_line_includes_tenant_bound_in_dependency(
    client: TestClient, logs: Logs
) -> None:
    client.post(
        "/api/v1/students",
        json={"name": SYNTHETIC_NAME, "dob": SYNTHETIC_DOB, "phone": SYNTHETIC_PHONE},
    )
    assert logs.events()[0]["tenant_id"] == TENANT


def test_NFR_OBS_001_plain_starlette_routes_log_their_template(logs: Logs) -> None:
    client = TestClient(create_app(Settings(env="ci")))
    setup_logging(Settings(env="ci"), stream=logs.stream)
    client.get("/api/v1/openapi.json")
    assert logs.events()[0]["route"] == "GET /api/v1/openapi.json"


def test_NFR_OBS_001_unmatched_routes_do_not_log_raw_paths(client: TestClient, logs: Logs) -> None:
    res = client.get(f"/api/v1/nope/{SYNTHETIC_NAME}")
    assert res.status_code == 404
    assert logs.events()[0]["route"] == "GET unmatched"
    _assert_no_pii(logs.text)


def test_NFR_OBS_001_successful_health_probes_are_not_logged(logs: Logs) -> None:
    client = TestClient(create_app())
    setup_logging(Settings(env="ci"), stream=logs.stream)
    assert client.get("/healthz").status_code == 200
    assert logs.events() == []


# --- Errors ---------------------------------------------------------------------------------


def test_SEC_010_problem_json_errors_carry_request_id(client: TestClient) -> None:
    res = client.get("/api/v1/missing", headers={REQUEST_ID_HEADER: "req_from_bff_0001"})
    assert res.status_code == 404
    assert res.headers["content-type"].startswith("application/problem+json")
    assert res.json()["request_id"] == "req_from_bff_0001"
    assert res.headers[REQUEST_ID_HEADER] == "req_from_bff_0001"


def test_SEC_010_unhandled_errors_are_problem_json_with_request_id(
    client: TestClient, logs: Logs
) -> None:
    res = client.get("/api/v1/boom")
    assert res.status_code == 500
    assert res.headers["content-type"].startswith("application/problem+json")
    body = res.json()
    assert body["code"] == "internal_error"
    assert body["request_id"] == res.headers[REQUEST_ID_HEADER]
    assert res.headers["X-Content-Type-Options"] == "nosniff"
    assert SYNTHETIC_NAME not in res.text
    line = logs.events()[0]
    assert line["status"] == 500
    assert line["level"] == "ERROR"
    assert line["error_type"] == "RuntimeError"
    _assert_no_pii(logs.text)


def test_SEC_010_unhandled_errors_still_propagate_to_the_server(logs: Logs) -> None:
    with pytest.raises(RuntimeError):
        TestClient(_app()).get("/api/v1/boom")


# --- Security headers -----------------------------------------------------------------------

EXPECTED_HEADERS = {
    "Strict-Transport-Security": "max-age=63072000; includeSubDomains; preload",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
}


@pytest.mark.parametrize("path", ["/api/v1/items/x", "/api/v1/missing", "/healthz", "/nope"])
def test_SEC_010_security_headers_on_every_response(client: TestClient, path: str) -> None:
    res = client.get(path)
    for name, value in EXPECTED_HEADERS.items():
        assert res.headers[name] == value


def test_SEC_010_api_responses_are_not_cached(client: TestClient) -> None:
    assert client.get("/api/v1/items/x").headers["Cache-Control"] == "no-store"
    assert client.get("/api/v1/missing").headers["Cache-Control"] == "no-store"
    assert "Cache-Control" not in client.get("/healthz").headers


def test_SEC_010_explicit_cache_control_is_respected(client: TestClient) -> None:
    assert client.get("/api/v1/cached").headers["Cache-Control"] == "private, max-age=60"


def test_SEC_010_docs_page_is_exempt_from_api_csp_outside_production() -> None:
    client = TestClient(create_app(Settings(env="ci")))
    res = client.get("/api/v1/docs")
    assert res.status_code == 200
    assert "Content-Security-Policy" not in res.headers
    assert res.headers["X-Content-Type-Options"] == "nosniff"
    api = client.get("/api/v1/openapi.json")
    assert api.headers["Content-Security-Policy"] == EXPECTED_HEADERS["Content-Security-Policy"]


# --- Body size limit ------------------------------------------------------------------------


def test_SEC_010_body_within_limit_is_accepted(client: TestClient) -> None:
    res = client.post("/api/v1/raw", content=b"x" * SMALL_LIMIT)
    assert res.status_code == 200
    assert res.json() == {"size": SMALL_LIMIT}


def test_SEC_010_declared_oversized_body_is_413_problem_json(client: TestClient) -> None:
    res = client.post(
        "/api/v1/raw",
        content=b"x" * (SMALL_LIMIT + 1),
        headers={REQUEST_ID_HEADER: "req_upload_00001"},
    )
    assert res.status_code == 413
    assert res.headers["content-type"].startswith("application/problem+json")
    body = res.json()
    assert body["code"] == "payload_too_large"
    assert body["request_id"] == "req_upload_00001"
    assert res.headers[REQUEST_ID_HEADER] == "req_upload_00001"
    assert res.headers["X-Content-Type-Options"] == "nosniff"


def test_SEC_010_streamed_oversized_body_is_413(client: TestClient, logs: Logs) -> None:
    def chunks() -> Iterator[bytes]:
        for _ in range(4):
            yield b"y" * (SMALL_LIMIT // 2)

    res = client.post("/api/v1/raw", content=chunks())
    assert res.status_code == 413
    assert res.json()["code"] == "payload_too_large"
    assert [ln["status"] for ln in logs.events()] == [413]


def test_SEC_010_oversized_json_is_413_before_validation(client: TestClient) -> None:
    payload = {"name": "a" * SMALL_LIMIT, "dob": SYNTHETIC_DOB, "phone": SYNTHETIC_PHONE}
    res = client.post("/api/v1/students", json=payload)
    assert res.status_code == 413


def test_SEC_010_multipart_has_its_own_limit(client: TestClient) -> None:
    files = {"file": ("register.csv", b"z" * (2 * SMALL_LIMIT), "text/csv")}
    assert client.post("/api/v1/raw", files=files).status_code == 200
    big = {"file": ("register.csv", b"z" * (5 * SMALL_LIMIT), "text/csv")}
    assert client.post("/api/v1/raw", files=big).status_code == 413


# --- ASGI hygiene ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "middleware_cls", [RequestContextMiddleware, SecurityHeadersMiddleware, BodySizeLimitMiddleware]
)
def test_NFR_OBS_001_non_http_scopes_pass_through(middleware_cls: type[Any]) -> None:
    import asyncio

    seen: list[str] = []

    async def inner(scope: dict[str, Any], receive: Any, send: Any) -> None:
        seen.append(scope["type"])

    async def receive() -> dict[str, Any]:
        return {"type": "lifespan.startup"}

    async def send(message: dict[str, Any]) -> None:
        return None

    asyncio.run(middleware_cls(inner)({"type": "lifespan"}, receive, send))
    assert seen == ["lifespan"]


def test_NFR_OBS_001_middleware_is_installed_by_create_app() -> None:
    app = create_app()
    classes: list[object] = [m.cls for m in app.user_middleware]
    # Outermost first: headers wrap request context, which wraps the body limit.
    assert classes == [SecurityHeadersMiddleware, RequestContextMiddleware, BodySizeLimitMiddleware]
