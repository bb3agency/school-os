"""Fee setup: settings, heads, plans and applying a plan to a class (US-2101; FR-FEE-002..005,
FR-FEE-023; internal: other modules use ``app.fees.service``).

A plan is edited only as a draft (DB trigger ``fee_plans_frozen``); an active plan in use is
changed by a new version whose differences per student are proposed as adjustments that need
approval (FR-FEE-004). Applying a plan creates one account per active student of the class and
one charge per instalment and head, idempotently (unique charge index, FR-FEE-005).
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections import defaultdict
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.authz.context import UserContext
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.ids import new_id
from app.fees import _common as c
from app.fees import ledger
from app.fees import repository as repo
from app.fees.models import FeeHead, FeePlan, FeePlanInstalment, FeePlanLine, FeeSettings
from app.fees.money import ZERO, total
from app.fees.schemas import (
    FeeSettingsIn,
    FeeSettingsOut,
    FeeStatusOut,
    HeadCreate,
    HeadOut,
    HeadPatch,
    InstalmentIn,
    InstalmentOut,
    LateFeeRule,
    PlanApplyIn,
    PlanApplyOut,
    PlanCreate,
    PlanDifference,
    PlanDifferencesOut,
    PlanLineIn,
    PlanLineOut,
    PlanOut,
    PlanPatch,
    ProposeAdjustmentsIn,
    ProposeAdjustmentsOut,
    SkippedStudent,
)
from app.students import service as students
from app.tenancy import service as tenancy
from app.tenancy.schemas import AcademicYearOut

# --- status and settings ------------------------------------------------------------------------


def _settings_row(session: Session) -> FeeSettings:
    row = repo.get_settings(session)
    if row is None:
        cfg = c.settings().receipt
        row = repo.insert_settings(
            session,
            {
                "paper_size": cfg.default_paper_size,
                "gst_note": cfg.default_gst_note,
                "reminder_offsets": [-3, 0, 7],
                "min_reminder_amount": ZERO,
            },
        )
    return row


def settings_row(session: Session) -> FeeSettings:
    return _settings_row(session)


def status(session: Session, ctx: UserContext) -> FeeStatusOut:
    """Whether fees are on (else 404 ``module_hidden``), today's IST date and whether it is
    closed (any fee permission)."""
    c.require_on(session, ctx)
    c.require_school_wide(ctx, *c.ALL_PERMISSIONS)
    today = c.today(session)
    row = repo.get_settings(session)
    paper = row.paper_size if row else c.settings().receipt.default_paper_size
    return FeeStatusOut(
        enabled=True,
        today=today,
        financial_year=c.fy(today),
        day_closed=repo.get_day_close(session, today) is not None,
        paper_size="A4" if paper == "A4" else "A5",
    )


def _settings_out(row: FeeSettings) -> FeeSettingsOut:
    return FeeSettingsOut(
        paper_size="A4" if row.paper_size == "A4" else "A5",
        gst_note=row.gst_note,
        reminder_offsets=list(row.reminder_offsets),
        min_reminder_amount=row.min_reminder_amount,
        version=row.version,
    )


def get_settings(session: Session, ctx: UserContext) -> FeeSettingsOut:
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.READ, c.MANAGE)
    return _settings_out(_settings_row(session))


def put_settings(
    session: Session, ctx: UserContext, data: FeeSettingsIn, *, expected_version: int | None
) -> FeeSettingsOut:
    """Receipt paper size, GST note and reminder defaults (``fee.manage``, ``If-Match``)."""
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.MANAGE)
    c.refuse_aadhaar("gst_note", data.gst_note)
    row = _settings_row(session)
    c.check_version(row, expected_version)
    with c.db_errors():
        row = repo.update_settings(
            session,
            row.id,
            {
                "paper_size": data.paper_size,
                "gst_note": data.gst_note,
                "reminder_offsets": sorted(set(data.reminder_offsets)),
                "min_reminder_amount": data.min_reminder_amount,
                "updated_by": ctx.membership_id,
            },
        )
    c.record(
        session,
        action="fee.settings.updated",
        resource_type="fee_settings",
        resource_id=row.id,
        summary={"paper_size": row.paper_size, "version": row.version},
    )
    return _settings_out(row)


# --- heads ---------------------------------------------------------------------------------------


def _head_out(row: FeeHead) -> HeadOut:
    return HeadOut.model_validate(row)


def list_heads(session: Session, ctx: UserContext) -> list[HeadOut]:
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.READ, c.COLLECT, c.MANAGE)
    return [_head_out(h) for h in repo.list_heads(session)]


def create_head(session: Session, ctx: UserContext, data: HeadCreate) -> HeadOut:
    """A fee head (FR-FEE-002; ``fee.manage``). Audit ``fee.head.created``."""
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.MANAGE)
    c.refuse_aadhaar("name", data.name)
    if repo.count_heads(session) >= c.settings().limits.max_heads:
        raise Conflict("This school has the most fee heads allowed.", code="too_many_heads")
    with c.db_errors():
        row = repo.insert_head(
            session,
            id=new_id(),
            tenant_id=repo.current_tenant_id(session),
            code=data.code,
            name=data.name,
            kind=data.kind,
            gst_exempt=data.gst_exempt,
            sort_order=data.sort_order,
            created_by=ctx.membership_id,
        )
    c.record(
        session,
        action="fee.head.created",
        resource_type="fee_head",
        resource_id=row.id,
        summary={"code": row.code, "kind": row.kind, "gst_exempt": row.gst_exempt},
    )
    return _head_out(row)


def update_head(
    session: Session,
    ctx: UserContext,
    head_id: uuid.UUID,
    data: HeadPatch,
    *,
    expected_version: int,
) -> HeadOut:
    """Rename, re-order, deactivate or change the tax flag of a head (``fee.manage``,
    ``If-Match``). Posted charges keep their head. Audit ``fee.head.updated``."""
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.MANAGE)
    row = repo.get_head(session, head_id, lock=True)
    if row is None:
        raise NotFound("Fee head not found")
    c.check_version(row, expected_version)
    values = data.model_dump(exclude_none=True)
    if "name" in values:
        c.refuse_aadhaar("name", values["name"])
    if not values:
        return _head_out(row)
    with c.db_errors():
        row = repo.update_head(session, row.id, values)
    c.record(
        session,
        action="fee.head.updated",
        resource_type="fee_head",
        resource_id=row.id,
        summary={"fields": sorted(values), "active": row.active, "gst_exempt": row.gst_exempt},
    )
    return _head_out(row)


# --- plans ---------------------------------------------------------------------------------------


def _plan_out(
    plan: FeePlan,
    lines: Sequence[FeePlanLine],
    instalments: Sequence[FeePlanInstalment],
    heads: dict[uuid.UUID, FeeHead],
    accounts: int,
) -> PlanOut:
    out_lines = []
    for line in lines:
        head = heads.get(line.head_id)
        out_lines.append(
            PlanLineOut(
                head_id=line.head_id,
                head_code=head.code if head else "",
                head_name=head.name if head else "",
                annual_amount=line.annual_amount,
                gst_exempt=head.gst_exempt if head else True,
            )
        )
    out_inst = [
        InstalmentOut(
            instalment_no=i.instalment_no,
            label=i.label,
            due_on=i.due_on,
            amounts={k: Decimal(v) for k, v in i.amounts.items()},
            total=total(Decimal(v) for v in i.amounts.values()),
        )
        for i in instalments
    ]
    return PlanOut(
        id=plan.id,
        academic_year_id=plan.academic_year_id,
        class_id=plan.class_id,
        category_code=plan.category_code,
        plan_version=plan.plan_version,
        status=plan.status,
        name=plan.name,
        late_fee_rule=dict(plan.late_fee_rule),
        previous_plan_id=plan.previous_plan_id,
        lines=out_lines,
        instalments=out_inst,
        annual_total=total(line.annual_amount for line in lines),
        accounts=accounts,
        version=plan.version,
    )


def _plans_out(session: Session, plans: Sequence[FeePlan]) -> list[PlanOut]:
    ids = [p.id for p in plans]
    lines: dict[uuid.UUID, list[FeePlanLine]] = defaultdict(list)
    for line in repo.plan_lines(session, ids):
        lines[line.plan_id].append(line)
    inst: dict[uuid.UUID, list[FeePlanInstalment]] = defaultdict(list)
    for row in repo.plan_instalments(session, ids):
        inst[row.plan_id].append(row)
    heads = repo.heads_by_id(session)
    counts = repo.account_counts(session, ids)
    return [_plan_out(p, lines[p.id], inst[p.id], heads, counts.get(p.id, 0)) for p in plans]


def _load_plan(session: Session, plan_id: uuid.UUID, *, lock: bool = False) -> FeePlan:
    plan = repo.get_plan(session, plan_id, lock=lock)
    if plan is None:
        raise NotFound("Fee plan not found")
    return plan


def list_plans(
    session: Session,
    ctx: UserContext,
    *,
    academic_year_id: uuid.UUID | None,
    class_id: uuid.UUID | None,
) -> list[PlanOut]:
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.READ, c.MANAGE)
    return _plans_out(
        session, repo.list_plans(session, academic_year_id=academic_year_id, class_id=class_id)
    )


def get_plan(session: Session, ctx: UserContext, plan_id: uuid.UUID) -> PlanOut:
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.READ, c.MANAGE)
    return _plans_out(session, [_load_plan(session, plan_id)])[0]


def _year(session: Session, year_id: uuid.UUID) -> AcademicYearOut:
    try:
        return tenancy.get_academic_year(session, year_id)
    except NotFound as exc:
        raise ValidationFailed([c.error("academic_year_id", "invalid")]) from exc


def _check_rule(rule: LateFeeRule) -> dict[str, Any]:
    if rule.kind == "none":
        if rule.amount is not None or rule.cap is not None:
            raise ValidationFailed([c.error("late_fee_rule", "invalid")])
        return {"kind": "none", "grace_days": rule.grace_days}
    if rule.amount is None:
        raise ValidationFailed([c.error("late_fee_rule.amount", "missing")])
    if rule.kind == "flat_per_instalment":
        if rule.cap is not None:
            raise ValidationFailed([c.error("late_fee_rule.cap", "invalid")])
        return {"kind": rule.kind, "amount": f"{rule.amount:.2f}", "grace_days": rule.grace_days}
    if rule.cap is None:
        raise ValidationFailed([c.error("late_fee_rule.cap", "missing")])
    if rule.cap < rule.amount:
        raise ValidationFailed([c.error("late_fee_rule.cap", "too_small")])
    return {
        "kind": rule.kind,
        "amount": f"{rule.amount:.2f}",
        "grace_days": rule.grace_days,
        "cap": f"{rule.cap:.2f}",
    }


def _check_lines(
    session: Session, lines: Sequence[PlanLineIn], *, new_heads_must_be_active: bool
) -> set[uuid.UUID]:
    limits = c.settings().limits
    if len(lines) > limits.max_heads_per_plan:
        raise ValidationFailed([c.error("lines", "too_many")])
    heads = repo.heads_by_id(session, [line.head_id for line in lines])
    seen: set[uuid.UUID] = set()
    for i, line in enumerate(lines):
        head = heads.get(line.head_id)
        if head is None or (new_heads_must_be_active and not head.active):
            raise ValidationFailed([c.error(f"lines.{i}.head_id", "invalid")])
        if line.head_id in seen:
            raise ValidationFailed([c.error(f"lines.{i}.head_id", "duplicate")])
        seen.add(line.head_id)
        if line.annual_amount > limits.max_amount:
            raise ValidationFailed([c.error(f"lines.{i}.annual_amount", "too_large")])
    return seen


def _check_schedule(
    session: Session,
    year: AcademicYearOut,
    lines: Sequence[PlanLineIn],
    instalments: Sequence[InstalmentIn],
    *,
    new_heads_must_be_active: bool,
) -> None:
    """FR-FEE-003: known heads once each, instalment numbers once each, due dates inside the
    year, and per head the instalments add up to the plan line (422
    ``instalments_do_not_add_up``)."""
    seen = _check_lines(session, lines, new_heads_must_be_active=new_heads_must_be_active)
    numbers: set[int] = set()
    per_head: dict[uuid.UUID, Decimal] = defaultdict(lambda: ZERO)
    for i, inst in enumerate(instalments):
        if inst.instalment_no in numbers:
            raise ValidationFailed([c.error(f"instalments.{i}.instalment_no", "duplicate")])
        numbers.add(inst.instalment_no)
        if not year.starts_on <= inst.due_on <= year.ends_on:
            raise ValidationFailed([c.error(f"instalments.{i}.due_on", "due_outside_year")])
        c.refuse_aadhaar(f"instalments.{i}.label", inst.label)
        for head_id, value in inst.amounts.items():
            if head_id not in seen:
                raise ValidationFailed([c.error(f"instalments.{i}.amounts", "unknown_head")])
            if value < 0:
                raise ValidationFailed([c.error(f"instalments.{i}.amounts", "negative")])
            per_head[head_id] += value
        if total(inst.amounts.values()) <= 0:
            raise ValidationFailed([c.error(f"instalments.{i}.amounts", "empty_instalment")])
    for line in lines:
        if per_head[line.head_id] != line.annual_amount:
            raise ValidationFailed(
                [c.error("instalments", "instalments_do_not_add_up")],
                detail="For every head, the instalment amounts must add up to the annual amount.",
            )


def _line_rows(
    tenant_id: uuid.UUID, plan_id: uuid.UUID, lines: Sequence[PlanLineIn]
) -> list[dict[str, Any]]:
    return [
        {
            "id": new_id(),
            "tenant_id": tenant_id,
            "plan_id": plan_id,
            "head_id": line.head_id,
            "annual_amount": line.annual_amount,
            "sort_order": i,
        }
        for i, line in enumerate(lines)
    ]


def _instalment_rows(
    tenant_id: uuid.UUID, plan_id: uuid.UUID, instalments: Sequence[InstalmentIn]
) -> list[dict[str, Any]]:
    return [
        {
            "id": new_id(),
            "tenant_id": tenant_id,
            "plan_id": plan_id,
            "instalment_no": inst.instalment_no,
            "label": inst.label,
            "due_on": inst.due_on,
            "amounts": {str(k): f"{v:.2f}" for k, v in inst.amounts.items() if v != 0},
        }
        for inst in sorted(instalments, key=lambda x: x.instalment_no)
    ]


def create_plan(session: Session, ctx: UserContext, data: PlanCreate) -> PlanOut:
    """A draft plan for (year, class, category) (FR-FEE-003; ``fee.manage``). 409
    ``plan_exists`` when one exists already (make a new version of it instead)."""
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.MANAGE)
    c.refuse_aadhaar("name", data.name)
    year = _year(session, data.academic_year_id)
    try:
        tenancy.get_class(session, data.class_id)
    except NotFound as exc:
        raise ValidationFailed([c.error("class_id", "invalid")]) from exc
    _check_schedule(session, year, data.lines, data.instalments, new_heads_must_be_active=True)
    rule = _check_rule(data.late_fee_rule)
    if repo.latest_plan_version(session, year.id, data.class_id, data.category_code):
        raise Conflict(
            "A plan for this class, year and category already exists. Open it, or make a new "
            "version of it.",
            code="plan_exists",
        )
    tenant_id = repo.current_tenant_id(session)
    with c.db_errors():
        plan = repo.insert_plan(
            session,
            id=new_id(),
            tenant_id=tenant_id,
            academic_year_id=year.id,
            class_id=data.class_id,
            category_code=data.category_code,
            plan_version=1,
            name=data.name,
            late_fee_rule=rule,
            created_by=ctx.membership_id,
        )
        repo.replace_lines(session, plan.id, _line_rows(tenant_id, plan.id, data.lines))
        repo.replace_instalments(
            session, plan.id, _instalment_rows(tenant_id, plan.id, data.instalments)
        )
    c.record(
        session,
        action="fee.plan.created",
        resource_type="fee_plan",
        resource_id=plan.id,
        summary={
            "academic_year_id": year.id,
            "class_id": data.class_id,
            "category_code": data.category_code,
            "heads": len(data.lines),
            "instalments": len(data.instalments),
        },
    )
    return _plans_out(session, [plan])[0]


def update_plan(
    session: Session,
    ctx: UserContext,
    plan_id: uuid.UUID,
    data: PlanPatch,
    *,
    expected_version: int,
) -> PlanOut:
    """Change a draft plan (``fee.manage``, ``If-Match``); an active plan answers 409
    ``plan_in_use`` (make a new version)."""
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.MANAGE)
    plan = _load_plan(session, plan_id, lock=True)
    c.check_version(plan, expected_version)
    if plan.status != "draft":
        raise Conflict(
            "This plan is active or superseded, so it cannot be changed. Make a new version.",
            code="plan_in_use",
        )
    year = _year(session, plan.academic_year_id)
    lines = data.lines
    if lines is None:
        lines = [
            PlanLineIn(head_id=line.head_id, annual_amount=line.annual_amount)
            for line in repo.plan_lines(session, [plan.id])
        ]
    instalments = data.instalments
    if instalments is None:
        instalments = [
            InstalmentIn(
                instalment_no=i.instalment_no,
                label=i.label,
                due_on=i.due_on,
                amounts={uuid.UUID(k): Decimal(v) for k, v in i.amounts.items()},
            )
            for i in repo.plan_instalments(session, [plan.id])
        ]
    _check_schedule(session, year, lines, instalments, new_heads_must_be_active=False)
    values: dict[str, Any] = {}
    if data.name is not None:
        c.refuse_aadhaar("name", data.name)
        values["name"] = data.name
    if data.late_fee_rule is not None:
        values["late_fee_rule"] = _check_rule(data.late_fee_rule)
    tenant_id = plan.tenant_id
    with c.db_errors():
        if data.lines is not None:
            repo.replace_lines(session, plan.id, _line_rows(tenant_id, plan.id, lines))
        if data.instalments is not None:
            repo.replace_instalments(
                session, plan.id, _instalment_rows(tenant_id, plan.id, instalments)
            )
        plan = repo.update_plan(session, plan.id, values)
    c.record(
        session,
        action="fee.plan.updated",
        resource_type="fee_plan",
        resource_id=plan.id,
        summary={
            "fields": sorted(
                {*values, *(k for k in ("lines", "instalments") if getattr(data, k) is not None)}
            )
        },
    )
    return _plans_out(session, [plan])[0]


def activate_plan(
    session: Session, ctx: UserContext, plan_id: uuid.UUID, *, expected_version: int
) -> PlanOut:
    """Make a draft the active plan (``fee.manage``, ``If-Match``). A new version supersedes
    the plan it was made from; its differences are then proposed with
    ``POST /fees/plans/{id}/adjustments`` (FR-FEE-004)."""
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.MANAGE)
    plan = _load_plan(session, plan_id, lock=True)
    c.check_version(plan, expected_version)
    if plan.status != "draft":
        raise Conflict("Only a draft plan can be activated.", code="plan_not_draft")
    current = repo.active_plan(
        session, plan.academic_year_id, plan.class_id, plan.category_code, lock=True
    )
    superseded: uuid.UUID | None = None
    with c.db_errors():
        if current is not None:
            if current.id != plan.previous_plan_id:
                raise Conflict(
                    "Another plan is already active for this class, year and category.",
                    code="plan_already_active",
                )
            repo.update_plan(session, current.id, {"status": "superseded"})
            superseded = current.id
        plan = repo.update_plan(
            session,
            plan.id,
            {"status": "active", "activated_by": ctx.membership_id, "activated_at": c.now(session)},
        )
    c.record(
        session,
        action="fee.plan.activated",
        resource_type="fee_plan",
        resource_id=plan.id,
        summary={"plan_version": plan.plan_version, "superseded_plan_id": superseded},
    )
    return _plans_out(session, [plan])[0]


def new_version(session: Session, ctx: UserContext, plan_id: uuid.UUID) -> PlanOut:
    """A draft copy of the active plan with the next version number (``fee.manage``)."""
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.MANAGE)
    source = _load_plan(session, plan_id, lock=True)
    if source.status != "active":
        raise Conflict("Make a new version of the active plan.", code="plan_not_active")
    latest = repo.latest_plan_version(
        session, source.academic_year_id, source.class_id, source.category_code
    )
    if latest != source.plan_version:
        raise Conflict("A newer draft of this plan already exists. Open it.", code="plan_exists")
    tenant_id = source.tenant_id
    with c.db_errors():
        plan = repo.insert_plan(
            session,
            id=new_id(),
            tenant_id=tenant_id,
            academic_year_id=source.academic_year_id,
            class_id=source.class_id,
            category_code=source.category_code,
            plan_version=source.plan_version + 1,
            name=source.name,
            late_fee_rule=source.late_fee_rule,
            previous_plan_id=source.id,
            created_by=ctx.membership_id,
        )
        repo.replace_lines(
            session,
            plan.id,
            [
                {
                    "id": new_id(),
                    "tenant_id": tenant_id,
                    "plan_id": plan.id,
                    "head_id": line.head_id,
                    "annual_amount": line.annual_amount,
                    "sort_order": line.sort_order,
                }
                for line in repo.plan_lines(session, [source.id])
            ],
        )
        repo.replace_instalments(
            session,
            plan.id,
            [
                {
                    "id": new_id(),
                    "tenant_id": tenant_id,
                    "plan_id": plan.id,
                    "instalment_no": i.instalment_no,
                    "label": i.label,
                    "due_on": i.due_on,
                    "amounts": dict(i.amounts),
                }
                for i in repo.plan_instalments(session, [source.id])
            ],
        )
    c.record(
        session,
        action="fee.plan.version_created",
        resource_type="fee_plan",
        resource_id=plan.id,
        summary={"previous_plan_id": source.id, "plan_version": plan.plan_version},
    )
    return _plans_out(session, [plan])[0]


# --- applying a plan (FR-FEE-005) ---------------------------------------------------------------


def _class_enrolments(
    session: Session, plan: FeePlan, data: PlanApplyIn
) -> dict[uuid.UUID, students.ActiveEnrolment]:
    sections = tenancy.list_sections(
        session, academic_year_id=plan.academic_year_id, class_id=plan.class_id
    )
    section_ids = {s.id for s in sections}
    if data.section_ids is not None:
        wanted = set(data.section_ids)
        if not wanted <= section_ids:
            raise ValidationFailed([c.error("section_ids", "invalid")])
        section_ids = wanted
    enrolments = students.enrolled_in_sections(
        session, academic_year_id=plan.academic_year_id, section_ids=section_ids
    )
    by_student = {e.student_id: e for e in enrolments}
    if len(by_student) > c.settings().limits.max_plan_students:
        raise Conflict(
            "This class is too large to apply at once. Apply it per section.",
            code="too_many_students",
        )
    return by_student


def _charge_rows(
    plan: FeePlan,
    *,
    account_id: uuid.UUID,
    due: Sequence[FeePlanInstalment],
    have: set[tuple[int, uuid.UUID | None]],
    today: dt.date,
    posted_by: uuid.UUID,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for inst in due:
        for head_key, value in inst.amounts.items():
            head_id = uuid.UUID(head_key)
            if (inst.instalment_no, head_id) in have or Decimal(value) <= 0:
                continue
            rows.append(
                {
                    "id": new_id(),
                    "tenant_id": plan.tenant_id,
                    "account_id": account_id,
                    "entry_kind": "charge",
                    "amount": Decimal(value),
                    "instalment_no": inst.instalment_no,
                    "head_id": head_id,
                    "entry_date": today,
                    "posted_by": posted_by,
                }
            )
    return rows


def apply_plan(
    session: Session, ctx: UserContext, plan_id: uuid.UUID, data: PlanApplyIn
) -> PlanApplyOut:
    """One account per active student of the class (or the given sections) of the plan's year
    and one charge per instalment and head, in one transaction (``fee.manage``). Idempotent:
    accounts and charges that exist are left alone. A student admitted after an instalment's due
    date gets only later instalments unless ``include_past_instalments``. Audit
    ``fee.plan.applied`` (counts)."""
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.MANAGE)
    plan = _load_plan(session, plan_id, lock=True)
    if plan.status != "active":
        raise Conflict("Activate the plan before applying it.", code="plan_not_active")
    by_student = _class_enrolments(session, plan, data)
    instalments = repo.plan_instalments(session, [plan.id])
    existing = {
        a.student_id: a
        for a in repo.accounts_for_year(session, plan.academic_year_id, list(by_student))
    }
    chain = _plan_chain(session, plan)
    today = c.today(session)
    tenant_id = plan.tenant_id
    skipped: list[SkippedStudent] = []
    created = present = 0
    entries: list[dict[str, Any]] = []
    charged_lines = {
        aid: {(b.instalment_no, b.head_id) for b in buckets if b.charged}
        for aid, buckets in ledger.buckets_by_account(
            session, [a.id for a in existing.values()]
        ).items()
    }
    for student_id in sorted(by_student, key=str):
        enrolment = by_student[student_id]
        due = [
            i
            for i in instalments
            if data.include_past_instalments
            or enrolment.started_on is None
            or i.due_on >= enrolment.started_on
        ]
        account = existing.get(student_id)
        if account is not None and account.plan_id not in chain:
            skipped.append(SkippedStudent(student_id=student_id, reason="account_on_other_plan"))
            continue
        if not due:
            skipped.append(SkippedStudent(student_id=student_id, reason="no_instalments_left"))
            continue
        if account is None:
            created += 1
            account_id = new_id()
            if not data.dry_run:
                with c.db_errors():
                    repo.insert_account(
                        session,
                        id=account_id,
                        tenant_id=tenant_id,
                        student_id=student_id,
                        academic_year_id=plan.academic_year_id,
                        plan_id=plan.id,
                        category_code=plan.category_code,
                        opened_by=ctx.membership_id,
                    )
            have: set[tuple[int, uuid.UUID | None]] = set()
        else:
            present += 1
            account_id = account.id
            have = charged_lines.get(account.id, set())
        entries += _charge_rows(
            plan,
            account_id=account_id,
            due=due,
            have=have,
            today=today,
            posted_by=ctx.membership_id,
        )
    posted = len(entries)
    if not data.dry_run:
        with c.db_errors():
            posted = repo.insert_entries(session, entries)
        c.record(
            session,
            action="fee.plan.applied",
            resource_type="fee_plan",
            resource_id=plan.id,
            summary={
                "students": len(by_student),
                "accounts_created": created,
                "already_present": present,
                "charges_posted": posted,
                "skipped": len(skipped),
                "include_past_instalments": data.include_past_instalments,
            },
        )
    return PlanApplyOut(
        dry_run=data.dry_run,
        students=len(by_student),
        accounts_created=created,
        already_present=present,
        charges_posted=posted,
        skipped=skipped,
    )


def _plan_chain(session: Session, plan: FeePlan) -> set[uuid.UUID]:
    """This plan and every earlier version of it."""
    chain = {plan.id}
    current = plan
    while current.previous_plan_id is not None and current.previous_plan_id not in chain:
        chain.add(current.previous_plan_id)
        previous = repo.get_plan(session, current.previous_plan_id)
        if previous is None:
            break
        current = previous
    return chain


# --- new versions: differences as adjustments (FR-FEE-004) --------------------------------------


def _differences(session: Session, plan: FeePlan) -> list[PlanDifference]:
    if plan.previous_plan_id is None or plan.status != "active":
        return []
    chain = _plan_chain(session, plan) - {plan.id}
    accounts = [a for pid in sorted(chain, key=str) for a in repo.accounts_on_plan(session, pid)]
    if not accounts:
        return []
    planned: dict[tuple[int, uuid.UUID], Decimal] = {}
    for inst in repo.plan_instalments(session, [plan.id]):
        for head_key, value in inst.amounts.items():
            planned[(inst.instalment_no, uuid.UUID(head_key))] = Decimal(value)
    buckets = ledger.buckets_by_account(session, [a.id for a in accounts])
    out: list[PlanDifference] = []
    for account in accounts:
        per = {(b.instalment_no, b.head_id): b for b in buckets.get(account.id, [])}
        # Only instalments the student was charged for (a late admission keeps its start).
        charged_instalments = {k[0] for k, b in per.items() if b.charged}
        keys = {k for k in planned if k[0] in charged_instalments}
        keys |= {(k[0], k[1]) for k, b in per.items() if b.charged and k[1] is not None}
        for inst_no, head_id in sorted(keys, key=lambda k: (k[0], str(k[1]))):
            bucket = per.get((inst_no, head_id))
            charged = bucket.charged if bucket else ZERO
            want = planned.get((inst_no, head_id), ZERO)
            if want != charged:
                out.append(
                    PlanDifference(
                        account_id=account.id,
                        student_id=account.student_id,
                        instalment_no=inst_no,
                        head_id=head_id,
                        charged=charged,
                        planned=want,
                        difference=want - charged,
                    )
                )
    return out


def plan_differences(session: Session, ctx: UserContext, plan_id: uuid.UUID) -> PlanDifferencesOut:
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.READ, c.MANAGE)
    plan = _load_plan(session, plan_id)
    return PlanDifferencesOut(
        plan_id=plan.id,
        previous_plan_id=plan.previous_plan_id,
        items=_differences(session, plan),
    )


def propose_adjustments(
    session: Session, ctx: UserContext, plan_id: uuid.UUID, data: ProposeAdjustmentsIn
) -> ProposeAdjustmentsOut:
    """Turn the differences of a new version into adjustment requests (reason ``plan_change``)
    that a holder of ``fee.concession.approve`` decides, and move the accounts to the new
    version (``fee.manage``). Posted charges are never rewritten (FR-FEE-004)."""
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.MANAGE)
    reason = c.clean_reason("reason", data.reason)
    plan = _load_plan(session, plan_id, lock=True)
    if plan.status != "active" or plan.previous_plan_id is None:
        raise Conflict(
            "Only an active new version has differences to propose.", code="plan_not_active"
        )
    items = _differences(session, plan)
    tenant_id = plan.tenant_id
    rows = [
        {
            "id": new_id(),
            "tenant_id": tenant_id,
            "account_id": d.account_id,
            "instalment_no": d.instalment_no,
            "head_id": d.head_id,
            "amount": d.difference,
            "reason_code": "plan_change",
            "reason": reason,
            "source_plan_id": plan.id,
            "requested_by": ctx.membership_id,
        }
        for d in items
    ]
    with c.db_errors():
        proposed = repo.insert_adjustments_skip_existing(session, rows)
        chain = _plan_chain(session, plan) - {plan.id}
        moved = 0
        for pid in chain:
            for account in repo.accounts_on_plan(session, pid):
                repo.update_account(session, account.id, {"plan_id": plan.id})
                moved += 1
    c.record(
        session,
        action="fee.adjustment.proposed",
        resource_type="fee_plan",
        resource_id=plan.id,
        summary={"proposed": proposed, "accounts_moved": moved},
    )
    if proposed:
        from app.fees import approvals  # noqa: PLC0415 - notification helper, no cycle at import

        approvals.notify_approvers(session, "adjustment", plan.id, plan.id)
    return ProposeAdjustmentsOut(proposed=proposed, already_proposed=len(rows) - proposed)


def as_date(value: dt.date | str) -> dt.date:
    return value if isinstance(value, dt.date) else dt.date.fromisoformat(value)
