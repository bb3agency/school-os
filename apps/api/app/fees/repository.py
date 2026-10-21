"""Database access for fees. Only ``app.fees`` modules call this module.

Every function runs in the caller's ``tenant_session``: RLS limits each statement to the current
school. Fee permissions are school-wide (FR-FEE-024), so there is no per-object scope filter here;
the service decides who may call what.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import Table, and_, delete, func, insert, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.errors import Conflict, DomainError, Forbidden, ValidationFailed
from app.core.ids import new_id
from app.core.record_tables import dump_table
from app.core.records import RecordTable
from app.fees.models import (
    ALL_MODELS,
    Adjustment,
    Concession,
    DayClose,
    FeeHead,
    FeePlan,
    FeePlanInstalment,
    FeePlanLine,
    FeeSettings,
    LedgerEntry,
    Receipt,
    ReceiptAllocation,
    ReceiptCancellation,
    Refund,
    StudentFeeAccount,
)

# --- errors ---------------------------------------------------------------------------------------

_SELF = "Someone else must decide this request: you made it."
_CONFLICTS: dict[str, tuple[str, str]] = {
    "fee_heads_code_unique": ("head_code_taken", "Another fee head already has this code."),
    "fee_plans_version_unique": (
        "plan_exists",
        "A plan for this class, year and category already exists. Open it instead.",
    ),
    "fee_plans_one_active": (
        "plan_already_active",
        "Another plan is already active for this class, year and category.",
    ),
    "fee_plans_frozen": (
        "plan_in_use",
        "This plan is active and in use, so it cannot be changed. Make a new version.",
    ),
    "fee_plan_lines_head_unique": ("duplicate_head", "Each head appears once in a plan."),
    "fee_plan_instalments_no_unique": (
        "duplicate_instalment",
        "Each instalment number appears once in a plan.",
    ),
    "student_fee_accounts_one_per_year": (
        "account_exists",
        "This student already has a fee account for the year.",
    ),
    "fee_requests_frozen": ("request_not_pending", "This request was already decided."),
    "receipts_frozen": ("receipt_frozen", "A receipt is never edited. Cancel it instead."),
    "receipts_utr_unique": (
        "utr_in_use",
        "This UPI reference is already on another receipt.",
    ),
    "receipts_number_unique": ("receipt_number_conflict", "Try again."),
    "receipts_number_text_unique": ("receipt_number_conflict", "Try again."),
    "receipt_counters_never_down": ("receipt_number_conflict", "Try again."),
    "receipt_cancellations_one_live": (
        "cancellation_exists",
        "A cancellation of this receipt is already waiting or approved.",
    ),
    "adjustments_one_plan_change": (
        "adjustment_exists",
        "This plan change is already proposed for the student.",
    ),
    "day_closes_one_per_day": ("day_closed", "This day is already closed."),
    "receipts_allocations_sum": (
        "allocations_do_not_add_up",
        "The allocation must add up to the receipt amount.",
    ),
}
_MAKER_CHECKER = frozenset(
    {
        "concessions_maker_checker",
        "adjustments_maker_checker",
        "receipt_cancellations_maker_checker",
        "refunds_maker_checker",
    }
)
_FIELDS: dict[str, str] = {
    "fee_plans_year_fk": "academic_year_id",
    "fee_plans_class_fk": "class_id",
    "fee_plan_lines_head_fk": "lines",
    "concessions_evidence_fk": "evidence_document_id",
    "adjustments_head_fk": "head_id",
    "fee_heads_code_shape": "code",
}


def translate_db_error(exc: DBAPIError) -> DomainError | None:
    """Map a PostgreSQL error to a domain error (never echoes values); ``None`` if unexpected."""
    orig = exc.orig
    state = getattr(orig, "sqlstate", None)
    constraint = getattr(getattr(orig, "diag", None), "constraint_name", None) or ""
    if constraint in _MAKER_CHECKER:
        return Forbidden(_SELF, code="self_approval_forbidden")
    if constraint in _CONFLICTS:
        code, message = _CONFLICTS[constraint]
        return Conflict(message, code=code)
    if state in ("23503", "23514") and constraint in _FIELDS:
        return ValidationFailed(
            [{"field": _FIELDS[constraint], "code": "invalid", "message_key": "errors.invalid"}]
        )
    return None


# --- plumbing -------------------------------------------------------------------------------------


def current_tenant_id(session: Session) -> uuid.UUID:
    value: object = session.execute(text("SELECT core.current_tenant()")).scalar_one()
    if value is None:
        raise RuntimeError("tenant context is not set")
    return uuid.UUID(str(value))


def now(session: Session) -> dt.datetime:
    value: dt.datetime = session.execute(select(func.now())).scalar_one()
    return value


def lock_day(session: Session, day: dt.date, *, exclusive: bool) -> None:
    """Transaction advisory lock on (school, IST date): receipts, cancellations and refunds of a
    day take it shared; closing the day takes it exclusive, so nothing is dated a day after its
    close commits (FR-FEE-019)."""
    fn = "pg_advisory_xact_lock" if exclusive else "pg_advisory_xact_lock_shared"
    session.execute(
        text(
            f"SELECT {fn}(hashtextextended('fees.day:' || core.current_tenant() || ':' || :d, 0))"
        ),
        {"d": day.isoformat()},
    )


def lock_utr(session: Session, utr: str) -> None:
    """Serialise receipts carrying the same UPI reference (FR-FEE-014): the second waits and then
    sees the first."""
    session.execute(
        text(
            "SELECT pg_advisory_xact_lock(hashtextextended('fees.utr:' || core.current_tenant() "
            "|| ':' || :u, 0))"
        ),
        {"u": utr},
    )


def _update(session: Session, model: Any, row_id: uuid.UUID, values: Mapping[str, Any]) -> Any:
    stmt = (
        update(model)
        .where(model.id == row_id)
        .values(**values, version=model.version + 1)
        .returning(model)
        .execution_options(synchronize_session=False)
    )
    return session.scalars(stmt, execution_options={"populate_existing": True}).one()


def _get(session: Session, model: Any, row_id: uuid.UUID, *, lock: bool = False) -> Any:
    stmt = select(model).where(model.id == row_id)
    if lock:
        stmt = stmt.with_for_update()
    return session.scalars(stmt, execution_options={"populate_existing": True}).one_or_none()


# --- settings -------------------------------------------------------------------------------------


def get_settings(session: Session) -> FeeSettings | None:
    return session.scalars(select(FeeSettings)).one_or_none()


def insert_settings(session: Session, values: Mapping[str, Any]) -> FeeSettings:
    stmt = (
        pg_insert(FeeSettings)
        .values(id=new_id(), tenant_id=text("core.current_tenant()"), **values)
        .on_conflict_do_nothing(index_elements=["tenant_id"])
    )
    session.execute(stmt)
    row = get_settings(session)
    if row is None:  # pragma: no cover - inserted above
        raise RuntimeError("fee settings missing")
    return row


def update_settings(session: Session, row_id: uuid.UUID, values: Mapping[str, Any]) -> FeeSettings:
    row: FeeSettings = _update(session, FeeSettings, row_id, values)
    return row


# --- heads ----------------------------------------------------------------------------------------


def list_heads(session: Session) -> list[FeeHead]:
    return list(session.scalars(select(FeeHead).order_by(FeeHead.sort_order, FeeHead.code)))


def heads_by_id(
    session: Session, ids: Collection[uuid.UUID] | None = None
) -> dict[uuid.UUID, FeeHead]:
    stmt = select(FeeHead)
    if ids is not None:
        if not ids:
            return {}
        stmt = stmt.where(FeeHead.id.in_(list(ids)))
    return {h.id: h for h in session.scalars(stmt)}


def get_head(session: Session, head_id: uuid.UUID, *, lock: bool = False) -> FeeHead | None:
    row: FeeHead | None = _get(session, FeeHead, head_id, lock=lock)
    return row


def count_heads(session: Session) -> int:
    value: int = session.execute(select(func.count()).select_from(FeeHead)).scalar_one()
    return value


def insert_head(session: Session, **values: Any) -> FeeHead:
    return session.scalars(insert(FeeHead).values(**values).returning(FeeHead)).one()


def update_head(session: Session, head_id: uuid.UUID, values: Mapping[str, Any]) -> FeeHead:
    row: FeeHead = _update(session, FeeHead, head_id, values)
    return row


# --- plans ----------------------------------------------------------------------------------------


def list_plans(
    session: Session,
    *,
    academic_year_id: uuid.UUID | None = None,
    class_id: uuid.UUID | None = None,
    status: str | None = None,
) -> list[FeePlan]:
    stmt = select(FeePlan)
    if academic_year_id is not None:
        stmt = stmt.where(FeePlan.academic_year_id == academic_year_id)
    if class_id is not None:
        stmt = stmt.where(FeePlan.class_id == class_id)
    if status is not None:
        stmt = stmt.where(FeePlan.status == status)
    stmt = stmt.order_by(
        FeePlan.academic_year_id, FeePlan.class_id, FeePlan.category_code, FeePlan.plan_version
    )
    return list(session.scalars(stmt))


def plans_by_id(session: Session, ids: Collection[uuid.UUID]) -> dict[uuid.UUID, FeePlan]:
    if not ids:
        return {}
    return {p.id: p for p in session.scalars(select(FeePlan).where(FeePlan.id.in_(list(ids))))}


def get_plan(session: Session, plan_id: uuid.UUID, *, lock: bool = False) -> FeePlan | None:
    row: FeePlan | None = _get(session, FeePlan, plan_id, lock=lock)
    return row


def latest_plan_version(
    session: Session, academic_year_id: uuid.UUID, class_id: uuid.UUID, category: str | None
) -> int:
    stmt = select(func.max(FeePlan.plan_version)).where(
        FeePlan.academic_year_id == academic_year_id,
        FeePlan.class_id == class_id,
        FeePlan.category_code.is_(None) if category is None else FeePlan.category_code == category,
    )
    value: int | None = session.execute(stmt).scalar_one()
    return value or 0


def active_plan(
    session: Session,
    academic_year_id: uuid.UUID,
    class_id: uuid.UUID,
    category: str | None,
    *,
    lock: bool = False,
) -> FeePlan | None:
    stmt = select(FeePlan).where(
        FeePlan.academic_year_id == academic_year_id,
        FeePlan.class_id == class_id,
        FeePlan.status == "active",
        FeePlan.category_code.is_(None) if category is None else FeePlan.category_code == category,
    )
    if lock:
        stmt = stmt.with_for_update()
    return session.scalars(stmt).one_or_none()


def insert_plan(session: Session, **values: Any) -> FeePlan:
    return session.scalars(insert(FeePlan).values(**values).returning(FeePlan)).one()


def update_plan(session: Session, plan_id: uuid.UUID, values: Mapping[str, Any]) -> FeePlan:
    row: FeePlan = _update(session, FeePlan, plan_id, values)
    return row


def plan_lines(session: Session, plan_ids: Collection[uuid.UUID]) -> list[FeePlanLine]:
    if not plan_ids:
        return []
    stmt = (
        select(FeePlanLine)
        .where(FeePlanLine.plan_id.in_(list(plan_ids)))
        .order_by(FeePlanLine.plan_id, FeePlanLine.sort_order, FeePlanLine.id)
    )
    return list(session.scalars(stmt))


def plan_instalments(session: Session, plan_ids: Collection[uuid.UUID]) -> list[FeePlanInstalment]:
    if not plan_ids:
        return []
    stmt = (
        select(FeePlanInstalment)
        .where(FeePlanInstalment.plan_id.in_(list(plan_ids)))
        .order_by(FeePlanInstalment.plan_id, FeePlanInstalment.instalment_no)
    )
    return list(session.scalars(stmt))


def replace_lines(session: Session, plan_id: uuid.UUID, rows: Sequence[Mapping[str, Any]]) -> None:
    session.execute(delete(FeePlanLine).where(FeePlanLine.plan_id == plan_id))
    if rows:
        session.execute(insert(FeePlanLine), [dict(r) for r in rows])


def replace_instalments(
    session: Session, plan_id: uuid.UUID, rows: Sequence[Mapping[str, Any]]
) -> None:
    session.execute(delete(FeePlanInstalment).where(FeePlanInstalment.plan_id == plan_id))
    if rows:
        session.execute(insert(FeePlanInstalment), [dict(r) for r in rows])


def account_counts(session: Session, plan_ids: Collection[uuid.UUID]) -> dict[uuid.UUID, int]:
    if not plan_ids:
        return {}
    stmt = (
        select(StudentFeeAccount.plan_id, func.count())
        .where(StudentFeeAccount.plan_id.in_(list(plan_ids)))
        .group_by(StudentFeeAccount.plan_id)
    )
    return {row[0]: int(row[1]) for row in session.execute(stmt)}


# --- accounts -------------------------------------------------------------------------------------


def get_account(
    session: Session, account_id: uuid.UUID, *, lock: bool = False
) -> StudentFeeAccount | None:
    row: StudentFeeAccount | None = _get(session, StudentFeeAccount, account_id, lock=lock)
    return row


def accounts_by_id(
    session: Session, ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, StudentFeeAccount]:
    if not ids:
        return {}
    stmt = select(StudentFeeAccount).where(StudentFeeAccount.id.in_(list(ids)))
    return {a.id: a for a in session.scalars(stmt)}


def accounts_for_year(
    session: Session,
    academic_year_id: uuid.UUID,
    student_ids: Collection[uuid.UUID] | None = None,
) -> list[StudentFeeAccount]:
    stmt = select(StudentFeeAccount).where(StudentFeeAccount.academic_year_id == academic_year_id)
    if student_ids is not None:
        if not student_ids:
            return []
        stmt = stmt.where(StudentFeeAccount.student_id.in_(list(student_ids)))
    return list(session.scalars(stmt.order_by(StudentFeeAccount.id)))


def accounts_of_students(
    session: Session, student_ids: Collection[uuid.UUID]
) -> list[StudentFeeAccount]:
    if not student_ids:
        return []
    stmt = select(StudentFeeAccount).where(StudentFeeAccount.student_id.in_(list(student_ids)))
    return list(session.scalars(stmt))


def accounts_on_plan(session: Session, plan_id: uuid.UUID) -> list[StudentFeeAccount]:
    stmt = select(StudentFeeAccount).where(StudentFeeAccount.plan_id == plan_id)
    return list(session.scalars(stmt.order_by(StudentFeeAccount.id)))


def insert_account(session: Session, **values: Any) -> StudentFeeAccount:
    return session.scalars(
        insert(StudentFeeAccount).values(**values).returning(StudentFeeAccount)
    ).one()


def update_account(
    session: Session, account_id: uuid.UUID, values: Mapping[str, Any]
) -> StudentFeeAccount:
    row: StudentFeeAccount = _update(session, StudentFeeAccount, account_id, values)
    return row


# --- ledger ---------------------------------------------------------------------------------------


def insert_entries(session: Session, rows: Sequence[Mapping[str, Any]]) -> int:
    """Insert ledger entries; charges and late fees already posted are skipped (their unique
    indexes), so posting twice adds nothing. Returns how many were inserted."""
    if not rows:
        return 0
    stmt = pg_insert(LedgerEntry).values([dict(r) for r in rows]).on_conflict_do_nothing()
    result = session.execute(stmt.returning(LedgerEntry.id))
    return len(result.all())


@dataclass(frozen=True, slots=True)
class BucketSum:
    account_id: uuid.UUID
    instalment_no: int
    head_id: uuid.UUID | None
    entry_kind: str
    amount: Decimal


def bucket_sums(session: Session, account_ids: Collection[uuid.UUID]) -> list[BucketSum]:
    """Sum of entries per (account, instalment, head, kind): the dues of each line."""
    if not account_ids:
        return []
    stmt = (
        select(
            LedgerEntry.account_id,
            LedgerEntry.instalment_no,
            LedgerEntry.head_id,
            LedgerEntry.entry_kind,
            func.sum(LedgerEntry.amount),
        )
        .where(LedgerEntry.account_id.in_(list(account_ids)))
        .group_by(
            LedgerEntry.account_id,
            LedgerEntry.instalment_no,
            LedgerEntry.head_id,
            LedgerEntry.entry_kind,
        )
    )
    return [
        BucketSum(r[0], int(r[1]), r[2], str(r[3]), Decimal(r[4])) for r in session.execute(stmt)
    ]


def entries_of(session: Session, account_id: uuid.UUID) -> list[LedgerEntry]:
    stmt = (
        select(LedgerEntry)
        .where(LedgerEntry.account_id == account_id)
        .order_by(LedgerEntry.posted_at, LedgerEntry.id)
    )
    return list(session.scalars(stmt))


def last_payment_dates(
    session: Session, account_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, dt.date]:
    if not account_ids:
        return {}
    stmt = (
        select(LedgerEntry.account_id, func.max(LedgerEntry.entry_date))
        .where(
            LedgerEntry.account_id.in_(list(account_ids)),
            LedgerEntry.entry_kind == "payment",
        )
        .group_by(LedgerEntry.account_id)
    )
    return {r[0]: r[1] for r in session.execute(stmt)}


def late_fee_keys(session: Session, account_id: uuid.UUID) -> set[str]:
    stmt = select(LedgerEntry.late_fee_key).where(
        LedgerEntry.account_id == account_id, LedgerEntry.late_fee_key.is_not(None)
    )
    return {str(k) for k in session.scalars(stmt)}


# --- requests -------------------------------------------------------------------------------------


def insert_concession(session: Session, **values: Any) -> Concession:
    return session.scalars(insert(Concession).values(**values).returning(Concession)).one()


def get_concession(session: Session, row_id: uuid.UUID, *, lock: bool = False) -> Concession | None:
    row: Concession | None = _get(session, Concession, row_id, lock=lock)
    return row


def update_concession(session: Session, row_id: uuid.UUID, values: Mapping[str, Any]) -> Concession:
    row: Concession = _update(session, Concession, row_id, values)
    return row


def concessions_of(session: Session, account_id: uuid.UUID) -> list[Concession]:
    stmt = (
        select(Concession)
        .where(Concession.account_id == account_id)
        .order_by(Concession.requested_at.desc(), Concession.id.desc())
    )
    return list(session.scalars(stmt))


def insert_adjustment(session: Session, **values: Any) -> Adjustment:
    return session.scalars(insert(Adjustment).values(**values).returning(Adjustment)).one()


def insert_adjustments_skip_existing(session: Session, rows: Sequence[Mapping[str, Any]]) -> int:
    if not rows:
        return 0
    stmt = pg_insert(Adjustment).values([dict(r) for r in rows]).on_conflict_do_nothing()
    return len(session.execute(stmt.returning(Adjustment.id)).all())


def get_adjustment(session: Session, row_id: uuid.UUID, *, lock: bool = False) -> Adjustment | None:
    row: Adjustment | None = _get(session, Adjustment, row_id, lock=lock)
    return row


def update_adjustment(session: Session, row_id: uuid.UUID, values: Mapping[str, Any]) -> Adjustment:
    row: Adjustment = _update(session, Adjustment, row_id, values)
    return row


def adjustments_of(session: Session, account_id: uuid.UUID) -> list[Adjustment]:
    stmt = (
        select(Adjustment)
        .where(Adjustment.account_id == account_id)
        .order_by(Adjustment.requested_at.desc(), Adjustment.id.desc())
    )
    return list(session.scalars(stmt))


def insert_cancellation(session: Session, **values: Any) -> ReceiptCancellation:
    return session.scalars(
        insert(ReceiptCancellation).values(**values).returning(ReceiptCancellation)
    ).one()


def get_cancellation(
    session: Session, row_id: uuid.UUID, *, lock: bool = False
) -> ReceiptCancellation | None:
    row: ReceiptCancellation | None = _get(session, ReceiptCancellation, row_id, lock=lock)
    return row


def update_cancellation(
    session: Session, row_id: uuid.UUID, values: Mapping[str, Any]
) -> ReceiptCancellation:
    row: ReceiptCancellation = _update(session, ReceiptCancellation, row_id, values)
    return row


def cancellations_of(
    session: Session, receipt_ids: Collection[uuid.UUID]
) -> list[ReceiptCancellation]:
    if not receipt_ids:
        return []
    stmt = (
        select(ReceiptCancellation)
        .where(ReceiptCancellation.receipt_id.in_(list(receipt_ids)))
        .order_by(ReceiptCancellation.requested_at, ReceiptCancellation.id)
    )
    return list(session.scalars(stmt))


def insert_refund(session: Session, **values: Any) -> Refund:
    return session.scalars(insert(Refund).values(**values).returning(Refund)).one()


def get_refund(session: Session, row_id: uuid.UUID, *, lock: bool = False) -> Refund | None:
    row: Refund | None = _get(session, Refund, row_id, lock=lock)
    return row


def update_refund(session: Session, row_id: uuid.UUID, values: Mapping[str, Any]) -> Refund:
    row: Refund = _update(session, Refund, row_id, values)
    return row


def refunds_of(session: Session, receipt_ids: Collection[uuid.UUID]) -> list[Refund]:
    if not receipt_ids:
        return []
    stmt = (
        select(Refund)
        .where(Refund.receipt_id.in_(list(receipt_ids)))
        .order_by(Refund.requested_at, Refund.id)
    )
    return list(session.scalars(stmt))


def pending_requests(session: Session, limit: int) -> dict[str, list[Any]]:
    """Every pending request of the school, oldest first (the approvals inbox)."""
    out: dict[str, list[Any]] = {}
    for kind, model in (
        ("concession", Concession),
        ("adjustment", Adjustment),
        ("cancellation", ReceiptCancellation),
        ("refund", Refund),
    ):
        stmt = (
            select(model)
            .where(model.status == "pending")
            .order_by(model.requested_at, model.id)
            .limit(limit)
        )
        out[kind] = list(session.scalars(stmt))
    return out


def count_pending_for_account(session: Session, account_id: uuid.UUID) -> int:
    total = 0
    for model in (Concession, Adjustment):
        total += int(
            session.execute(
                select(func.count())
                .select_from(model)
                .where(model.account_id == account_id, model.status == "pending")
            ).scalar_one()
        )
    total += int(
        session.execute(
            select(func.count())
            .select_from(Refund)
            .where(Refund.account_id == account_id, Refund.status == "pending")
        ).scalar_one()
    )
    total += int(
        session.execute(
            select(func.count())
            .select_from(ReceiptCancellation)
            .join(
                Receipt,
                and_(
                    Receipt.tenant_id == ReceiptCancellation.tenant_id,
                    Receipt.id == ReceiptCancellation.receipt_id,
                ),
            )
            .where(Receipt.account_id == account_id, ReceiptCancellation.status == "pending")
        ).scalar_one()
    )
    return total


# --- receipts -------------------------------------------------------------------------------------


def allocate_number(session: Session, series: str, financial_year: str) -> int:
    """The next number of (school, series, financial year), in the caller's transaction.

    ``INSERT ... ON CONFLICT DO UPDATE`` locks the counter row until commit: a concurrent issue
    waits and gets the following number; a rollback releases this one (gap-free, FR-FEE-010)."""
    stmt = text(
        """
        INSERT INTO fin.receipt_counters (id, tenant_id, series, financial_year, last_no)
        VALUES (:id, core.current_tenant(), :s, :fy, 1)
        ON CONFLICT (tenant_id, series, financial_year)
        DO UPDATE SET last_no = fin.receipt_counters.last_no + 1, updated_at = now()
        RETURNING last_no
        """
    )
    value: int = session.execute(
        stmt, {"id": new_id(), "s": series, "fy": financial_year}
    ).scalar_one()
    return value


def insert_receipt(session: Session, **values: Any) -> Receipt:
    return session.scalars(insert(Receipt).values(**values).returning(Receipt)).one()


def insert_allocations(session: Session, rows: Sequence[Mapping[str, Any]]) -> None:
    session.execute(insert(ReceiptAllocation), [dict(r) for r in rows])


def get_receipt(session: Session, receipt_id: uuid.UUID, *, lock: bool = False) -> Receipt | None:
    row: Receipt | None = _get(session, Receipt, receipt_id, lock=lock)
    return row


def receipts_by_id(session: Session, ids: Collection[uuid.UUID]) -> dict[uuid.UUID, Receipt]:
    if not ids:
        return {}
    return {r.id: r for r in session.scalars(select(Receipt).where(Receipt.id.in_(list(ids))))}


def update_receipt(session: Session, receipt_id: uuid.UUID, values: Mapping[str, Any]) -> Receipt:
    row: Receipt = _update(session, Receipt, receipt_id, values)
    return row


def issued_receipt_with_utr(session: Session, utr: str) -> Receipt | None:
    stmt = select(Receipt).where(
        Receipt.mode == "upi_qr", Receipt.status == "issued", Receipt.utr == utr
    )
    return session.scalars(stmt).first()


def allocations_of(session: Session, receipt_ids: Collection[uuid.UUID]) -> list[ReceiptAllocation]:
    if not receipt_ids:
        return []
    stmt = (
        select(ReceiptAllocation)
        .where(ReceiptAllocation.receipt_id.in_(list(receipt_ids)))
        .order_by(ReceiptAllocation.receipt_id, ReceiptAllocation.instalment_no)
    )
    return list(session.scalars(stmt))


def list_receipts(
    session: Session,
    *,
    receipt_date: dt.date | None = None,
    mode: str | None = None,
    collected_by: uuid.UUID | None = None,
    status: str | None = None,
    account_id: uuid.UUID | None = None,
    after: tuple[dt.datetime, uuid.UUID] | None = None,
    limit: int = 50,
) -> list[Receipt]:
    stmt = select(Receipt)
    if receipt_date is not None:
        stmt = stmt.where(Receipt.receipt_date == receipt_date)
    if mode is not None:
        stmt = stmt.where(Receipt.mode == mode)
    if collected_by is not None:
        stmt = stmt.where(Receipt.collected_by == collected_by)
    if status is not None:
        stmt = stmt.where(Receipt.status == status)
    if account_id is not None:
        stmt = stmt.where(Receipt.account_id == account_id)
    if after is not None:
        at, last = after
        stmt = stmt.where(
            or_(Receipt.issued_at < at, and_(Receipt.issued_at == at, Receipt.id < last))
        )
    stmt = stmt.order_by(Receipt.issued_at.desc(), Receipt.id.desc()).limit(limit)
    return list(session.scalars(stmt))


def receipts_on(session: Session, day: dt.date) -> list[Receipt]:
    stmt = select(Receipt).where(Receipt.receipt_date == day).order_by(Receipt.receipt_no)
    return list(session.scalars(stmt))


def cancellations_on(session: Session, day: dt.date) -> list[ReceiptCancellation]:
    stmt = (
        select(ReceiptCancellation)
        .where(ReceiptCancellation.cancelled_on == day, ReceiptCancellation.status == "approved")
        .order_by(ReceiptCancellation.decided_at)
    )
    return list(session.scalars(stmt))


def refunds_on(session: Session, day: dt.date) -> list[Refund]:
    stmt = (
        select(Refund)
        .where(Refund.voucher_date == day, Refund.status == "processed")
        .order_by(Refund.voucher_no)
    )
    return list(session.scalars(stmt))


# --- day close ------------------------------------------------------------------------------------


def get_day_close(session: Session, day: dt.date) -> DayClose | None:
    return session.scalars(select(DayClose).where(DayClose.close_date == day)).one_or_none()


def insert_day_close(session: Session, **values: Any) -> DayClose:
    return session.scalars(insert(DayClose).values(**values).returning(DayClose)).one()


# --- export ---------------------------------------------------------------------------------------


def export_tables(session: Session) -> list[RecordTable]:
    """Every fees table of the current school for its full data export (FR-FEE-027). No table of
    this wave holds secrets or ciphertext; content hashes are hex."""
    out: list[RecordTable] = []
    for model in ALL_MODELS:
        table = model.__table__
        if not isinstance(table, Table):  # pragma: no cover - declarative tables
            raise TypeError(model.__name__)
        order: tuple[str, ...] | None = ("created_at", "id") if "created_at" in table.c else None
        if model is LedgerEntry:
            order = ("posted_at", "id")
        if model is DayClose:
            order = ("close_date",)
        out.append(dump_table(session, table, name=f"fees_{table.name}", order_by=order))
    return out


# --- data-principal report (R3-1A; FR-DPDP-009) ---------------------------------------------------


def receipts_of_accounts(
    session: Session, account_ids: Collection[uuid.UUID], *, limit: int
) -> tuple[list[Receipt], int]:
    """The receipts of ``account_ids``, newest first (at most ``limit``), and how many there are."""
    if not account_ids:
        return [], 0
    where = Receipt.account_id.in_(list(account_ids))
    rows = list(
        session.scalars(
            select(Receipt)
            .where(where)
            .order_by(Receipt.issued_at.desc(), Receipt.id.desc())
            .limit(limit)
        )
    )
    total = session.scalar(select(func.count()).select_from(Receipt).where(where))
    return rows, int(total or 0)


def receipt_ids_of_accounts(
    session: Session, account_ids: Collection[uuid.UUID]
) -> list[uuid.UUID]:
    if not account_ids:
        return []
    stmt = select(Receipt.id).where(Receipt.account_id.in_(list(account_ids)))
    return list(session.scalars(stmt))


def concessions_of_accounts(
    session: Session, account_ids: Collection[uuid.UUID]
) -> list[Concession]:
    if not account_ids:
        return []
    stmt = (
        select(Concession)
        .where(Concession.account_id.in_(list(account_ids)))
        .order_by(Concession.requested_at.desc(), Concession.id.desc())
    )
    return list(session.scalars(stmt))


def refunds_of_accounts(session: Session, account_ids: Collection[uuid.UUID]) -> list[Refund]:
    if not account_ids:
        return []
    stmt = (
        select(Refund)
        .where(Refund.account_id.in_(list(account_ids)))
        .order_by(Refund.requested_at.desc(), Refund.id.desc())
    )
    return list(session.scalars(stmt))


def ids(rows: Iterable[Any]) -> list[uuid.UUID]:
    return [r.id for r in rows]


__all__ = [
    "BucketSum",
    "account_counts",
    "accounts_by_id",
    "accounts_for_year",
    "accounts_of_students",
    "accounts_on_plan",
    "active_plan",
    "adjustments_of",
    "allocate_number",
    "allocations_of",
    "bucket_sums",
    "cancellations_of",
    "cancellations_on",
    "concessions_of",
    "concessions_of_accounts",
    "count_heads",
    "count_pending_for_account",
    "current_tenant_id",
    "entries_of",
    "export_tables",
    "get_account",
    "get_adjustment",
    "get_cancellation",
    "get_concession",
    "get_day_close",
    "get_head",
    "get_plan",
    "get_receipt",
    "get_refund",
    "get_settings",
    "heads_by_id",
    "insert_account",
    "insert_adjustment",
    "insert_adjustments_skip_existing",
    "insert_allocations",
    "insert_cancellation",
    "insert_concession",
    "insert_day_close",
    "insert_entries",
    "insert_head",
    "insert_plan",
    "insert_receipt",
    "insert_refund",
    "insert_settings",
    "issued_receipt_with_utr",
    "last_payment_dates",
    "late_fee_keys",
    "latest_plan_version",
    "list_heads",
    "list_plans",
    "list_receipts",
    "lock_day",
    "lock_utr",
    "now",
    "pending_requests",
    "plan_instalments",
    "plan_lines",
    "plans_by_id",
    "receipt_ids_of_accounts",
    "receipts_by_id",
    "receipts_of_accounts",
    "receipts_on",
    "refunds_of",
    "refunds_of_accounts",
    "refunds_on",
    "replace_instalments",
    "replace_lines",
    "translate_db_error",
    "update_account",
    "update_adjustment",
    "update_cancellation",
    "update_concession",
    "update_head",
    "update_plan",
    "update_receipt",
    "update_refund",
    "update_settings",
]
