"""Database access for data-quality runs and findings (tenant_session, RLS applies; bound
parameters only). Bulk reads and writes: one statement per step of a run, never per student."""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import (
    ColumnElement,
    RowMapping,
    and_,
    bindparam,
    case,
    func,
    or_,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.record_tables import dump_table
from app.core.records import RecordTable
from app.dq import models as m
from app.dq.rules import Severity

F = m.dq_findings
R = m.dq_runs

# Columns a re-run refreshes on an existing finding (executemany by id).
REFRESH_COLUMNS = (
    "rule_version",
    "severity",
    "match_class",
    "explanation_code",
    "explanation_params",
    "route_codes",
    "details",
    "conflict_hash",
    "sources",
    "related_student_id",
    "status",
    "reopened_count",
    "last_seen_run_id",
    "last_seen_at",
    "version",
)
CLEAR_COLUMNS = ("status", "resolution", "change_request_id", "resolved_at", "version")


def current_tenant(session: Session) -> uuid.UUID:
    value: object = session.execute(text("SELECT core.current_tenant()")).scalar_one()
    if value is None:
        raise RuntimeError("tenant context is not set; use core.db.tenant_session()")
    return uuid.UUID(str(value))


def now(session: Session) -> dt.datetime:
    value: dt.datetime = session.execute(select(func.now())).scalar_one()
    return value


# --- runs ----------------------------------------------------------------------------------------


def insert_run(session: Session, **values: Any) -> RowMapping:
    return session.execute(R.insert().values(**values).returning(*R.c)).mappings().one()


def get_run(session: Session, run_id: uuid.UUID, *, lock: bool = False) -> RowMapping | None:
    stmt = select(R).where(R.c.id == run_id)
    if lock:
        stmt = stmt.with_for_update()
    return session.execute(stmt).mappings().one_or_none()


def update_run(session: Session, run_id: uuid.UUID, values: Mapping[str, Any]) -> RowMapping:
    return (
        session.execute(update(R).where(R.c.id == run_id).values(**values).returning(*R.c))
        .mappings()
        .one()
    )


def last_manual_run(session: Session, profile_key: str | None) -> RowMapping | None:
    stmt = select(R).where(R.c.trigger == "manual", R.c.status == "completed")
    stmt = stmt.where(
        R.c.profile_key.is_(None) if profile_key is None else R.c.profile_key == profile_key
    )
    return (
        session.execute(stmt.order_by(R.c.created_at.desc(), R.c.id.desc()).limit(1))
        .mappings()
        .one_or_none()
    )


# --- findings: reconcile ---------------------------------------------------------------------


def findings_to_reconcile(
    session: Session, student_ids: Collection[uuid.UUID], fingerprints: Collection[str]
) -> list[RowMapping]:
    """Findings of these students (either side of a DQ-008 pair) or with these fingerprints,
    locked in id order (concurrent runs on the same students wait instead of deadlocking)."""
    ids, fps = list(student_ids), list(fingerprints)
    conditions: list[ColumnElement[bool]] = []
    if ids:
        conditions += [F.c.student_id.in_(ids), F.c.related_student_id.in_(ids)]
    if fps:
        conditions.append(F.c.fingerprint.in_(fps))
    if not conditions:
        return []
    stmt = select(F).where(or_(*conditions)).order_by(F.c.id).with_for_update()
    return list(session.execute(stmt).mappings())


def insert_findings(session: Session, rows: Sequence[Mapping[str, Any]]) -> int:
    """Insert new findings; a fingerprint inserted meanwhile by a concurrent run is skipped."""
    if not rows:
        return 0
    stmt = (
        pg_insert(F)
        .on_conflict_do_nothing(constraint="dq_findings_fingerprint_key")
        .returning(F.c.id)
    )
    inserted = 0
    for start in range(0, len(rows), 1000):
        inserted += len(session.execute(stmt, list(rows[start : start + 1000])).all())
    return inserted


def _update_many(
    session: Session, columns: Sequence[str], rows: Sequence[Mapping[str, Any]]
) -> None:
    if not rows:
        return
    stmt = (
        update(F)
        .where(F.c.id == bindparam("b_id"))
        .values({c: bindparam(f"b_{c}") for c in columns} | {"updated_at": func.now()})
    )
    params = [{"b_id": r["id"], **{f"b_{c}": r[c] for c in columns}} for r in rows]
    for start in range(0, len(params), 1000):
        session.connection().execute(stmt, params[start : start + 1000])


def refresh_findings(session: Session, rows: Sequence[Mapping[str, Any]]) -> None:
    _update_many(session, REFRESH_COLUMNS, rows)


def clear_findings(session: Session, rows: Sequence[Mapping[str, Any]]) -> None:
    _update_many(session, CLEAR_COLUMNS, rows)


def touch_findings(
    session: Session, finding_ids: Collection[uuid.UUID], run_id: uuid.UUID, at: dt.datetime
) -> None:
    """Mark unchanged findings as seen by this run (no version bump: nothing changed)."""
    ids = list(finding_ids)
    for start in range(0, len(ids), 5000):
        session.execute(
            update(F)
            .where(F.c.id.in_(ids[start : start + 5000]))
            .values(last_seen_run_id=run_id, last_seen_at=at)
        )


def link_change_request(
    session: Session,
    *,
    student_id: uuid.UUID,
    attribute_key: str | None,
    change_request_id: uuid.UUID,
) -> list[uuid.UUID]:
    """Link the student's unresolved findings (of ``attribute_key``, if given) to the request."""
    stmt = update(F).where(
        F.c.student_id == student_id,
        F.c.status.in_(("open", "reopened", "needs_confirmation", "waived")),
    )
    if attribute_key is not None:
        stmt = stmt.where(F.c.attribute_key == attribute_key)
    stmt = stmt.values(
        change_request_id=change_request_id, updated_at=func.now(), version=F.c.version + 1
    )
    return list(session.execute(stmt.returning(F.c.id)).scalars())


def unlink_change_request(session: Session, change_request_id: uuid.UUID) -> list[uuid.UUID]:
    """Forget a rejected request on findings that are still unresolved."""
    stmt = (
        update(F)
        .where(F.c.change_request_id == change_request_id, F.c.status != "resolved")
        .values(change_request_id=None, updated_at=func.now(), version=F.c.version + 1)
        .returning(F.c.id)
    )
    return list(session.execute(stmt).scalars())


def profiles_in_use(session: Session, student_ids: Collection[uuid.UUID]) -> list[str]:
    """Profiles with unresolved findings for these students (incremental runs re-check them)."""
    ids = list(student_ids)
    if not ids:
        return []
    stmt = (
        select(F.c.profile_key)
        .where(
            F.c.profile_key.is_not(None),
            F.c.status != "resolved",
            or_(F.c.student_id.in_(ids), F.c.related_student_id.in_(ids)),
        )
        .distinct()
    )
    rows: Sequence[object] = session.execute(stmt).scalars().all()
    return sorted(str(key) for key in rows)


# --- findings: read and workflow -------------------------------------------------------------


def get_finding(
    session: Session, finding_id: uuid.UUID, *, lock: bool = False
) -> RowMapping | None:
    stmt = select(F).where(F.c.id == finding_id)
    if lock:
        stmt = stmt.with_for_update()
    return session.execute(stmt).mappings().one_or_none()


def update_finding(
    session: Session,
    finding_id: uuid.UUID,
    values: Mapping[str, Any],
    *,
    expected_version: int | None = None,
) -> RowMapping | None:
    stmt = update(F).where(F.c.id == finding_id)
    if expected_version is not None:
        stmt = stmt.where(F.c.version == expected_version)
    stmt = stmt.values(**values, updated_at=func.now(), version=F.c.version + 1)
    return session.execute(stmt.returning(*F.c)).mappings().one_or_none()


@dataclass(frozen=True, slots=True)
class FindingFilter:
    student_ids: Collection[uuid.UUID] | None = None  # None = every student of the school
    statuses: Collection[str] | None = None
    severities: Collection[str] | None = None
    rule_ids: Collection[str] | None = None
    profile_key: str | None = None
    attribute_key: str | None = None


def _where(flt: FindingFilter) -> list[ColumnElement[bool]]:
    out: list[ColumnElement[bool]] = []
    if flt.student_ids is not None:
        out.append(F.c.student_id.in_(list(flt.student_ids)))
    if flt.statuses:
        out.append(F.c.status.in_(list(flt.statuses)))
    if flt.severities:
        out.append(F.c.severity.in_(list(flt.severities)))
    if flt.rule_ids:
        out.append(F.c.rule_id.in_(list(flt.rule_ids)))
    if flt.profile_key is not None:
        out.append(or_(F.c.profile_key.is_(None), F.c.profile_key == flt.profile_key))
    if flt.attribute_key is not None:
        out.append(F.c.attribute_key == flt.attribute_key)
    return out


_RANK = case(
    {s.value: s.rank for s in Severity},
    value=F.c.severity,
    else_=-1,
)


def list_findings(
    session: Session, flt: FindingFilter, *, offset: int, limit: int
) -> list[RowMapping]:
    """Most severe first, then rule, student and id (stable pages)."""
    stmt = (
        select(F)
        .where(and_(*_where(flt)))
        .order_by(_RANK.desc(), F.c.rule_id, F.c.student_id, F.c.id)
        .offset(offset)
        .limit(limit)
    )
    return list(session.execute(stmt).mappings())


def count_findings(session: Session, flt: FindingFilter) -> list[RowMapping]:
    """Counts by (rule, severity) and distinct students with a blocker."""
    stmt = (
        select(F.c.rule_id, F.c.severity, func.count().label("n"))
        .where(and_(*_where(flt)))
        .group_by(F.c.rule_id, F.c.severity)
        .order_by(F.c.rule_id, F.c.severity)
    )
    return list(session.execute(stmt).mappings())


def students_with_severity(session: Session, flt: FindingFilter, severity: str) -> int:
    stmt = select(func.count(func.distinct(F.c.student_id))).where(
        and_(*_where(flt)), F.c.severity == severity
    )
    return int(session.execute(stmt).scalar_one())


def export_record_tables(session: Session) -> list[RecordTable]:
    """Every run and finding of the current school (FR-ADM-001). Findings hold masked values
    only (FR-DQ-006), so they are exported as stored."""
    return [
        dump_table(session, R, name="dq_runs", order_by=("created_at", "id")),
        dump_table(session, F, name="dq_findings", order_by=("created_at", "id")),
    ]
