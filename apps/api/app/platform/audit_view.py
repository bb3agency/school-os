"""Platform audit log viewer and chain verification (FR-PLT-029; docs/16 §5.17)."""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import uuid

from sqlalchemy import select

from app.audit import service as audit
from app.core.db import platform_session
from app.core.errors import NotFound
from app.platform import models as m
from app.platform import repository as repo
from app.platform.common import Actor, audit_platform, clamp_limit, now
from app.platform.schemas import AuditVerifyOut, JobOut, PlatformAuditEventOut

CSV_COLUMNS = (
    "seq",
    "occurred_at",
    "actor_type",
    "actor_id",
    "action",
    "resource_type",
    "resource_id",
    "subject_tenant_id",
    "summary",
)


def list_events(
    *,
    actor_id: uuid.UUID | None = None,
    action: str | None = None,
    tenant_id: uuid.UUID | None = None,
    start: dt.datetime | None = None,
    end: dt.datetime | None = None,
    limit: int = 50,
    cursor: int | None = None,
) -> tuple[list[PlatformAuditEventOut], str | None]:
    limit = clamp_limit(limit)
    t = m.audit_events
    stmt = select(t)
    if actor_id:
        stmt = stmt.where(t.c.actor_id == actor_id)
    if action:
        stmt = stmt.where(t.c.action == action)
    if tenant_id:
        stmt = stmt.where(t.c.subject_tenant_id == tenant_id)
    if start:
        stmt = stmt.where(t.c.occurred_at >= start)
    if end:
        stmt = stmt.where(t.c.occurred_at < end)
    if cursor is not None:
        stmt = stmt.where(t.c.seq < cursor)
    with platform_session() as s:
        rows = list(s.execute(stmt.order_by(t.c.seq.desc()).limit(limit + 1)).mappings())
    items = [PlatformAuditEventOut.model_validate(dict(r)) for r in rows[:limit]]
    return items, (str(rows[limit - 1]["seq"]) if len(rows) > limit else None)


def to_csv(events: list[PlatformAuditEventOut]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(CSV_COLUMNS)
    for e in events:
        row = e.model_dump(mode="json")
        row["summary"] = json.dumps(row["summary"], sort_keys=True)
        # Neutralise spreadsheet formulas (CSV injection).
        writer.writerow(
            [
                ("'" + str(v))
                if isinstance(v, str) and v[:1] in "=+-@"
                else ("" if v is None else v)
                for v in (row[c] for c in CSV_COLUMNS)
            ]
        )
    return buf.getvalue()


def verify(actor: Actor) -> AuditVerifyOut:
    """Verify the platform chain now; the result is kept as a platform job."""
    with platform_session() as s:
        job, _ = repo.start_job(
            s,
            task_name="platform.audit_verify",
            idempotency_key=f"audit.verify:{uuid.uuid4()}",
            created_by=actor.operator_id,
        )
    with platform_session() as s:
        result = audit.verify_platform_chain(s)
    with platform_session() as s:
        repo.update_row(
            s,
            m.job_runs,
            job["id"],
            {
                "status": "succeeded",
                "finished_at": now(),
                "progress": {
                    "ok": result.ok,
                    "checked": result.checked,
                    "first_bad_seq": result.first_bad_seq,
                    "reason": result.reason,
                },
            },
            bump_version=False,
        )
        audit_platform(
            s,
            actor,
            "audit.verify_run",
            "job",
            job["id"],
            {"ok": result.ok, "checked": result.checked},
        )
    return AuditVerifyOut(
        job_id=job["id"],
        ok=result.ok,
        checked=result.checked,
        first_bad_seq=result.first_bad_seq,
        reason=result.reason,
    )


def get_job(job_id: uuid.UUID, *, operator_id: uuid.UUID, can_read_all: bool) -> JobOut:
    with platform_session() as s:
        row = repo.get(s, m.job_runs, job_id)
    if row is None or not (can_read_all or row["created_by"] == operator_id):
        raise NotFound("Job not found")
    return JobOut.model_validate(dict(row))
