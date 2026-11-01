"""Incident deadline alerts for operators (R3-2G; lead decision D49 part 2; ADR-0050;
FR-DPDP-031, SEC-052; incident drill 2026-11-01 gap 2).

Every 5 minutes (beat ``platform.incident_clock_alerts``, shared deployment only) the control
plane looks at every incident that is not ``closed`` and its two kinds of clock
(:mod:`app.platform.incidents`):

- ``cert_in``: T0 + 6 h while CERT-In reporting is required and not yet recorded;
- ``school_notice``: confirmation + 24 h for each affected school not yet notified.

A clock with ``incident_alerts.due_soon_minutes`` (billing.yaml, default 120) or less left is
``due_soon``; a clock past its deadline is ``overdue``. Each incident x clock (x school) x stage
alerts **once**:

- a structured log line ``platform.incident.clock_due_soon`` (warning) or
  ``platform.incident.clock_overdue`` (error) with the incident id, clock name, minutes left and,
  for a school-notice clock, the school's tenant id. Never the summary, a school name or a count.
  CloudWatch metric filters turn the lines into metrics and alarms that page the on-call list on
  the alarm topic (``infra/terraform/modules/observability``);
- the platform audit event ``incident.clock_alert`` (``clock``, ``stage``, ``minutes_left``;
  ``subject_tenant_id`` for a school clock), written in the same transaction. The audit chain is
  the record of what was sent and the dedupe key, so no new table is needed: a later run that
  finds the event for the same incident, clock, school and stage sends nothing.

The incident rows are locked ``FOR UPDATE SKIP LOCKED`` while a run checks and records them, so
two overlapping runs cannot alert twice (a row an operator is editing is retried 5 minutes
later). An overdue clock whose due-soon alert was never sent (for example an incident recorded
late) alerts as overdue only. Reads platform tables only (ADR-0020).
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from typing import Any, Final, Literal

from sqlalchemy import and_, func, or_, select

from app.core.db import platform_session
from app.core.logging import get_logger
from app.platform import models as m
from app.platform.common import SYSTEM, audit_platform, config, now
from app.platform.incidents import CERT_IN_HOURS, SCHOOL_NOTICE_HOURS

log = get_logger(__name__)

ACTION: Final = "incident.clock_alert"
CERT_IN: Final = "cert_in"
SCHOOL_NOTICE: Final = "school_notice"
EVENTS: Final[dict[str, str]] = {
    "due_soon": "platform.incident.clock_due_soon",
    "overdue": "platform.incident.clock_overdue",
}

Clock = Literal["cert_in", "school_notice"]
Stage = Literal["due_soon", "overdue"]


@dataclass(frozen=True)
class ClockAlert:
    """One alert sent by a run (ids, clock names and times only)."""

    incident_id: uuid.UUID
    clock: Clock
    stage: Stage
    due_at: dt.datetime
    minutes_left: int
    tenant_id: uuid.UUID | None = None


def due_soon_window() -> dt.timedelta:
    """``billing.yaml`` → ``incident_alerts.due_soon_minutes``."""
    minutes = int(config()["incident_alerts"]["due_soon_minutes"])
    if minutes <= 0:
        raise ValueError("incident_alerts.due_soon_minutes must be positive")
    return dt.timedelta(minutes=minutes)


def stage_at(due_at: dt.datetime, at: dt.datetime, window: dt.timedelta) -> Stage | None:
    """``overdue`` after the deadline, ``due_soon`` within ``window`` of it, else None."""
    if at > due_at:
        return "overdue"
    if at >= due_at - window:
        return "due_soon"
    return None


def _minutes_left(due_at: dt.datetime, at: dt.datetime) -> int:
    return int((due_at - at).total_seconds() // 60)


def _sent(
    s: Any, incident_ids: list[uuid.UUID], since: dt.datetime
) -> set[tuple[uuid.UUID, str, str, uuid.UUID | None]]:
    """Alerts already recorded in the platform audit chain (the dedupe key)."""
    a = m.audit_events
    rows = s.execute(
        select(a.c.resource_id, a.c.subject_tenant_id, a.c.summary).where(
            a.c.action == ACTION,
            a.c.resource_type == "incident",
            a.c.resource_id.in_(incident_ids),
            a.c.occurred_at >= since,
        )
    )
    return {
        (
            r.resource_id,
            str(r.summary.get("clock")),
            str(r.summary.get("stage")),
            r.subject_tenant_id,
        )
        for r in rows
    }


def _candidates(
    s: Any, at: dt.datetime, window: dt.timedelta
) -> list[tuple[uuid.UUID, Clock, dt.datetime, uuid.UUID | None]]:
    i, t = m.incidents, m.incident_schools
    incidents = list(
        s.execute(
            select(
                i.c.id,
                i.c.opened_at,
                i.c.cert_in_required,
                i.c.cert_in_reported_at,
                i.c.confirmed_at,
            )
            .where(
                i.c.status != "closed",
                or_(
                    and_(i.c.cert_in_required.is_(True), i.c.cert_in_reported_at.is_(None)),
                    i.c.confirmed_at.is_not(None),
                ),
            )
            .order_by(i.c.id)
            .with_for_update(skip_locked=True)
        ).mappings()
    )
    out: list[tuple[uuid.UUID, Clock, dt.datetime, uuid.UUID | None]] = []
    confirmed: dict[uuid.UUID, dt.datetime] = {}
    for row in incidents:
        if row["cert_in_required"] and row["cert_in_reported_at"] is None:
            due = row["opened_at"] + dt.timedelta(hours=CERT_IN_HOURS)
            if stage_at(due, at, window):
                out.append((row["id"], CERT_IN, due, None))
        if row["confirmed_at"] is not None:
            confirmed[row["id"]] = row["confirmed_at"] + dt.timedelta(hours=SCHOOL_NOTICE_HOURS)
    if confirmed:
        schools = s.execute(
            select(t.c.incident_id, t.c.tenant_id)
            .where(t.c.incident_id.in_(list(confirmed)), t.c.school_notified_at.is_(None))
            .order_by(t.c.incident_id, t.c.tenant_id)
        )
        for incident_id, tenant_id in schools:
            due = confirmed[incident_id]
            if stage_at(due, at, window):
                out.append((incident_id, SCHOOL_NOTICE, due, tenant_id))
    return out


def check_clocks(at: dt.datetime | None = None) -> list[ClockAlert]:
    """Send the due-soon and overdue alerts not sent yet (idempotent across runs)."""
    at = at or now()
    window = due_soon_window()
    sent: list[ClockAlert] = []
    with platform_session() as s:
        candidates = _candidates(s, at, window)
        if not candidates:
            return sent
        ids = sorted({c[0] for c in candidates})
        # Every alert of an incident is written after its T0: bound the audit scan by it.
        since: dt.datetime = s.execute(
            select(func.min(m.incidents.c.opened_at)).where(m.incidents.c.id.in_(ids))
        ).scalar_one()
        already = _sent(s, ids, since)
        for incident_id, clock, due_at, tenant_id in candidates:
            stage = stage_at(due_at, at, window)
            if stage is None or (incident_id, clock, stage, tenant_id) in already:
                continue
            alert = ClockAlert(
                incident_id=incident_id,
                clock=clock,
                stage=stage,
                due_at=due_at,
                minutes_left=_minutes_left(due_at, at),
                tenant_id=tenant_id,
            )
            _log(alert)
            summary: dict[str, Any] = {
                "clock": clock,
                "stage": stage,
                "minutes_left": alert.minutes_left,
            }
            audit_platform(s, SYSTEM, ACTION, "incident", incident_id, summary, tenant_id=tenant_id)
            already.add((incident_id, clock, stage, tenant_id))
            sent.append(alert)
    return sent


def _log(alert: ClockAlert) -> None:
    fields: dict[str, Any] = {
        "incident_id": str(alert.incident_id),
        "clock": alert.clock,
        "minutes_left": alert.minutes_left,
        "alert": f"incident_clock_{alert.stage}",
    }
    if alert.tenant_id is not None:
        fields["tenant_id"] = str(alert.tenant_id)
    if alert.stage == "overdue":
        log.error(EVENTS["overdue"], **fields)
    else:
        log.warning(EVENTS["due_soon"], **fields)


__all__ = [
    "ACTION",
    "CERT_IN",
    "EVENTS",
    "SCHOOL_NOTICE",
    "ClockAlert",
    "check_clocks",
    "due_soon_window",
    "stage_at",
]
