"""Portal calendar: the school's view, confirmations, own events and the next window of a
readiness profile (US-3101, US-3102, US-3104; FR-RDY-001..005, FR-RDY-009; ADR-0053). Routes:
``app.dq.api`` (``/api/v1/dq/calendar...``). The daily reminders and task links, which work
through ``circulars.service`` and ``notifications.service``, are in :mod:`app.dq.portal_tasks`.

- **Expected vs confirmed.** The calendar file (:mod:`app.dq.calendar`, ``verified: false``)
  gives each applicable event an expected window; a row in ``ops.portal_calendar_dates`` (who
  confirmed it, from where, and who owns the work) always wins. The school's own events live
  only in that table (``school:<slug>`` keys).
- **Permissions.** Reading needs ``dq.readiness.read``; confirming dates, adding or removing the
  school's own events and choosing owners need ``readiness.calendar.manage``. Readiness counts
  on the calendar are the reader's own scope.
- **Logs and audit** carry ids, keys, codes and counts only; the school's own event label is
  never logged or audited.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final
from zoneinfo import ZoneInfo

from sqlalchemy import RowMapping
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.authz.context import UserContext
from app.core.errors import BadRequest, Conflict, NotFound, PreconditionFailed, ValidationFailed
from app.core.ids import new_id
from app.core.languages import telugu_text
from app.core.logging import get_context, get_logger
from app.core.redaction import contains_full_aadhaar
from app.documents import service as documents
from app.dq import calendar as cal
from app.dq import repository as repo
from app.dq.profiles import load_profiles
from app.dq.schemas import (
    CalendarAssigneeOut,
    CalendarConfirmIn,
    CalendarEventIn,
    CalendarEventOut,
    CalendarMemberOut,
    CalendarOut,
    CalendarReadinessOut,
    ExpectedWindowOut,
    PortalWindowOut,
)
from app.identity import service as identity
from app.identity.schemas import StaffMemberOut
from app.tenancy import service as tenancy
from app.tenancy.schemas import AcademicYearOut

log = get_logger(__name__)

READ: Final = "dq.readiness.read"
READINESS_MANAGE: Final = "dq.readiness.manage"
MANAGE: Final = "readiness.calendar.manage"
STUDENT_READ: Final = "student.read_basic"
TEMPLATE: Final = "portal_calendar.reminder"
IST: Final = ZoneInfo("Asia/Kolkata")
SYSTEM_ID: Final = uuid.UUID(int=0)

# Validated when the API (or a worker) imports this module: a broken calendar file stops the
# start-up instead of a morning's reminders (FR-RDY-001).
cal.load_calendar()


def today_ist(now: dt.datetime | None = None) -> dt.date:
    return (now or dt.datetime.now(dt.UTC)).astimezone(IST).date()


def _error(field: str, code: str) -> dict[str, str]:
    return {"field": field, "code": code, "message_key": f"errors.{code}"}


def _request_id() -> str | None:
    value = get_context().get("request_id")
    return value if isinstance(value, str) else None


def audit_calendar(
    session: Session,
    action: str,
    resource_id: uuid.UUID | None,
    summary: Mapping[str, Any],
    *,
    system: bool = False,
) -> None:
    audit.record(
        session,
        action=action,
        resource_type="portal_calendar",
        resource_id=resource_id,
        summary=summary,
        actor_type="system" if system else "user",
        request_id=None if system else _request_id(),
    )


# --- events in force --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CalendarEvent:
    key: str
    year: AcademicYearOut
    own: bool
    portal: str
    label_en: str
    label_te: str
    profile_keys: tuple[str, ...]
    boards: tuple[str, ...]
    classes: tuple[str, ...]
    expected: cal.Window | None
    window: cal.Window
    lead_days: tuple[int, ...]
    sources: tuple[str, ...]
    verified: bool
    row: RowMapping | None


def _confirmed(row: RowMapping) -> cal.Window:
    return cal.Window(opens_on=row["opens_on"], closes_on=row["closes_on"], confirmed=True)


def events_in_force(session: Session, years: Sequence[AcademicYearOut]) -> list[CalendarEvent]:
    """Every event of these academic years that concerns the school (FR-RDY-002): the calendar
    file's applicable events with the school's confirmation laid over them, and the school's own
    events. Soonest closing first."""
    if not years:
        return []
    calendar = cal.load_calendar()
    boards, class_boards = tenancy.school_boards(session)
    codes = {c.code for c in tenancy.list_classes(session, include_archived=False)}
    rows = {
        (r["academic_year_id"], r["event_key"]): r
        for r in repo.calendar_rows(session, [y.id for y in years])
    }
    out: list[CalendarEvent] = []
    for year in years:
        for event in calendar.for_year(cal.year_key(year.starts_on)):
            if not cal.applies(event, boards=boards, class_boards=class_boards, class_codes=codes):
                continue
            row = rows.pop((year.id, event.key), None)
            expected = cal.expected_window(event)
            out.append(
                CalendarEvent(
                    key=event.key,
                    year=year,
                    own=False,
                    portal=event.portal,
                    label_en=event.label_en,
                    label_te=event.label_te,
                    profile_keys=event.profile_keys,
                    boards=event.boards,
                    classes=event.classes,
                    expected=expected,
                    window=_confirmed(row) if row is not None else expected,
                    lead_days=event.lead_days,
                    sources=event.source,
                    verified=event.verified,
                    row=row,
                )
            )
    by_id = {y.id: y for y in years}
    for (year_id, key), row in rows.items():
        if not cal.is_own_key(key):
            continue  # a confirmation of an event that no longer applies (boards changed)
        out.append(
            CalendarEvent(
                key=key,
                year=by_id[year_id],
                own=True,
                portal="school",
                label_en=row["label"],
                label_te=row["label"],
                profile_keys=tuple(row["profile_keys"]),
                boards=(),
                classes=(),
                expected=None,
                window=_confirmed(row),
                lead_days=calendar.default_lead_days,
                sources=(),
                verified=False,
                row=row,
            )
        )
    return sorted(out, key=lambda e: (e.window.closes_on, e.window.opens_on, e.key))


def academic_year(session: Session, academic_year_id: uuid.UUID | None) -> AcademicYearOut:
    """The given academic year of this school (404 otherwise), else the current one."""
    if academic_year_id is not None:
        return tenancy.get_academic_year(session, academic_year_id)
    years = tenancy.list_academic_years(session, include_archived=False)
    current = [y for y in years if y.is_current]
    if current:
        return current[0]
    if years:
        return years[0]
    raise NotFound("Academic year not found")


def find_event(session: Session, year: AcademicYearOut, event_key: str) -> CalendarEvent:
    for event in events_in_force(session, [year]):
        if event.key == event_key:
            return event
    raise NotFound("Calendar event not found")


def readiness_keys(event: CalendarEvent) -> list[str]:
    profiles = load_profiles()
    return [k for k in event.profile_keys if k in profiles and profiles[k].readiness is not None]


def readiness_counts(
    session: Session, ctx: UserContext, profile_key: str, cache: dict[str, tuple[int, int]]
) -> tuple[int, int]:
    """(ready, students) of a readiness profile in ``ctx``'s reach, once per call."""
    if profile_key not in cache:
        from app.dq import readiness_service  # noqa: PLC0415 - it imports this module

        totals = readiness_service.summary(session, ctx, profile_key).totals
        cache[profile_key] = (totals.ready, totals.students)
    return cache[profile_key]


