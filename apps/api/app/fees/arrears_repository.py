"""Reads for earlier-year dues, left-with-dues and write-offs (FM-01; FR-FEE-031..045).

Kept apart from ``repository.py`` so the arrears reads stay in one place; every query runs in
the caller's ``tenant_session`` (RLS) and binds its parameters.
"""

from __future__ import annotations

import uuid
from collections.abc import Collection
from decimal import Decimal
from typing import cast

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.fees.models import Adjustment, StudentFeeAccount
from app.fees.schemas import AdjustmentReasonOut


def accounts_in_years(
    session: Session,
    year_ids: Collection[uuid.UUID],
    *,
    current_year_id: uuid.UUID | None,
    student_ids: Collection[uuid.UUID] | None = None,
) -> list[StudentFeeAccount]:
    """Accounts of ``year_ids``: every account of ``current_year_id`` (as the dues list always
    read) and the **open** accounts of the other years (a closed earlier account owes nothing)."""
    if not year_ids or (student_ids is not None and not student_ids):
        return []
    stmt = select(StudentFeeAccount).where(
        StudentFeeAccount.academic_year_id.in_(list(year_ids)),
        (StudentFeeAccount.status == "open")
        | (StudentFeeAccount.academic_year_id == current_year_id),
    )
    if student_ids is not None:
        stmt = stmt.where(StudentFeeAccount.student_id.in_(list(student_ids)))
    return list(session.scalars(stmt.order_by(StudentFeeAccount.id)))


def write_off_sums(
    session: Session, account_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, Decimal]:
    """Approved write-offs per account, as a positive amount (FR-FEE-042)."""
    if not account_ids:
        return {}
    stmt = (
        select(Adjustment.account_id, func.sum(Adjustment.amount))
        .where(
            Adjustment.account_id.in_(list(account_ids)),
            Adjustment.reason_code == "write_off",
            Adjustment.status == "approved",
        )
        .group_by(Adjustment.account_id)
    )
    return {r[0]: -Decimal(r[1]) for r in session.execute(stmt)}


def adjustment_reasons(
    session: Session, adjustment_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, AdjustmentReasonOut]:
    if not adjustment_ids:
        return {}
    stmt = select(Adjustment.id, Adjustment.reason_code).where(
        Adjustment.id.in_(list(adjustment_ids))
    )
    return {r[0]: cast(AdjustmentReasonOut, r[1]) for r in session.execute(stmt)}


__all__ = ["accounts_in_years", "adjustment_reasons", "write_off_sums"]
