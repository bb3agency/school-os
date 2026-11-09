"""The UDISE+ to-do list (FO-03; FR-UDI-031..FR-UDI-040). Routes: ``app.dq.followup_api``
(``/api/v1/dq/udise-todo``).

SchoolOS already records what happened at the school: a transfer certificate issued, a child who
left without one, a new admission, a year-end promotion. Each of these has to be repeated by the
clerk in the UDISE+ Student Module (mark transferred, move to the dropbox, create or import by
PEN, progression). This module lists that work; it never touches the portal (no automation, no
portal credentials; docs/18 "Kept out of scope").

- **Live.** The items are computed from the records on every read, from the current academic
  year's start minus ``window_days_before_year`` days. Only the clerk's tick is stored
  (``sis.udise_movement_marks``: ``done`` or ``not_needed``; undo deletes it).
- **Kinds.** ``transfer_out`` (an issued TC of a student who left), ``left_without_tc`` (left,
  no issued TC: the UDISE+ dropbox), ``admission`` (admission date in the window; the action is
  "import by PEN" when the record holds a PEN, otherwise "search, create only if not found, then
  record the PEN"), ``progression`` (one item per committed promotion into the current year;
  school-wide readers only).
- **Scope.** Reading needs ``dq.readiness.read``, ticking ``dq.readiness.manage``, each with
  ``student.read_basic`` reaching the student; an item outside the reach, or of another school,
  is 404. Ticks are audited (``dq.udise_todo.marked`` / ``dq.udise_todo.reopened``) with ids
  and codes only.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Collection
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.authz.context import UserContext
from app.certificates import service as certificates
from app.core.errors import BadRequest, NotFound, PreconditionFailed
from app.core.ids import new_id
from app.core.logging import get_context, get_logger
from app.dq import calendar_service
from app.dq import followup_config as fc
from app.dq import followup_repository as repo
from app.dq.followup_schemas import (
    MovementCounts,
    MovementItemOut,
    MovementListOut,
    MovementTotals,
)
from app.dq.schemas import StudentRef
from app.students import service as students
from app.tenancy import service as tenancy

log = get_logger(__name__)

READ: Final = "dq.readiness.read"
MANAGE: Final = "dq.readiness.manage"
PEN_KEY: Final = "udise_pen"
ADMISSION_KEY: Final = "admission_date"
LIVE: Final = ("active", "provisional")


@dataclass(frozen=True, slots=True)
class _Item:
    key: str
    kind: fc.MovementKind
    action: fc.MovementAction
    student_id: uuid.UUID | None
    event_on: dt.date
    serial: str | None = None
    progression: MovementCounts | None = None


def _request_id() -> str | None:
    value = get_context().get("request_id")
    return value if isinstance(value, str) else None


def _parse_date(value: str | None) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def _current_year(session: Session) -> Any:
    years = [
        y for y in tenancy.list_academic_years(session, include_archived=False) if y.is_current
    ]
    return years[0] if years else None


def _items(
    session: Session, ctx: UserContext, permissions: tuple[str, ...]
) -> tuple[list[_Item], dt.date | None]:
    """Every item in the caller's reach (``permissions`` and ``student.read_basic``)."""
    year = _current_year(session)
    if year is None:
        return [], None
    window = fc.load_followup_config().movements.window_days_before_year
    since = year.starts_on - dt.timedelta(days=window)
    reach = students.list_students_in_scope(session, ctx, permissions=permissions)
    statuses = students.record_statuses(session, reach)
    left = [sid for sid in reach if statuses.get(sid) == "left"]
    live = [sid for sid in reach if statuses.get(sid) in LIVE]
    out: list[_Item] = []
    tcs = certificates.issued_transfer_certificates(session, left)
    for sid, tc in tcs.items():
        if tc.issued_on >= since:
            out.append(
                _Item(
                    f"transfer_out:{tc.certificate_id}",
                    "transfer_out",
                    "transfer_out",
                    sid,
                    tc.issued_on,
                    serial=tc.serial,
                )
            )
    without_tc = [sid for sid in left if sid not in tcs]
    for sid, left_on in students.leaving_dates(session, without_tc).items():
        if left_on >= since:
            out.append(
                _Item(f"left_without_tc:{sid}", "left_without_tc", "left_without_tc", sid, left_on)
            )
    values = students.canonical_values(session, live, [ADMISSION_KEY, PEN_KEY])
    for sid in live:
        per = values.get(sid, {})
        admitted = _parse_date(per[ADMISSION_KEY].value) if ADMISSION_KEY in per else None
        if admitted is None or admitted < since:
            continue
        has_pen = PEN_KEY in per and bool(per[PEN_KEY].value)
        action: fc.MovementAction = "admission_with_pen" if has_pen else "admission_without_pen"
        out.append(_Item(f"admission:{sid}", "admission", action, sid, admitted))
    if all(ctx.scope_for(p).school_wide for p in permissions):
        for run in students.promotions_into(session, year.id):
            on = run.committed_at.astimezone(calendar_service.IST).date()
            out.append(
                _Item(
                    f"progression:{run.run_id}",
                    "progression",
                    "progression",
                    None,
                    on,
                    progression=MovementCounts(
                        promoted=run.promoted, held_back=run.held_back, graduated=run.graduated
                    ),
                )
            )
    return out, since


