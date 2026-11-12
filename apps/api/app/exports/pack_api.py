"""Enrolment statistics (FO-06; FR-EXP-009..019) and scheme verification pack (FO-11;
FR-EXP-020..030) routes (docs/09 Exports).

Every route declares its guard. The enrolment statistics count the whole school:
``export.portal`` school-wide (the service also needs school-wide ``student.read_basic``); the
print and the XLSX need a recent MFA sign-in (``step_up``). The scheme attendance list reads one
section with ``attendance.read`` in scope (404 outside it); its print and XLSX ask for the recent
MFA sign-in in the service, because ``attendance.read`` is not a step-up permission in the
catalog (the print jobs' rule, ADR-0021). Bulk certificates need ``certificate.issue``; each
certificate still checks the student's reach. Nothing here changes a student record.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import HTMLResponse

from app.authz.context import UserContext
from app.authz.dependencies import TenantDB, require, require_any
from app.authz.http import IdempotencyDep
from app.exports import enrolment_stats as stats
from app.exports import scheme_packs as schemes
from app.exports.pack_pages import STYLE_CSP
from app.exports.pack_schemas import (
    EnrolmentStatisticsOut,
    SchemeAttendanceListOut,
    SchemeCertificatesIn,
    SchemeCertificatesOut,
    SchemeKey,
    SchemePackConfigOut,
)
from app.exports.tables import XLSX_MIME

router = APIRouter(prefix="/api/v1", tags=["exports"])

StatsReader = Annotated[UserContext, Depends(require(stats.PORTAL, scope="school"))]
StatsPrinter = Annotated[UserContext, Depends(require(stats.PORTAL, scope="school", step_up=True))]
SchemeReader = Annotated[
    UserContext,
    Depends(require_any(schemes.ATTENDANCE_READ, schemes.CERTIFICATE_ISSUE)),
]
AttendanceReader = Annotated[UserContext, Depends(require(schemes.ATTENDANCE_READ))]
CertificateIssuer = Annotated[UserContext, Depends(require(schemes.CERTIFICATE_ISSUE))]

YearQuery = Annotated[
    uuid.UUID | None, Query(description="Academic year (default: the current one)")
]
AsOfQuery = Annotated[
    dt.date | None,
    Query(description="Count on this day (default: the configured reference date, 30 September)"),
]
CheckQuery = Annotated[
    bool, Query(description="true: only check that the page can be printed now; 204")
]
SchemeQuery = Annotated[SchemeKey, Query(description="Scheme key (GET /scheme-packs)")]
SectionQuery = Annotated[uuid.UUID, Query(description="Section of the current academic year")]
FromQuery = Annotated[dt.date, Query(description="First day of the period")]
ToQuery = Annotated[dt.date, Query(description="Last day of the period (up to today)")]

_PAGE_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": STYLE_CSP,
    "X-Content-Type-Options": "nosniff",
}
_PAGE_RESPONSES: dict[int | str, dict[str, object]] = {
    200: {"content": {"text/html": {}}, "description": "Print-ready A4 page"},
    204: {"description": "With check=true: the page can be printed now (nothing audited)"},
}
_XLSX_RESPONSES: dict[int | str, dict[str, object]] = {
    200: {"content": {XLSX_MIME: {}}, "description": "XLSX workbook"},
}


def _page(page: str | None, name: str) -> Response:
    if page is None:
        return Response(status_code=204, headers={"Cache-Control": "no-store"})
    return HTMLResponse(
        page, headers={**_PAGE_HEADERS, "Content-Disposition": f'inline; filename="{name}"'}
    )


def _xlsx(data: bytes, name: str) -> Response:
    return Response(
        data,
        media_type=XLSX_MIME,
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f'attachment; filename="{name}"',
        },
    )


# --- FO-06 enrolment statistics ---------------------------------------------------------------


@router.get("/reports/enrolment-statistics", response_model=EnrolmentStatisticsOut)
def get_enrolment_statistics(
    ctx: StatsReader, db: TenantDB, academic_year_id: YearQuery = None, as_of: AsOfQuery = None
) -> EnrolmentStatisticsOut:
    """Enrolment counts per class on one day of an academic year, in the UDISE+ table shapes
    (permission ``export.portal`` school-wide, and school-wide ``student.read_basic``, else 403
    ``school_wide_read_required``): by gender, by age (completed years on the day), repeaters,
    admissions and leavers; with school-wide ``student.read_sensitive`` also by social category,
    minority group and CWSN (else ``restricted_hidden``). Counts only, never names. ``gaps``
    counts students without a value. ``verified`` is false: the shapes are not checked against
    the official form. 422 ``as_of_out_of_year``. No RTE count (the RTE register is not
    built)."""
    return stats.enrolment_statistics(db, ctx, academic_year_id=academic_year_id, as_of=as_of)


@router.get(
    "/reports/enrolment-statistics/print",
    response_class=HTMLResponse,
    responses=_PAGE_RESPONSES,
)
def print_enrolment_statistics(
    *,
    ctx: StatsPrinter,
    db: TenantDB,
    academic_year_id: YearQuery = None,
    as_of: AsOfQuery = None,
    check: CheckQuery = False,
) -> Response:
    """The A4 enrolment statistics sheet with signature lines (``export.portal`` school-wide
    with a recent MFA sign-in, 428 ``step_up_required``). Audited as an export; with
    ``check=true`` 204 only, not audited."""
    page = stats.print_page(db, ctx, academic_year_id=academic_year_id, as_of=as_of, check=check)
    return _page(page, "enrolment-statistics.html")


@router.get(
    "/reports/enrolment-statistics/xlsx",
    response_class=Response,
    responses=_XLSX_RESPONSES,
)
def download_enrolment_statistics(
    ctx: StatsPrinter, db: TenantDB, academic_year_id: YearQuery = None, as_of: AsOfQuery = None
) -> Response:
    """The enrolment statistics as XLSX, one worksheet per table (``export.portal``
    school-wide with a recent MFA sign-in, 428). Audited as an export download."""
    data, name = stats.xlsx_file(db, ctx, academic_year_id=academic_year_id, as_of=as_of)
    return _xlsx(data, name)


# --- FO-11 scheme verification pack -----------------------------------------------------------


@router.get("/scheme-packs", response_model=SchemePackConfigOut)
def get_scheme_packs(ctx: SchemeReader) -> SchemePackConfigOut:
    """The schemes the pack prints for and how the attendance percentage is counted
    (``attendance.read`` or ``certificate.issue``). ``verified`` is false: scheme rules change
    every year and SchoolOS makes no eligibility decision."""
    return schemes.scheme_config()


@router.get("/scheme-packs/attendance", response_model=SchemeAttendanceListOut)
def get_scheme_attendance(
    *,
    ctx: AttendanceReader,
    db: TenantDB,
    scheme_key: SchemeQuery,
    section_id: SectionQuery,
    from_date: FromQuery,
    to_date: ToQuery,
) -> SchemeAttendanceListOut:
    """One section's attendance percentages over a period for a scheme's verification
    (``attendance.read`` in the section's scope, 404 outside it): roll and admission numbers,
    names and the percentage only, no eligibility. Errors: 422 ``unknown_scheme``,
    ``invalid_period``, ``date_out_of_range``, ``future_date``."""
    return schemes.attendance_list(
        db, ctx, scheme_key=scheme_key, section_id=section_id, from_date=from_date, to_date=to_date
    )


@router.get(
    "/scheme-packs/attendance/print",
    response_class=HTMLResponse,
    responses=_PAGE_RESPONSES,
)
def print_scheme_attendance(
    *,
    ctx: AttendanceReader,
    db: TenantDB,
    scheme_key: SchemeQuery,
    section_id: SectionQuery,
    from_date: FromQuery,
    to_date: ToQuery,
    check: CheckQuery = False,
) -> Response:
    """The A4 attendance list for the head's signature (``attendance.read`` in scope, recent
    MFA sign-in, 428 ``step_up_required``). Audited as an export; with ``check=true`` 204
    only, not audited."""
    page = schemes.print_attendance(
        db,
        ctx,
        scheme_key=scheme_key,
        section_id=section_id,
        from_date=from_date,
        to_date=to_date,
        check=check,
    )
    return _page(page, "scheme-attendance-list.html")


@router.get(
    "/scheme-packs/attendance/xlsx",
    response_class=Response,
    responses=_XLSX_RESPONSES,
)
def download_scheme_attendance(
    *,
    ctx: AttendanceReader,
    db: TenantDB,
    scheme_key: SchemeQuery,
    section_id: SectionQuery,
    from_date: FromQuery,
    to_date: ToQuery,
) -> Response:
    """The attendance list as XLSX (``attendance.read`` in scope, recent MFA sign-in, 428).
    Audited as an export download."""
    data, name = schemes.attendance_xlsx(
        db, ctx, scheme_key=scheme_key, section_id=section_id, from_date=from_date, to_date=to_date
    )
    return _xlsx(data, name)


@router.post("/scheme-packs/certificates", response_model=SchemeCertificatesOut, status_code=201)
def issue_scheme_certificates(
    ctx: CertificateIssuer, db: TenantDB, body: SchemeCertificatesIn, idem: IdempotencyDep
) -> Response:
    """Issue one study or bonafide certificate per chosen student for a scheme's verification
    (``certificate.issue``; each student in your reach, else that student is ``refused`` with
    ``not_found``). Each certificate gets its own serial and register line, approved per the
    type's rule; a refused student does not stop the others. Errors: 422 ``unknown_scheme``,
    ``too_many_students``. Accepts ``Idempotency-Key``."""
    return idem.run(db, body, lambda: schemes.issue_certificates(db, ctx, body))
