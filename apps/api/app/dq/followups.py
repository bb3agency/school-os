"""Follow-ups on fixes outside the school (FO-01; FR-UDI-021..FR-UDI-030). Routes:
``app.dq.followup_api`` (``/api/v1/dq/readiness/{profile_key}/follow-ups``).

The readiness check says *who* must fix a difference; this module records whether the fix is
happening. A person records each step (the slip given, the parent contacted and how, the date
the parent promised, the update reported; for UDISE+ fixes the request sent to the MEO / MIS
coordinator and the follow-up) in the append-only ``sis.dq_followups``.

- **Live.** Which follow-ups are open is computed from the live readiness on every read
  (:func:`app.dq.readiness_service.open_fixes`): when the new Aadhaar-as-printed value (or the
  UDISE+ value) matches, the difference goes and the follow-up closes by itself. Nothing is
  ever corrected automatically (invariant 6).
- **Overdue.** Nothing recorded for ``overdue_after_days`` days, or a promised date that has
  passed (``app/dq/config/udise_followup.yaml``).
- **Scope (SEC-015).** Reading needs ``dq.readiness.read``, recording ``dq.readiness.manage``,
  each with ``student.read_basic`` reaching the student; a student outside it, or of another
  school, is 404.
- **Data (invariants 4, 5, 7).** Codes, dates and ids only: no value, no name in the log, no
  free text (D56). Recording is audited ``dq.followup.recorded`` in the same transaction with
  ids and codes; logs carry counts only.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections import defaultdict
from collections.abc import Collection
from typing import Any, Final, cast

from sqlalchemy.orm import Session

from app.audit import service as audit
from app.authz.context import UserContext
from app.core.errors import NotFound, ValidationFailed
from app.core.ids import new_id
from app.core.logging import get_context, get_logger
from app.dq import calendar_service
from app.dq import followup_config as fc
from app.dq import followup_repository as repo
from app.dq import readiness_service as readiness
from app.dq.followup_schemas import (
    FollowupCounts,
    FollowupHistoryOut,
    FollowupIn,
    FollowupItemOut,
    FollowupListOut,
    FollowupOwnerCounts,
    FollowupRecordedOut,
    FollowupStepOut,
)
from app.dq.schemas import StudentRef
from app.students import service as students

log = get_logger(__name__)

READ: Final = "dq.readiness.read"
MANAGE: Final = "dq.readiness.manage"
STUDENT_READ: Final = "student.read_basic"

# Validated when the API or a worker imports this module (a broken file stops the start-up).
fc.load_followup_config()


def _error(field: str, code: str) -> dict[str, str]:
    return {"field": field, "code": code, "message_key": f"errors.{code}"}


def _request_id() -> str | None:
    value = get_context().get("request_id")
    return value if isinstance(value, str) else None


def _steps_by_key(
    session: Session, profile_key: str, student_ids: Collection[uuid.UUID]
) -> dict[tuple[uuid.UUID, str], list[fc.Step]]:
    out: dict[tuple[uuid.UUID, str], list[fc.Step]] = defaultdict(list)
    for row in repo.followups_of(session, profile_key, student_ids):
        out[(row["student_id"], row["fix_owner"])].append(
            fc.Step(row["event"], row["occurred_on"], row["promised_on"])
        )
    return out


def _items(
    session: Session,
    ctx: UserContext,
    profile_key: str,
    fixes: list[readiness.OpenFix],
    today: dt.date,
) -> list[FollowupItemOut]:
    cfg = fc.load_followup_config().followups
    ids = [f.student_id for f in fixes]
    steps = _steps_by_key(session, profile_key, ids)
    names = students.summaries(session, ctx, ids)
    items: list[FollowupItemOut] = []
    for fix in fixes:
        ref = names.get(fix.student_id)
        for owner in fc.OWNERS:
            if owner not in fix.owners:
                continue
            state = fc.followup_state(
                steps.get((fix.student_id, owner), []), today, cfg.overdue_after_days
            )
            items.append(
                FollowupItemOut(
                    student=StudentRef(
                        id=fix.student_id,
                        display_name=ref.display_name if ref else None,
                        admission_no=ref.admission_no if ref else None,
                    ),
                    section_id=fix.section_id,
                    readiness_status=cast(Any, fix.status),
                    fix_owner=owner,
                    state=state.state,
                    overdue_reason=state.overdue_reason,
                    last_event=cast(Any, state.last_event),
                    last_on=state.last_on,
                    promised_on=state.promised_on,
                    steps=state.steps,
                )
            )
    return items


_ORDER: Final = {"overdue": 0, "not_started": 1, "update_reported": 2, "waiting": 3}


def _totals(items: list[FollowupItemOut]) -> list[FollowupOwnerCounts]:
    out: list[FollowupOwnerCounts] = []
    for owner in fc.OWNERS:
        mine = [i for i in items if i.fix_owner == owner]
        counts = {s: sum(1 for i in mine if i.state == s) for s in _ORDER}
        out.append(
            FollowupOwnerCounts(fix_owner=owner, counts=FollowupCounts(open=len(mine), **counts))
        )
    return out


def list_followups(
    session: Session,
    ctx: UserContext,
    profile_key: str,
    *,
    section_id: uuid.UUID | None = None,
    states: Collection[str] | None = None,
    fix_owner: str | None = None,
) -> FollowupListOut:
    """Open follow-ups of the profile in the caller's reach, overdue first (FR-UDI-023): one
    per student and fix owner (parent via Aadhaar, MEO via UDISE+) while the readiness check
    still finds a difference that owner must fix. Totals count every open follow-up of the
    reach (before the ``states``/``fix_owner`` filters)."""
    today = calendar_service.today_ist()
    fixes = readiness.open_fixes(session, ctx, profile_key, section_id=section_id)
    items = _items(session, ctx, profile_key, fixes, today)
    totals = _totals(items)
    shown = [
        i
        for i in items
        if (not states or i.state in states) and (fix_owner is None or i.fix_owner == fix_owner)
    ]
    shown.sort(
        key=lambda i: (
            _ORDER[i.state],
            i.last_on or dt.date.min,
            i.student.display_name or "",
            str(i.student.id),
        )
    )
    log.info("dq.followup.listed", count=len(shown))
    return FollowupListOut(
        profile_key=profile_key,
        today=today,
        overdue_after_days=fc.load_followup_config().followups.overdue_after_days,
        totals=totals,
        items=shown,
    )


def history(
    session: Session, ctx: UserContext, profile_key: str, student_id: uuid.UUID
) -> FollowupHistoryOut:
    """Every step recorded for one student and profile, and the follow-ups still open
    (FR-UDI-024). A student outside the caller's reach: 404."""
    readiness.readiness_profile(profile_key)
    students.ensure_in_scope(session, ctx, student_id, READ)
    today = calendar_service.today_ist()
    fixes = readiness.open_fixes(session, ctx, profile_key, student_ids=[student_id])
    rows = repo.followups_of(session, profile_key, [student_id])
    return FollowupHistoryOut(
        student_id=student_id,
        profile_key=profile_key,
        open=_items(session, ctx, profile_key, fixes, today),
        steps=[
            FollowupStepOut(
                id=r["id"],
                fix_owner=r["fix_owner"],
                event=r["event"],
                channel=r["channel"],
                occurred_on=r["occurred_on"],
                promised_on=r["promised_on"],
                recorded_at=r["recorded_at"],
            )
            for r in rows
        ],
    )


