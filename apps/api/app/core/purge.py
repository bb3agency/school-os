"""Offboarding purge helpers: count and delete one school's rows (FR-PLT-005; ADR-0029).

A neutral building block: each tenant module declares the tables it owns (children before
parents) and calls :func:`count_rows` / :func:`delete_rows` from its own
``purge_tenant_data`` / ``tenant_data_counts``; ``app.tenancy`` orders the modules and runs
the purge inside :func:`purge_role`.

- Everything runs in the school's ``tenant_session`` (``sos_app``, RLS), with an explicit
  ``tenant_id = core.current_tenant()`` filter on top of RLS.
- :func:`purge_role` sets the transaction-local flag ``app.purge_tenant`` (or ``app.purge_audit``)
  and switches to ``sos_purger`` with ``SET LOCAL ROLE``. The database allows that role to see
  and delete rows only while the school is ``offboarding`` (``core.tenant_purge_allowed()``) or,
  for the audit chain, ``deleted`` (``core.tenant_audit_purge_allowed()``).
- Table names are constants of the owning module, checked against :data:`TABLE_NAME`, and
  built with SQLAlchemy's ``table()`` construct (never from input).
- :func:`remaining_rows` counts every catalog table with a ``tenant_id`` column, so a table no
  module registered is still found before keys are destroyed (fail closed).
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Final, Literal, cast

import psycopg
from psycopg import sql as pgsql
from sqlalchemy import column, delete, func, select, table, text
from sqlalchemy.orm import Session

PURGE_ROLE: Final = "sos_purger"
# A constant statement (no interpolation, SEC-001): keep the role name in step with PURGE_ROLE.
_SET_PURGE_ROLE: Final = text("SET LOCAL ROLE sos_purger")
_CONSTRAINT_NAME: Final = re.compile(r"(core|sis|kb|ops)\.([a-z][a-z0-9_]{0,62})")
TENANT_SCHEMAS: Final = ("core", "sis", "kb", "audit", "ops")
TABLE_NAME: Final = re.compile(r"^(core|sis|kb|audit|ops)\.[a-z][a-z0-9_]{0,62}$")

Flag = Literal["app.purge_tenant", "app.purge_audit"]

_REMAINING_SQL = text(
    """
    SELECT n.nspname || '.' || c.relname
    FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = ANY(:schemas) AND c.relkind IN ('r', 'p') AND NOT c.relispartition
      AND EXISTS (SELECT 1 FROM pg_catalog.pg_attribute a
                  WHERE a.attrelid = c.oid AND a.attname = 'tenant_id' AND NOT a.attisdropped)
    ORDER BY 1
    """
)


@dataclass(frozen=True, slots=True)
class PurgeTables:
    """The tables one module owns, in deletion order.

    ``deleted``: removed with ``DELETE`` (children before parents). ``cascaded``: removed by the
    ``ON DELETE CASCADE`` of a parent in ``deleted`` (counted and verified, never deleted
    directly: the app roles hold no privilege on them).
    """

    deleted: tuple[str, ...]
    cascaded: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in (*self.deleted, *self.cascaded):
            _check(name)

    @property
    def all(self) -> tuple[str, ...]:
        return (*self.cascaded, *self.deleted)

    def count(self, session: Session) -> dict[str, int]:
        return count_rows(session, self.all)

    def delete(self, session: Session) -> dict[str, int]:
        """Delete the ``deleted`` tables in order (as ``sos_purger``; cascaded rows go with their
        parent). Returns the rows each ``DELETE`` removed; count first with :meth:`count`."""
        return delete_rows(session, self.deleted)


def _check(name: str) -> tuple[str, str]:
    if not TABLE_NAME.fullmatch(name):
        raise ValueError(f"not a tenant table name: {name!r}")
    schema, _, rel = name.partition(".")
    return schema, rel


def _table(name: str):  # type: ignore[no-untyped-def]  # sqlalchemy TableClause
    schema, rel = _check(name)
    return table(rel, column("tenant_id"), schema=schema)


def _current_tenant() -> object:
    return func.core.current_tenant()


def count_rows(session: Session, tables: Iterable[str]) -> dict[str, int]:
    """Rows of the current school per table (RLS applies to the session's role)."""
    out: dict[str, int] = {}
    for name in tables:
        t = _table(name)
        stmt = select(func.count()).select_from(t).where(t.c.tenant_id == _current_tenant())
        out[name] = int(session.execute(stmt).scalar_one())
    return out


def delete_rows(session: Session, tables: Sequence[str]) -> dict[str, int]:
    """``DELETE`` the current school's rows, table by table, in the given order."""
    out: dict[str, int] = {}
    for name in tables:
        t = _table(name)
        result = session.execute(delete(t).where(t.c.tenant_id == _current_tenant()))
        out[name] = int(getattr(result, "rowcount", 0) or 0)
    return out


def tenant_tables(session: Session) -> list[str]:
    """Every table of the tenant schemas with a ``tenant_id`` column (partitions excluded)."""
    rows: Sequence[object] = (
        session.execute(_REMAINING_SQL, {"schemas": list(TENANT_SCHEMAS)}).scalars().all()
    )
    return [str(n) for n in rows]


def remaining_rows(session: Session, *, exclude: Iterable[str] = ()) -> dict[str, int]:
    """Tables that still hold rows of the current school (catalog-driven; zero counts omitted)."""
    skip = set(exclude)
    names = [n for n in tenant_tables(session) if n not in skip]
    return {name: n for name, n in count_rows(session, names).items() if n}


@contextmanager
def purge_role(session: Session, tenant_id: uuid.UUID, *, flag: Flag) -> Iterator[None]:
    """Act as ``sos_purger`` for the current school inside the caller's transaction.

    Sets ``flag`` to ``tenant_id`` (transaction-local) and ``SET LOCAL ROLE sos_purger``; on
    exit (success) switches back to the session role so audit events are written as ``sos_app``.
    On an exception the transaction rolls back, which also undoes the role and the flag.
    """
    session.execute(
        text("SELECT set_config(:flag, :tenant, true)"),
        {"flag": flag, "tenant": str(tenant_id)},
    )
    session.execute(_SET_PURGE_ROLE)
    yield
    session.execute(text("RESET ROLE"))


def defer_constraint(session: Session, name: str) -> None:
    """``SET CONSTRAINTS <name> DEFERRED`` for a deferrable key (checked at commit).

    ``SET CONSTRAINTS`` takes no bound parameters, so the schema-qualified name is checked
    against :data:`_CONSTRAINT_NAME` and composed as quoted identifiers by psycopg
    (``psycopg.sql.Identifier``), never interpolated into SQL text. It runs on the session's own
    DBAPI connection, inside the caller's transaction.
    """
    match = _CONSTRAINT_NAME.fullmatch(name)
    if match is None:
        raise ValueError(f"not a constraint name: {name!r}")
    statement = pgsql.SQL("SET CONSTRAINTS {} DEFERRED").format(
        pgsql.Identifier(match.group(1), match.group(2))
    )
    dbapi = cast("psycopg.Connection[object]", session.connection().connection.driver_connection)
    dbapi.execute(statement)


__all__ = [
    "PURGE_ROLE",
    "PurgeTables",
    "count_rows",
    "defer_constraint",
    "delete_rows",
    "purge_role",
    "remaining_rows",
    "tenant_tables",
]
