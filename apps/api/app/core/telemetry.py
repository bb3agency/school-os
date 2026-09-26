"""OpenTelemetry tracing (docs/11 §4, NFR-OBS-001) with allowlisted span attributes (SEC-008).

- One process-wide ``TracerProvider`` with resource attributes ``service.name``,
  ``service.version`` and ``deployment.environment``.
- Spans are exported over OTLP/HTTP only when ``SOS_OTEL_EXPORTER_OTLP_ENDPOINT`` is set;
  otherwise nothing is exported (no console output).
- Auto-instrumentation: FastAPI (health checks excluded), httpx, Celery and Redis (commands
  sanitised to ``CMD ? ?``). SQL is traced by ``_instrument_sqlalchemy``: statements are recorded
  WITHOUT bind values and no sqlcommenter is added. (opentelemetry-instrumentation-sqlalchemy
  0.66b0 supports SQLAlchemy < 2.1 only and imports ``sqlalchemy.ext.asyncio``, which needs
  ``greenlet`` on 2.1; the engine-event listener below is the same mechanism, on the Engine class.)
- ``TenantContextSpanProcessor`` tags every new span with ``sos.tenant_id``/``sos.job_id`` from the
  log context. ``SanitizingSpanExporter`` wraps every exporter: finished spans are immutable, so
  it exports sanitised copies keeping only allowlisted attributes, reducing exception events to
  ``exception.type``, dropping status descriptions and redacting SQL text and span names.

Sampling: the SDK records every span (parent-based); the collector applies tail sampling
(100% errors, ~20% of successful requests, docs/11 §4).
"""

from __future__ import annotations

import threading
from collections.abc import Mapping, Sequence
from typing import Any

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.celery import CeleryInstrumentor
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import Event, ReadableSpan, Span, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.sdk.trace.sampling import ALWAYS_ON, ParentBased
from opentelemetry.trace import Link, SpanKind, Status, StatusCode
from sqlalchemy import event
from sqlalchemy.engine import Connection, Engine, ExceptionContext, ExecutionContext

from app.core.config import Settings
from app.core.logging import get_context
from app.core.redaction import redact

__all__ = [
    "ALLOWED_SPAN_ATTRIBUTES",
    "EXCLUDED_URLS",
    "SanitizingSpanExporter",
    "TenantContextSpanProcessor",
    "add_span_exporter",
    "build_otlp_exporter",
    "sanitize_attributes",
    "sanitize_span",
    "setup_telemetry",
]

# Both the legacy and the stable HTTP/DB semantic-convention names are listed so that an
# OTEL_SEMCONV_STABILITY_OPT_IN switch does not silently drop useful technical attributes.
ALLOWED_SPAN_ATTRIBUTES: frozenset[str] = frozenset(
    {
        "http.method",
        "http.request.method",
        "http.route",
        "http.status_code",
        "http.response.status_code",
        "db.system",
        "db.system.name",
        "db.operation",
        "db.operation.name",
        "db.statement",
        "db.query.text",
        "celery.action",
        "celery.state",
        "celery.task_name",
        "error.type",
        "sos.tenant_id",
        "sos.job_id",
    }
)
ALLOWED_SPAN_ATTRIBUTE_PREFIXES: tuple[str, ...] = ("messaging.",)
_REDACTED_TEXT_ATTRIBUTES = frozenset({"db.statement", "db.query.text"})
_EVENT_ATTRIBUTES = frozenset({"exception.type"})
_MAX_ATTRIBUTE_LENGTH = 2048
EXCLUDED_URLS = r"/healthz$,/readyz$"

_lock = threading.Lock()
_provider: TracerProvider | None = None


# --- Sanitising -----------------------------------------------------------------------------


def _clean_value(key: str, value: object) -> object:
    if isinstance(value, str):
        text = value[:_MAX_ATTRIBUTE_LENGTH]
        return redact(text) if key in _REDACTED_TEXT_ATTRIBUTES else text
    if isinstance(value, Sequence) and not isinstance(value, bytes):
        return tuple(redact(v) if isinstance(v, str) else v for v in value)
    return value


def _allowed(key: str) -> bool:
    return key in ALLOWED_SPAN_ATTRIBUTES or key.startswith(ALLOWED_SPAN_ATTRIBUTE_PREFIXES)


def sanitize_attributes(attributes: Mapping[str, object] | None) -> dict[str, Any]:
    """Keep only allowlisted attributes; redact free text in the ones kept."""
    return {k: _clean_value(k, v) for k, v in (attributes or {}).items() if _allowed(k)}


def _sanitize_event(event: Event) -> Event:
    attrs = {k: v for k, v in (event.attributes or {}).items() if k in _EVENT_ATTRIBUTES}
    return Event(event.name, attrs, event.timestamp)


def sanitize_span(span: ReadableSpan) -> ReadableSpan:
    """Return a copy of ``span`` that is safe to export (no PII, allowlisted attributes)."""
    return ReadableSpan(
        name=redact(span.name),
        context=span.context,
        parent=span.parent,
        resource=span.resource,
        attributes=sanitize_attributes(span.attributes),
        events=[_sanitize_event(e) for e in span.events],
        links=[Link(link.context, sanitize_attributes(link.attributes)) for link in span.links],
        kind=span.kind,
        status=Status(span.status.status_code),
        start_time=span.start_time,
        end_time=span.end_time,
        instrumentation_scope=span.instrumentation_scope,
    )


