"""Government scheme verification pack (FO-11; FR-EXP-020..FR-EXP-030; invariants 3, 5, 7, 14).

For the July-August scheme verifications (BRD §2.1) the office hands the department two things
from its own records:

1. **A class-wise attendance list** for a chosen period (FR-EXP-020..023): names, admission
   numbers and the attendance percentage only, signed by the head. Read through
   ``app.academics.service.attendance_period`` with the caller's ``attendance.read`` reach (404
   outside it). The print (A4 HTML) and the XLSX need a recent MFA sign-in (428) and are
   audited as exports (``export.scheme_attendance.printed`` / ``.downloaded``).
2. **Bulk study or bonafide certificates** for chosen students with one purpose (FR-EXP-024..026):
   each goes through ``app.certificates.service.request_certificate`` (``certificate.issue``,
   the caller's reach, the type's own rules), so each gets its own serial, register line,
   verification code and audit events. A student who cannot be issued one (outside reach,
   blocked record) is reported and the others still issue (one savepoint per student). The
   batch is audited as ``export.scheme_certificates.issued`` with counts.

No eligibility decision is made anywhere (FR-EXP-027): no threshold, no "eligible" column; the
schemes and the counting rule live in ``schemes.yaml`` with ``verified: false``. SchoolOS sends
nothing to any government system. Purpose limit (invariant 14): the school's own lists for a
state child-welfare scheme it takes part in; the privacy review note is in docs/08.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Final, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.academics import service as academics
from app.audit import service as audit
from app.authz.context import UserContext
from app.certificates import service as certificates
from app.certificates.schemas import CertificateRequest
from app.core.errors import DomainError, StepUpRequired, ValidationFailed
from app.core.logging import get_context, get_logger
from app.exports import pack_pages as pages
from app.exports.config import load_config
from app.exports.pack_schemas import (
    SchemeAttendanceListOut,
    SchemeAttendanceStudentOut,
    SchemeCertificateResultOut,
    SchemeCertificatesIn,
    SchemeCertificatesOut,
    SchemeOut,
    SchemePackConfigOut,
)
from app.exports.tables import Table, write_xlsx
from app.identity.principal import STEP_UP_MAX_AGE
from app.tenancy import service as tenancy

log = get_logger(__name__)

CONFIG_PATH: Final = Path(__file__).with_name("schemes.yaml")
ATTENDANCE_READ: Final = "attendance.read"
CERTIFICATE_ISSUE: Final = "certificate.issue"
CertificateKind = Literal["study", "bonafide"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AttendanceRule(_Model):
    present_statuses: tuple[str, ...] = Field(min_length=1)
    excluded_statuses: tuple[str, ...] = ()


class Scheme(_Model):
    label: str = Field(min_length=1, max_length=120)
    certificate_type: CertificateKind
    purpose_note: str = Field(min_length=1, max_length=120)
    note: str = Field(min_length=1, max_length=600)
    verified: bool
    source: tuple[str, ...] = Field(min_length=1)


class SchemeConfig(_Model):
    version: int = Field(ge=1)
    verified: bool
    note: str = Field(min_length=1, max_length=600)
    attendance: AttendanceRule
    max_certificates: int = Field(ge=1, le=200)
    schemes: dict[str, Scheme] = Field(min_length=1)


@lru_cache(maxsize=1)
def load_scheme_config() -> SchemeConfig:
    data = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    return SchemeConfig.model_validate(data)


def _scheme_out(key: str, s: Scheme) -> SchemeOut:
    return SchemeOut(
        key=key,
        label=s.label,
        certificate_type=s.certificate_type,
        purpose_note=s.purpose_note,
        note=s.note,
        verified=s.verified,
        source=list(s.source),
    )


def scheme_config() -> SchemePackConfigOut:
    """The schemes and the counting rule (FR-EXP-028)."""
    cfg = load_scheme_config()
    return SchemePackConfigOut(
        verified=cfg.verified,
        note=cfg.note,
        present_statuses=list(cfg.attendance.present_statuses),
        excluded_statuses=list(cfg.attendance.excluded_statuses),
        max_certificates=cfg.max_certificates,
        schemes=[_scheme_out(k, s) for k, s in cfg.schemes.items()],
    )


def _scheme(key: str) -> tuple[SchemeConfig, Scheme]:
    cfg = load_scheme_config()
    scheme = cfg.schemes.get(key)
    if scheme is None:
        raise ValidationFailed(
            [
                {
                    "field": "scheme_key",
                    "code": "unknown_scheme",
                    "message_key": "errors.exports.unknown_scheme",
                }
            ],
            detail="Choose one of the listed schemes.",
        )
    return cfg, scheme


def _request_id() -> str | None:
    value = get_context().get("request_id")
    return value if isinstance(value, str) else None


def _require_step_up(ctx: UserContext) -> None:
    """FR-EXP-022: MFA-backed sign-in within 5 minutes (428 ``step_up_required``)."""
    if not ctx.mfa or ctx.auth_time is None:
        raise StepUpRequired()
    age = dt.datetime.now(dt.UTC) - ctx.auth_time
    if age > STEP_UP_MAX_AGE or age < -dt.timedelta(seconds=30):
        raise StepUpRequired()


# --- attendance list ------------------------------------------------------------------------------


def percent(present: int, counted: int) -> float | None:
    """Present share of the counted days, one decimal; None without counted days."""
    if counted <= 0:
        return None
    return round(present * 100 / counted, 1)


def attendance_list(
    session: Session,
    ctx: UserContext,
    *,
    scheme_key: str,
    section_id: uuid.UUID,
    from_date: dt.date,
    to_date: dt.date,
) -> SchemeAttendanceListOut:
    """One section's attendance percentages over the period (FR-EXP-020, FR-EXP-021):
    ``attendance.read`` in the section's scope (404 outside it) and ``student.read_basic``.
    Errors: 422 ``unknown_scheme``, ``invalid_period``, ``date_out_of_range``,
    ``future_date``."""
    cfg, scheme = _scheme(scheme_key)
    period = academics.attendance_period(session, ctx, section_id, from_date, to_date)
    rule = cfg.attendance
    rows: list[SchemeAttendanceStudentOut] = []
    for s in period.students:
        by = {"present": s.present, "late": s.late, "absent": s.absent, "leave": s.leave}
        counted = sum(n for st, n in by.items() if st not in rule.excluded_statuses)
        present = sum(by.get(st, 0) for st in rule.present_statuses)
        rows.append(
            SchemeAttendanceStudentOut(
                student_id=s.student.student_id,
                roll_no=s.student.roll_no,
                full_name=s.student.full_name,
                admission_no=s.student.admission_no,
                days_counted=counted,
                days_present=present,
                leave_days=s.leave,
                percent=percent(present, counted),
            )
        )
    return SchemeAttendanceListOut(
        scheme=_scheme_out(scheme_key, scheme),
        section_id=period.section.id,
        section_label=period.section.label,
        academic_year_label=period.year.label,
        from_date=period.first,
        to_date=period.last,
        students=rows,
    )


def _audit_list(session: Session, action: str, out: SchemeAttendanceListOut, fmt: str) -> None:
    audit.record(
        session,
        action=action,
        resource_type="export",
        resource_id=None,
        summary={
            "kind": "scheme_attendance_list",
            "scheme_key": out.scheme.key,
            "section_id": out.section_id,
            "from_date": out.from_date.isoformat(),
            "to_date": out.to_date.isoformat(),
            "students": len(out.students),
            "format": fmt,
            "layout_version": load_scheme_config().version,
        },
        request_id=_request_id(),
    )
    log.info("exports.scheme_attendance", action=action, resource_type="export")


def _pct(value: float | None) -> str:
    return "No marks" if value is None else f"{value:.1f}%"


def print_attendance(
    session: Session,
    ctx: UserContext,
    *,
    scheme_key: str,
    section_id: uuid.UUID,
    from_date: dt.date,
    to_date: dt.date,
    check: bool = False,
) -> str | None:
    """The A4 attendance list, signed by the head (FR-EXP-022): recent MFA sign-in (428).
    ``check``: every check runs, nothing is printed or audited (204). Audit
    ``export.scheme_attendance.printed``."""
    _require_step_up(ctx)
    out = attendance_list(
        session,
        ctx,
        scheme_key=scheme_key,
        section_id=section_id,
        from_date=from_date,
        to_date=to_date,
    )
    if check:
        return None
    cfg = load_scheme_config()
    tenant = tenancy.get_tenant(session)
    head = tenant.settings.certificate_letterhead
    page = pages.render(
        pages.PageData(
            school_name=tenant.name,
            school_address=head.address_en,
            school_affiliation=head.affiliation,
            place=head.place,
            title=f"Attendance list: {out.scheme.label}",
            meta=[
                ("Class and section", out.section_label),
                ("Academic year", out.academic_year_label),
                (
                    "Period",
                    f"{out.from_date.strftime('%d/%m/%Y')} to {out.to_date.strftime('%d/%m/%Y')}",
                ),
                ("Students", str(len(out.students))),
            ],
            verified=cfg.verified and out.scheme.verified,
            unverified_note=cfg.note,
            tables=[
                pages.PageTable(
                    caption="Attendance in the period",
                    header=[
                        "Roll no.",
                        "Admission no.",
                        "Student",
                        "Days counted",
                        "Days present",
                        "Attendance",
                    ],
                    rows=[
                        [
                            s.roll_no or "",
                            s.admission_no or "",
                            s.full_name or "",
                            s.days_counted,
                            s.days_present,
                            _pct(s.percent),
                        ]
                        for s in out.students
                    ],
                    numeric_from=3,
                    note="Days present counts present and late days; leave days are not "
                    "counted. Days not marked are not counted.",
                )
            ],
            notes=[out.scheme.note],
            printed_at=dt.datetime.now(dt.UTC),
            reference=f"ATT-{out.section_label}-{out.from_date:%Y%m%d}-{out.to_date:%Y%m%d}",
            signatures=("Class teacher", "Head of the institution (signature and seal)"),
            footer="Taken from the school's attendance register. Eligibility is decided by the "
            "department, not by the school's software.",
        )
    )
    _audit_list(session, "export.scheme_attendance.printed", out, "html")
    return page


def attendance_xlsx(
    session: Session,
    ctx: UserContext,
    *,
    scheme_key: str,
    section_id: uuid.UUID,
    from_date: dt.date,
    to_date: dt.date,
) -> tuple[bytes, str]:
    """The list as XLSX (FR-EXP-022): recent MFA sign-in (428); every cell passes
    ``safe_cell``. Audit ``export.scheme_attendance.downloaded``."""
    _require_step_up(ctx)
    out = attendance_list(
        session,
        ctx,
        scheme_key=scheme_key,
        section_id=section_id,
        from_date=from_date,
        to_date=to_date,
    )
    table = Table(
        name="Attendance",
        header=(
            "Roll no.",
            "Admission no.",
            "Student",
            "Days counted",
            "Days present",
            "Attendance %",
        ),
        rows=tuple(
            (
                s.roll_no,
                s.admission_no,
                s.full_name,
                s.days_counted,
                s.days_present,
                "" if s.percent is None else f"{s.percent:.1f}",
            )
            for s in out.students
        ),
    )
    data = write_xlsx(
        [table],
        watermark=load_config().watermark_text(),
        title=f"Attendance list {out.scheme.label} {out.section_label}",
    )
    _audit_list(session, "export.scheme_attendance.downloaded", out, "xlsx")
    name = f"attendance-{out.section_label}-{out.from_date}-{out.to_date}.xlsx"
    return data, name


# --- bulk certificates ----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Outcome:
    status: Literal["issued", "pending", "refused"]
    certificate_id: uuid.UUID | None
    serial: str | None
    error_code: str | None


def _inputs(kind: CertificateKind, note: str) -> dict[str, str]:
    if kind == "bonafide":
        return {"purpose": "scholarship", "purpose_note": note}
    return {"purpose_note": note}


def _unique(ids: Sequence[uuid.UUID]) -> list[uuid.UUID]:
    return list(dict.fromkeys(ids))


def issue_certificates(
    session: Session, ctx: UserContext, data: SchemeCertificatesIn
) -> SchemeCertificatesOut:
    """One study or bonafide certificate per chosen student (FR-EXP-024..026), each through
    ``certificates.request_certificate`` (``certificate.issue`` in the student's reach; the
    type's own rules, serial and register line). A refused student does not stop the others:
    ``refused`` with the error code (``not_found`` also for students outside reach or of another
    school). 422 ``unknown_scheme``, ``too_many_students``. Audit
    ``export.scheme_certificates.issued`` (counts)."""
    cfg, scheme = _scheme(data.scheme_key)
    ids = _unique(data.student_ids)
    if len(ids) > cfg.max_certificates:
        raise ValidationFailed(
            [
                {
                    "field": "student_ids",
                    "code": "too_many_students",
                    "message_key": "errors.exports.too_many_students",
                }
            ],
            detail=f"Choose at most {cfg.max_certificates} students at a time.",
        )
    kind: CertificateKind = data.certificate_type or scheme.certificate_type
    note = data.purpose_note or scheme.purpose_note
    request = CertificateRequest(certificate_type=kind, inputs=_inputs(kind, note))
    results: list[SchemeCertificateResultOut] = []
    for sid in ids:
        outcome: _Outcome
        try:
            with session.begin_nested():
                made = certificates.request_certificate(session, ctx, sid, request)
            outcome = _Outcome(
                status="issued" if made.status == "issued" else "pending",
                certificate_id=made.id,
                serial=made.serial,
                error_code=None,
            )
        except DomainError as exc:
            outcome = _Outcome(
                status="refused", certificate_id=None, serial=None, error_code=exc.code
            )
        results.append(
            SchemeCertificateResultOut(
                student_id=sid,
                status=outcome.status,
                certificate_id=outcome.certificate_id,
                serial=outcome.serial,
                error_code=outcome.error_code,
            )
        )
    issued = sum(1 for r in results if r.status != "refused")
    refused = len(results) - issued
    audit.record(
        session,
        action="export.scheme_certificates.issued",
        resource_type="export",
        resource_id=None,
        summary={
            "kind": "scheme_certificates",
            "scheme_key": data.scheme_key,
            "certificate_type": kind,
            "requested": len(ids),
            "issued": issued,
            "refused": refused,
            "refusal_codes": sorted({r.error_code for r in results if r.error_code}),
        },
        request_id=_request_id(),
    )
    log.info("exports.scheme_certificates", action="issued", resource_type="export", count=issued)
    return SchemeCertificatesOut(
        scheme_key=data.scheme_key,
        certificate_type=kind,
        issued=issued,
        refused=refused,
        results=results,
    )


__all__ = [
    "CERTIFICATE_ISSUE",
    "attendance_list",
    "attendance_xlsx",
    "issue_certificates",
    "load_scheme_config",
    "percent",
    "print_attendance",
    "scheme_config",
]
