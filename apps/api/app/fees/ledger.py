"""Fee accounts and their dues, computed from the append-only ledger (FR-FEE-006, FR-FEE-020,
FR-FEE-021; internal: other modules use ``app.fees.service``).

A line is one (instalment, head) of an account; ``head_id`` None is the late-fee line of an
instalment. Its due is the plain sum of its ledger entries (charges and amounts owed positive,
credits negative), so the balance of an account is ``sum(amount)`` of its entries.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections import defaultdict
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy.orm import Session

from app.authz.context import UserContext
from app.core.errors import NotFound
from app.fees import _common as c
from app.fees import repository as repo
from app.fees.models import FeeHead, FeePlan, FeePlanInstalment, StudentFeeAccount
from app.fees.money import ZERO
from app.fees.schemas import (
    AccountOut,
    AccountSearch,
    AccountSummary,
    AccountTotals,
    DueLine,
    StatementEntry,
    StatementOut,
)
from app.students import service as students
from app.students.schemas import SearchFilters, StudentSummary
from app.tenancy import service as tenancy

LATE_FEE_NAME = "Late fee"


@dataclass
class Bucket:
    instalment_no: int
    head_id: uuid.UUID | None
    charged: Decimal = ZERO
    concession: Decimal = ZERO  # negative
    late_fee: Decimal = ZERO
    adjustment: Decimal = ZERO
    payment: Decimal = ZERO  # negative
    reversal: Decimal = ZERO
    refund: Decimal = ZERO

    @property
    def due(self) -> Decimal:
        return (
            self.charged
            + self.concession
            + self.late_fee
            + self.adjustment
            + self.payment
            + self.reversal
            + self.refund
        )

    @property
    def paid(self) -> Decimal:
        return -self.payment - self.reversal

    def add(self, kind: str, amount: Decimal) -> None:
        attr = {
            "charge": "charged",
            "concession": "concession",
            "late_fee": "late_fee",
            "adjustment": "adjustment",
            "payment": "payment",
            "payment_reversal": "reversal",
            "refund": "refund",
        }[kind]
        setattr(self, attr, getattr(self, attr) + amount)


@dataclass
class Dues:
    """Every line of one account, ordered for allocation (oldest due first; within an
    instalment the plan's head order, the late fee last)."""

    account: StudentFeeAccount
    plan: FeePlan
    lines: list[tuple[Bucket, DueLine]] = field(default_factory=list)

    def line(self, instalment_no: int, head_id: uuid.UUID | None) -> tuple[Bucket, DueLine] | None:
        for bucket, out in self.lines:
            if bucket.instalment_no == instalment_no and bucket.head_id == head_id:
                return bucket, out
        return None

    @property
    def balance(self) -> Decimal:
        return sum((b.due for b, _ in self.lines), ZERO)

    @property
    def collectable(self) -> Decimal:
        return sum((o.due for _, o in self.lines if o.collectable and o.due > 0), ZERO)

    @property
    def overdue(self) -> Decimal:
        """Overdue amounts SchoolOS collects (taxable heads are invoiced in Tally, FR-FEE-023)."""
        return sum((o.due for _, o in self.lines if o.overdue and o.collectable), ZERO)

    @property
    def oldest_due_on(self) -> dt.date | None:
        dates = [
            o.due_on for _, o in self.lines if o.overdue and o.collectable and o.due_on is not None
        ]
        return min(dates) if dates else None

    def totals(self) -> AccountTotals:
        def s(attr: str) -> Decimal:
            return sum((getattr(b, attr) for b, _ in self.lines), ZERO)

        return AccountTotals(
            charged=s("charged"),
            concessions=-s("concession"),
            late_fees=s("late_fee"),
            adjustments=s("adjustment"),
            paid=s("paid"),
            refunded=s("refund"),
            balance=self.balance,
            overdue=self.overdue,
            collectable=self.collectable,
        )


@dataclass(frozen=True)
class Context:
    """Heads, plans and instalments needed to describe accounts (one load per request)."""

    heads: dict[uuid.UUID, FeeHead]
    plans: dict[uuid.UUID, FeePlan]
    instalments: dict[uuid.UUID, dict[int, FeePlanInstalment]]
    head_order: dict[uuid.UUID, dict[uuid.UUID, int]]

    @classmethod
    def load(cls, session: Session, plan_ids: Collection[uuid.UUID]) -> Context:
        plans = repo.plans_by_id(session, plan_ids)
        # Earlier versions label instalments that later ones dropped.
        extra = {p.previous_plan_id for p in plans.values() if p.previous_plan_id}
        plans.update(repo.plans_by_id(session, extra - set(plans)))
        instalments: dict[uuid.UUID, dict[int, FeePlanInstalment]] = defaultdict(dict)
        for row in repo.plan_instalments(session, list(plans)):
            instalments[row.plan_id][row.instalment_no] = row
        order: dict[uuid.UUID, dict[uuid.UUID, int]] = defaultdict(dict)
        for line in repo.plan_lines(session, list(plans)):
            order[line.plan_id][line.head_id] = line.sort_order
        return cls(
            heads=repo.heads_by_id(session),
            plans=plans,
            instalments=dict(instalments),
            head_order=dict(order),
        )

    def instalment(self, plan: FeePlan, no: int) -> FeePlanInstalment | None:
        found = self.instalments.get(plan.id, {}).get(no)
        if found is None and plan.previous_plan_id is not None:
            previous = self.plans.get(plan.previous_plan_id)
            if previous is not None:
                return self.instalment(previous, no)
        return found


def _describe(
    ctx: Context, account: StudentFeeAccount, buckets: Iterable[Bucket], today: dt.date
) -> Dues:
    plan = ctx.plans[account.plan_id]
    order = ctx.head_order.get(plan.id, {})
    out = Dues(account=account, plan=plan)
    rows: list[tuple[tuple[object, ...], Bucket, DueLine]] = []
    for b in buckets:
        inst = ctx.instalment(plan, b.instalment_no)
        head = ctx.heads.get(b.head_id) if b.head_id else None
        due_on = inst.due_on if inst else None
        collectable = head is None or head.gst_exempt
        line = DueLine(
            instalment_no=b.instalment_no,
            label=inst.label if inst else f"Instalment {b.instalment_no}",
            due_on=due_on,
            head_id=b.head_id,
            head_code=head.code if head else None,
            head_name=head.name if head else None,
            collectable=collectable,
            charged=b.charged,
            concession=-b.concession,
            late_fee=b.late_fee,
            adjustment=b.adjustment,
            paid=b.paid,
            due=b.due,
            overdue=b.due > 0 and due_on is not None and due_on < today,
        )
        key = (
            due_on or dt.date.max,
            b.instalment_no,
            1 if b.head_id is None else 0,
            order.get(b.head_id, 10_000) if b.head_id else 0,
            head.code if head else "",
        )
        rows.append((key, b, line))
    rows.sort(key=lambda r: r[0])
    out.lines = [(b, line) for _, b, line in rows]
    return out


def buckets_by_account(
    session: Session, account_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, list[Bucket]]:
    grouped: dict[uuid.UUID, dict[tuple[int, uuid.UUID | None], Bucket]] = defaultdict(dict)
    for row in repo.bucket_sums(session, account_ids):
        key = (row.instalment_no, row.head_id)
        bucket = grouped[row.account_id].get(key)
        if bucket is None:
            bucket = grouped[row.account_id][key] = Bucket(row.instalment_no, row.head_id)
        bucket.add(row.entry_kind, row.amount)
    return {aid: list(per.values()) for aid, per in grouped.items()}


def dues_of(
    session: Session, accounts: Sequence[StudentFeeAccount], today: dt.date
) -> dict[uuid.UUID, Dues]:
    if not accounts:
        return {}
    ctx = Context.load(session, {a.plan_id for a in accounts})
    buckets = buckets_by_account(session, [a.id for a in accounts])
    return {a.id: _describe(ctx, a, buckets.get(a.id, []), today) for a in accounts}


def dues_for(session: Session, account: StudentFeeAccount, today: dt.date) -> Dues:
    return dues_of(session, [account], today)[account.id]


# --- reads ---------------------------------------------------------------------------------------


def load_account(
    session: Session, account_id: uuid.UUID, *, lock: bool = False
) -> StudentFeeAccount:
    row = repo.get_account(session, account_id, lock=lock)
    if row is None:
        raise NotFound("Fee account not found")
    return row


def _summary_of(
    ctx: UserContext, session: Session, student_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, StudentSummary]:
    return students.summaries(session, ctx, student_ids)


def account_out(session: Session, ctx: UserContext, account: StudentFeeAccount) -> AccountOut:
    today = c.today(session)
    dues = dues_for(session, account, today)
    student = _summary_of(ctx, session, [account.student_id]).get(account.student_id)
    return AccountOut(
        id=account.id,
        student_id=account.student_id,
        display_name=student.display_name if student else None,
        admission_no=student.admission_no if student else None,
        class_section=student.class_section if student else None,
        academic_year_id=account.academic_year_id,
        plan_id=account.plan_id,
        plan_name=dues.plan.name,
        category_code=account.category_code,
        status="closed" if account.status == "closed" else "open",
        lines=[line for _, line in dues.lines],
        totals=dues.totals(),
        pending_requests=repo.count_pending_for_account(session, account.id),
        version=account.version,
    )


def get_account(session: Session, ctx: UserContext, account_id: uuid.UUID) -> AccountOut:
    """Dues per instalment and head with late fees and the balance (FR-FEE-021; ``fee.collect``
    or ``finance.read``, school-wide)."""
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.COLLECT, c.READ)
    return account_out(session, ctx, load_account(session, account_id))


