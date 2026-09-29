"""Database engines and transaction-scoped sessions.

Invariant 1 (CLAUDE.md §6): every request and job runs inside ``tenant_session()``, which opens
one transaction and sets ``app.tenant_id`` / ``app.user_id`` with ``set_config(..., true)``
(transaction-local, the parameterised equivalent of ``SET LOCAL``). The settings vanish at
COMMIT/ROLLBACK, so a pooled connection never carries a previous tenant's context.

Three separate engines keep privileges apart (ADR-0013):
- app engine      -> role ``sos_app`` (RLS enforced, no BYPASSRLS)
- platform engine -> role ``sos_platform`` (schema ``platform`` only; cannot read tenant data)
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Connection, Engine, create_engine, event, text
from sqlalchemy.orm import Session, SessionTransaction

from app.core.config import Settings, get_settings

_lock = threading.Lock()
_engines: dict[str, Engine] = {}

_SET_CONTEXT = text(
    "SELECT set_config('app.tenant_id', :tenant_id, true), "
    "set_config('app.user_id', :user_id, true), "
    "set_config('statement_timeout', :timeout, true)"
)
_SET_TIMEOUT = text("SELECT set_config('statement_timeout', :timeout, true)")


def _make_engine(url: str, settings: Settings) -> Engine:
    return create_engine(
        url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_pool_size,
        pool_pre_ping=True,
        pool_recycle=1800,
        future=True,
    )


def get_engine(kind: str = "app", settings: Settings | None = None) -> Engine:
    """Return (and cache) the engine for ``kind`` in {"app", "platform"}."""
    settings = settings or get_settings()
    with _lock:
        engine = _engines.get(kind)
        if engine is None:
            if kind == "app":
                url = settings.database_url.get_secret_value()
            elif kind == "platform":
                url = settings.platform_database_url.get_secret_value()
            else:
                raise ValueError(f"unknown engine kind: {kind}")
            engine = _make_engine(url, settings)
            _engines[kind] = engine
        return engine


def set_engine(kind: str, engine: Engine) -> None:
    """Override an engine (tests, worker bootstrap)."""
    with _lock:
        _engines[kind] = engine


def dispose_engines() -> None:
    with _lock:
        for engine in _engines.values():
            engine.dispose()
        _engines.clear()


def _as_uuid(value: uuid.UUID | str, name: str) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except ValueError as exc:
        raise ValueError(f"{name} must be a UUID") from exc


@contextmanager
def tenant_session(
    tenant_id: uuid.UUID | str,
    user_id: uuid.UUID | str | None = None,
    *,
    statement_timeout_ms: int | None = None,
    engine: Engine | None = None,
    deferred: bool = False,
) -> Iterator[Session]:
    """Open one transaction bound to ``tenant_id`` (and optionally ``user_id``).

    Commits on success, rolls back on any exception. RLS policies read the context via
    ``core.current_tenant()`` / ``core.current_user_id()`` and fail closed when it is unset.

    ``deferred=True``: no connection is taken (and no transaction begins in the database) until
    the first statement, which gets the context first. For a request that must call something
    slow (an AI model) before it writes, without holding a transaction open meanwhile
    (``idle_in_transaction_session_timeout`` is 30 s for ``sos_app``, docs/05 §3.1).
    """
    tid = _as_uuid(tenant_id, "tenant_id")
    uid = _as_uuid(user_id, "user_id") if user_id is not None else None
    timeout = statement_timeout_ms or get_settings().db_statement_timeout_ms
    engine = engine or get_engine("app")
    params = {"tenant_id": str(tid), "user_id": str(uid) if uid else "", "timeout": str(timeout)}
    with Session(engine, expire_on_commit=False, autoflush=False) as session, session.begin():
        if deferred:
            # No connection until the first statement; the context is set on that connection
            # before the statement runs (same transaction, same bound parameters).
            def _set_context(_s: Session, _t: SessionTransaction, connection: Connection) -> None:
                connection.execute(_SET_CONTEXT, params)

            event.listen(session, "after_begin", _set_context)
        else:
            session.execute(_SET_CONTEXT, params)
        yield session


@contextmanager
def context_free_session(
    *, statement_timeout_ms: int | None = None, engine: Engine | None = None
) -> Iterator[Session]:
    """A transaction as ``sos_app`` with NO tenant context.

    Tenant tables return zero rows here (fail closed). Use only to call the allowlisted
    SECURITY DEFINER functions (e.g. ``core.resolve_login``, ``core.list_tenant_ids``).
    """
    timeout = statement_timeout_ms or get_settings().db_statement_timeout_ms
    engine = engine or get_engine("app")
    with Session(engine, expire_on_commit=False, autoflush=False) as session, session.begin():
        session.execute(_SET_TIMEOUT, {"timeout": str(timeout)})
        yield session


@contextmanager
def platform_session(
    *, statement_timeout_ms: int | None = None, engine: Engine | None = None
) -> Iterator[Session]:
    """A transaction as ``sos_platform`` (control plane). It cannot see tenant schemas."""
    timeout = statement_timeout_ms or get_settings().db_statement_timeout_ms
    engine = engine or get_engine("platform")
    with Session(engine, expire_on_commit=False, autoflush=False) as session, session.begin():
        session.execute(_SET_TIMEOUT, {"timeout": str(timeout)})
        yield session
