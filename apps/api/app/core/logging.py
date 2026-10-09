"""Structured JSON logging with a field allowlist and redaction (docs/11 §2).

SEC-008, NFR-OBS-001, CLAUDE.md invariant 5: logs carry IDs and technical data only.

- One JSON object per line on stdout, UTC timestamps (``ts``), fields from ``ALLOWED_FIELDS``.
  Any other key is dropped and counted in ``dropped_fields``; non-scalar values are dropped.
- ``event`` is a constant snake/dot-case name (``change_request.approved``). Free text is replaced
  by ``log.invalid_event``. Standard-library records keep only their constant message *template*;
  their arguments and ``extra`` are never rendered.
- Every string value passes through ``core.redaction.redact`` (Aadhaar/Verhoeff, mobiles, emails)
  as a last line of defence.
- Exceptions are logged as ``error_type`` only; a redacted traceback is added in ``local`` only.
- Context (request_id, tenant_id, user_id, job_id, ...) is bound with ``bind_context`` and merged
  into every line together with the active OpenTelemetry trace_id/span_id.

Usage::

    log = get_logger(__name__)
    log.info("change_request.approved", resource_type="change_request", resource_id=cr.id)
"""

from __future__ import annotations

import logging
import re
import sys
import traceback
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import UTC, datetime
from types import TracebackType
from typing import Any, TextIO

import structlog
from opentelemetry import trace
from structlog.typing import EventDict, Processor, WrappedLogger

from app.core.config import Environment, Settings, get_settings
from app.core.redaction import redact

__all__ = [
    "ALLOWED_FIELDS",
    "CONTEXT_FIELDS",
    "INVALID_EVENT",
    "bind_context",
    "bind_task_context",
    "clear_context",
    "get_context",
    "get_logger",
    "request_scope",
    "reset_context",
    "setup_logging",
]

# Output order follows the docs/11 §2 example.
ALLOWED_FIELDS: tuple[str, ...] = (
    "ts",
    "level",
    "service",
    "version",
    "env",
    "request_id",
    "trace_id",
    "span_id",
    "tenant_id",
    "user_id",
    "event",
    "route",
    "method",
    "status",
    "duration_ms",
    "job_id",
    "task_name",
    "queue",
    "attempt",
    "error_code",
    "error_type",
    "count",
    "resource_type",
    "resource_id",
    "action",
    "outcome",
    "policy",
    "ip_hash",
    "retry_after_s",
    "logger",
)
CONTEXT_FIELDS: frozenset[str] = frozenset(
    {"request_id", "tenant_id", "user_id", "job_id", "task_name", "queue", "attempt"}
)
INVALID_EVENT = "log.invalid_event"
UNSTRUCTURED_EVENT = "log.unstructured"

_EVENT_RE = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)*")
_EVENT_MAX = 100
_VALUE_MAX = 512
_TRACEBACK_MAX = 8000
_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_HEX_RE = re.compile(r"[0-9a-f]{16}|[0-9a-f]{32}")
# Values we generate ourselves (not user input) and machine identifiers validated by shape.
_GENERATED = frozenset({"ts", "level", "service", "version", "env", "logger"})
_UUID_FIELDS = frozenset({"tenant_id", "user_id", "job_id", "resource_id", "request_id"})
_HEX_FIELDS = frozenset({"trace_id", "span_id", "ip_hash"})
_LEVELS = {
    "debug": "DEBUG",
    "info": "INFO",
    "warn": "WARN",
    "warning": "WARN",
    "error": "ERROR",
    "exception": "ERROR",
    "critical": "ERROR",
    "fatal": "ERROR",
}
# Loggers that would print SQL with bind values or raw URLs at INFO/DEBUG.
_CAPPED_LOGGERS = ("sqlalchemy.engine", "sqlalchemy.pool", "httpx", "httpcore")


@dataclass(frozen=True, slots=True)
class _Static:
    service: str
    version: str
    env: str
    local: bool


_static = _Static(service="api", version="0.0.0-dev", env="local", local=True)
_configured = False
_request_fields: ContextVar[dict[str, Any] | None] = ContextVar("sos_request_fields", default=None)


# --- Processors -----------------------------------------------------------------------------


def _add_timestamp(_: WrappedLogger, __: str, ed: EventDict) -> EventDict:
    ed["ts"] = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return ed


def _add_level(_: WrappedLogger, method_name: str, ed: EventDict) -> EventDict:
    ed["level"] = _LEVELS.get(method_name, "INFO")
    return ed