def _out(
    item: _Item,
    mark: Any,
    names: dict[uuid.UUID, Any],
    today: dt.date,
    overdue_after: int,
) -> MovementItemOut:
    state = mark["state"] if mark is not None else "open"
    ref = names.get(item.student_id) if item.student_id is not None else None
    return MovementItemOut(
        item_key=item.key,
        kind=item.kind,
        action=item.action,
        student=StudentRef(
            id=item.student_id,
            display_name=ref.display_name if ref else None,
            admission_no=ref.admission_no if ref else None,
        )
        if item.student_id is not None
        else None,
        class_section=ref.class_section if ref else None,
        event_on=item.event_on,
        certificate_serial=item.serial,
        progression=item.progression,
        state=state,
        overdue=fc.movement_overdue(item.event_on, today, overdue_after, open_=state == "open"),
        marked_at=mark["marked_at"] if mark is not None else None,
        version=mark["version"] if mark is not None else None,
    )


def list_items(
    session: Session, ctx: UserContext, *, states: Collection[str] | None = None
) -> MovementListOut:
    """The UDISE+ to-do list in the caller's reach (FR-UDI-031..034): open items first (the
    longest waiting first), then the ticked ones, newest first. Totals count the whole reach
    (before the ``states`` filter)."""
    spec = fc.load_followup_config().movements
    today = calendar_service.today_ist()
    items, since = _items(session, ctx, (READ,))
    marks = repo.marks(session, [i.key for i in items])
    names = students.summaries(
        session, ctx, [i.student_id for i in items if i.student_id is not None]
    )
    outs = [_out(i, marks.get(i.key), names, today, spec.overdue_after_days) for i in items]
    totals = MovementTotals(
        open=sum(1 for o in outs if o.state == "open"),
        overdue=sum(1 for o in outs if o.overdue),
        done=sum(1 for o in outs if o.state == "done"),
        not_needed=sum(1 for o in outs if o.state == "not_needed"),
    )
    shown = [o for o in outs if not states or o.state in states]
    shown.sort(
        key=lambda o: (
            o.state != "open",
            o.event_on.toordinal() if o.state == "open" else -o.event_on.toordinal(),
            o.item_key,
        )
    )
    log.info("dq.udise_todo.listed", count=len(shown))
    return MovementListOut(
        verified=spec.verified,
        source=list(spec.source),
        since=since,
        today=today,
        overdue_after_days=spec.overdue_after_days,
        totals=totals,
        items=shown,
    )


def _find(session: Session, ctx: UserContext, item_key: str) -> _Item:
    items, _ = _items(session, ctx, (READ, MANAGE))
    for item in items:
        if item.key == item_key:
            return item
    raise NotFound("To-do item not found")