def _member(
    names: Mapping[uuid.UUID, str], membership_id: uuid.UUID | None
) -> CalendarMemberOut | None:
    if membership_id is None:
        return None
    return CalendarMemberOut(membership_id=membership_id, display_name=names.get(membership_id))


def _event_out(
    event: CalendarEvent,
    today: dt.date,
    names: Mapping[uuid.UUID, str],
    readiness: list[CalendarReadinessOut],
) -> CalendarEventOut:
    row, window, expected = event.row, event.window, event.expected
    return CalendarEventOut(
        event_key=event.key,
        academic_year_id=event.year.id,
        own=event.own,
        portal=event.portal,
        label_en=event.label_en,
        label_te=telugu_text(event.label_te) or "",  # empty while Telugu is hidden (ADR-0036)
        profile_keys=list(event.profile_keys),
        boards=list(event.boards),
        classes=list(event.classes),
        expected=None
        if expected is None
        else ExpectedWindowOut(
            opens_on=expected.opens_on,
            closes_on=expected.closes_on,
            opens_approximate=expected.opens_approximate,
            closes_approximate=expected.closes_approximate,
        ),
        opens_on=window.opens_on,
        closes_on=window.closes_on,
        opens_approximate=window.opens_approximate,
        closes_approximate=window.closes_approximate,
        confirmed=window.confirmed,
        state=cal.state(window, today),
        days_left=cal.days_left(window, today),
        source_kind=None if row is None else row["source_kind"],
        document_id=None if row is None else row["document_id"],
        confirmed_by=None if row is None else _member(names, row["confirmed_by_membership"]),
        confirmed_at=None if row is None else row["confirmed_at"],
        owner=None if row is None else _member(names, row["owner_membership_id"]),
        lead_days=list(event.lead_days),
        sources=list(event.sources),
        verified=event.verified,
        readiness=readiness,
        version=None if row is None else row["version"],
    )


