"""Earlier-year dues routes (docs/09 "Fees"; FM-01; FR-FEE-036..038; ADR-0042 follow-up D84).

Every route declares its guard; fee permissions are school-wide only (FR-FEE-024): the
:func:`require_any` read is re-checked school-wide by the service. While fees are off for the
school every route answers 404 ``module_hidden``. No personal data in URLs: ids and amounts.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response

from app.authz.context import UserContext
from app.authz.dependencies import TenantDB, require, require_any
from app.fees import service
from app.fees.arrears_schemas import LeftWithDuesOut, StudentYearsOut

router = APIRouter(prefix="/api/v1", tags=["fees"])

R = service
Viewer = Annotated[UserContext, Depends(require_any(R.COLLECT, R.READ))]
FinanceReader = Annotated[UserContext, Depends(require(R.READ, scope="school"))]
Reporter = Annotated[UserContext, Depends(require(R.REPORT, scope="school", step_up=True))]

_CSV: dict[int | str, dict[str, Any]] = {
    200: {"content": {"text/csv": {"schema": {"type": "string"}}}, "description": "CSV file"}
}


@router.get("/fees/students/{student_id}/years", response_model=StudentYearsOut)
def student_fee_years(
    ctx: Viewer,
    db: TenantDB,
    student_id: uuid.UUID,
    split_amount: Annotated[Decimal | None, Query(max_digits=14, decimal_places=2)] = None,
) -> StudentYearsOut:
    """The student's open fee accounts, earlier years first (``fee.collect`` or
    ``finance.read``). With ``split_amount`` (the clerk chose "oldest first across years"): how
    much to take on each account, oldest year first; nothing is written, each part is its own
    receipt. 422 ``split_amount`` (``not_positive``, ``above_outstanding``)."""
    return service.student_years(db, ctx, student_id, split_amount=split_amount)


@router.get("/fees/reports/left-with-dues", response_model=LeftWithDuesOut)
def fee_left_with_dues(ctx: FinanceReader, db: TenantDB) -> LeftWithDuesOut:
    """Students who left (withdrawn, TC issued or graduated) with an open balance
    (``finance.read``). For management follow-up only: never printed on a TC and never
    blocking one."""
    return service.left_with_dues(db, ctx)


@router.get("/fees/reports/left-with-dues.csv", response_class=Response, responses=_CSV)
def fee_left_with_dues_csv(ctx: Reporter, db: TenantDB) -> Response:
    """The left-with-dues report as CSV (``fee.report``, step-up). Audited like an export."""
    filename, text = service.left_with_dues_csv(db, ctx)
    return Response(
        content="﻿" + text,
        media_type="text/csv; charset=utf-8",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Content-Type-Options": "nosniff",
        },
    )
