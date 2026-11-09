"""Per-student add-on fees: transport bands, hostel, optional activities (FM-02;
FR-FEE-081..100; ADR-0042 follow-up; lead decision D85; internal: other modules use
``app.fees.service``).

- **Templates (FR-FEE-081..083).** Per academic year: one collectable head of an allowed kind
  (``addons.yaml``), a label and the amount per instalment number. The amounts change only while
  nobody was ever assigned (the posted charges must keep matching their template).
- **Assign (FR-FEE-084..088).** A class, some sections or a picked list of students, from a start
  instalment. Each assignment posts one ``charge`` entry per instalment of the template that the
  student's plan has, from the start on, with the assignment as its source
  (``ledger_entries.addon_assignment_id``). The account then shows them as ordinary lines (the
  head's name), so the counter, receipts, the parent dues page, the dues list and late fees
  treat them like plan charges. A plan and an add-on may charge the same head: plan versions
  compare only what the plan charged (FR-FEE-093). One active add-on per head per account: a
  band change ``replace``-s the old band.
- **Unassign (FR-FEE-089, FR-FEE-090).** The assignment ends before the given instalment; the
  charges already posted from it that are still unpaid are proposed as credits (adjustments
  with reason ``addon_removal``) that another holder of ``fee.concession.approve`` decides. Money
  already paid comes back only through a refund. D85 default (PO to confirm): only a reduction
  of money owed needs maker-checker; an assignment (an increase) is a ``fee.manage`` action,
  audited.
- **Who is on it (FR-FEE-095).** ``fee.addon.read`` (a transport in-charge) lists the students of
  an add-on without amounts; ``finance.read`` or ``fee.manage`` also see the amounts.

Transport here is a fee only: routes, stops and vehicles stay out of scope (docs/18 §3).
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from typing import Any, Final

from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.authz.context import UserContext
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.ids import new_id
from app.fees import _common as c
from app.fees import addons_config, ledger
from app.fees import addons_repository as arepo
from app.fees import repository as repo
from app.fees.addon_models import FeeAddon, FeeAddonAssignment
from app.fees.addons_schemas import (
    AddonAssignIn,
    AddonAssignOut,
    AddonCreate,
    AddonOut,
    AddonPatch,
    AddonSkipped,
    AddonSkipReason,
    AddonsOut,
    AddonStudentOut,
    AddonStudentsOut,
    AddonTarget,
    AddonUnassignIn,
    AddonUnassignOut,
)
from app.fees.models import FeeHead, StudentFeeAccount
from app.fees.money import ZERO
from app.students import service as students
from app.tenancy import service as tenancy

ADDON_READ: Final = "fee.addon.read"
REMOVAL: Final = "addon_removal"

_CONFLICTS: Final[dict[str, tuple[str, str]]] = {
    "fee_addons_label_unique": (
        "addon_label_taken",
        "Another add-on of this year already has this name.",
    ),
    "fee_addon_assignments_one_per_head": (
        "addon_head_taken",
        "A student is already on another add-on of this fee head. Use a band change.",
    ),
}


def cfg() -> addons_config.AddonsConfig:
    return addons_config.load_config()


@contextmanager
def _db_errors() -> Iterator[None]:
    try:
        with c.db_errors():
            yield
    except DBAPIError as exc:
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None) or ""
        if constraint in _CONFLICTS:
            code, message = _CONFLICTS[constraint]
            raise Conflict(message, code=code) from exc
        raise


def amounts_shown(ctx: UserContext) -> bool:
    """FR-FEE-095: amounts only for ``finance.read`` or ``fee.manage`` (school-wide)."""
    return c.holds(ctx, c.READ) or c.holds(ctx, c.MANAGE)


def _amounts(row: FeeAddon) -> dict[int, Decimal]:
    return {int(k): Decimal(v) for k, v in sorted(row.amounts.items(), key=lambda kv: int(kv[0]))}


def _out(row: FeeAddon, head: FeeHead | None, assigned: int, *, show: bool) -> AddonOut:
    amounts = _amounts(row)
    return AddonOut(
        id=row.id,
        academic_year_id=row.academic_year_id,
        head_id=row.head_id,
        head_code=head.code if head else "",
        head_name=head.name if head else "",
        head_kind=head.kind if head else "",
        label=row.label,
        active=row.active,
        assigned=assigned,
        amounts=amounts if show else None,
        total=sum(amounts.values(), ZERO) if show else None,
        version=row.version,
    )


def _load(session: Session, addon_id: uuid.UUID, *, lock: bool = False) -> FeeAddon:
    row = arepo.get_addon(session, addon_id, lock=lock)
    if row is None:
        raise NotFound("Add-on fee not found")
    return row


def _year_id(session: Session, year_id: uuid.UUID | None) -> uuid.UUID | None:
    if year_id is not None:
        return year_id
    current = tenancy.get_current_academic_year(session)
    return current.id if current else None


def _check_amounts(amounts: dict[int, Decimal]) -> dict[str, str]:
    limit = c.settings().limits.max_amount
    if any(v > limit for v in amounts.values()):
        raise ValidationFailed([c.error("amounts", "too_large")])
    return {str(k): f"{v:.2f}" for k, v in sorted(amounts.items())}


# --- templates (FR-FEE-081..083) -----------------------------------------------------------------


def list_addons(
    session: Session, ctx: UserContext, *, academic_year_id: uuid.UUID | None = None
) -> AddonsOut:
    """The add-on templates of a year (current unless given), with how many students are on
    each (``fee.addon.read``, ``finance.read`` or ``fee.manage``; amounts per FR-FEE-095)."""
    c.require_on(session, ctx)
    c.require_school_wide(ctx, ADDON_READ, c.READ, c.MANAGE)
    show = amounts_shown(ctx)
    note = cfg().gst_note.text
    year_id = _year_id(session, academic_year_id)
    if year_id is None:
        return AddonsOut(items=[], amounts_shown=show, gst_note=note)
    rows = arepo.list_addons(session, year_id)
    heads = repo.heads_by_id(session, {r.head_id for r in rows})
    counts = arepo.assigned_counts(session, [r.id for r in rows])
    return AddonsOut(
        items=[_out(r, heads.get(r.head_id), counts.get(r.id, 0), show=show) for r in rows],
        amounts_shown=show,
        gst_note=note,
    )


def create_addon(session: Session, ctx: UserContext, data: AddonCreate) -> AddonOut:
    """A template (``fee.manage``). 422 ``head_id`` (``invalid``, ``head_kind_not_allowed``,
    ``head_not_collectable``), ``academic_year_id`` ``invalid``; 409 ``addon_label_taken``,
    ``too_many_addons``. Audit ``fee.addon.created``."""
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.MANAGE)
    c.refuse_aadhaar("label", data.label)
    if data.academic_year_id not in {y.id for y in tenancy.list_academic_years(session)}:
        raise ValidationFailed([c.error("academic_year_id", "invalid")])
    head = repo.get_head(session, data.head_id)
    if head is None or not head.active:
        raise ValidationFailed([c.error("head_id", "invalid")])
    if head.kind not in cfg().head_kinds:
        raise ValidationFailed([c.error("head_id", "head_kind_not_allowed")])
    if not head.gst_exempt:
        raise ValidationFailed([c.error("head_id", "head_not_collectable")])
    amounts = _check_amounts(dict(data.amounts))
    if arepo.count_addons(session, data.academic_year_id) >= cfg().limits.max_templates_per_year:
        raise Conflict("This year has the most add-on fees allowed.", code="too_many_addons")
    with _db_errors():
        row = arepo.insert_addon(
            session,
            id=new_id(),
            tenant_id=repo.current_tenant_id(session),
            academic_year_id=data.academic_year_id,
            head_id=head.id,
            label=data.label,
            amounts=amounts,
            created_by=ctx.membership_id,
        )
    c.record(
        session,
        action="fee.addon.created",
        resource_type="fee_addon",
        resource_id=row.id,
        summary={
            "head_id": head.id,
            "academic_year_id": row.academic_year_id,
            "instalments": len(amounts),
            "total": c.money_text(sum(_amounts(row).values(), ZERO)),
        },
    )
    return _out(row, head, 0, show=True)


def update_addon(
    session: Session,
    ctx: UserContext,
    addon_id: uuid.UUID,
    data: AddonPatch,
    *,
    expected_version: int,
) -> AddonOut:
    """Rename, switch off (no new assignments) or, while nobody was ever assigned, change the
    amounts (``fee.manage``, ``If-Match``). 409 ``addon_in_use``. Audit ``fee.addon.updated``."""
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.MANAGE)
    row = _load(session, addon_id, lock=True)
    c.check_version(row, expected_version)
    values: dict[str, Any] = {}
    if data.label is not None and data.label != row.label:
        c.refuse_aadhaar("label", data.label)
        values["label"] = data.label
    if data.active is not None and data.active != row.active:
        values["active"] = data.active
    if data.amounts is not None:
        amounts = _check_amounts(dict(data.amounts))
        if amounts != {k: f"{Decimal(v):.2f}" for k, v in row.amounts.items()}:
            if arepo.assigned_counts(session, [row.id], active_only=False).get(row.id, 0):
                raise Conflict(
                    "Students were put on this add-on, so its amounts stay as charged. Make a "
                    "new add-on with the new amounts and move the students to it.",
                    code="addon_in_use",
                )
            values["amounts"] = amounts
    head = repo.get_head(session, row.head_id)
    if values:
        with _db_errors():
            row = arepo.update_addon(session, row.id, values)
        c.record(
            session,
            action="fee.addon.updated",
            resource_type="fee_addon",
            resource_id=row.id,
            summary={"fields": sorted(values), "active": row.active},
        )
    count = arepo.assigned_counts(session, [row.id]).get(row.id, 0)
    return _out(row, head, count, show=True)


# --- whom ----------------------------------------------------------------------------------------


def _target_students(session: Session, addon: FeeAddon, data: AddonTarget) -> list[uuid.UUID]:
    """The students named by a class, sections of the template's year, or a picked list."""
    limits = cfg().limits
    if data.student_ids is not None:
        picked = list(dict.fromkeys(data.student_ids))
        if len(picked) > limits.max_picked_students:
            raise ValidationFailed([c.error("student_ids", "too_many")])
        return picked
    year = addon.academic_year_id
    if data.class_id is not None:
        sections = tenancy.list_sections(session, academic_year_id=year, class_id=data.class_id)
        if not sections:
            raise ValidationFailed([c.error("class_id", "invalid")])
        section_ids = {s.id for s in sections}
    else:
        wanted = set(data.section_ids or [])
        if not wanted <= {s.id for s in tenancy.list_sections(session, academic_year_id=year)}:
            raise ValidationFailed([c.error("section_ids", "invalid")])
        section_ids = wanted
    enrolments = students.enrolled_in_sections(
        session, academic_year_id=year, section_ids=section_ids
    )
    found = sorted({e.student_id for e in enrolments}, key=str)
    if len(found) > limits.max_bulk_students:
        raise Conflict(
            "Too many students at once. Choose one class or section at a time.",
            code="too_many_students",
        )
    return found