def _outs(
    session: Session, ctx: UserContext, events: Sequence[CalendarEvent], today: dt.date
) -> list[CalendarEventOut]:
    members: set[uuid.UUID] = set()
    for event in events:
        if event.row is not None:
            members.add(event.row["confirmed_by_membership"])
            if event.row["owner_membership_id"] is not None:
                members.add(event.row["owner_membership_id"])
    names = identity.member_display_names(session, members)
    profiles = load_profiles()
    cache: dict[str, tuple[int, int]] = {}
    out: list[CalendarEventOut] = []
    for event in events:
        readiness = []
        for key in readiness_keys(event):
            ready, students = readiness_counts(session, ctx, key, cache)
            readiness.append(
                CalendarReadinessOut(
                    profile_key=key,
                    label_en=profiles[key].label_en,
                    ready=ready,
                    students=students,
                )
            )
        out.append(_event_out(event, today, names, readiness))
    return out


# --- reads (FR-RDY-003, FR-RDY-009) -----------------------------------------------------------


def list_calendar(
    session: Session,
    ctx: UserContext,
    academic_year_id: uuid.UUID | None = None,
    *,
    today: dt.date | None = None,
) -> CalendarOut:
    """The school's portal calendar for one academic year (default: the current one), soonest
    closing first, with the readiness of each event's linked profiles in the reader's scope
    (``dq.readiness.read``)."""
    year = academic_year(session, academic_year_id)
    events = events_in_force(session, [year])
    log.info("dq.calendar.listed", count=len(events))
    return CalendarOut(
        academic_year_id=year.id,
        academic_year_label=year.label,
        events=_outs(session, ctx, events, today or today_ist()),
    )


def next_window(
    session: Session, profile_key: str, *, today: dt.date | None = None
) -> PortalWindowOut | None:
    """The next window of ``profile_key`` that has not closed, across the school's academic
    years still running or to come (FR-RDY-009); ``None`` when none is known."""
    day = today or today_ist()
    years = [
        y for y in tenancy.list_academic_years(session, include_archived=False) if y.ends_on >= day
    ]
    candidates = [
        e
        for e in events_in_force(session, years)
        if profile_key in e.profile_keys and cal.state(e.window, day) != "closed"
    ]
    if not candidates:
        return None
    event = candidates[0]  # soonest closing first
    return PortalWindowOut(
        event_key=event.key,
        academic_year_id=event.year.id,
        portal=event.portal,
        label_en=event.label_en,
        label_te=telugu_text(event.label_te) or "",
        opens_on=event.window.opens_on,
        closes_on=event.window.closes_on,
        opens_approximate=event.window.opens_approximate,
        closes_approximate=event.window.closes_approximate,
        confirmed=event.window.confirmed,
        state=cal.state(event.window, day),
        days_left=cal.days_left(event.window, day),
    )


def assignees(session: Session) -> list[CalendarAssigneeOut]:
    """Active staff who can own a portal window (``readiness.calendar.manage``; names and role
    keys only)."""
    members: list[StaffMemberOut] = identity.active_members(session)
    return sorted(
        (
            CalendarAssigneeOut(
                membership_id=m.membership_id, display_name=m.display_name, roles=list(m.roles)
            )
            for m in members
        ),
        key=lambda a: (a.display_name.casefold(), str(a.membership_id)),
    )


# --- writes (FR-RDY-004, FR-RDY-005, FR-RDY-010) ----------------------------------------------


def _check_document(session: Session, ctx: UserContext, document_id: uuid.UUID | None) -> None:
    if document_id is not None and not documents.is_visible(session, ctx, document_id):
        raise ValidationFailed([_error("document_id", "not_found")])


def active_member_ids(session: Session) -> set[uuid.UUID]:
    return {m.membership_id for m in identity.active_members(session)}


def _check_owner(session: Session, membership_id: uuid.UUID | None) -> None:
    if membership_id is not None and membership_id not in active_member_ids(session):
        raise ValidationFailed([_error("owner_membership_id", "owner_not_active")])


def _check_version(row: RowMapping | None, expected: int | None) -> None:
    if row is None:
        if expected is not None:
            raise PreconditionFailed(
                "Someone else changed this in the meantime. Reload it and try again.",
                code="version_mismatch",
            )
        return
    if expected is None:
        raise BadRequest(
            "Send If-Match with the ETag you last read, so changes are not overwritten.",
            code="if_match_required",
        )
    if row["version"] != expected:
        raise PreconditionFailed(
            "Someone else changed this in the meantime. Reload it and try again.",
            code="version_mismatch",
        )


def confirmation_values(ctx: UserContext, **values: Any) -> dict[str, Any]:
    return {
        **values,
        "confirmed_by": ctx.user_id,
        "confirmed_by_membership": ctx.membership_id,
        "confirmed_at": dt.datetime.now(dt.UTC),
    }


def event_detail(
    session: Session, ctx: UserContext, year: AcademicYearOut, key: str
) -> CalendarEventOut:
    return _outs(session, ctx, [find_event(session, year, key)], today_ist())[0]