def statement(session: Session, ctx: UserContext, account_id: uuid.UUID) -> StatementOut:
    """Every ledger entry with the running balance (FR-FEE-021)."""
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.COLLECT, c.READ)
    account = load_account(session, account_id)
    heads = repo.heads_by_id(session)
    entries = repo.entries_of(session, account.id)
    numbers = {
        r.id: r.receipt_number
        for r in repo.receipts_by_id(session, c.unique_ids(e.receipt_id for e in entries)).values()
    }
    running = ZERO
    out: list[StatementEntry] = []
    for e in entries:
        running += e.amount
        source = e.concession_id or e.adjustment_id or e.cancellation_id or e.refund_id
        head = heads.get(e.head_id) if e.head_id else None
        out.append(
            StatementEntry(
                id=e.id,
                entry_date=e.entry_date,
                entry_kind=e.entry_kind,
                instalment_no=e.instalment_no,
                head_code=head.code if head else None,
                amount=e.amount,
                balance=running,
                receipt_id=e.receipt_id,
                receipt_number=numbers.get(e.receipt_id) if e.receipt_id else None,
                source_id=source,
            )
        )
    return StatementOut(account_id=account.id, entries=out, balance=running)


def search_accounts(
    session: Session, ctx: UserContext, data: AccountSearch
) -> list[AccountSummary]:
    """Counter search (FR-FEE-010 AC1; ``fee.collect`` or ``finance.read``): students by part of
    a name or the admission number through ``students.search`` (ranked), then their fee account
    of the year (current unless given). Students without an account are left out."""
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.COLLECT, c.READ)
    year_id = data.academic_year_id
    if year_id is None:
        current = tenancy.get_current_academic_year(session)
        if current is None:
            return []
        year_id = current.id
    limit = c.settings().limits.counter_matches if data.query else c.settings().limits.list_max
    page = students.search(
        session,
        ctx,
        SearchFilters(
            query=data.query or None,
            section_id=data.section_id,
            class_id=data.class_id,
            academic_year_id=data.academic_year_id,
        ),
        limit=limit * 3 if data.query else limit,
    )
    found = {s.id: s for s in page.data}
    accounts = repo.accounts_for_year(session, year_id, list(found))
    dues = dues_of(session, accounts, c.today(session))
    rank = {sid: i for i, sid in enumerate(found)}
    out: list[AccountSummary] = []
    for account in sorted(accounts, key=lambda a: rank.get(a.student_id, 0)):
        d = dues[account.id]
        if data.has_dues is not None and (d.balance > 0) != data.has_dues:
            continue
        s = found[account.student_id]
        out.append(
            AccountSummary(
                account_id=account.id,
                student_id=account.student_id,
                display_name=s.display_name,
                admission_no=s.admission_no,
                class_section=s.class_section,
                academic_year_id=account.academic_year_id,
                balance=d.balance,
                overdue=d.overdue,
                status="closed" if account.status == "closed" else "open",
            )
        )
        if len(out) >= limit:
            break
    return out