def _accounts(
    session: Session, addon: FeeAddon, student_ids: list[uuid.UUID]
) -> dict[uuid.UUID, StudentFeeAccount]:
    """The fee accounts of the template's year of these students, locked (id order, YF-05)."""
    found = repo.accounts_for_year(session, addon.academic_year_id, student_ids)
    return {a.student_id: a for a in repo.lock_accounts(session, [a.id for a in found])}


def _plan_instalments(
    session: Session, accounts: list[StudentFeeAccount]
) -> dict[uuid.UUID, set[int]]:
    rows = repo.plan_instalments(session, {a.plan_id for a in accounts})
    out: dict[uuid.UUID, set[int]] = {}
    for r in rows:
        out.setdefault(r.plan_id, set()).add(r.instalment_no)
    return out


# --- ending and credits (FR-FEE-089, FR-FEE-090) -------------------------------------------------


def _credits(
    session: Session,
    account: StudentFeeAccount,
    assignment: FeeAddonAssignment,
    from_instalment: int,
    charges: dict[int, Decimal],
) -> list[tuple[int, Decimal]]:
    """What to credit per instalment from ``from_instalment``: the add-on charge, at most what
    the line still owes (never below zero; a paid part is refunded, not credited)."""
    dues = ledger.dues_for(session, account, c.today(session))
    out: list[tuple[int, Decimal]] = []
    for no, charged in sorted(charges.items()):
        if no < from_instalment:
            continue
        found = dues.line(no, assignment.head_id)
        owed = found[0].due if found else ZERO
        credit = min(charged, max(owed, ZERO))
        if credit > 0:
            out.append((no, credit))
    return out


