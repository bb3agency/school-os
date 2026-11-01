"""Synthetic fee ledger and WhatsApp messaging rows for a seeded school (docs/12 §3;
SEC-024, NFR-AVL-005: the restore drill must restore ``fin`` and ``msg`` with rows in them).
NEVER real data.

Called by :mod:`app.devtools.seeder` after the school's students exist. Every write goes through
the owning module's public service (``app.fees.service``, ``app.messaging.service``) in a
``tenant_session`` as a synthetic member holding the permission, so RLS, validation,
maker-checker and audit apply as in production:

- **Fees on.** The school setting ``fees_enabled`` through ``tenancy.update_tenant_settings``,
  and the per-school override of the platform flag ``fees.enabled`` through
  ``platform.service.set_school_flag_override`` (works without a fleet ``deployments`` row,
  which shared-tier seeding does not make; audited ``flag.override_set`` on the platform chain).
- **Fees** (first accountant): heads ``TUI`` (tuition) and ``EXM`` (exam); one active plan for
  Class X of the current year with two instalments (15 June 2026, 15 January 2027); the plan
  applied to every Class X student (accounts and charges); four cash receipts on the first
  accounts by admission number; the last one cancelled (requested by the accountant, approved by
  the principal) and part of the third refunded in cash (requested by the accountant, approved
  by the principal; voucher series ``RFD``).
- **Messaging** (principal; only when messaging is available on this deployment,
  ``SOS_MESSAGING_ENABLED`` on the shared tier): the school's settings row with every switch off
  and no WhatsApp connection (nothing reaches Meta); a staff-recorded opt-OUT for fee messages
  for the primary guardians of the first accounts (synthetic guardians have no phone number, and
  an opt-in needs one); one ``fee_receipt`` message per issued receipt queued to the primary
  guardian through ``queue_message`` and handed to ``deliver``, which records it as ``skipped``
  (``messaging_not_connected``) without calling the provider.

Deterministic for a plan (codes, amounts, due dates and which students are fixed; receipt and
decision dates are the day of the run). Idempotent: each step looks for what it would create
(heads by code, the plan by name, accounts by student, receipts by account, the cancellation
and refund by receipt state, settings by version, opt-ins by guardian history, messages by
dedupe key) and only adds what is missing.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections import Counter
from collections.abc import Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from sqlalchemy.orm import Session

from app.authz.context import UserContext
from app.core.db import tenant_session
from app.devtools.plan import TenantPlan
from app.fees import service as fees
from app.fees.schemas import (
    AccountSearch,
    CancellationRequest,
    DecisionIn,
    FeePlanCreate,
    HeadCreate,
    InstalmentIn,
    PlanApplyIn,
    PlanLineIn,
    ReceiptCreate,
    ReceiptListItem,
    RefundCreate,
)
from app.messaging import service as messaging
from app.messaging.schemas import MessagingSettingsIn, OptinIn
from app.platform import service as control_plane
from app.students import service as students
from app.tenancy import service as tenancy
from app.tenancy.schemas import TenantSettingsPatch

FEES_FLAG: Final = "fees.enabled"
AUDIT_SOURCE: Final = "synthetic_seed"
PLAN_CLASS: Final = "X"
PLAN_NAME: Final = "Synthetic Class X fees 2026-27"
HEADS: Final = (
    ("TUI", "Tuition fee", "tuition", Decimal("12000.00")),
    ("EXM", "Exam fee", "exam", Decimal("1000.00")),
)
INSTALMENTS: Final = (
    (1, "Term 1", dt.date(2026, 6, 15)),
    (2, "Term 2", dt.date(2027, 1, 15)),
)
# Cash receipts on the first accounts (by admission number); all within Term 1 (₹6,500 due).
RECEIPT_AMOUNTS: Final = (
    Decimal("2000.00"),
    Decimal("3000.00"),
    Decimal("6500.00"),
    Decimal("1500.00"),
)
CANCEL_INDEX: Final = 3  # the fourth receipt: a duplicate entry, cancelled
REFUND_INDEX: Final = 2  # the third receipt: part refunded in cash
REFUND_AMOUNT: Final = Decimal("500.00")
OPTOUT_GUARDIANS: Final = 4
REASON: Final = "Synthetic seed request"
LIST_LIMIT: Final = 100


class FinanceSeedError(RuntimeError):
    """Fees or messaging could not be seeded (message never contains personal data)."""


@dataclass(frozen=True)
class Actor:
    user_id: uuid.UUID
    ctx: UserContext


@dataclass(frozen=True)
class Actors:
    accountant: Actor
    """Fee setup, receipts, cancellation and refund requests (``fee.manage``, ``fee.collect``)."""
    principal: Actor
    """Maker-checker approvals (``fee.receipt.cancel``, ``fee.refund.approve``) and messaging
    (``messaging.configure``, ``messaging.optin.record``)."""


def _session(tenant_id: uuid.UUID, actor: Actor) -> AbstractContextManager[Session]:
    return tenant_session(tenant_id, actor.user_id)


# --- switching fees on ---------------------------------------------------------------------------


def _ensure_flag(tenant_id: uuid.UUID, created: Counter[str]) -> None:
    """The per-school ``fees.enabled`` override (see the module docstring)."""
    if control_plane.set_school_flag_override(
        FEES_FLAG, tenant_id, True, source=AUDIT_SOURCE, request_id="seed-synthetic"
    ):
        created["fee_flags"] += 1


def _ensure_setting(tenant_id: uuid.UUID, actor: Actor, created: Counter[str]) -> None:
    with _session(tenant_id, actor) as session:
        tenant = tenancy.get_tenant(session)
        if tenancy.session_settings(session).fees_enabled:
            return
        tenancy.update_tenant_settings(
            session, TenantSettingsPatch(fees_enabled=True), expected_version=tenant.version
        )
    created["fee_settings"] += 1


# --- fee setup -----------------------------------------------------------------------------------


def _ensure_plan(tenant_id: uuid.UUID, actor: Actor, created: Counter[str]) -> uuid.UUID:
    ctx = actor.ctx
    with _session(tenant_id, actor) as session:
        heads = {h.code: h.id for h in fees.list_heads(session, ctx)}
        for code, name, kind, _annual in HEADS:
            if code not in heads:
                heads[code] = fees.create_head(
                    session, ctx, HeadCreate(code=code, name=name, kind=kind, gst_exempt=True)
                ).id
                created["fee_heads"] += 1
        year = tenancy.get_current_academic_year(session)
        if year is None:
            raise FinanceSeedError("the school has no current academic year")
        class_id = next((c.id for c in tenancy.list_classes(session) if c.code == PLAN_CLASS), None)
        if class_id is None:
            raise FinanceSeedError(f"class {PLAN_CLASS} is missing")
        plans = fees.list_plans(session, ctx, academic_year_id=year.id, class_id=class_id)
        plan = next((p for p in plans if p.name == PLAN_NAME), None)
        if plan is None:
            plan = fees.create_plan(
                session,
                ctx,
                FeePlanCreate(
                    academic_year_id=year.id,
                    class_id=class_id,
                    name=PLAN_NAME,
                    lines=[
                        PlanLineIn(head_id=heads[code], annual_amount=annual)
                        for code, _n, _k, annual in HEADS
                    ],
                    instalments=[
                        InstalmentIn(
                            instalment_no=no,
                            label=label,
                            due_on=due_on,
                            amounts={heads[code]: annual / 2 for code, _n, _k, annual in HEADS},
                        )
                        for no, label, due_on in INSTALMENTS
                    ],
                ),
            )
            created["fee_plans"] += 1
        if plan.status == "draft":
            plan = fees.activate_plan(session, ctx, plan.id, expected_version=plan.version)
        if plan.status != "active":
            raise FinanceSeedError(f"the synthetic fee plan is {plan.status}")
        applied = fees.apply_plan(session, ctx, plan.id, PlanApplyIn(include_past_instalments=True))
        created["fee_accounts"] += applied.accounts_created
        created["fee_charges"] += applied.charges_posted
        return plan.id


def _accounts(tenant_id: uuid.UUID, actor: Actor) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """(account id, student id) of the Class X accounts, ordered by admission number."""
    with _session(tenant_id, actor) as session:
        class_id = next(c.id for c in tenancy.list_classes(session) if c.code == PLAN_CLASS)
        found = fees.search_accounts(session, actor.ctx, AccountSearch(class_id=class_id))
    found.sort(key=lambda a: (a.admission_no or "", str(a.account_id)))
    return [(a.account_id, a.student_id) for a in found]


# --- receipts, cancellation and refund -----------------------------------------------------------


def _receipts_of(
    tenant_id: uuid.UUID, actor: Actor, account_id: uuid.UUID
) -> list[ReceiptListItem]:
    with _session(tenant_id, actor) as session:
        page = fees.list_receipts(
            session,
            actor.ctx,
            receipt_date=None,
            mode=None,
            collected_by=None,
            status=None,
            account_id=account_id,
            limit=LIST_LIMIT,
            cursor=None,
        )
    return list(page.data)


def _ensure_receipts(
    tenant_id: uuid.UUID,
    actors: Actors,
    accounts: Sequence[tuple[uuid.UUID, uuid.UUID]],
    created: Counter[str],
) -> list[uuid.UUID]:
    """One cash receipt per account in :data:`RECEIPT_AMOUNTS`; returns their ids in order."""
    out: list[uuid.UUID] = []
    acc = actors.accountant
    for (account_id, _student), amount in zip(accounts, RECEIPT_AMOUNTS, strict=False):
        existing = _receipts_of(tenant_id, acc, account_id)
        if existing:
            out.append(min(existing, key=lambda r: r.receipt_number).id)
            continue
        with _session(tenant_id, acc) as session:
            receipt = fees.issue_receipt(
                session, acc.ctx, ReceiptCreate(account_id=account_id, amount=amount, mode="cash")
            )
        out.append(receipt.id)
        created["fee_receipts"] += 1
    return out


def _ensure_cancellation(
    tenant_id: uuid.UUID, actors: Actors, receipt_id: uuid.UUID, created: Counter[str]
) -> None:
    """Accountant asks, principal approves (maker-checker, FR-FEE-012)."""
    acc, head = actors.accountant, actors.principal
    with _session(tenant_id, acc) as session:
        receipt = fees.get_receipt(session, acc.ctx, receipt_id)
        if receipt.status != "issued":
            return
        request = receipt.cancellation
        if request is None or request.status != "pending":
            request = fees.request_cancellation(
                session,
                acc.ctx,
                receipt_id,
                CancellationRequest(reason_code="duplicate_entry", reason=REASON),
            )
    with _session(tenant_id, head) as session:
        fees.approve_cancellation(
            session, head.ctx, request.id, DecisionIn(), expected_version=request.version
        )
    created["fee_cancellations"] += 1


def _ensure_refund(
    tenant_id: uuid.UUID, actors: Actors, receipt_id: uuid.UUID, created: Counter[str]
) -> None:
    """Accountant asks, principal approves; a cash refund is written as a voucher (FR-FEE-013)."""
    acc, head = actors.accountant, actors.principal
    with _session(tenant_id, acc) as session:
        receipt = fees.get_receipt(session, acc.ctx, receipt_id)
        if receipt.status != "issued":
            return
        pending = [r for r in receipt.refunds if r.status == "pending"]
        if receipt.refunds and not pending:
            return
        refund = (
            pending[0]
            if pending
            else fees.request_refund(
                session,
                acc.ctx,
                receipt_id,
                RefundCreate(amount=REFUND_AMOUNT, mode="cash", reason=REASON),
            )
        )
    with _session(tenant_id, head) as session:
        fees.approve_refund(
            session, head.ctx, refund.id, DecisionIn(), expected_version=refund.version
        )
    created["fee_refunds"] += 1


# --- messaging -----------------------------------------------------------------------------------


def _primary_guardians(
    tenant_id: uuid.UUID, actor: Actor, student_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, uuid.UUID]:
    """student id -> primary guardian id (``student_ids`` come from fee accounts the caller
    reached)."""
    with _session(tenant_id, actor) as session:
        briefs = students.guardians_of_students(session, list(student_ids))
    out: dict[uuid.UUID, uuid.UUID] = {}
    for b in sorted(briefs, key=lambda g: (not g.is_primary, str(g.guardian_id))):
        out.setdefault(b.student_id, b.guardian_id)
    return out


def _ensure_messaging_settings(tenant_id: uuid.UUID, actor: Actor, created: Counter[str]) -> None:
    with _session(tenant_id, actor) as session:
        current = messaging.get_messaging_settings(session, actor.ctx)
        if current.version > 0:
            return
        messaging.update_messaging_settings(
            session,
            actor.ctx,
            MessagingSettingsIn(
                notices_enabled=False,
                absence_alerts_enabled=False,
                fee_reminders_enabled=False,
                fee_receipts_enabled=False,
                absence_alert_time=dt.time(11, 0),
                quiet_start=dt.time(21, 0),
                quiet_end=dt.time(7, 0),
            ),
            expected_version=0,
        )
    created["msg_settings"] += 1


def _ensure_optouts(
    tenant_id: uuid.UUID, actor: Actor, guardian_ids: Sequence[uuid.UUID], created: Counter[str]
) -> None:
    for guardian_id in guardian_ids:
        with _session(tenant_id, actor) as session:
            current = messaging.get_guardian_optins(session, actor.ctx, guardian_id)
            if current.version > 0:
                continue
            messaging.record_optin(
                session,
                actor.ctx,
                guardian_id,
                OptinIn(
                    category="fees",
                    status="opted_out",
                    method="staff_recorded_stop",
                    decided_on=fees.today(session),
                ),
                expected_version=0,
            )
        created["msg_optins"] += 1


def _queue_receipt_messages(
    tenant_id: uuid.UUID,
    actor: Actor,
    receipts: Sequence[uuid.UUID],
    guardians: dict[uuid.UUID, uuid.UUID],
    created: Counter[str],
) -> None:
    queued: list[uuid.UUID] = []
    with _session(tenant_id, actor) as session:
        school = tenancy.school_display_name(session)[:60]
        for receipt_id in receipts:
            receipt = fees.receipt_for_message(session, receipt_id)
            if receipt is None or receipt.cancelled:
                continue
            guardian_id = guardians.get(receipt.student_id)
            name = students.first_names(session, [receipt.student_id]).get(receipt.student_id)
            if guardian_id is None or name is None:
                continue
            out = messaging.queue_message(
                session,
                purpose="fee_receipt",
                guardian_id=guardian_id,
                student_id=receipt.student_id,
                source_type="fee_receipt",
                source_id=receipt.receipt_id,
                dedupe_key="receipt",
                variables={
                    "school_name": school,
                    "amount": f"{receipt.amount:.2f}",
                    "child_first_name": name[:40],
                    "receipt_number": receipt.receipt_number,
                    "date": receipt.receipt_date.strftime("%d %b %Y"),
                },
                created_by=actor.user_id,
            )
            if out is not None:
                queued.append(out.message_id)
    for message_id in queued:
        # No WhatsApp connection: deliver records "skipped" and never calls the provider.
        messaging.deliver(tenant_id, message_id)
        created["msg_messages"] += 1


# --- entry point ---------------------------------------------------------------------------------


def seed_fees_and_messaging(plan: TenantPlan, actors: Actors) -> dict[str, int]:
    """Fees, then messaging (when available) for one seeded school. Returns what was created."""
    created: Counter[str] = Counter()
    tenant_id = plan.tenant_id
    _ensure_flag(tenant_id, created)
    _ensure_setting(tenant_id, actors.principal, created)
    _ensure_plan(tenant_id, actors.accountant, created)
    accounts = _accounts(tenant_id, actors.accountant)
    receipts = _ensure_receipts(tenant_id, actors, accounts, created)
    if len(receipts) > CANCEL_INDEX:
        _ensure_cancellation(tenant_id, actors, receipts[CANCEL_INDEX], created)
    if len(receipts) > REFUND_INDEX:
        _ensure_refund(tenant_id, actors, receipts[REFUND_INDEX], created)
    if messaging.available():
        _ensure_messaging_settings(tenant_id, actors.principal, created)
        firsts = [student for _account, student in accounts[: max(OPTOUT_GUARDIANS, 1)]]
        guardians = _primary_guardians(tenant_id, actors.principal, firsts)
        ordered = [guardians[s] for s in firsts if s in guardians]
        _ensure_optouts(tenant_id, actors.principal, ordered[:OPTOUT_GUARDIANS], created)
        _queue_receipt_messages(tenant_id, actors.principal, receipts, guardians, created)
    return {k: v for k, v in created.items() if v}


def counts(tenant_id: uuid.UUID) -> dict[str, int]:
    """Rows per ``fin``/``msg`` table of the school (the modules' purge counters)."""
    with tenant_session(tenant_id) as session:
        found = {**fees.tenant_data_counts(session), **messaging.tenant_data_counts(session)}
    return {table: n for table, n in sorted(found.items()) if n}