def _add_static(_: WrappedLogger, __: str, ed: EventDict) -> EventDict:
    ed["service"] = _static.service
    ed["version"] = _static.version
    ed["env"] = _static.env
    return ed


def _add_trace_ids(_: WrappedLogger, __: str, ed: EventDict) -> EventDict:
    ctx = trace.get_current_span().get_span_context()
    if ctx.is_valid:
        ed.setdefault("trace_id", format(ctx.trace_id, "032x"))
        ed.setdefault("span_id", format(ctx.span_id, "016x"))
    return ed


def _sanitize_event(_: WrappedLogger, __: str, ed: EventDict) -> EventDict:
    event = ed.get("event")
    if ed.get("_from_structlog", True):
        valid = isinstance(event, str) and len(event) <= _EVENT_MAX and _EVENT_RE.fullmatch(event)
        if not valid:
            ed["event"] = INVALID_EVENT
    else:
        # Foreign (stdlib) record: ProcessorFormatter(use_get_message=False) gives us the
        # unformatted template, which is a code constant. Arguments are never interpolated.
        record = ed.get("_record")
        msg = record.msg if isinstance(record, logging.LogRecord) else event
        ed["event"] = msg[: _EVENT_MAX * 2] if isinstance(msg, str) else UNSTRUCTURED_EVENT
    return ed


_ExcInfo = tuple[type[BaseException], BaseException, TracebackType | None]


def _normalise_exc_info(value: Any) -> _ExcInfo | None:
    if value is True:
        info = sys.exc_info()
        return None if info[0] is None else info
    if isinstance(value, BaseException):
        return (type(value), value, value.__traceback__)
    if isinstance(value, tuple) and len(value) == 3 and value[0] is not None:
        return value
    return None


def _exceptions(_: WrappedLogger, __: str, ed: EventDict) -> EventDict:
    info = _normalise_exc_info(ed.pop("exc_info", None))
    ed.pop("exception", None)
    ed.pop("stack_info", None)
    if info is not None:
        ed.setdefault("error_type", info[0].__name__)
        if _static.local:
            text = "".join(traceback.format_exception(*info))
            ed["traceback"] = redact(text)[-_TRACEBACK_MAX:]
    return ed


def _scalar(value: Any) -> Any:
    """Return a loggable scalar, or ``...`` if the value must be dropped."""
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, uuid.UUID):
        return str(value)
    return ...


def _allowlist(_: WrappedLogger, __: str, ed: EventDict) -> EventDict:
    allowed = set(ALLOWED_FIELDS) | ({"traceback"} if _static.local else set())
    out: dict[str, Any] = {}
    dropped = 0
    for key in (*ALLOWED_FIELDS, "traceback"):
        if key in ed and key in allowed:
            value = _scalar(ed[key])
            if value is ...:
                dropped += 1
            else:
                out[key] = value
    dropped += sum(1 for key in ed if key not in allowed)
    if dropped:
        out["dropped_fields"] = dropped
    return out


def _redact_value(key: str, value: Any) -> Any:
    if key in _GENERATED or key == "traceback" or value is None or isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        if abs(value) < 10**9:
            return value
        text = str(value)
        masked = redact(text)
        return value if masked == text else masked
    text = str(value)[:_VALUE_MAX]
    if key in _UUID_FIELDS and _UUID_RE.fullmatch(text):
        return text
    if key in _HEX_FIELDS and _HEX_RE.fullmatch(text):
        return text
    return redact(text)


def _redact(_: WrappedLogger, __: str, ed: EventDict) -> EventDict:
    return {key: _redact_value(key, value) for key, value in ed.items()}


def _shared_processors() -> list[Processor]:
    return [
        structlog.contextvars.merge_contextvars,
        _add_level,
        structlog.stdlib.add_logger_name,
        _add_timestamp,
        _add_static,
        _add_trace_ids,
    ]


def _final_processors() -> list[Processor]:
    return [
        _sanitize_event,
        _exceptions,
        structlog.stdlib.ProcessorFormatter.remove_processors_meta,
        _allowlist,
        _redact,
        structlog.processors.JSONRenderer(ensure_ascii=False, separators=(",", ":")),
    ]


# --- Handler --------------------------------------------------------------------------------


class _StdoutHandler(logging.StreamHandler[TextIO]):
    """Writes to ``sys.stdout`` as it is at emit time (or to a fixed stream if given)."""

    sos_handler = True

    def __init__(self, stream: TextIO | None) -> None:
        super().__init__(stream or sys.stdout)
        self._fixed = stream

    def emit(self, record: logging.LogRecord) -> None:
        if self._fixed is None:
            self.stream = sys.stdout
        super().emit(record)