class SanitizingSpanExporter(SpanExporter):
    """Wraps an exporter and hands it sanitised copies of every span."""

    def __init__(self, inner: SpanExporter) -> None:
        self.inner = inner

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        return self.inner.export([sanitize_span(s) for s in spans])

    def shutdown(self) -> None:
        self.inner.shutdown()

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return self.inner.force_flush(timeout_millis)


class TenantContextSpanProcessor(SpanProcessor):
    """Adds ``sos.tenant_id``/``sos.job_id`` from the bound log context to every new span."""

    def on_start(self, span: Span, parent_context: Context | None = None) -> None:
        ctx = get_context()
        for key in ("tenant_id", "job_id"):
            value = ctx.get(key)
            if value is not None:
                span.set_attribute(f"sos.{key}", str(value))

    def on_end(self, span: ReadableSpan) -> None:
        return None

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return True


# --- Setup ----------------------------------------------------------------------------------


def build_otlp_exporter(settings: Settings) -> SanitizingSpanExporter | None:
    """OTLP/HTTP exporter (sanitised) when an endpoint is configured, else ``None``."""
    endpoint = settings.otel_exporter_otlp_endpoint
    if not endpoint:
        return None
    return SanitizingSpanExporter(
        OTLPSpanExporter(endpoint=endpoint.rstrip("/") + "/v1/traces", timeout=10)
    )


_SQL_SPAN_KEY = "_sos_otel_span"


def _instrument_sqlalchemy(provider: TracerProvider) -> None:
    """Trace every SQL statement on every Engine (existing and future) without bind values."""
    tracer = provider.get_tracer("app.core.telemetry.sqlalchemy")

    def before(**kw: Any) -> None:
        context: ExecutionContext | None = kw.get("context")
        statement: str = kw["statement"]
        conn: Connection = kw["conn"]
        if context is None:
            return
        tokens = statement.split(maxsplit=1)
        operation = tokens[0].upper() if tokens else "SQL"
        database = conn.engine.url.database or ""
        name = f"{operation} {database}".strip() if database and "/" not in database else operation
        span = tracer.start_span(
            name,
            kind=SpanKind.CLIENT,
            attributes={
                "db.system": conn.dialect.name,
                "db.operation": operation,
                "db.statement": statement,  # the parameterised text; values are never recorded
            },
        )
        setattr(context, _SQL_SPAN_KEY, span)

    def after(**kw: Any) -> None:
        context = kw.get("context")
        span = getattr(context, _SQL_SPAN_KEY, None)
        if span is not None:
            span.end()
            setattr(context, _SQL_SPAN_KEY, None)

    def on_error(exception_context: ExceptionContext) -> None:
        span = getattr(exception_context.execution_context, _SQL_SPAN_KEY, None)
        if span is not None:
            span.set_status(Status(StatusCode.ERROR))
            span.set_attribute("error.type", type(exception_context.original_exception).__name__)
            span.end()
            setattr(exception_context.execution_context, _SQL_SPAN_KEY, None)

    event.listen(Engine, "before_cursor_execute", before, named=True)
    event.listen(Engine, "after_cursor_execute", after, named=True)
    event.listen(Engine, "handle_error", on_error)


def _instrument_libraries(provider: TracerProvider) -> None:
    _instrument_sqlalchemy(provider)
    celery = CeleryInstrumentor()  # type: ignore[no-untyped-call]  # untyped __init__ upstream
    for instrumentor in (HTTPXClientInstrumentor(), RedisInstrumentor(), celery):
        if not instrumentor.is_instrumented_by_opentelemetry:
            instrumentor.instrument(tracer_provider=provider)


def _create_provider(settings: Settings, service: str) -> TracerProvider:
    resource = Resource.create(
        {
            "service.name": service,
            "service.version": settings.version,
            "deployment.environment": str(settings.env),
            "deployment.environment.name": str(settings.env),
        }
    )
    provider = TracerProvider(resource=resource, sampler=ParentBased(ALWAYS_ON))
    provider.add_span_processor(TenantContextSpanProcessor())
    exporter = build_otlp_exporter(settings)
    if exporter is not None:
        provider.add_span_processor(BatchSpanProcessor(exporter))
    return provider


def setup_telemetry(
    app: FastAPI | None, settings: Settings, *, service: str | None = None
) -> TracerProvider:
    """Create the tracer provider and instrument libraries once; instrument ``app`` if given.

    Idempotent: tests and the app factory may call it many times. Workers call it from
    ``worker_process_init`` (after fork) with ``app=None``.
    """
    global _provider  # noqa: PLW0603 - process-wide tracer provider
    with _lock:
        if _provider is None:
            _provider = _create_provider(settings, service or settings.service_name)
            trace.set_tracer_provider(_provider)
            _instrument_libraries(_provider)
        provider = _provider
    if app is not None and not getattr(app, "_is_instrumented_by_opentelemetry", False):
        FastAPIInstrumentor.instrument_app(
            app,
            tracer_provider=provider,
            excluded_urls=EXCLUDED_URLS,
            exclude_spans=["receive", "send"],
        )
    return provider


def add_span_exporter(exporter: SpanExporter) -> None:
    """Attach an extra exporter (sanitised, synchronous). Used by tests and local debugging."""
    if _provider is None:
        raise RuntimeError("call setup_telemetry() first")
    _provider.add_span_processor(SimpleSpanProcessor(SanitizingSpanExporter(exporter)))
