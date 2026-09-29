"""Read a whole tenant table as a :class:`~app.core.records.RecordTable` (FR-ADM-001).

Used by modules' repositories for the school's full data export: every row visible in the
caller's ``tenant_session`` (RLS: this school only), without ``tenant_id`` and without the
columns the owning module excludes (ciphertext, blind indexes, search vectors).
"""

from __future__ import annotations

from collections.abc import Collection, Sequence

from sqlalchemy import FromClause, select
from sqlalchemy.orm import Session

from app.core.records import RecordTable


def _plain(value: object) -> object:
    if isinstance(value, bytes | bytearray | memoryview):
        return bytes(value).hex()
    return value


def dump_table(
    session: Session,
    table: FromClause,
    *,
    name: str,
    exclude: Collection[str] = (),
    order_by: Sequence[str] | None = None,
) -> RecordTable:
    """Every row of ``table`` visible in the caller's session, without the ``exclude`` columns.
    ``tenant_id`` is always left out (the archive is one school's). ``bytea`` columns become hex
    (checksums); name ciphertext columns in ``exclude``. Ordered by ``order_by`` (default: the
    primary key)."""
    skip = {"tenant_id", *exclude}
    unknown = set(exclude) - {c.name for c in table.columns}
    if unknown:
        raise ValueError(f"{table.description} has no columns {sorted(unknown)}")
    columns = [c for c in table.columns if c.name not in skip]
    order = (
        [table.c[n] for n in order_by]
        if order_by
        else [c for c in table.primary_key if c.name not in skip]
    )
    stmt = select(*columns).order_by(*order)
    rows = [tuple(_plain(v) for v in r) for r in session.execute(stmt)]
    return RecordTable(name=name, columns=tuple(c.name for c in columns), rows=rows)


__all__ = ["dump_table"]
