"""OpenTelemetry tracing with allowlisted span attributes (NFR-OBS-001, SEC-008, invariant 5).

Spans are captured with an in-memory exporter wrapped in the same sanitising exporter that
production uses. Seeded values are synthetic.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from app.core.config import Settings
from app.core.logging import bind_context, clear_context
from app.core.redaction import verhoeff_check_digit
from app.core.telemetry import (
    ALLOWED_SPAN_ATTRIBUTES,
    SanitizingSpanExporter,
    add_span_exporter,
    build_otlp_exporter,
    sanitize_span,
    setup_telemetry,
)
from app.main import create_app
from celery import Celery
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.celery import CeleryInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.sdk.trace import Event, ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, Status, StatusCode
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

SYNTHETIC_NAME = "Kommineni Venkata Sai"
SYNTHETIC_PHONE = "9876543210"
_BODY = "73920184556"
SYNTHETIC_AADHAAR = _BODY + verhoeff_check_digit(_BODY)
SEEDED = [SYNTHETIC_NAME, SYNTHETIC_PHONE, SYNTHETIC_AADHAAR, "Agent/1.0", "name="]


@pytest.fixture(scope="module")
def provider() -> TracerProvider:
    return setup_telemetry(None, Settings(env="ci"))


@pytest.fixture(scope="module")
def _exporter(provider: TracerProvider) -> InMemorySpanExporter:
    exporter = InMemorySpanExporter()
    add_span_exporter(exporter)
    return exporter


@pytest.fixture
def spans(_exporter: InMemorySpanExporter) -> Iterator[InMemorySpanExporter]:
    _exporter.clear()
    clear_context()
    yield _exporter
    clear_context()


def _all_values(span: ReadableSpan) -> list[str]:
    values = [span.name, str(span.status.description or "")]
    values += [str(v) for v in (span.attributes or {}).values()]
    for event in span.events:
        values += [str(v) for v in (event.attributes or {}).values()]
    return values


def _assert_clean(finished: tuple[ReadableSpan, ...]) -> None:
    assert finished
    for span in finished:
        for key in span.attributes or {}:
            assert key in ALLOWED_SPAN_ATTRIBUTES or key.startswith("messaging."), key
        for value in _all_values(span):
            for seeded in SEEDED:
                assert seeded not in value, (span.name, value)


def _app() -> FastAPI:
    app = create_app(Settings(env="ci"))

    @app.get("/api/v1/items/{item_id}")
    def get_item(item_id: str) -> dict[str, str]:
        bind_context(tenant_id="0192f3a4-5b6c-7d8e-9f01-23456789abcd")
        return {"ok": "yes"}

    @app.get("/api/v1/boom")
    def boom() -> None:
        raise RuntimeError(f"failed for {SYNTHETIC_NAME}")

    return app


# --- Provider -------------------------------------------------------------------------------


def test_NFR_OBS_001_resource_attributes(provider: TracerProvider) -> None:
    attrs = provider.resource.attributes
    assert attrs["service.name"] == "api"
    assert attrs["service.version"] == Settings().version
    assert attrs["deployment.environment"] in {"local", "ci", "staging", "prod"}


def test_NFR_OBS_001_setup_is_idempotent(provider: TracerProvider) -> None:
    assert setup_telemetry(None, Settings(env="ci")) is provider
    assert HTTPXClientInstrumentor().is_instrumented_by_opentelemetry
    assert RedisInstrumentor().is_instrumented_by_opentelemetry
    celery = CeleryInstrumentor()  # type: ignore[no-untyped-call]  # untyped __init__ upstream
    assert celery.is_instrumented_by_opentelemetry


def test_NFR_OBS_001_no_exporter_without_endpoint() -> None:
    assert build_otlp_exporter(Settings(env="ci")) is None
    exporter = build_otlp_exporter(
        Settings(env="ci", otel_exporter_otlp_endpoint="http://collector:4318")
    )
    assert isinstance(exporter, SanitizingSpanExporter)
    assert isinstance(exporter.inner, OTLPSpanExporter)


# --- FastAPI --------------------------------------------------------------------------------


def test_NFR_OBS_001_request_span_uses_route_template(spans: InMemorySpanExporter) -> None:
    client = TestClient(_app())
    res = client.get(
        f"/api/v1/items/{SYNTHETIC_PHONE}?name={SYNTHETIC_NAME}",
        headers={"User-Agent": "Agent/1.0"},
    )
    assert res.status_code == 200
    server = [s for s in spans.get_finished_spans() if s.kind is SpanKind.SERVER]
    assert len(server) == 1
    attrs = server[0].attributes or {}
    assert server[0].name == "GET /api/v1/items/{item_id}"
    assert attrs["http.route"] == "/api/v1/items/{item_id}"
    assert attrs["http.status_code"] == 200
    assert attrs["sos.tenant_id"] == "0192f3a4-5b6c-7d8e-9f01-23456789abcd"
    for dropped in ("http.url", "http.target", "http.user_agent", "net.peer.ip", "http.host"):
        assert dropped not in attrs
    _assert_clean(spans.get_finished_spans())


def test_NFR_OBS_001_one_server_span_per_request_across_create_app_calls(
    spans: InMemorySpanExporter,
) -> None:
    for _ in range(3):
        TestClient(_app()).get("/api/v1/items/a")
    server = [s for s in spans.get_finished_spans() if s.kind is SpanKind.SERVER]
    assert len(server) == 3


@pytest.mark.parametrize("path", ["/healthz", "/readyz"])
def test_NFR_OBS_001_health_checks_are_not_traced(spans: InMemorySpanExporter, path: str) -> None:
    app = _app()
    from app.core.health import get_checks

    app.dependency_overrides[get_checks] = lambda: {"database": lambda: True}
    TestClient(app).get(path)
    assert [s for s in spans.get_finished_spans() if s.kind is SpanKind.SERVER] == []


def test_SEC_008_exception_messages_do_not_reach_spans(spans: InMemorySpanExporter) -> None:
    res = TestClient(_app(), raise_server_exceptions=False).get("/api/v1/boom")
    assert res.status_code == 500
    server = next(s for s in spans.get_finished_spans() if s.kind is SpanKind.SERVER)
    assert server.status.status_code is StatusCode.ERROR
    assert server.status.description is None
    for event in server.events:
        assert set(event.attributes or {}) <= {"exception.type"}
    _assert_clean(spans.get_finished_spans())


# --- SQLAlchemy -----------------------------------------------------------------------------


def test_SEC_008_sql_spans_have_statement_without_bind_values(
    provider: TracerProvider, spans: InMemorySpanExporter
) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.connect() as conn:
        conn.execute(text("SELECT :name AS n, :phone AS p"), {"name": SYNTHETIC_NAME, "phone": 1})
    db = [s for s in spans.get_finished_spans() if (s.attributes or {}).get("db.system")]
    assert db
    statement = str((db[0].attributes or {})["db.statement"])
    assert statement.startswith("SELECT ?")
    _assert_clean(spans.get_finished_spans())
    engine.dispose()


def test_SEC_008_sql_literals_are_redacted_as_last_line_of_defence(
    spans: InMemorySpanExporter,
) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.connect() as conn:
        conn.execute(text(f"SELECT '{SYNTHETIC_AADHAAR}' AS x"))  # never do this in app code
    db = [s for s in spans.get_finished_spans() if (s.attributes or {}).get("db.system")]
    assert "XXXX XXXX" in str((db[0].attributes or {})["db.statement"])
    _assert_clean(spans.get_finished_spans())
    engine.dispose()


def test_NFR_OBS_001_sql_errors_record_type_only(spans: InMemorySpanExporter) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.connect() as conn, pytest.raises(OperationalError):
        conn.execute(text("SELECT * FROM missing_students WHERE name = :n"), {"n": SYNTHETIC_NAME})
    (span,) = [s for s in spans.get_finished_spans() if (s.attributes or {}).get("db.system")]
    assert span.status.status_code is StatusCode.ERROR
    assert (span.attributes or {})["error.type"] == "OperationalError"
    _assert_clean(spans.get_finished_spans())
    engine.dispose()


# --- Tenant context -------------------------------------------------------------------------


def test_NFR_OBS_001_spans_carry_tenant_and_job_from_log_context(
    provider: TracerProvider, spans: InMemorySpanExporter
) -> None:
    tenant, job = str(uuid.uuid4()), str(uuid.uuid4())
    tokens = bind_context(tenant_id=tenant, job_id=job)
    with trace.get_tracer("test", tracer_provider=provider).start_as_current_span("dq.run"):
        pass
    (span,) = spans.get_finished_spans()
    assert (span.attributes or {})["sos.tenant_id"] == tenant
    assert (span.attributes or {})["sos.job_id"] == job
    del tokens


def test_NFR_OBS_001_bind_context_tags_the_current_span(
    provider: TracerProvider, spans: InMemorySpanExporter
) -> None:
    tenant = str(uuid.uuid4())
    with trace.get_tracer("test", tracer_provider=provider).start_as_current_span("kb.ask"):
        bind_context(tenant_id=tenant)
    (span,) = spans.get_finished_spans()
    assert (span.attributes or {})["sos.tenant_id"] == tenant


# --- Celery ---------------------------------------------------------------------------------


def test_NFR_OBS_001_celery_task_spans_are_sanitised(spans: InMemorySpanExporter) -> None:
    app = Celery("telemetry-test", broker="memory://", backend="cache+memory://")

    @app.task(name="dq.echo")
    def echo(name: str) -> str:
        return name

    assert echo.apply(kwargs={"name": SYNTHETIC_NAME}).get() == SYNTHETIC_NAME
    finished = spans.get_finished_spans()
    run = [s for s in finished if (s.attributes or {}).get("celery.task_name") == "dq.echo"]
    assert run
    _assert_clean(finished)


# --- Sanitiser unit -------------------------------------------------------------------------


def _raw_span(**overrides: Any) -> ReadableSpan:
    ctx = trace.SpanContext(
        trace_id=0x4BF92F3577B34DA6A3CE929D0E0E4736,
        span_id=0x00F067AA0BA902B7,
        is_remote=False,
        trace_flags=trace.TraceFlags(1),
    )
    fields: dict[str, Any] = {
        "name": "GET /api/v1/students/{id}",
        "context": ctx,
        "attributes": {
            "http.method": "GET",
            "http.route": "/api/v1/students/{id}",
            "http.status_code": 200,
            "http.url": f"https://api/api/v1/students/1?name={SYNTHETIC_NAME}",
            "http.target": "/api/v1/students/1?phone=9876543210",
            "http.user_agent": "Agent/1.0",
            "student.name": SYNTHETIC_NAME,
            "db.statement": f"SELECT * FROM t WHERE a = '{SYNTHETIC_AADHAAR}'",
            "messaging.destination": "dq",
            "sos.tenant_id": "0192f3a4-5b6c-7d8e-9f01-23456789abcd",
        },
        "events": [
            Event(
                "exception",
                {
                    "exception.type": "ValueError",
                    "exception.message": f"bad {SYNTHETIC_NAME}",
                    "exception.stacktrace": f"Traceback ... {SYNTHETIC_NAME}",
                },
            ),
            Event("note", {"text": SYNTHETIC_NAME}),
        ],
        "status": Status(StatusCode.ERROR, f"ValueError: bad {SYNTHETIC_NAME}"),
        "kind": SpanKind.SERVER,
        "start_time": 1,
        "end_time": 2,
    }
    fields.update(overrides)
    return ReadableSpan(**fields)


def test_SEC_008_sanitiser_applies_attribute_allowlist() -> None:
    clean = sanitize_span(_raw_span())
    attrs = dict(clean.attributes or {})
    assert attrs == {
        "http.method": "GET",
        "http.route": "/api/v1/students/{id}",
        "http.status_code": 200,
        "db.statement": f"SELECT * FROM t WHERE a = 'XXXX XXXX {SYNTHETIC_AADHAAR[-4:]}'",
        "messaging.destination": "dq",
        "sos.tenant_id": "0192f3a4-5b6c-7d8e-9f01-23456789abcd",
    }
    assert [(e.name, dict(e.attributes or {})) for e in clean.events] == [
        ("exception", {"exception.type": "ValueError"}),
        ("note", {}),
    ]
    assert clean.status.status_code is StatusCode.ERROR
    assert clean.status.description is None
    assert clean.context == _raw_span().context
    assert clean.name == "GET /api/v1/students/{id}"
    _assert_clean((clean,))


def test_SEC_008_sanitising_exporter_wraps_inner_exporter() -> None:
    inner = InMemorySpanExporter()
    exporter = SanitizingSpanExporter(inner)
    exporter.export([_raw_span()])
    (exported,) = inner.get_finished_spans()
    assert "student.name" not in (exported.attributes or {})
    assert exporter.force_flush()
    exporter.shutdown()
