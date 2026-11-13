"""Apply the demo scenario to a seeded demo school (FA-12; FR-DEMO-005..012). NEVER real data.

Called by :mod:`app.devtools.seed_demo` after :func:`app.devtools.seeder.seed_tenant` made the
school, its staff, students, documents and Class X fees. Every write goes through the owning
module's public functions in a ``tenant_session`` as a synthetic member holding the permission
(or, for the weekly brief, as the system job does), so RLS, validation, maker-checker and audit
apply as in production. The scenario data is :mod:`app.devtools.demo`.

Steps, in order (each idempotent: it looks for what it would create and adds only what is
missing; the summary counts only what this run created):

1. **Earlier-year dues** (accountant, office admin; FM-01): a 2025-26 Class IX plan; some of
   today's Class X students get a 2025-26 Class IX enrolment, the plan is applied to them and the
   enrolment is closed as ``completed`` on 30 April 2026, so their 2025-26 accounts stay open
   with dues (FR-FEE-031).
2. **Add-on fees** (accountant; FM-02): transport bands 1 and 2 on one head and an optional
   activity, assigned to picked Class X students from instalment 1.
3. **Compliance register** (principal; FC-01): valid, expiring, expired and no-expiry
   certificates, the principal as renewal owner.
4. **School calendar** (principal; FT-06): the weekly pattern and a few entries for 2026-27.
5. **Data-quality run** (office admin): every rule over the whole school, so the planted
   mismatches become findings and the readiness pages have work (only when there is none yet).
6. **UDISE+ follow-ups** (office admin; FO-01): slips given to parents, and correction requests
   sent long enough ago to be overdue.
7. **CBSE disclosure draft** (principal; FC-02): fictional typed values; nothing published.
8. **Weekly brief** (system job; FA-05): last week's brief, stored once.

Year-end results (FT-08) are not seeded: the generator has no exams or marks yet (follow-up).
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections import Counter
from collections.abc import Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.academics import service as academics
from app.academics.schemas import CalendarEntryIn, CalendarPatternIn
from app.brief import service as brief
from app.compliance import service as compliance
from app.compliance.schemas import CertificateCreate
from app.core.db import tenant_session
from app.devtools import demo, seeder
from app.devtools.finance_seeder import Actor
from app.devtools.plan import TenantPlan
from app.disclosure import service as disclosure
from app.disclosure.schemas import DisclosureDraftIn
from app.dq import followups
from app.dq import service as dq
from app.dq.followup_schemas import FollowupIn
from app.dq.schemas import FindingFilters
from app.fees import service as fees
from app.fees.addons_schemas import AddonAssignIn, AddonCreate
from app.fees.schemas import (
    AccountSearch,
    FeePlanCreate,
    HeadCreate,
    InstalmentIn,
    PlanApplyIn,
    PlanLineIn,
)
from app.students import service as students
from app.students.schemas import EnrollmentEnd, EnrollmentIn
from app.tenancy import service as tenancy


class DemoSeedError(RuntimeError):
    """The demo scenario could not be applied (message never contains personal data)."""


@dataclass(frozen=True)
class DemoActors:
    principal: Actor
    office_admin: Actor
    accountant: Actor


def _session(tenant_id: uuid.UUID, actor: Actor) -> AbstractContextManager[Session]:
    return tenant_session(tenant_id, actor.user_id)


def actors_for(plan: TenantPlan) -> DemoActors:
    try:
        return DemoActors(
            principal=seeder.actor(plan, "principal"),
            office_admin=seeder.actor(plan, "office_admin"),
            accountant=seeder.actor(plan, "accountant"),
        )
    except seeder.SeedError as exc:
        raise DemoSeedError(str(exc)) from exc


def _class_id(session: Session, code: str) -> uuid.UUID:
    found = next((c.id for c in tenancy.list_classes(session) if c.code == code), None)
    if found is None:
        raise DemoSeedError(f"class {code} is missing")
    return found


def _class_x_students(tenant_id: uuid.UUID, actor: Actor) -> list[uuid.UUID]:
    """Student ids of the current-year Class X fee accounts, by admission number."""
    with _session(tenant_id, actor) as session:
        class_id = _class_id(session, "X")
        found = fees.search_accounts(session, actor.ctx, AccountSearch(class_id=class_id))
    found.sort(key=lambda a: (a.admission_no or "", str(a.account_id)))
    return [a.student_id for a in found]


def _ensure_head(
    session: Session, actor: Actor, created: Counter[str], *, code: str, name: str, kind: str
) -> uuid.UUID:
    heads = {h.code: h.id for h in fees.list_heads(session, actor.ctx)}
    if code in heads:
        return heads[code]
    created["fee_heads"] += 1
    return fees.create_head(
        session, actor.ctx, HeadCreate.model_validate({"code": code, "name": name, "kind": kind})
    ).id


# --- 1. earlier-year dues ------------------------------------------------------------------------


def _previous_year(session: Session, plan: TenantPlan) -> uuid.UUID:
    label = next(y.label for y in plan.years if not y.is_current)
    found = next((y.id for y in tenancy.list_academic_years(session) if y.label == label), None)
    if found is None:
        raise DemoSeedError(f"academic year {label} is missing")
    return found


def _ensure_arrears(
    plan: TenantPlan, actors: DemoActors, class_x: Sequence[uuid.UUID], created: Counter[str]
) -> None:
    tenant_id, acc, office = plan.tenant_id, actors.accountant, actors.office_admin
    targets = list(class_x[demo.ARREARS_ACCOUNTS])
    if not targets:
        return
    with _session(tenant_id, acc) as session:
        year_id = _previous_year(session, plan)
        class_id = _class_id(session, demo.ARREARS_CLASS)
        head_id = _ensure_head(
            session, acc, created, code=demo.ARREARS_HEAD, name="Tuition fee", kind="tuition"
        )
        plans = fees.list_plans(session, acc.ctx, academic_year_id=year_id, class_id=class_id)
        old = next((p for p in plans if p.name == demo.ARREARS_PLAN_NAME), None)
        if old is None:
            half = demo.ARREARS_ANNUAL / 2
            old = fees.create_plan(
                session,
                acc.ctx,
                FeePlanCreate(
                    academic_year_id=year_id,
                    class_id=class_id,
                    name=demo.ARREARS_PLAN_NAME,
                    lines=[PlanLineIn(head_id=head_id, annual_amount=demo.ARREARS_ANNUAL)],
                    instalments=[
                        InstalmentIn(
                            instalment_no=no, label=label, due_on=due_on, amounts={head_id: half}
                        )
                        for no, label, due_on in demo.ARREARS_INSTALMENTS
                    ],
                ),
            )
            created["fee_plans"] += 1
        if old.status == "draft":
            old = fees.activate_plan(session, acc.ctx, old.id, expected_version=old.version)
        section = next(
            (
                s
                for s in tenancy.list_sections(session, academic_year_id=year_id)
                if s.class_id == class_id and s.name == demo.ARREARS_SECTION
            ),
            None,
        )
        if section is None:
            raise DemoSeedError("the 2025-26 Class IX section is missing")
    # The office admin places the students in last year's class (a past enrolment) ...
    with _session(tenant_id, office) as session:
        for student_id in targets:
            past = [
                e
                for e in students.list_enrollments(session, office.ctx, student_id)
                if e.academic_year_id == year_id
            ]
            if not past:
                students.enrol(
                    session,
                    office.ctx,
                    student_id,
                    EnrollmentIn(section_id=section.id, started_on=demo.ARREARS_STARTED_ON),
                )
                created["past_enrolments"] += 1
    # ... the accountant charges last year's fees to them ...
    with _session(tenant_id, acc) as session:
        applied = fees.apply_plan(
            session,
            acc.ctx,
            old.id,
            PlanApplyIn(section_ids=[section.id], include_past_instalments=True),
        )
        created["fee_accounts"] += applied.accounts_created
        created["fee_charges"] += applied.charges_posted
    # ... and the year is closed for them (the accounts stay open: earlier-year dues).
    with _session(tenant_id, office) as session:
        for student_id in targets:
            for e in students.list_enrollments(session, office.ctx, student_id):
                if e.academic_year_id == year_id and e.status == "active":
                    students.end_enrollment(
                        session,
                        office.ctx,
                        student_id,
                        e.id,
                        EnrollmentEnd(status="completed", ended_on=demo.ARREARS_ENDED_ON),
                        expected_version=e.version,
                    )


# --- 2. add-on fees ------------------------------------------------------------------------------


def _ensure_addons(
    plan: TenantPlan, actor: Actor, class_x: Sequence[uuid.UUID], created: Counter[str]
) -> None:
    with _session(plan.tenant_id, actor) as session:
        year = tenancy.get_current_academic_year(session)
        if year is None:
            raise DemoSeedError("the school has no current academic year")
        existing = {a.label: a for a in fees.list_addons(session, actor.ctx).items}
        for spec in demo.ADDONS:
            head_id = _ensure_head(
                session,
                actor,
                created,
                code=spec.head_code,
                name=spec.head_name,
                kind=spec.head_kind,
            )
            addon = existing.get(spec.label)
            if addon is None:
                addon = fees.create_addon(
                    session,
                    actor.ctx,
                    AddonCreate(
                        academic_year_id=year.id,
                        head_id=head_id,
                        label=spec.label,
                        amounts=dict.fromkeys(demo.ADDON_INSTALMENTS, spec.per_instalment),
                    ),
                )
                existing[spec.label] = addon
                created["fee_addons"] += 1
            picked = list(class_x[spec.accounts])
            if addon.assigned or not picked:
                continue
            out = fees.assign_addon(
                session, actor.ctx, addon.id, AddonAssignIn(student_ids=picked, start_instalment=1)
            )
            created["fee_addon_assignments"] += out.assigned


# --- 3. compliance register ----------------------------------------------------------------------


def _ensure_certificates(plan: TenantPlan, actor: Actor, created: Counter[str]) -> None:
    with _session(plan.tenant_id, actor) as session:
        today = compliance.today(session)
        held = {r.kind for r in compliance.register(session, actor.ctx).rows if r.certificate}
        for spec in demo.CERTIFICATES:
            if spec.kind in held:
                continue
            until = (
                None
                if spec.valid_until_days is None
                else today + dt.timedelta(days=spec.valid_until_days)
            )
            compliance.record(
                session,
                actor.ctx,
                CertificateCreate(
                    kind=spec.kind,
                    reference_no=spec.reference_no,
                    issued_by=spec.issued_by,
                    valid_from=today + dt.timedelta(days=spec.valid_from_days),
                    valid_until=until,
                    owner_membership_id=actor.ctx.membership_id,
                ),
            )
            created["compliance_certificates"] += 1


# --- 4. school calendar --------------------------------------------------------------------------


def _ensure_calendar(plan: TenantPlan, actor: Actor, created: Counter[str]) -> None:
    with _session(plan.tenant_id, actor) as session:
        year = tenancy.get_current_academic_year(session)
        if year is None:
            raise DemoSeedError("the school has no current academic year")
        cal = academics.get_calendar(session, actor.ctx, year.id)
        if not cal.set_up:
            cal = academics.set_pattern(
                session,
                actor.ctx,
                year.id,
                CalendarPatternIn(
                    weekly_off=list(demo.CALENDAR_WEEKLY_OFF),
                    saturdays_off=list(demo.CALENDAR_SATURDAYS_OFF),
                ),
                expected_version=0,
            )
            created["calendars"] += 1
        names = {e.name for e in cal.entries}
        for entry in demo.CALENDAR_ENTRIES:
            if entry.name in names:
                continue
            academics.add_entry(
                session,
                actor.ctx,
                year.id,
                CalendarEntryIn(
                    kind=entry.kind,
                    name=entry.name,
                    starts_on=entry.starts_on,
                    ends_on=entry.ends_on,
                ),
            )
            created["calendar_entries"] += 1


# --- 5. data-quality run and 6. UDISE+ follow-ups -------------------------------------------------


def _ensure_findings(plan: TenantPlan, actor: Actor, created: Counter[str]) -> None:
    with _session(plan.tenant_id, actor) as session:
        if dq.list_findings(session, actor.ctx, FindingFilters(), limit=1).data:
            return
        dq.run_checks(session, actor.ctx)
        created["dq_runs"] += 1


def _ensure_followups(plan: TenantPlan, actor: Actor, created: Counter[str]) -> None:
    with _session(plan.tenant_id, actor) as session:
        listed = followups.list_followups(session, actor.ctx, demo.READINESS_PROFILE)
        if any(i.state != "not_started" for i in listed.items):
            return
        today = listed.today

        def pick(owner: str, n: int) -> list[uuid.UUID]:
            ids = sorted({i.student.id for i in listed.items if i.fix_owner == owner}, key=str)
            return ids[:n]

        slips = pick("parent_aadhaar", demo.FOLLOWUP_SLIPS)
        if slips:
            followups.record(
                session,
                actor.ctx,
                demo.READINESS_PROFILE,
                FollowupIn(student_ids=slips, fix_owner="parent_aadhaar", event="slip_given"),
            )
            created["dq_followups"] += len(slips)
        requests = pick("school_udise", demo.FOLLOWUP_REQUESTS)
        if requests:
            followups.record(
                session,
                actor.ctx,
                demo.READINESS_PROFILE,
                FollowupIn(
                    student_ids=requests,
                    fix_owner="school_udise",
                    event="request_sent",
                    channel="letter",
                    occurred_on=today - dt.timedelta(days=demo.FOLLOWUP_REQUEST_DAYS_AGO),
                ),
            )
            created["dq_followups"] += len(requests)


# --- 7. disclosure draft and 8. weekly brief ------------------------------------------------------


def _ensure_disclosure(plan: TenantPlan, actor: Actor, created: Counter[str]) -> None:
    with _session(plan.tenant_id, actor) as session:
        if disclosure.get_draft(session, actor.ctx).version:
            return
        disclosure.put_draft(
            session,
            actor.ctx,
            DisclosureDraftIn(inputs=dict(demo.DISCLOSURE_INPUTS)),
            expected_version=0,
        )
        created["disclosure_drafts"] += 1


def _ensure_brief(plan: TenantPlan, created: Counter[str]) -> None:
    with tenant_session(plan.tenant_id) as session:
        before = brief.tenant_data_counts(session).get("ops.weekly_briefs", 0)
        brief.build_week(session)
        after = brief.tenant_data_counts(session).get("ops.weekly_briefs", 0)
    created["weekly_briefs"] += max(after - before, 0)


# --- entry point ---------------------------------------------------------------------------------


def seed_scenario(plan: TenantPlan) -> dict[str, int]:
    """Apply the demo scenario to the seeded school ``plan``. Returns what was created."""
    created: Counter[str] = Counter()
    actors = actors_for(plan)
    class_x = _class_x_students(plan.tenant_id, actors.accountant)
    _ensure_arrears(plan, actors, class_x, created)
    _ensure_addons(plan, actors.accountant, class_x, created)
    _ensure_certificates(plan, actors.principal, created)
    _ensure_calendar(plan, actors.principal, created)
    _ensure_findings(plan, actors.office_admin, created)
    _ensure_followups(plan, actors.office_admin, created)
    _ensure_disclosure(plan, actors.principal, created)
    _ensure_brief(plan, created)
    return {k: v for k, v in sorted(created.items()) if v}


def counts(tenant_id: uuid.UUID) -> dict[str, int]:
    """Rows of the modules the scenario writes (their offboarding counters; counts only)."""
    with tenant_session(tenant_id) as session:
        found = {
            **compliance.tenant_data_counts(session),
            **disclosure.tenant_data_counts(session),
            **brief.tenant_data_counts(session),
        }
    return {table: n for table, n in sorted(found.items()) if n}