def _is_foreign_harness_handler(handler: logging.Handler) -> bool:
    # Keep handlers installed by the test harness (pytest's log capture) intact.
    return type(handler).__module__.startswith("_pytest")


def _level_for(settings: Settings) -> int:
    level = logging.getLevelNamesMapping().get(settings.log_level.upper(), logging.INFO)
    if settings.is_production_like:
        level = max(level, logging.INFO)  # DEBUG is disabled in staging/prod (docs/11 §2)
    return level


def setup_logging(
    settings: Settings, *, service: str | None = None, stream: TextIO | None = None
) -> None:
    """Configure structlog and route the standard library through the same pipeline.

    Idempotent: safe to call from every ``create_app()`` and from Celery signals.
    """
    global _static, _configured  # noqa: PLW0603 - process-wide logging configuration
    _static = _Static(
        service=service or settings.service_name,
        version=settings.version,
        env=str(settings.env),
        local=settings.env is Environment.LOCAL,
    )
    level = _level_for(settings)

    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            *_shared_processors(),
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=_shared_processors(),
        processors=_final_processors(),
        use_get_message=False,
    )
    handler = _StdoutHandler(stream)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    for existing in list(root.handlers):
        if not _is_foreign_harness_handler(existing):
            root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)

    # Uvicorn: our middleware writes the access line; error logs propagate to root.
    for name in ("uvicorn", "uvicorn.error"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True
    access = logging.getLogger("uvicorn.access")
    access.handlers.clear()
    access.propagate = False
    access.disabled = True
    for name in _CAPPED_LOGGERS:
        logging.getLogger(name).setLevel(max(level, logging.WARNING))
    logging.captureWarnings(True)
    _configured = True


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a structured logger. Configures logging from settings on first use."""
    if not _configured:
        setup_logging(get_settings())
    logger: structlog.stdlib.BoundLogger = structlog.stdlib.get_logger(name)
    return logger


# --- Context --------------------------------------------------------------------------------


def _normalise_context_value(value: object) -> str | int:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, bool) or not isinstance(value, str | int):
        raise TypeError("context values must be str, int or UUID")
    return value


def bind_context(**fields: object) -> Mapping[str, Token[Any]]:
    """Bind allowlisted context fields for every log line in this context.

    Returns tokens for ``reset_context``. ``tenant_id``/``job_id`` are also set on the current
    trace span (``sos.tenant_id``/``sos.job_id``). ``None`` values are ignored.
    """
    unknown = sorted(set(fields) - CONTEXT_FIELDS)
    if unknown:
        raise ValueError(f"not a log context field: {', '.join(unknown)}")
    values = {k: _normalise_context_value(v) for k, v in fields.items() if v is not None}
    holder = _request_fields.get()
    if holder is not None:
        holder.update(values)
    span = trace.get_current_span()
    if span.is_recording():
        for key in ("tenant_id", "job_id"):
            if key in values:
                span.set_attribute(f"sos.{key}", values[key])
    return structlog.contextvars.bind_contextvars(**values)


def reset_context(tokens: Mapping[str, Token[Any]]) -> None:
    structlog.contextvars.reset_contextvars(**tokens)


def clear_context() -> None:
    structlog.contextvars.clear_contextvars()


def get_context() -> dict[str, Any]:
    return structlog.contextvars.get_contextvars()


@contextmanager
def request_scope() -> Iterator[dict[str, Any]]:
    """Collect context bound anywhere during a request (including threadpool dependencies).

    Context variables set in a child context (e.g. a sync dependency run in a thread) are not
    visible to the caller; this shared dict is, so the access log line can include tenant/user.
    """
    holder: dict[str, Any] = {}
    token = _request_fields.set(holder)
    try:
        yield holder
    finally:
        _request_fields.reset(token)


def _uuid_or_none(value: object) -> str | None:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, str) and _UUID_RE.fullmatch(value.lower()):
        return value.lower()
    return None


def bind_task_context(
    *,
    task_name: str,
    kwargs: Mapping[str, object] | None,
    queue: str | None,
    attempt: int | None,
) -> Mapping[str, Token[Any]]:
    """Bind Celery task context. ``job_id``/``tenant_id`` come from task kwargs when valid UUIDs."""
    kwargs = kwargs or {}
    return bind_context(
        task_name=task_name,
        job_id=_uuid_or_none(kwargs.get("job_id")),
        tenant_id=_uuid_or_none(kwargs.get("tenant_id")),
        queue=queue,
        attempt=attempt,
    )