def _check_step(data: FollowupIn, today: dt.date) -> dt.date:
    cfg = fc.load_followup_config()
    spec = cfg.followups
    errors: list[dict[str, str]] = []
    if data.event not in spec.owners[data.fix_owner].events:
        errors.append(_error("event", "event_not_for_owner"))
    channels = cfg.channels_for(data.event)
    if channels and data.channel is None:
        errors.append(_error("channel", "channel_required"))
    elif data.channel is not None and data.channel not in channels:
        errors.append(_error("channel", "channel_not_allowed"))
    occurred = data.occurred_on or today
    if occurred > today:
        errors.append(_error("occurred_on", "future_date"))
    elif (today - occurred).days > spec.max_backdate_days:
        errors.append(_error("occurred_on", "too_old"))
    if data.event == "promised_by":
        if data.promised_on is None:
            errors.append(_error("promised_on", "required"))
        elif data.promised_on < occurred:
            errors.append(_error("promised_on", "before_step"))
        elif (data.promised_on - occurred).days > spec.max_promise_days:
            errors.append(_error("promised_on", "too_far"))
    elif data.promised_on is not None:
        errors.append(_error("promised_on", "not_allowed"))
    if errors:
        raise ValidationFailed(errors)
    return occurred


def record(
    session: Session, ctx: UserContext, profile_key: str, data: FollowupIn
) -> FollowupRecordedOut:
    """Record one step for one or more students (FR-UDI-025..027): each student must be in the
    caller's reach with ``dq.readiness.read`` and ``dq.readiness.manage`` (else 404, no
    existence oracle) and must still have an open difference for ``fix_owner`` (else 422
    ``no_open_fix``). Audited ``dq.followup.recorded`` (ids and codes only)."""
    readiness.readiness_profile(profile_key)
    today = calendar_service.today_ist()
    occurred = _check_step(data, today)
    wanted = list(data.student_ids)
    reach = set(students.list_students_in_scope(session, ctx, permissions=(READ, MANAGE)))
    if any(sid not in reach for sid in wanted):
        raise NotFound("Student not found")
    fixes = {
        f.student_id: f for f in readiness.open_fixes(session, ctx, profile_key, student_ids=wanted)
    }
    if any(sid not in fixes or data.fix_owner not in fixes[sid].owners for sid in wanted):
        raise ValidationFailed([_error("student_ids", "no_open_fix")])
    ids = [new_id() for _ in wanted]
    rows = [
        {
            "id": row_id,
            "student_id": sid,
            "profile_key": profile_key,
            "fix_owner": data.fix_owner,
            "event": data.event,
            "channel": data.channel,
            "occurred_on": occurred,
            "promised_on": data.promised_on,
            "recorded_by": ctx.user_id,
            "recorded_by_membership": ctx.membership_id,
        }
        for row_id, sid in zip(ids, wanted, strict=True)
    ]
    repo.insert_followups(session, rows)
    audit.record(
        session,
        action="dq.followup.recorded",
        resource_type="dq_followup",
        resource_id=ids[0] if len(ids) == 1 else None,
        summary={
            "profile_key": profile_key,
            "fix_owner": data.fix_owner,
            "event": data.event,
            "channel": data.channel,
            "occurred_on": occurred.isoformat(),
            "promised_on": data.promised_on.isoformat() if data.promised_on else None,
            "student_ids": wanted,
            "count": len(wanted),
        },
        request_id=_request_id(),
    )
    log.info("dq.followup.recorded", count=len(wanted))
    return FollowupRecordedOut(recorded=len(wanted))


def overdue_count(session: Session, ctx: UserContext, profile_key: str) -> int:
    """Overdue follow-ups of the profile in ``ctx``'s reach (the portal reminder's count,
    FR-UDI-028); 0 for a profile without a readiness check."""
    try:
        listed = list_followups(session, ctx, profile_key)
    except NotFound:
        return 0
    return sum(t.counts.overdue for t in listed.totals)


__all__ = ["MANAGE", "READ", "history", "list_followups", "overdue_count", "record"]