def accounts_of_student(
    session: Session, ctx: UserContext, student_id: uuid.UUID
) -> list[AccountSummary]:
    """Every fee account of one student, newest year first (the student page's fee card)."""
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.COLLECT, c.READ)
    student = _summary_of(ctx, session, [student_id]).get(student_id)
    if student is None:
        raise NotFound("Student not found")
    accounts = repo.accounts_of_students(session, [student_id])
    dues = dues_of(session, accounts, c.today(session))
    years = {y.id: y.starts_on for y in tenancy.list_academic_years(session)}
    accounts.sort(key=lambda a: years.get(a.academic_year_id, dt.date.min), reverse=True)
    return [
        AccountSummary(
            account_id=a.id,
            student_id=a.student_id,
            display_name=student.display_name,
            admission_no=student.admission_no,
            class_section=student.class_section,
            academic_year_id=a.academic_year_id,
            balance=dues[a.id].balance,
            overdue=dues[a.id].overdue,
            status="closed" if a.status == "closed" else "open",
        )
        for a in accounts
    ]


__all__ = [
    "Bucket",
    "Context",
    "Dues",
    "account_out",
    "accounts_of_student",
    "buckets_by_account",
    "dues_for",
    "dues_of",
    "get_account",
    "load_account",
    "search_accounts",
    "statement",
]