def _end(
    session: Session,
    ctx: UserContext,
    account: StudentFeeAccount,
    assignment: FeeAddonAssignment,
    *,
    from_instalment: int,
    reason: str,
    dry_run: bool,
) -> tuple[int, Decimal]:
    """End ``assignment`` before ``from_instalment`` and propose its credits. Returns (credits
    proposed, their total)."""
    start = max(from_instalment, assignment.start_instalment)
    charges = arepo.addon_charges(session, [assignment.id]).get(assignment.id, {})
    credits = _credits(session, account, assignment, start, charges)
    if dry_run:
        return len(credits), sum((a for _, a in credits), ZERO)
    with _db_errors():
        arepo.end_assignment(
            session, assignment.id, end_instalment=start - 1, ended_by=ctx.membership_id
        )
        proposed = arepo.insert_removals(
            session,
            [
                {
                    "id": new_id(),
                    "tenant_id": account.tenant_id,
                    "account_id": account.id,
                    "instalment_no": no,
                    "head_id": assignment.head_id,
                    "amount": -amount,
                    "reason_code": REMOVAL,
                    "reason": reason,
                    "addon_assignment_id": assignment.id,
                    "requested_by": ctx.membership_id,
                }
                for no, amount in credits
            ],
        )
    return proposed, sum((a for _, a in credits), ZERO)