def confirm(
    session: Session,
    ctx: UserContext,
    event_key: str,
    data: CalendarConfirmIn,
    *,
    expected_version: int | None,
) -> CalendarEventOut:
    """Confirm an event's dates with where they came from and choose the owner of the work
    (``readiness.calendar.manage``; FR-RDY-004). The first confirmation needs no ``If-Match``;
    changing one does (412 when stale). An event that does not concern the school: 404.
    Audited ``readiness.calendar.confirmed``."""
    year = academic_year(session, data.academic_year_id)
    find_event(session, year, event_key)
    row = repo.calendar_row(session, year.id, event_key, lock=True)
    _check_version(row, expected_version)
    _check_document(session, ctx, data.document_id)
    _check_owner(session, data.owner_membership_id)
    values = confirmation_values(
        ctx,
        opens_on=data.opens_on,
        closes_on=data.closes_on,
        source_kind=data.source_kind,
        document_id=data.document_id,
        owner_membership_id=data.owner_membership_id,
    )
    if row is None:
        row = repo.insert_calendar_row(
            session,
            {"id": new_id(), "academic_year_id": year.id, "event_key": event_key, **values},
        )
        created = True
    else:
        row = repo.update_calendar_row(session, row["id"], values)
        created = False
    audit_calendar(
        session,
        "readiness.calendar.confirmed",
        row["id"],
        {
            "event_key": event_key,
            "academic_year_id": year.id,
            "source_kind": data.source_kind,
            "document_id": data.document_id,
            "owner_membership_id": data.owner_membership_id,
            "created": created,
        },
    )
    return event_detail(session, ctx, year, event_key)


def add_event(session: Session, ctx: UserContext, data: CalendarEventIn) -> CalendarEventOut:
    """Add the school's own event (``readiness.calendar.manage``; FR-RDY-005): a label in the
    school's words, its dates, the readiness profiles it concerns and an owner. Audited
    ``readiness.calendar.event_added`` (the label is not audited)."""
    if contains_full_aadhaar(data.label):
        raise ValidationFailed([_error("label", "aadhaar_full_number_rejected")])
    year = academic_year(session, data.academic_year_id)
    unknown = [k for k in data.profile_keys if k not in load_profiles()]
    if unknown:
        raise ValidationFailed([_error("profile_keys", "unknown_profile")])
    _check_document(session, ctx, data.document_id)
    _check_owner(session, data.owner_membership_id)
    key = cal.own_event_key(data.label, salt=new_id())
    try:
        with session.begin_nested():
            row = repo.insert_calendar_row(
                session,
                {
                    "id": new_id(),
                    "academic_year_id": year.id,
                    "event_key": key,
                    "label": data.label.strip(),
                    "profile_keys": list(dict.fromkeys(data.profile_keys)),
                    **confirmation_values(
                        ctx,
                        opens_on=data.opens_on,
                        closes_on=data.closes_on,
                        source_kind=data.source_kind,
                        document_id=data.document_id,
                        owner_membership_id=data.owner_membership_id,
                    ),
                },
            )
    except IntegrityError:
        raise Conflict(
            "Try again: an event with this name was just added.", code="conflict"
        ) from None
    audit_calendar(
        session,
        "readiness.calendar.event_added",
        row["id"],
        {
            "event_key": key,
            "academic_year_id": year.id,
            "profile_keys": list(row["profile_keys"]),
            "owner_membership_id": data.owner_membership_id,
        },
    )
    return event_detail(session, ctx, year, key)


def remove(
    session: Session,
    ctx: UserContext,
    event_key: str,
    academic_year_id: uuid.UUID,
    *,
    expected_version: int,
) -> None:
    """Remove the school's own event, or undo a confirmation so the expected dates show again
    (``readiness.calendar.manage``; ``If-Match``). Nothing confirmed or added: 404. Reminders
    already sent stay in the history. Audited ``readiness.calendar.removed``."""
    year = academic_year(session, academic_year_id)
    row = repo.calendar_row(session, year.id, event_key, lock=True)
    if row is None:
        raise NotFound("Calendar event not found")
    _check_version(row, expected_version)
    repo.delete_calendar_row(session, row["id"])
    audit_calendar(
        session,
        "readiness.calendar.removed",
        row["id"],
        {"event_key": event_key, "academic_year_id": year.id, "own": cal.is_own_key(event_key)},
    )


__all__ = [
    "MANAGE",
    "READ",
    "CalendarEvent",
    "academic_year",
    "active_member_ids",
    "add_event",
    "assignees",
    "audit_calendar",
    "confirm",
    "confirmation_values",
    "event_detail",
    "events_in_force",
    "find_event",
    "list_calendar",
    "next_window",
    "readiness_counts",
    "readiness_keys",
    "remove",
    "today_ist",
]
