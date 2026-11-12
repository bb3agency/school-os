"""Safety drills and periodic checks log (FC-09; FR-COMP-046..FR-COMP-055; docs/05 §16.10).

School-level records: which drill or check was done, when, who led it (a staff member), how
many took part and how long it took (drills), the result and a short note, and a report in
Documents. Never student names (the form says so; a full Aadhaar number is refused) and never
CCTV footage (only that the cameras were checked).

- **Kinds (FR-COMP-046, FR-COMP-047).** ``checks.yaml`` (``verified: false``) lists the kinds,
  how often each is usually done and the boards it applies to; ``other`` is a check the school
  names itself (no reminders).
- **Log (FR-COMP-048, FR-COMP-049).** ``compliance.manage`` logs an entry; an entry logged by
  mistake is ``removed`` (kept, left out of the log and of the due dates).
- **Due dates (FR-COMP-050).** The next check of a kind is due ``every_months`` after the latest
  entry; ``never`` until one is logged.
- **Reminders (FR-COMP-051, FR-COMP-052).** Daily, in-app, to the holders of
  ``compliance.manage``: at the lead days before the due date, then every
  ``overdue_every_days`` while overdue. Only for kinds logged at least once. Idempotent per
  entry and lead day; a new entry restarts them.
- **Audit (FR-COMP-053).** ``compliance.check.recorded`` / ``.removed`` / ``.reminded`` in the
  same transaction: ids, kind, dates and counts, never the typed note.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterable, Mapping
from typing import Any, Final
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.audit import service as audit
from app.authz.context import UserContext
from app.compliance import repository as cert_repo
from app.compliance import safety_config as cfg
from app.compliance import safety_repository as repo
from app.compliance.config import due_reminder
from app.compliance.safety_models import ComplianceCheck as Row
from app.compliance.safety_schemas import (
    CheckCountsOut,
    CheckCreate,
    CheckKindOut,
    ChecksLogOut,
    ChecksOverviewOut,
    CheckStatusOut,
    ComplianceCheckOut,
)
from app.compliance.schemas import ComplianceOwnerOut, DocumentRefOut
from app.core.db import tenant_session
from app.core.errors import Conflict, NotFound, PreconditionFailed, ValidationFailed
from app.core.ids import new_id
from app.core.logging import get_context, get_logger
from app.core.redaction import contains_full_aadhaar
from app.documents import service as documents
from app.identity import service as identity
from app.notifications import service as notifications
from app.tenancy import service as tenancy

MANAGE: Final = "compliance.manage"
RESOURCE: Final = "compliance_check"
TEMPLATE: Final = "compliance.check.due"
MANAGERS = notifications.PermissionSelector(MANAGE)
RECENT: Final = 50
IST: Final = ZoneInfo("Asia/Kolkata")

log = get_logger(__name__)


def _error(field: str, code: str) -> dict[str, str]:
    return {"field": field, "code": code, "message_key": f"errors.{code}"}


def _request_id() -> str | None:
    value = get_context().get("request_id")
    return value if isinstance(value, str) else None


def _audit(
    session: Session,
    action: str,
    row: Row,
    extra: Mapping[str, Any] | None = None,
    *,
    system: bool = False,
) -> None:
    audit.record(
        session,
        action=action,
        resource_type=RESOURCE,
        resource_id=row.id,
        summary={
            "kind": row.kind,
            "done_on": row.done_on.isoformat(),
            "participants_count": row.participants_count,
            "result": row.result,
            "document_id": str(row.document_id) if row.document_id else None,
            **dict(extra or {}),
        },
        actor_type="system" if system else "user",
        request_id=None if system else _request_id(),
    )


def today(session: Session) -> dt.date:
    """Today in IST from the database clock (as the certificate register)."""
    return cert_repo.now(session).astimezone(IST).date()


def _kind_label(row: Row) -> str:
    if row.kind == cfg.OTHER and row.label:
        return row.label
    kind = cfg.checks().kinds.get(row.kind)
    return kind.label if kind is not None else row.kind


def _outs(session: Session, ctx: UserContext, rows: Iterable[Row]) -> list[ComplianceCheckOut]:
    rows = list(rows)
    leader_ids = {r.led_by_membership_id for r in rows if r.led_by_membership_id}
    names = identity.member_display_names(session, leader_ids) if leader_ids else {}
    active = {m for m in leader_ids if identity.is_active_member(session, m)}
    found = identity.members_for_users(session, [r.recorded_by for r in rows]) if rows else {}
    doc_ids = [r.document_id for r in rows if r.document_id is not None]
    visible = documents.visible_document_ids(session, ctx, doc_ids) if doc_ids else set()
    titles = {d: documents.get_document(session, ctx, d).title for d in visible}
    out = []
    for r in rows:
        led = None
        if r.led_by_membership_id is not None:
            led = ComplianceOwnerOut(
                membership_id=r.led_by_membership_id,
                display_name=names.get(r.led_by_membership_id),
                active=r.led_by_membership_id in active,
            )
        doc = (
            DocumentRefOut(id=r.document_id, title=titles.get(r.document_id))
            if (r.document_id is not None)
            else None
        )
        out.append(
            ComplianceCheckOut(
                id=r.id,
                kind=r.kind,
                kind_label=_kind_label(r),
                label=r.label,
                done_on=r.done_on,
                led_by=led,
                participants_count=r.participants_count,
                duration_minutes=r.duration_minutes,
                result="action_needed" if r.result == "action_needed" else "ok",
                observations=r.observations,
                document=doc,
                status="removed" if r.status == "removed" else "recorded",
                recorded_by_name=found.get(r.recorded_by, (None, None))[1],
                created_at=r.created_at,
                version=r.version,
            )
        )
    return out


_ORDER: Final = {"overdue": 0, "never": 1, "due_soon": 2, "ok": 3, "logged": 4}


def _statuses(session: Session, day: dt.date) -> list[CheckStatusOut]:
    cat = cfg.checks()
    boards, _ = tenancy.school_boards(session)
    latest = repo.latest_by_kind(session)
    out = []
    for key, kind in cat.kinds_for(boards).items():
        last = latest.get(key)
        done = last.done_on if last else None
        due = cfg.next_due(kind, done)
        out.append(
            CheckStatusOut(
                kind=key,
                kind_label=kind.label,
                every_months=kind.every_months,
                drill=kind.drill,
                state=cfg.check_state(kind, done, day, due_soon_days=cat.due_soon_days),
                last_done_on=done,
                next_due=due,
                days_left=(due - day).days if due else None,
                last_entry_id=last.id if last else None,
            )
        )
    keys = list(cat.kinds)
    out.sort(key=lambda s: (_ORDER[s.state], s.next_due or dt.date.max, keys.index(s.kind)))
    return out


def overview(session: Session, ctx: UserContext) -> ChecksOverviewOut:
    """Every check kind that applies to the school with its due date, counts, the latest
    entries and the catalogue (``compliance.read``; FR-COMP-050, FR-COMP-054)."""
    cat = cfg.checks()
    day = today(session)
    boards, _ = tenancy.school_boards(session)
    statuses = _statuses(session, day)
    counts = CheckCountsOut(
        overdue=sum(1 for s in statuses if s.state == "overdue"),
        due_soon=sum(1 for s in statuses if s.state == "due_soon"),
        never=sum(1 for s in statuses if s.state == "never"),
        ok=sum(1 for s in statuses if s.state == "ok"),
    )
    leaders = []
    if ctx.has(MANAGE):
        leaders = sorted(
            (
                ComplianceOwnerOut(
                    membership_id=m.membership_id, display_name=m.display_name, active=True
                )
                for m in identity.active_members(session)
            ),
            key=lambda o: (o.display_name or "").casefold(),
        )
    return ChecksOverviewOut(
        statuses=statuses,
        counts=counts,
        recent=_outs(session, ctx, repo.log(session, limit=RECENT)),
        kinds=[
            CheckKindOut(
                kind=key,
                label=k.label,
                every_months=k.every_months,
                drill=k.drill,
                note=k.note,
                applies=key != cfg.OTHER and k.applies_to(boards),
            )
            for key, k in cat.kinds.items()
        ],
        due_soon_days=cat.due_soon_days,
        verified=cat.verified,
        today=day,
        leaders=leaders,
    )


def entries(session: Session, ctx: UserContext, *, kind: str | None = None) -> ChecksLogOut:
    """The log, newest first, optionally of one kind (``compliance.read``; FR-COMP-049)."""
    return ChecksLogOut(items=_outs(session, ctx, repo.log(session, kind=kind)))


def record(session: Session, ctx: UserContext, data: CheckCreate) -> ComplianceCheckOut:
    """Log a drill or check (``compliance.manage``; FR-COMP-048). Errors (422):
    ``unknown_check_kind``, ``check_label_required``, ``check_label_not_allowed``,
    ``done_in_future``, ``drill_fields_not_allowed``, ``document_not_found``,
    ``leader_not_active``, ``aadhaar_full_number_rejected``."""
    cat = cfg.checks()
    day = today(session)
    errors: list[dict[str, str]] = []
    kind = cat.kinds.get(data.kind)
    if kind is None:
        errors.append(_error("kind", "unknown_check_kind"))
    if data.kind == cfg.OTHER and (data.label is None or len(data.label) < 3):
        errors.append(_error("label", "check_label_required"))
    if data.kind != cfg.OTHER and data.label is not None:
        errors.append(_error("label", "check_label_not_allowed"))
    if data.done_on > day:
        errors.append(_error("done_on", "done_in_future"))
    if kind is not None and not kind.drill and data.kind != cfg.OTHER:
        for field in ("participants_count", "duration_minutes"):
            if getattr(data, field) is not None:
                errors.append(_error(field, "drill_fields_not_allowed"))
    for field in ("label", "observations"):
        value = getattr(data, field)
        if value and contains_full_aadhaar(value):
            errors.append(_error(field, "aadhaar_full_number_rejected"))
    if data.document_id is not None and not (
        documents.is_visible(session, ctx, data.document_id)
        and documents.evidence_exists(session, data.document_id)
    ):
        errors.append(_error("document_id", "document_not_found"))
    if data.led_by_membership_id is not None and not identity.is_active_member(
        session, data.led_by_membership_id
    ):
        errors.append(_error("led_by_membership_id", "leader_not_active"))
    if errors:
        raise ValidationFailed(errors)
    row = repo.insert_check(
        session,
        id=new_id(),
        tenant_id=cert_repo.current_tenant_id(session),
        kind=data.kind,
        label=data.label if data.kind == cfg.OTHER else None,
        done_on=data.done_on,
        led_by_membership_id=data.led_by_membership_id,
        participants_count=data.participants_count,
        duration_minutes=data.duration_minutes,
        result=data.result,
        observations=data.observations,
        document_id=data.document_id,
        recorded_by=ctx.user_id,
    )
    _audit(session, "compliance.check.recorded", row)
    (out,) = _outs(session, ctx, [row])
    return out


def remove(
    session: Session, ctx: UserContext, check_id: uuid.UUID, *, expected_version: int
) -> ComplianceCheckOut:
    """Remove an entry logged by mistake (``compliance.manage``, ``If-Match``; FR-COMP-049). It
    is kept as ``removed`` and no longer counts for the due date. Errors: 404 (another
    school's), 409 ``check_removed``, 412 stale ``If-Match``."""
    row = repo.get_check(session, check_id, lock=True)
    if row is None:
        raise NotFound("Check not found")
    if row.version != expected_version:
        raise PreconditionFailed("The entry was changed by someone else. Reload it.")
    if row.status != repo.RECORDED:
        raise Conflict("This entry was already removed.", code="check_removed")
    updated = repo.update_check(
        session,
        row,
        {"status": "removed", "removed_at": cert_repo.now(session), "removed_by": ctx.user_id},
        expected_version=expected_version,
    )
    if updated is None:
        raise PreconditionFailed("The entry was changed by someone else. Reload it.")
    _audit(session, "compliance.check.removed", updated)
    (out,) = _outs(session, ctx, [updated])
    return out


def send_reminders(tenant_id: uuid.UUID, *, day: dt.date | None = None) -> int:
    """Daily, one school: in-app reminders of checks due soon or overdue, to the holders of
    ``compliance.manage`` (FR-COMP-051, FR-COMP-052). Returns how many kinds were reminded."""
    cat = cfg.checks()
    rules = cat.reminders
    sent = 0
    with tenant_session(tenant_id) as session:
        on = day or today(session)
        boards, _ = tenancy.school_boards(session)
        expected = cat.kinds_for(boards)
        for row in repo.latest_for_reminder(session):
            kind = expected.get(row.kind)
            due_on = cfg.next_due(kind, row.done_on) if kind is not None else None
            if kind is None or due_on is None:
                continue
            due = due_reminder(
                due_on,
                on,
                lead_days=rules.lead_days,
                expired_every_days=rules.overdue_every_days,
                last_lead=row.last_reminded_lead,
                last_on=row.last_reminded_on,
            )
            if due is None:
                continue
            notified = notifications.notify(
                session,
                tenant_id=tenant_id,
                recipients=MANAGERS,
                template_key=TEMPLATE,
                params={
                    "check": kind.label,
                    "days": due.days,
                    "state": "overdue" if due.expired else "due",
                },
                resource_id=row.id,
                dedupe_key=f"{TEMPLATE}:{row.id}:{due.lead}:{on.isoformat()}",
            )
            repo.mark_check_reminded(session, row.id, due.lead, on)
            _audit(
                session,
                "compliance.check.reminded",
                row,
                {"lead_day": due.lead, "notified": notified},
                system=True,
            )
            sent += 1
    log.info("compliance.check_reminders_sent", count=sent)
    return sent


__all__ = [
    "RESOURCE",
    "TEMPLATE",
    "entries",
    "overview",
    "record",
    "remove",
    "send_reminders",
    "today",
]