# --- assign (FR-FEE-084..088) --------------------------------------------------------------------


def assign(
    session: Session, ctx: UserContext, addon_id: uuid.UUID, data: AddonAssignIn
) -> AddonAssignOut:
    """Put students on an add-on from ``start_instalment`` and post its charges in one
    transaction (``fee.manage``). Idempotent: a student already on it is skipped. 409
    ``addon_inactive``; 422 ``reason`` with ``replace``. Audit ``fee.addon.assigned`` (counts)."""
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.MANAGE)
    reason = c.clean_reason("reason", data.reason) if data.replace else None
    addon = _load(session, addon_id, lock=True)
    if not addon.active:
        raise Conflict("This add-on is switched off. Switch it on first.", code="addon_inactive")
    wanted = _target_students(session, addon, data)
    accounts = _accounts(session, addon, wanted)
    current = arepo.active_on_heads(session, [a.id for a in accounts.values()], addon.head_id)
    numbers = _plan_instalments(session, list(accounts.values()))
    amounts = _amounts(addon)
    today = c.today(session)
    skipped: list[AddonSkipped] = []
    assigned = posted = replaced = credits = 0
    entries: list[dict[str, Any]] = []
    for student_id in wanted:
        account = accounts.get(student_id)
        existing = current.get(account.id) if account else None
        due = (
            [
                n
                for n in amounts
                if n >= data.start_instalment and n in numbers.get(account.plan_id, set())
            ]
            if account
            else []
        )
        why = _skip_reason(account, due, existing, addon, replace=reason is not None)
        if why is not None or account is None:
            skipped.append(AddonSkipped(student_id=student_id, reason=why or "no_fee_account"))
            continue
        if existing is not None and reason is not None:
            proposed, _ = _end(
                session,
                ctx,
                account,
                existing,
                from_instalment=data.start_instalment,
                reason=reason,
                dry_run=data.dry_run,
            )
            replaced += 1
            credits += proposed
        assigned += 1
        if data.dry_run:
            posted += len(due)
            continue
        with _db_errors():
            row = arepo.insert_assignment(
                session,
                id=new_id(),
                tenant_id=account.tenant_id,
                addon_id=addon.id,
                account_id=account.id,
                head_id=addon.head_id,
                start_instalment=data.start_instalment,
                assigned_by=ctx.membership_id,
            )
        entries += _charge_rows(addon, row, due, today=today, by=ctx.membership_id)
    if not data.dry_run:
        with _db_errors():
            posted = repo.insert_entries(session, entries)
        c.record(
            session,
            action="fee.addon.assigned",
            resource_type="fee_addon",
            resource_id=addon.id,
            summary={
                "students": len(wanted),
                "assigned": assigned,
                "charges_posted": posted,
                "replaced": replaced,
                "credits_proposed": credits,
                "skipped": len(skipped),
                "start_instalment": data.start_instalment,
            },
        )
        if credits:
            _notify(session, addon.id)
    return AddonAssignOut(
        dry_run=data.dry_run,
        students=len(wanted),
        assigned=assigned,
        charges_posted=posted,
        replaced=replaced,
        credits_proposed=credits,
        skipped=skipped,
    )


def _skip_reason(
    account: StudentFeeAccount | None,
    due: list[int],
    existing: FeeAddonAssignment | None,
    addon: FeeAddon,
    *,
    replace: bool,
) -> AddonSkipReason | None:
    if account is None:
        return "no_fee_account"
    if account.status == "closed":
        return "account_closed"
    if not due:
        return "no_instalments_left"
    if existing is not None and existing.addon_id == addon.id:
        return "already_assigned"
    if existing is not None and not replace:
        return "head_taken"
    return None


def _charge_rows(
    addon: FeeAddon,
    assignment: FeeAddonAssignment,
    due: list[int],
    *,
    today: dt.date,
    by: uuid.UUID,
) -> list[dict[str, Any]]:
    """One add-on charge per instalment, its source the assignment (FR-FEE-087)."""
    amounts = _amounts(addon)
    return [
        {
            "id": new_id(),
            "tenant_id": assignment.tenant_id,
            "account_id": assignment.account_id,
            "entry_kind": "charge",
            "amount": amounts[n],
            "instalment_no": n,
            "head_id": addon.head_id,
            "entry_date": today,
            "addon_assignment_id": assignment.id,
            "posted_by": by,
        }
        for n in sorted(due)
    ]