def _audit(session: Session, action: str, item: _Item, summary: dict[str, Any]) -> None:
    ref = uuid.UUID(item.key.split(":", 1)[1])
    audit.record(
        session,
        action=action,
        resource_type="udise_todo",
        resource_id=ref,
        summary={
            "kind": item.kind,
            "action": item.action,
            "student_id": item.student_id,
            **summary,
        },
        request_id=_request_id(),
    )


def _item_out(session: Session, ctx: UserContext, item: _Item, mark: Any) -> MovementItemOut:
    spec = fc.load_followup_config().movements
    names = (
        students.summaries(session, ctx, [item.student_id]) if item.student_id is not None else {}
    )
    return _out(item, mark, dict(names), calendar_service.today_ist(), spec.overdue_after_days)


def mark(
    session: Session,
    ctx: UserContext,
    item_key: str,
    state: str,
    *,
    expected_version: int | None,
) -> MovementItemOut:
    """Tick an item ``done`` (repeated in UDISE+) or ``not_needed`` (FR-UDI-035). The first tick
    needs no ``If-Match``; changing one does (400 ``if_match_required``, 412 when stale). An item
    outside the caller's reach (``dq.readiness.manage``): 404. Audited ``dq.udise_todo.marked``.
    """
    item = _find(session, ctx, item_key)
    row = repo.mark(session, item_key, lock=True)
    previous = row["state"] if row is not None else "open"
    values = {
        "state": state,
        "marked_by": ctx.user_id,
        "marked_by_membership": ctx.membership_id,
        "marked_at": dt.datetime.now(dt.UTC),
    }
    if row is None:
        if expected_version is not None:
            raise PreconditionFailed(
                "Someone else changed this in the meantime. Reload it and try again.",
                code="version_mismatch",
            )
        try:
            with session.begin_nested():
                saved = repo.insert_mark(
                    session,
                    {
                        "id": new_id(),
                        "item_key": item_key,
                        "kind": item.kind,
                        "student_id": item.student_id,
                        **values,
                    },
                )
        except IntegrityError as exc:
            raise PreconditionFailed(
                "Someone else ticked this in the meantime. Reload it and try again.",
                code="version_mismatch",
            ) from exc
    else:
        if expected_version is None:
            raise BadRequest(
                "Send If-Match with the ETag you last read, so changes are not overwritten.",
                code="if_match_required",
            )
        updated = repo.update_mark(session, row["id"], expected_version, values)
        if updated is None:
            raise PreconditionFailed(
                "Someone else changed this in the meantime. Reload it and try again.",
                code="version_mismatch",
            )
        saved = updated
    _audit(session, "dq.udise_todo.marked", item, {"from": previous, "to": state})
    return _item_out(session, ctx, item, saved)


def reopen(session: Session, ctx: UserContext, item_key: str, *, expected_version: int) -> None:
    """Undo a tick (``If-Match``): the item is open again (FR-UDI-036). Nothing ticked: 404.
    Audited ``dq.udise_todo.reopened``."""
    item = _find(session, ctx, item_key)
    row = repo.mark(session, item_key, lock=True)
    if row is None:
        raise NotFound("To-do item not ticked")
    if row["version"] != expected_version or not repo.delete_mark(
        session, row["id"], expected_version
    ):
        raise PreconditionFailed(
            "Someone else changed this in the meantime. Reload it and try again.",
            code="version_mismatch",
        )
    _audit(session, "dq.udise_todo.reopened", item, {"from": row["state"], "to": "open"})


def open_counts(session: Session, ctx: UserContext) -> tuple[int, int]:
    """(open, waiting too long) in ``ctx``'s reach, for the home screen and the UDISE+ portal
    reminder (FR-UDI-037, FR-UDI-038)."""
    totals = list_items(session, ctx).totals
    return totals.open, totals.overdue


__all__ = ["MANAGE", "READ", "list_items", "mark", "open_counts", "reopen"]