def _notify(session: Session, addon_id: uuid.UUID) -> None:
    from app.fees import approvals  # noqa: PLC0415 - notification helper, no cycle at import

    approvals.notify_approvers(session, "adjustment", addon_id, addon_id)


# --- unassign (FR-FEE-089, FR-FEE-090) -----------------------------------------------------------


def unassign(
    session: Session, ctx: UserContext, addon_id: uuid.UUID, data: AddonUnassignIn
) -> AddonUnassignOut:
    """Take students off an add-on from ``from_instalment`` (``fee.manage``): the assignment
    ends and unpaid charges from that instalment are proposed as credits for approval (D85).
    Students not on it are counted, not refused. Audit ``fee.addon.unassigned`` (counts)."""
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.MANAGE)
    reason = c.clean_reason("reason", data.reason) or ""
    addon = _load(session, addon_id, lock=True)
    wanted = _target_students(session, addon, data)
    accounts = _accounts(session, addon, wanted)
    current = arepo.active_on_heads(session, [a.id for a in accounts.values()], addon.head_id)
    ended = missing = proposed = 0
    total = ZERO
    for student_id in wanted:
        account = accounts.get(student_id)
        assignment = current.get(account.id) if account else None
        if account is None or assignment is None or assignment.addon_id != addon.id:
            missing += 1
            continue
        n, amount = _end(
            session,
            ctx,
            account,
            assignment,
            from_instalment=data.from_instalment,
            reason=reason,
            dry_run=data.dry_run,
        )
        ended += 1
        proposed += n
        total += amount
    if not data.dry_run:
        c.record(
            session,
            action="fee.addon.unassigned",
            resource_type="fee_addon",
            resource_id=addon.id,
            summary={
                "students": len(wanted),
                "ended": ended,
                "credits_proposed": proposed,
                "credit_total": c.money_text(total),
                "from_instalment": data.from_instalment,
            },
        )
        if proposed:
            _notify(session, addon.id)
    return AddonUnassignOut(
        dry_run=data.dry_run,
        students=len(wanted),
        ended=ended,
        not_assigned=missing,
        credits_proposed=proposed,
        credit_total=total,
    )


# --- who is on it (FR-FEE-095) -------------------------------------------------------------------


def students_of(
    session: Session, ctx: UserContext, addon_id: uuid.UUID, *, include_ended: bool = False
) -> AddonStudentsOut:
    """The students on an add-on (``fee.addon.read``, ``finance.read`` or ``fee.manage``):
    class and section, start and end instalment; the charged amount only with
    ``finance.read`` or ``fee.manage``. Names need ``student.read_basic`` as everywhere."""
    c.require_on(session, ctx)
    c.require_school_wide(ctx, ADDON_READ, c.READ, c.MANAGE)
    show = amounts_shown(ctx)
    addon = _load(session, addon_id)
    head = repo.get_head(session, addon.head_id)
    rows = [
        r
        for r in arepo.assignments_of_addon(session, addon.id)
        if include_ended or r.status == "active"
    ]
    accounts = repo.accounts_by_id(session, {r.account_id for r in rows})
    people = students.summaries(session, ctx, {a.student_id for a in accounts.values()})
    charges = arepo.addon_charges(session, [r.id for r in rows]) if show else {}
    items: list[AddonStudentOut] = []
    for r in rows:
        account = accounts.get(r.account_id)
        if account is None:  # pragma: no cover - FK
            continue
        person = people.get(account.student_id)
        items.append(
            AddonStudentOut(
                assignment_id=r.id,
                student_id=account.student_id,
                display_name=person.display_name if person else None,
                admission_no=person.admission_no if person else None,
                class_section=person.class_section if person else None,
                start_instalment=r.start_instalment,
                end_instalment=r.end_instalment,
                status="ended" if r.status == "ended" else "active",
                charged=sum(charges.get(r.id, {}).values(), ZERO) if show else None,
            )
        )
    items.sort(key=lambda i: (i.class_section or "", i.display_name or "", str(i.student_id)))
    return AddonStudentsOut(
        addon_id=addon.id,
        label=addon.label,
        head_name=head.name if head else "",
        amounts_shown=show,
        items=items,
    )


__all__ = [
    "ADDON_READ",
    "amounts_shown",
    "assign",
    "create_addon",
    "list_addons",
    "students_of",
    "unassign",
    "update_addon",
]
