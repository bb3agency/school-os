"""Privacy public API: notices, data-principal requests and grievances, access reports, lawful
erasure and record classes (R3-1A; ADR-0048; US-2601..US-2609; FR-DPDP-001..018; NFR-PRV-004,
NFR-PRV-007; PRV-030..PRV-034). Other modules call only these functions.

- **Templates and notices (FR-DPDP-001..003).** The parents' and staff notices are the app copies
  of ``docs/legal/`` (``templates/*.yaml``), filled from school settings (name, letterhead
  address, ``privacy_contact``); unfilled placeholders print ``[not set]``. A filled notice can be
  made into an A4 PDF by the worker (export kind ``privacy_notice``). Notice issues are recorded
  append-only (``privacy.notice.issued``); a newer template version than the last one issued is
  flagged.
- **One register (FR-DPDP-005..007).** Every request has exactly one subject (student, guardian
  or staff member), who asked and how the school checked them, a due date at most 7 days after
  receipt (the school's ``privacy_request_target_days``), a status and an append-only timeline.
  The requester's name and words, notes, the response and nominee details are encrypted with the
  school's key (AAD ``ops.privacy_requests|<column>|<id>``) and never logged or audited.
- **Reach (FR-DPDP-005, SEC-015).** ``privacy.read`` plus the reader's ``student.read_basic``
  reach: requests about a student outside it (or a guardian of none of the reader's students) are
  404; requests about staff members are visible to every ``privacy.read`` holder.
- **Access reports (FR-DPDP-008..010).** Built by the worker from every contributor in
  ``subjects.yaml`` (each module's ``service.subject_report``), per record class with source and
  retention; released by a second person (``privacy.request.decide``, step-up, not the person who
  logged it, verification recorded), then downloadable for 24 hours. SchoolOS sends it nowhere.
- **Correction (FR-DPDP-011).** Links to change requests; closes only when they are decided.
  Nothing is edited because a request says so (invariant 6).
- **Erasure (FR-DPDP-012..014).** The plan lists every record class with its outcome from
  ``record_classes.yaml`` and each eraser's dry run; approval (decide, step-up, maker-checker)
  erases the ticked erasable classes in this transaction and lists manual steps. Official records,
  financial records and the audit log are never erased on request.
- **Audit and logs (invariants 5, 7).** Every step is audited in its transaction with ids, kinds,
  statuses and counts only; free texts never reach logs, audit summaries or events.
"""

from __future__ import annotations

import datetime as dt
import importlib
import uuid
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Final, cast
from zoneinfo import ZoneInfo

from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.authz.context import UserContext
from app.authz.http import decode_cursor, encode_cursor
from app.changes import service as changes
from app.core import purge as purging
from app.core.crypto import ciphertext_key_version
from app.core.errors import (
    Conflict,
    Forbidden,
    NotFound,
    PreconditionFailed,
    StepUpRequired,
    ValidationFailed,
)
from app.core.ids import new_id
from app.core.logging import get_context, get_logger
from app.core.pdf import PrintDocument
from app.core.records import RecordTable
from app.core.redaction import contains_full_aadhaar
from app.core.subjects import ErasureOutcome, ReportSection, Subject, SubjectEraser, SubjectReporter
from app.documents import service as documents
from app.exports import service as exports
from app.identity import service as identity
from app.identity.principal import STEP_UP_MAX_AGE
from app.notifications import service as notifications
from app.privacy import config as cfg
from app.privacy import render
from app.privacy import repository as repo
from app.privacy.models import PrivacyNotice, PrivacyRequest, PrivacyRequestEvent
from app.privacy.schemas import (
    PrivacyCloseIn,
    PrivacyDownloadOut,
    PrivacyErasureIn,
    PrivacyErasurePlanOut,
    PrivacyErasurePlanRowOut,
    PrivacyErasureResultOut,
    PrivacyErasureResultRowOut,
    PrivacyEventOut,
    PrivacyLinkIn,
    PrivacyLinkOut,
    PrivacyModuleFileOut,
    PrivacyNomineeOut,
    PrivacyNoticeIn,
    PrivacyNoticeOut,
    PrivacyRecordClassesOut,
    PrivacyRecordClassOut,
    PrivacyRequestCreate,
    PrivacyRequestOut,
    PrivacyRequestPageOut,
    PrivacyRequestPatch,
    PrivacyRequestRowOut,
    PrivacyRequestSearchIn,
    PrivacySubjectIn,
    PrivacyTemplateOut,
)
from app.students import crypto
from app.students import rotation as key_rotation
from app.students import service as students
from app.tenancy import service as tenancy

READ: Final = "privacy.read"
MANAGE: Final = "privacy.request.manage"
DECIDE: Final = "privacy.request.decide"
STUDENT_READ: Final = "student.read_basic"
IST: Final = ZoneInfo("Asia/Kolkata")
TABLE: Final = "ops.privacy_requests"
DEFAULT_TARGET_DAYS: Final = 7
REPORT_KEEP: Final = dt.timedelta(hours=24)
DUE_TASK: Final = "privacy.notify_due"
OPEN: Final = repo.OPEN_STATUSES
FINAL: Final = ("closed", "withdrawn")
# Kinds whose closing is a decision (maker-checker, step-up; ADR-0048 decisions 6 and 8).
DECIDE_KINDS: Final = frozenset({"access", "erasure", "grievance"})
CHANGE_DECIDED: Final = frozenset({"approved", "rejected", "cancelled", "expired", "applied"})
ERASURE_WORDS: Final[Mapping[str, str]] = {
    "allowed": "Can be erased on request",
    "allowed_after_retention": "Deleted when its retention ends",
    "refused_legal_obligation": "Kept: required by law",
    "refused_official_record": "Kept: school record",
    "no_personal_data": "No personal data",
}
ERASURE_OUTCOME: Final[Mapping[str, str]] = {
    "allowed": "erasable",
    "allowed_after_retention": "retention_running",
    "refused_legal_obligation": "refused_legal_obligation",
    "refused_official_record": "refused_official_record",
}
TEXT_COLUMNS: Final = (
    "requester_name_ciphertext",
    "request_ciphertext",
    "notes_ciphertext",
    "response_ciphertext",
    "nominee_ciphertext",
)

log = get_logger(__name__)


class SelfApproval(Forbidden):
    code = "self_approval_forbidden"
    title = "Someone other than the person who logged this request must decide it"


class NotVerified(Conflict):
    code = "not_verified"
    title = "Record how you checked who asked first"


# --- plumbing ---------------------------------------------------------------------------------


@contextmanager
def _db_errors() -> Iterator[None]:
    try:
        yield
    except DBAPIError as exc:
        mapped = repo.translate_db_error(exc)
        if mapped is None:
            raise
        raise mapped from exc


def _invalid(field: str, code: str) -> ValidationFailed:
    return ValidationFailed([{"field": field, "code": code, "message_key": f"errors.{code}"}])


def _audit(
    session: Session,
    action: str,
    resource_id: uuid.UUID | None,
    summary: Mapping[str, Any],
    *,
    resource_type: str = "privacy_request",
) -> None:
    request_id = get_context().get("request_id")
    audit.record(
        session,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        summary=summary,
        request_id=request_id if isinstance(request_id, str) else None,
    )


def _today(session: Session) -> dt.date:
    return repo.now(session).astimezone(IST).date()


def _fmt(day: dt.date | None) -> str:
    return day.strftime("%d/%m/%Y") if day is not None else ""


def _require_step_up(ctx: UserContext) -> None:
    if not ctx.mfa or ctx.auth_time is None:
        raise StepUpRequired()
    age = dt.datetime.now(dt.UTC) - ctx.auth_time
    if age > STEP_UP_MAX_AGE or age < -dt.timedelta(seconds=30):
        raise StepUpRequired()


def _need(ctx: UserContext, permission: str) -> None:
    if not ctx.has(permission):
        raise Forbidden()


def _encrypt(session: Session, row_id: uuid.UUID, column: str, value: str | None) -> bytes | None:
    if value is None:
        return None
    blob, _ = crypto.encrypt_value(session, value, table=TABLE, column=column, row_id=row_id)
    return blob


def _decrypt(session: Session, row: PrivacyRequest, column: str) -> str | None:
    blob = getattr(row, column)
    if blob is None:
        return None
    return crypto.decrypt_value(session, bytes(blob), table=TABLE, column=column, row_id=row.id)


def _no_aadhaar(field: str, value: str | None) -> None:
    if value is not None and contains_full_aadhaar(value):
        raise _invalid(field, "aadhaar_full_number_rejected")


def _event(
    session: Session,
    ctx: UserContext | None,
    row: PrivacyRequest,
    event: str,
    detail: Mapping[str, Any] | None = None,
) -> None:
    with _db_errors():
        repo.add_event(
            session,
            row.id,
            event,
            detail or {},
            actor_user_id=ctx.user_id if ctx is not None else None,
            actor_membership_id=ctx.membership_id if ctx is not None else None,
        )


# --- settings ---------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PrivacyContact:
    name: str | None
    phone: str | None
    email: str | None


def _contact(session: Session) -> PrivacyContact:
    settings = tenancy.session_settings(session)
    contact = settings.privacy_contact
    if contact is None:
        return PrivacyContact(None, None, None)
    return PrivacyContact(contact.name, contact.phone, contact.email)


def target_days(session: Session) -> int:
    value = tenancy.session_settings(session).privacy_request_target_days
    return max(1, min(DEFAULT_TARGET_DAYS, value))


def _letterhead(session: Session) -> render.Letterhead:
    settings = tenancy.session_settings(session)
    contact = _contact(session)
    bits = [
        contact.name or render.NOT_SET,
        contact.phone or render.NOT_SET,
        contact.email or render.NOT_SET,
    ]
    address = settings.certificate_letterhead.address_en or None
    return render.Letterhead(
        school=tenancy.school_display_name(session),
        address=address,
        contact_line="Privacy contact: " + " · ".join(bits),
    )


def _school_values(session: Session) -> dict[str, str | None]:
    settings = tenancy.session_settings(session)
    contact = _contact(session)
    name = tenancy.school_display_name(session)
    return {
        "school_legal_name": name,
        "school_short_name": name,
        "school_address": settings.certificate_letterhead.address_en or None,
        "school_affiliation": settings.certificate_letterhead.affiliation or None,
        "privacy_contact_name": contact.name,
        "privacy_contact_phone": contact.phone,
        "privacy_contact_email": contact.email,
    }


# --- templates and notices (FR-DPDP-001..003) -------------------------------------------------


def _audience(key: cfg.TemplateKey) -> str:
    return cfg.notice_template(key).audience


def list_templates(session: Session) -> list[PrivacyTemplateOut]:
    """The two notice templates with their versions, the placeholders the school has not filled
    and whether a newer version exists than the last one issued (``privacy.read``)."""
    values = _school_values(session)
    issued = repo.list_notices(session)
    out = []
    for key in cfg.TEMPLATE_KEYS:
        template = cfg.notice_template(key)
        last = next((n for n in issued if n.audience == template.audience), None)
        unfilled = [
            p
            for p in template.placeholders
            if p in values and not values[p] and p != "optional_sections"
        ]
        out.append(
            PrivacyTemplateOut(
                key=key,
                template_version=template.template_version,
                audience=template.audience,
                languages=["en"],
                placeholders=list(template.placeholders),
                unfilled=unfilled,
                last_issued_version=last.template_version if last else None,
                newer_version_available=last is not None
                and _version_tuple(last.template_version)
                < _version_tuple(template.template_version),
            )
        )
    return out


def _version_tuple(value: str) -> tuple[int, ...]:
    return tuple(int(p) for p in value.split("."))


def _filled_notice(session: Session, key: cfg.TemplateKey) -> tuple[str, str]:
    template = cfg.notice_template(key)
    values = {
        **_school_values(session),
        "effective_date": _fmt(_today(session)),
        "template_version": template.template_version,
        "optional_sections": "",
    }
    title = "Privacy notice for parents" if key == "notice_parents" else "Privacy notice for staff"
    markdown = render.fill(template.body_markdown, {**values, "optional_sections": " "})
    return title, markdown


def preview_template(session: Session, key: cfg.TemplateKey) -> str:
    """The notice filled from school settings as an A4 HTML page (FR-DPDP-002)."""
    title, markdown = _filled_notice(session, key)
    return render.notice_page(title, _letterhead(session), markdown)


def _notice_document(
    session: Session, ctx: UserContext, scope: Mapping[str, Any]
) -> exports.ModuleDocument:
    key = str(scope.get("template_key"))
    if key not in cfg.TEMPLATE_KEYS:
        raise NotFound("Unknown template")
    page = preview_template(session, key)
    return exports.ModuleDocument(
        document=PrintDocument(html=page), filename=f"privacy-{key.replace('_', '-')}.pdf"
    )


def _file_out(state: exports.ModuleDocumentState) -> PrivacyModuleFileOut:
    status = "expired" if state.files_deleted and state.status == "ready" else state.status
    return PrivacyModuleFileOut(
        export_id=state.id,
        status=cast(Any, status),
        error_code=state.error_code,
        created_at=state.created_at,
        finished_at=state.finished_at,
        expires_at=state.expires_at,
    )


def request_notice_pdf(
    session: Session, ctx: UserContext, key: cfg.TemplateKey
) -> PrivacyModuleFileOut:
    """Queue the filled notice as an A4 PDF (worker queue ``pdf``; ``privacy.read``)."""
    state = exports.request_module_document(
        session,
        ctx,
        kind=exports.PRIVACY_NOTICE,
        scope={"template_key": key},
        details={"template_key": key},
    )
    return _file_out(state)


def notice_pdf_state(session: Session, export_id: uuid.UUID) -> PrivacyModuleFileOut:
    state = exports.module_document_state(session, export_id, kind=exports.PRIVACY_NOTICE)
    if state is None:
        raise NotFound("File not found")
    return _file_out(state)


def notice_pdf_download(session: Session, export_id: uuid.UUID) -> PrivacyDownloadOut:
    out = exports.module_document_download_url(
        session, export_id, kind=exports.PRIVACY_NOTICE, filename_stem="privacy-notice"
    )
    return PrivacyDownloadOut(
        url=out.url, expires_at=out.expires_at, filename=out.filename, content_type=out.content_type
    )


def _notice_out(row: PrivacyNotice, names: Mapping[uuid.UUID, str]) -> PrivacyNoticeOut:
    return PrivacyNoticeOut(
        id=row.id,
        template_key=cast(Any, row.template_key),
        template_version=row.template_version,
        audience=cast(Any, row.audience),
        language=cast(Any, row.language),
        issued_on=row.issued_on,
        channel=cast(Any, row.channel),
        document_id=row.document_id,
        recorded_by_name=names.get(row.recorded_by_membership),
        recorded_at=row.recorded_at,
    )


def list_notices(session: Session) -> list[PrivacyNoticeOut]:
    rows = repo.list_notices(session)
    names = identity.member_display_names(session, {r.recorded_by_membership for r in rows})
    return [_notice_out(r, names) for r in rows]


def record_notice(session: Session, ctx: UserContext, data: PrivacyNoticeIn) -> PrivacyNoticeOut:
    """Record that the school issued a notice (FR-DPDP-003; ``privacy.request.manage``).
    Append-only. Audit ``privacy.notice.issued``."""
    template = cfg.notice_template(data.template_key)
    errors = []
    if data.audience != template.audience:
        errors.append({"field": "audience", "code": "audience_mismatch"})
    if _version_tuple(data.template_version) > _version_tuple(template.template_version):
        errors.append({"field": "template_version", "code": "unknown_version"})
    if data.issued_on > _today(session):
        errors.append({"field": "issued_on", "code": "date_in_future"})
    if data.document_id is not None and not documents.evidence_exists(session, data.document_id):
        errors.append({"field": "document_id", "code": "not_found"})
    if errors:
        raise ValidationFailed([{**e, "message_key": f"errors.{e['code']}"} for e in errors])
    row = repo.insert_notice(
        session,
        {
            "id": new_id(),
            "tenant_id": ctx.tenant_id,
            "template_key": data.template_key,
            "template_version": data.template_version,
            "audience": data.audience,
            "language": data.language,
            "issued_on": data.issued_on,
            "channel": data.channel,
            "document_id": data.document_id,
            "recorded_by": ctx.user_id,
            "recorded_by_membership": ctx.membership_id,
        },
    )
    _audit(
        session,
        "privacy.notice.issued",
        row.id,
        {
            "template_key": data.template_key,
            "template_version": data.template_version,
            "audience": data.audience,
            "channel": data.channel,
            "has_document": data.document_id is not None,
        },
        resource_type="privacy_notice",
    )
    names = identity.member_display_names(session, {ctx.membership_id})
    return _notice_out(row, names)


# --- record classes (FR-DPDP-017) -------------------------------------------------------------


def record_classes() -> PrivacyRecordClassesOut:
    catalogue = cfg.record_classes()
    return PrivacyRecordClassesOut(
        version=catalogue.version,
        data=[
            PrivacyRecordClassOut(
                key=key,
                label=c.label,
                owner=c.owner,
                tables=list(c.tables),
                classification=c.classification,
                retention=c.retention,
                basis=c.basis,
                erasure=c.erasure,
                source=c.source,
            )
            for key, c in catalogue.classes.items()
        ],
    )


# --- subjects and reach -----------------------------------------------------------------------


def _subject(row: PrivacyRequest) -> Subject | None:
    if row.subject_kind == "student" and row.student_id is not None:
        return Subject("student", row.student_id)
    if row.subject_kind == "guardian" and row.guardian_id is not None:
        return Subject("guardian", row.guardian_id)
    if row.subject_kind == "member" and row.membership_id is not None:
        return Subject("member", row.membership_id)
    return None


@dataclass(frozen=True, slots=True)
class _Reach:
    """``None``: the whole school."""

    students: frozenset[uuid.UUID] | None
    guardians: frozenset[uuid.UUID] | None


def _reach(session: Session, ctx: UserContext) -> _Reach:
    if not ctx.has(STUDENT_READ):
        return _Reach(frozenset(), frozenset())
    if ctx.scope_for(STUDENT_READ).school_wide:
        return _Reach(None, None)
    ids = frozenset(students.list_students_in_scope(session, ctx))
    guardians = frozenset(g.guardian_id for g in students.guardians_of_students(session, ids))
    return _Reach(ids, guardians)


def _guardian_reachable(session: Session, ctx: UserContext, guardian_id: uuid.UUID) -> bool:
    linked = students.guardian_student_ids(session, guardian_id)
    reach = _reach(session, ctx)
    if reach.students is None:
        return ctx.has(STUDENT_READ)
    return bool(set(linked) & reach.students)


def _can_see(session: Session, ctx: UserContext, row: PrivacyRequest) -> bool:
    if row.subject_kind == "member":
        return True
    if row.subject_kind == "student" and row.student_id is not None:
        try:
            students.ensure_in_scope(session, ctx, row.student_id)
        except NotFound:
            return False
        return True
    if row.guardian_id is None:
        # A former guardian: only school-wide readers.
        return ctx.has(STUDENT_READ) and ctx.scope_for(STUDENT_READ).school_wide
    return _guardian_reachable(session, ctx, row.guardian_id)


def _load(
    session: Session, ctx: UserContext, request_id: uuid.UUID, *, lock: bool = False
) -> PrivacyRequest:
    row = repo.get_request(session, request_id, lock=lock)
    if row is None or not _can_see(session, ctx, row):
        raise NotFound("Request not found")
    return row


def _labels(
    session: Session, ctx: UserContext, rows: Sequence[PrivacyRequest]
) -> dict[uuid.UUID, str]:
    student_ids = {r.student_id for r in rows if r.student_id is not None}
    member_ids = {r.membership_id for r in rows if r.membership_id is not None}
    summaries = students.summaries(session, ctx, student_ids) if student_ids else {}
    members = identity.member_display_names(session, member_ids) if member_ids else {}
    out: dict[uuid.UUID, str] = {}
    for r in rows:
        if r.student_id is not None:
            s = summaries.get(r.student_id)
            name = s.display_name if s is not None and s.display_name else "Student"
            adm = f" ({s.admission_no})" if s is not None and s.admission_no else ""
            out[r.id] = f"{name}{adm}"
        elif r.membership_id is not None:
            out[r.id] = members.get(r.membership_id, "Staff member")
        elif r.guardian_id is not None:
            out[r.id] = _guardian_label(session, r.guardian_id)
        else:
            out[r.id] = "Former guardian"
    return out


def _guardian_label(session: Session, guardian_id: uuid.UUID) -> str:
    linked = students.guardian_student_ids(session, guardian_id)
    for brief in students.guardians_of_students(session, linked):
        if brief.guardian_id == guardian_id:
            return brief.full_name
    return "Former guardian"


def _subject_name(session: Session, ctx: UserContext, row: PrivacyRequest) -> str:
    return _labels(session, ctx, [row])[row.id]


def _days_left(today: dt.date, row: PrivacyRequest) -> int:
    return (row.due_on - today).days


# --- requests (FR-DPDP-005..007) --------------------------------------------------------------


def _check_subject(session: Session, ctx: UserContext, subject: PrivacySubjectIn) -> Subject:
    given = [
        ("student", subject.student_id),
        ("guardian", subject.guardian_id),
        ("member", subject.membership_id),
    ]
    chosen = [(k, v) for k, v in given if v is not None]
    if len(chosen) != 1:
        raise _invalid("subject", "subject_required")
    kind, value = chosen[0]
    if kind == "student":
        try:
            students.ensure_in_scope(session, ctx, value)
        except NotFound as exc:
            raise _invalid("subject.student_id", "not_found") from exc
        return Subject("student", value)
    if kind == "guardian":
        if not students.guardian_student_ids(session, value) or not _guardian_reachable(
            session, ctx, value
        ):
            raise _invalid("subject.guardian_id", "not_found")
        return Subject("guardian", value)
    if value not in identity.member_display_names(session, {value}):
        raise _invalid("subject.membership_id", "not_found")
    return Subject("member", value)


def _nominee_text(data: PrivacyRequestCreate) -> str | None:
    if data.kind != "nomination":
        return None
    if not data.nominee_name:
        raise _invalid("nominee_name", "required")
    return "\n".join(
        [data.nominee_name, data.nominee_relationship or "", data.nominee_contact or ""]
    )


def create_request(
    session: Session, ctx: UserContext, data: PrivacyRequestCreate
) -> PrivacyRequestOut:
    """Log a request or grievance (FR-DPDP-005..007; ``privacy.request.manage``). Due date =
    received + the school's target (1-7 days, NFR-PRV-004). Texts encrypted. Audit
    ``privacy.request.logged`` (ids, kind, subject kind only)."""
    subject = _check_subject(session, ctx, data.subject)
    if data.kind == "nomination" and subject.kind == "student":
        raise _invalid("kind", "nomination_adults_only")
    if data.verification_method == "staff_sign_in" and subject.kind != "member":
        raise _invalid("verification_method", "staff_sign_in_staff_only")
    if data.verification_method == "guardian_phone_on_record" and subject.kind == "member":
        raise _invalid("verification_method", "guardian_phone_guardians_only")
    for field, value in (
        ("request_text", data.request_text),
        ("requester_name", data.requester_name),
        ("nominee_contact", data.nominee_contact),
    ):
        _no_aadhaar(field, value)
    today = _today(session)
    received = data.received_on or today
    if received > today:
        raise _invalid("received_on", "date_in_future")
    if received < today - dt.timedelta(days=366):
        raise _invalid("received_on", "too_old")
    nominee = _nominee_text(data)
    year = received.year
    with _db_errors():
        number = repo.next_number(session, year)
    row_id = new_id()
    now = repo.now(session)
    row = repo.insert_request(
        session,
        {
            "id": row_id,
            "tenant_id": ctx.tenant_id,
            "reference": f"PR-{year}-{number:04d}",
            "year": year,
            "number": number,
            "kind": data.kind,
            "subject_kind": subject.kind,
            "student_id": subject.id if subject.kind == "student" else None,
            "guardian_id": subject.id if subject.kind == "guardian" else None,
            "membership_id": subject.id if subject.kind == "member" else None,
            "requester_relationship": data.requester_relationship,
            "verification_method": data.verification_method,
            "verified_at": now if data.verification_method else None,
            "received_on": received,
            "due_on": received + dt.timedelta(days=target_days(session)),
            "status": "received",
            "requester_name_ciphertext": _encrypt(
                session, row_id, "requester_name_ciphertext", data.requester_name
            ),
            "request_ciphertext": _encrypt(
                session, row_id, "request_ciphertext", data.request_text
            ),
            "nominee_ciphertext": _encrypt(session, row_id, "nominee_ciphertext", nominee),
            "logged_by": ctx.user_id,
            "logged_by_membership": ctx.membership_id,
        },
    )
    _event(session, ctx, row, "logged", {"kind": data.kind, "subject_kind": subject.kind})
    if data.verification_method:
        _event(session, ctx, row, "verified", {"method": data.verification_method})
    _audit(
        session,
        "privacy.request.logged",
        row.id,
        {
            "kind": data.kind,
            "subject_kind": subject.kind,
            "verified": data.verification_method is not None,
            "due_in_days": (row.due_on - received).days,
        },
    )
    log.info("privacy.request.logged", resource_type="privacy_request", resource_id=row.id)
    return _out(session, ctx, row)


def _cursor(cursor: str | None) -> tuple[int, dt.date, str] | None:
    value = decode_cursor(cursor)
    if value is None:
        return None
    try:
        return int(value["c"]), dt.date.fromisoformat(str(value["d"])), str(value["r"])
    except (KeyError, ValueError) as exc:
        raise ValidationFailed(
            [{"field": "cursor", "code": "invalid", "message_key": "errors.invalid_cursor"}]
        ) from exc


def search_requests(
    session: Session, ctx: UserContext, data: PrivacyRequestSearchIn
) -> PrivacyRequestPageOut:
    """Requests the caller may see (``privacy.read`` and student reach), open first, then by
    due date (FR-DPDP-006)."""
    reach = _reach(session, ctx)
    today = _today(session)
    subject = data.subject
    student_ids = guardian_ids = None
    membership_id = None
    if subject is not None:
        student_ids = [subject.student_id] if subject.student_id else None
        guardian_ids = [subject.guardian_id] if subject.guardian_id else None
        membership_id = subject.membership_id
    rows = repo.search_requests(
        session,
        kinds=data.kind,
        statuses=data.status,
        overdue_before=today if data.overdue else None,
        student_ids=student_ids,
        guardian_ids=guardian_ids,
        membership_id=membership_id,
        reach_students=reach.students,
        reach_guardians=reach.guardians,
        after=_cursor(data.cursor),
        limit=data.limit + 1,
    )
    page = rows[: data.limit]
    labels = _labels(session, ctx, page)
    next_cursor = None
    if len(rows) > data.limit and page:
        last = page[-1]
        next_cursor = encode_cursor(
            {
                "c": 1 if last.status in FINAL else 0,
                "d": last.due_on.isoformat(),
                "r": last.reference,
            }
        )
    return PrivacyRequestPageOut(
        data=[
            PrivacyRequestRowOut(
                id=r.id,
                reference=r.reference,
                kind=cast(Any, r.kind),
                subject_kind=cast(Any, r.subject_kind),
                subject_label=labels[r.id],
                status=cast(Any, r.status),
                received_on=r.received_on,
                due_on=r.due_on,
                days_left=_days_left(today, r),
                overdue=r.status in OPEN and r.due_on < today,
            )
            for r in page
        ],
        next_cursor=next_cursor,
    )


def _report_state(session: Session, row: PrivacyRequest) -> PrivacyModuleFileOut | None:
    if row.report_export_id is None:
        return None
    state = exports.module_document_state(
        session, row.report_export_id, kind=exports.SUBJECT_REPORT
    )
    return _file_out(state) if state is not None else None


def _out(session: Session, ctx: UserContext, row: PrivacyRequest) -> PrivacyRequestOut:
    today = _today(session)
    events = repo.events_of(session, row.id)
    links = repo.links_of(session, row.id)
    members = {e.actor_membership_id for e in events if e.actor_membership_id is not None}
    members.add(row.logged_by_membership)
    names = identity.member_display_names(session, members)
    decided_name = None
    if row.decided_by is not None:
        found = identity.members_for_users(session, [row.decided_by]).get(row.decided_by)
        decided_name = found[1] if found is not None else None
    nominee = None
    if row.kind == "nomination":
        text = _decrypt(session, row, "nominee_ciphertext") or ""
        parts = [*text.split("\n"), "", "", ""][:3]
        nominee = PrivacyNomineeOut(
            name=parts[0] or None,
            relationship=parts[1] or None,
            contact=parts[2] or None,
            withdrawn_at=row.nomination_withdrawn_at,
        )
    change_status: dict[uuid.UUID, str] = {}
    for link in links:
        if link.link_kind == "change_request":
            info = changes.request_link(session, link.target_id)
            if info is not None:
                change_status[link.target_id] = info.status
    return PrivacyRequestOut(
        id=row.id,
        reference=row.reference,
        kind=cast(Any, row.kind),
        subject_kind=cast(Any, row.subject_kind),
        student_id=row.student_id,
        guardian_id=row.guardian_id,
        membership_id=row.membership_id,
        subject_label=_subject_name(session, ctx, row),
        requester_relationship=cast(Any, row.requester_relationship),
        requester_name=_decrypt(session, row, "requester_name_ciphertext"),
        verification_method=cast(Any, row.verification_method),
        verified_at=row.verified_at,
        received_on=row.received_on,
        due_on=row.due_on,
        days_left=_days_left(today, row),
        overdue=row.status in OPEN and row.due_on < today,
        status=cast(Any, row.status),
        request_text=_decrypt(session, row, "request_ciphertext"),
        notes=_decrypt(session, row, "notes_ciphertext"),
        response_text=_decrypt(session, row, "response_ciphertext"),
        responded_on=row.responded_on,
        nominee=nominee,
        logged_by_name=names.get(row.logged_by_membership),
        logged_by_me=row.logged_by == ctx.user_id,
        decided_by_name=decided_name,
        decided_at=row.decided_at,
        closed_at=row.closed_at,
        report=_report_state(session, row),
        report_released_at=row.report_released_at,
        can_decide=ctx.has(DECIDE) and row.logged_by != ctx.user_id,
        events=[
            PrivacyEventOut(
                seq=e.seq,
                event=e.event,
                detail=dict(e.detail or {}),
                actor_name=names.get(e.actor_membership_id) if e.actor_membership_id else None,
                at=e.at,
            )
            for e in events
        ],
        links=[
            PrivacyLinkOut(
                link_kind=link.link_kind,
                target_id=link.target_id,
                created_at=link.created_at,
                status=change_status.get(link.target_id),
            )
            for link in links
        ],
        version=row.version,
    )


def get_request(session: Session, ctx: UserContext, request_id: uuid.UUID) -> PrivacyRequestOut:
    """One request with its texts, timeline and links (``privacy.read``; 404 outside reach)."""
    return _out(session, ctx, _load(session, ctx, request_id))


def _check_version(row: PrivacyRequest, expected: int | None) -> None:
    if expected is not None and expected != row.version:
        raise PreconditionFailed("Someone else changed this request just now. Reload it.")


def _open(row: PrivacyRequest) -> None:
    if row.status in FINAL:
        raise Conflict("This request is closed.", code="request_closed")


def update_request(  # noqa: PLR0912 - one branch per optional field of the PATCH
    session: Session,
    ctx: UserContext,
    request_id: uuid.UUID,
    data: PrivacyRequestPatch,
    *,
    expected_version: int,
) -> PrivacyRequestOut:
    """Change status, notes, verification, response or withdraw a nomination
    (``privacy.request.manage``; ``If-Match``). Audit ``privacy.request.updated`` with the field
    names only."""
    row = _load(session, ctx, request_id, lock=True)
    _check_version(row, expected_version)
    _open(row)
    values: dict[str, Any] = {}
    changed: list[str] = []
    for field, value in (("notes", data.notes), ("response_text", data.response_text)):
        _no_aadhaar(field, value)
    if data.notes is not None:
        values["notes_ciphertext"] = _encrypt(session, row.id, "notes_ciphertext", data.notes)
        changed.append("notes")
    if data.response_text is not None:
        values["response_ciphertext"] = _encrypt(
            session, row.id, "response_ciphertext", data.response_text
        )
        values["responded_on"] = _today(session)
        changed.append("response_text")
    if data.verification_method is not None and data.verification_method != (
        row.verification_method
    ):
        if data.verification_method == "staff_sign_in" and row.subject_kind != "member":
            raise _invalid("verification_method", "staff_sign_in_staff_only")
        if data.verification_method == "guardian_phone_on_record" and row.subject_kind == "member":
            raise _invalid("verification_method", "guardian_phone_guardians_only")
        values["verification_method"] = data.verification_method
        values["verified_at"] = repo.now(session)
        changed.append("verification_method")
    if data.nomination_withdrawn:
        if row.kind != "nomination":
            raise _invalid("nomination_withdrawn", "not_a_nomination")
        if row.nomination_withdrawn_at is None:
            values["nomination_withdrawn_at"] = repo.now(session)
            changed.append("nomination_withdrawn")
    if data.status is not None and data.status != row.status:
        values["status"] = data.status
        if data.status == "withdrawn":
            values["closed_at"] = repo.now(session)
        changed.append("status")
    if not values:
        return _out(session, ctx, row)
    with _db_errors():
        row = repo.update_request(session, row, values)
    if "verification_method" in changed:
        _event(session, ctx, row, "verified", {"method": row.verification_method})
    if "notes" in changed:
        _event(session, ctx, row, "notes_changed")
    if "response_text" in changed:
        _event(session, ctx, row, "responded")
    if "nomination_withdrawn" in changed:
        _event(session, ctx, row, "nomination_withdrawn")
    if "status" in changed:
        _event(session, ctx, row, "status_changed", {"status": row.status})
    _audit(
        session,
        "privacy.request.updated",
        row.id,
        {"fields": changed, "status": row.status, "kind": row.kind},
    )
    return _out(session, ctx, row)


def add_link(
    session: Session, ctx: UserContext, request_id: uuid.UUID, data: PrivacyLinkIn
) -> PrivacyRequestOut:
    """Link a change request to a correction request (FR-DPDP-011; ``privacy.request.manage``).
    The change request must be about this request's student."""
    row = _load(session, ctx, request_id, lock=True)
    _open(row)
    if row.kind != "correction":
        raise _invalid("kind", "not_a_correction")
    info = changes.request_link(session, data.target_id)
    if info is None or row.student_id is None or info.student_id != row.student_id:
        raise _invalid("target_id", "not_found")
    if repo.add_link(session, row.id, data.kind, data.target_id, created_by=ctx.user_id):
        _event(session, ctx, row, "change_request_linked", {"change_request_id": data.target_id})
        if row.status == "received":
            row = repo.update_request(session, row, {"status": "in_progress"})
        _audit(
            session,
            "privacy.request.linked",
            row.id,
            {"link_kind": data.kind, "target_id": data.target_id},
        )
    return _out(session, ctx, row)


# --- letters ----------------------------------------------------------------------------------


def _event_detail(events: Sequence[PrivacyRequestEvent], name: str) -> Mapping[str, Any]:
    for e in reversed(events):
        if e.event == name:
            return e.detail or {}
    return {}


def letter_html(
    session: Session, ctx: UserContext, request_id: uuid.UUID, letter: cfg.LetterKey
) -> str:
    """A response letter for printing (``privacy.read``). Audit ``privacy.letter.printed``."""
    row = _load(session, ctx, request_id)
    templates = cfg.letter_templates()
    template = templates.letters[letter]
    classes = cfg.record_classes().classes
    events = repo.events_of(session, row.id)
    erased = _event_detail(events, "erasure_executed")
    decision_lines = ""
    refusal_lines = ""
    if letter == "erasure":
        counts = cast(Mapping[str, int], erased.get("counts", {}))
        decision_lines = (
            "; ".join(f"{classes[k].label} ({v})" for k, v in counts.items() if k in classes)
            or "nothing yet"
        )
        refused = cast(Sequence[str], erased.get("refused", []))
        refusal_lines = "; ".join(classes[k].label for k in refused if k in classes) or "none"
    response = _decrypt(session, row, "response_ciphertext")
    nominee = (_decrypt(session, row, "nominee_ciphertext") or "").split("\n")
    contact = _contact(session)
    school = tenancy.school_display_name(session)
    values: dict[str, str | None] = {
        "school_short_name": school,
        "privacy_contact_name": contact.name,
        "privacy_contact_phone": contact.phone,
        "privacy_contact_email": contact.email,
        "request_reference": row.reference,
        "received_on": _fmt(row.received_on),
        "due_on": _fmt(row.due_on),
        "subject_name": _subject_name(session, ctx, row),
        "requester_name": _decrypt(session, row, "requester_name_ciphertext"),
        "decision_lines": decision_lines or response,
        "refusal_lines": refusal_lines,
        "next_steps": response,
        "decided_on": _fmt(row.decided_at.astimezone(IST).date()) if row.decided_at else None,
        "nominee_name": nominee[0] if nominee and nominee[0] else None,
        "nominee_relationship": nominee[1] if len(nominee) > 1 and nominee[1] else None,
        "purpose_label": None,
    }
    body = render.fill(template.body, values)
    _audit(
        session,
        "privacy.letter.printed",
        row.id,
        {"letter": letter, "template_version": templates.template_version},
    )
    return render.letter_page(
        template.title.split(" (")[0],
        _letterhead(session),
        reference=row.reference,
        date_line=_fmt(_today(session)),
        body=body,
    )


# --- contributors (FR-DPDP-008) ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Contributor:
    module: str
    classes: tuple[str, ...]
    report: SubjectReporter
    eraser: SubjectEraser | None


def contributors() -> list[_Contributor]:
    """Every registered contributor, resolved from ``subjects.yaml`` (fail closed: a listed
    module without its functions raises at first use; the registry test catches it in CI)."""
    out = []
    for module, entry in cfg.subjects_registry().contributors.items():
        if module == "privacy":
            out.append(_Contributor(module, entry.classes, subject_report, None))
            continue
        svc = importlib.import_module(f"app.{module}.service")
        report = cast(SubjectReporter, svc.subject_report)
        eraser = cast(SubjectEraser, svc.erase_for_subject) if entry.eraser else None
        out.append(_Contributor(module, entry.classes, report, eraser))
    return out


def subject_report(session: Session, subject: Subject) -> list[ReportSection]:
    """This module's own contribution: the requests and complaints about the subject (no texts:
    they may name other people)."""
    rows = repo.requests_about(
        session,
        student_id=subject.id if subject.kind == "student" else None,
        guardian_id=subject.id if subject.kind == "guardian" else None,
        membership_id=subject.id if subject.kind == "member" else None,
    )
    if not rows:
        return []
    return [
        ReportSection(
            class_key="privacy_requests",
            title="Privacy requests and complaints",
            columns=("Reference", "Kind", "Received", "Status"),
            rows=tuple(
                (
                    r.reference,
                    r.kind.replace("_", " ").capitalize(),
                    _fmt(r.received_on),
                    r.status.replace("_", " ").capitalize(),
                )
                for r in rows
            ),
        )
    ]


def _report_blocks(session: Session, subject: Subject) -> list[render.ReportBlock]:
    classes = cfg.record_classes().classes
    by_class: dict[str, list[ReportSection]] = {}
    for contributor in contributors():
        allowed = set(contributor.classes)
        for section in contributor.report(session, subject):
            if section.class_key not in allowed:
                log.warning(
                    "privacy.report.section_dropped",
                    action=contributor.module,
                    error_code="class_not_owned",
                )
                continue
            by_class.setdefault(section.class_key, []).append(section)
    blocks = []
    for key, cls in classes.items():
        sections = by_class.get(key)
        if not sections:
            continue
        blocks.append(
            render.ReportBlock(
                label=cls.label,
                source=cls.source,
                retention=cls.retention.replace("**", ""),
                erasure_words=ERASURE_WORDS[cls.erasure],
                sections=[(s.title, s.columns, s.rows, s.note) for s in sections],
            )
        )
    return blocks


def _report_document(
    session: Session, ctx: UserContext, scope: Mapping[str, Any]
) -> exports.ModuleDocument:
    request_id = uuid.UUID(str(scope.get("privacy_request_id")))
    row = repo.get_request(session, request_id)
    if row is None:
        raise NotFound("Request not found")
    subject = _subject(row)
    if subject is None:
        raise NotFound("Subject not found")
    blocks = _report_blocks(session, subject)
    page = render.report_page(
        _letterhead(session),
        subject_name=_subject_name(session, ctx, row),
        reference=row.reference,
        date_line=_fmt(_today(session)),
        blocks=blocks,
    )
    return exports.ModuleDocument(
        document=PrintDocument(html=page),
        filename=f"access-report-{row.reference.lower()}.pdf",
        keep=REPORT_KEEP,
    )


def build_report(session: Session, ctx: UserContext, request_id: uuid.UUID) -> PrivacyRequestOut:
    """Queue the access report (FR-DPDP-009; ``privacy.request.manage``). Any earlier report of
    the request is replaced (it must be released again)."""
    row = _load(session, ctx, request_id, lock=True)
    _open(row)
    if row.kind != "access":
        raise _invalid("kind", "not_an_access_request")
    if _subject(row) is None:
        raise Conflict(
            "The person this request is about is no longer on record.", code="no_subject"
        )
    state = exports.request_module_document(
        session,
        ctx,
        kind=exports.SUBJECT_REPORT,
        scope={"privacy_request_id": str(row.id)},
        student_ids=[row.student_id] if row.student_id else [],
        details={"privacy_request_id": row.id, "subject_kind": row.subject_kind},
    )
    values: dict[str, Any] = {
        "report_export_id": state.id,
        "report_released_by": None,
        "report_released_at": None,
    }
    if row.status == "received":
        values["status"] = "in_progress"
    row = repo.update_request(session, row, values)
    repo.add_link(session, row.id, "export", state.id, created_by=ctx.user_id)
    _event(session, ctx, row, "report_built", {"export_id": state.id})
    _audit(session, "privacy.report.requested", row.id, {"export_id": state.id})
    return _out(session, ctx, row)


def _decider(ctx: UserContext, row: PrivacyRequest) -> None:
    _need(ctx, DECIDE)
    _require_step_up(ctx)
    if row.logged_by == ctx.user_id:
        raise SelfApproval()


def release_report(session: Session, ctx: UserContext, request_id: uuid.UUID) -> PrivacyRequestOut:
    """Release a ready access report (FR-DPDP-010; ``privacy.request.decide``, step-up, not the
    person who logged it; verification recorded). Audit ``privacy.report.released``."""
    row = _load(session, ctx, request_id, lock=True)
    _decider(ctx, row)
    _open(row)
    if row.verification_method is None:
        raise NotVerified()
    state = _report_state(session, row)
    if state is None or state.status != "ready":
        raise Conflict("Build the report and wait until it is ready.", code="report_not_ready")
    now = repo.now(session)
    with _db_errors():
        row = repo.update_request(
            session,
            row,
            {
                "report_released_by": ctx.user_id,
                "report_released_at": now,
                "status": "decided",
                "decided_by": ctx.user_id,
                "decided_at": now,
            },
        )
    _event(session, ctx, row, "report_released", {"export_id": row.report_export_id})
    _audit(session, "privacy.report.released", row.id, {"export_id": row.report_export_id})
    return _out(session, ctx, row)


def report_download(
    session: Session, ctx: UserContext, request_id: uuid.UUID
) -> PrivacyDownloadOut:
    """A presigned link (<= 5 minutes) to the released report, for 24 hours after release
    (``privacy.request.manage`` or ``privacy.request.decide``, step-up). Audit
    ``privacy.report.downloaded``; SchoolOS sends it nowhere."""
    row = _load(session, ctx, request_id)
    if not (ctx.has(MANAGE) or ctx.has(DECIDE)):
        raise Forbidden()
    _require_step_up(ctx)
    if row.report_export_id is None or row.report_released_at is None:
        raise Conflict("The report has not been released yet.", code="report_not_released")
    if repo.now(session) > row.report_released_at + REPORT_KEEP:
        raise Conflict(
            "The report was released more than 24 hours ago. Build it again.", code="export_expired"
        )
    out = exports.module_document_download_url(
        session,
        row.report_export_id,
        kind=exports.SUBJECT_REPORT,
        filename_stem=f"access-report-{row.reference.lower()}",
        summary={"privacy_request_id": row.id},
    )
    _event(session, ctx, row, "report_downloaded", {"export_id": row.report_export_id})
    _audit(session, "privacy.report.downloaded", row.id, {"export_id": row.report_export_id})
    return PrivacyDownloadOut(
        url=out.url, expires_at=out.expires_at, filename=out.filename, content_type=out.content_type
    )


# --- erasure (FR-DPDP-012..014) ---------------------------------------------------------------


def _plan(
    session: Session, subject: Subject
) -> tuple[list[PrivacyErasurePlanRowOut], dict[str, _Contributor]]:
    classes = cfg.record_classes().classes
    registry = cfg.subjects_registry().contributors
    owners = {c.module: c for c in contributors()}
    dry: dict[str, ErasureOutcome] = {}
    for contributor in owners.values():
        if contributor.eraser is None:
            continue
        erasable = [k for k in contributor.classes if classes[k].erasure == "allowed"]
        if not erasable:
            continue
        for outcome in contributor.eraser(session, subject, erasable, dry_run=True):
            if outcome.class_key in erasable:
                dry[outcome.class_key] = outcome
    rows = []
    for key, cls in classes.items():
        if cls.erasure == "no_personal_data":
            continue
        entry = registry.get(cls.owner)
        # Classes of modules that are not contributors (the audit log) apply to everyone.
        if entry is not None and subject.kind not in entry.subjects:
            continue
        code = ERASURE_OUTCOME[cls.erasure]
        found = dry.get(key)
        reason = cls.basis
        if code == "retention_running":
            reason = f"Kept until its retention ends ({cls.retention.replace('**', '')}). {reason}"
        rows.append(
            PrivacyErasurePlanRowOut(
                class_key=key,
                label=cls.label,
                outcome=cast(Any, code),
                reason=reason,
                count=found.count if found is not None else None,
                manual_steps=list(found.manual_steps) if found is not None else [],
            )
        )
    return rows, owners


def erasure_plan(
    session: Session, ctx: UserContext, request_id: uuid.UUID
) -> PrivacyErasurePlanOut:
    """What can and cannot be erased for an erasure request (``privacy.read``)."""
    row = _load(session, ctx, request_id)
    if row.kind != "erasure":
        raise _invalid("kind", "not_an_erasure_request")
    subject = _subject(row)
    if subject is None:
        return PrivacyErasurePlanOut(request_id=row.id, rows=[])
    rows, _ = _plan(session, subject)
    return PrivacyErasurePlanOut(request_id=row.id, rows=rows)


def execute_erasure(
    session: Session, ctx: UserContext, request_id: uuid.UUID, data: PrivacyErasureIn
) -> PrivacyErasureResultOut:
    """Erase the chosen erasable classes in this transaction (FR-DPDP-013;
    ``privacy.request.decide``, step-up, maker-checker, verification recorded). Audit
    ``privacy.erasure.executed`` with counts per class."""
    row = _load(session, ctx, request_id, lock=True)
    _decider(ctx, row)
    _open(row)
    if row.kind != "erasure":
        raise _invalid("kind", "not_an_erasure_request")
    if row.verification_method is None:
        raise NotVerified()
    subject = _subject(row)
    if subject is None:
        raise Conflict(
            "The person this request is about is no longer on record.", code="no_subject"
        )
    plan, owners = _plan(session, subject)
    erasable = {p.class_key for p in plan if p.outcome == "erasable"}
    wanted = list(dict.fromkeys(data.class_keys))
    refused = [k for k in wanted if k not in erasable]
    if refused:
        raise Conflict(
            "Some of these records cannot be erased on request.", code="class_not_erasable"
        )
    classes = cfg.record_classes().classes
    counts: dict[str, int] = {}
    steps: dict[str, list[str]] = {}
    for module, contributor in owners.items():
        mine = [k for k in wanted if classes[k].owner == module]
        if not mine or contributor.eraser is None:
            continue
        for outcome in contributor.eraser(session, subject, mine, dry_run=False):
            if outcome.class_key not in mine:
                continue
            counts[outcome.class_key] = counts.get(outcome.class_key, 0) + outcome.count
            if outcome.manual_steps:
                steps.setdefault(outcome.class_key, []).extend(outcome.manual_steps)
    if "early_warning" in wanted:
        # Lead decision (R3-1A review): attendance and marks stay as school records, so the
        # daily early-warning rules may raise a new flag later; tell the person who decided.
        steps.setdefault("early_warning", []).append(
            "Attendance and marks are kept as school records, so the daily early-warning check "
            "may raise a new flag for this student later. Close it with a note if it is not "
            "needed."
        )
    kept = [p.class_key for p in plan if p.outcome != "erasable"]
    now = repo.now(session)
    with _db_errors():
        row = repo.update_request(
            session,
            row,
            {"status": "decided", "decided_by": ctx.user_id, "decided_at": now},
        )
    detail = {
        "counts": counts,
        "refused": kept,
        "manual_steps": sum(len(v) for v in steps.values()),
    }
    _event(session, ctx, row, "erasure_executed", detail)
    _audit(session, "privacy.erasure.executed", row.id, detail)
    return PrivacyErasureResultOut(
        request_id=row.id,
        erased=[
            PrivacyErasureResultRowOut(
                class_key=k, count=counts.get(k, 0), manual_steps=steps.get(k, [])
            )
            for k in wanted
        ],
        manual_steps=[s for v in steps.values() for s in v],
    )


# --- closing ----------------------------------------------------------------------------------


def close_request(
    session: Session, ctx: UserContext, request_id: uuid.UUID, data: PrivacyCloseIn
) -> PrivacyRequestOut:
    """Close a request (access, erasure and grievance: ``privacy.request.decide``, step-up, not
    the person who logged it; others: ``privacy.request.manage``). A grievance needs a written
    response (FR-DPDP-016); a correction closes only when its change requests are decided
    (FR-DPDP-011). Audit ``privacy.request.closed``."""
    row = _load(session, ctx, request_id, lock=True)
    _open(row)
    decide = row.kind in DECIDE_KINDS
    if decide:
        _decider(ctx, row)
    else:
        _need(ctx, MANAGE)
    _no_aadhaar("response_text", data.response_text)
    values: dict[str, Any] = {}
    if data.response_text is not None:
        values["response_ciphertext"] = _encrypt(
            session, row.id, "response_ciphertext", data.response_text
        )
        values["responded_on"] = _today(session)
    if row.kind == "grievance" and data.response_text is None and row.response_ciphertext is None:
        raise _invalid("response_text", "response_required")
    if row.kind == "correction":
        pending = []
        for link in repo.links_of(session, row.id):
            if link.link_kind != "change_request":
                continue
            info = changes.request_link(session, link.target_id)
            if info is not None and info.status not in CHANGE_DECIDED:
                pending.append(link.target_id)
        if pending:
            raise Conflict(
                "Wait until the linked change requests are decided.", code="changes_pending"
            )
    if decide and row.verification_method is None and row.kind != "grievance":
        raise NotVerified()
    now = repo.now(session)
    values.update({"status": "closed", "closed_at": now})
    if decide and row.decided_by is None:
        values.update({"decided_by": ctx.user_id, "decided_at": now})
    with _db_errors():
        row = repo.update_request(session, row, values)
    if data.response_text is not None:
        _event(session, ctx, row, "responded")
    _event(session, ctx, row, "closed", {"kind": row.kind})
    _audit(session, "privacy.request.closed", row.id, {"kind": row.kind})
    return _out(session, ctx, row)


# --- due-date reminders (FR-DPDP-006) ---------------------------------------------------------


def notify_due(session: Session) -> int:
    """Daily (07:00 IST): tell ``privacy.request.manage`` holders how many open requests are
    due tomorrow or overdue (counts only). Idempotent per school and day."""
    today = _today(session)
    rows = repo.due_open_requests(session, today + dt.timedelta(days=1))
    if not rows:
        return 0
    overdue = sum(1 for r in rows if r.due_on < today)
    due = len(rows) - overdue
    recipients = identity.active_holders(session, MANAGE)
    if not recipients:
        return 0
    tenant_id = repo.current_tenant_id(session)
    return notifications.notify(
        session,
        tenant_id=tenant_id,
        recipients=recipients,
        template_key="privacy.requests_due",
        params={"due": due, "overdue": overdue},
        dedupe_key=f"privacy-due:{today.isoformat()}",
    )


# --- export, key rotation, offboarding --------------------------------------------------------


def export_records(session: Session, *, include_sensitive: bool = False) -> list[RecordTable]:
    """The register for the school's full data export (FR-DPDP-018). Free texts only with
    ``include_sensitive``, decrypted, as a separate table."""
    tables = repo.export_tables(session)
    if include_sensitive:
        rows = []
        for r in repo.all_requests(session):
            rows.append(
                (
                    str(r.id),
                    _decrypt(session, r, "requester_name_ciphertext") or "",
                    _decrypt(session, r, "request_ciphertext") or "",
                    _decrypt(session, r, "notes_ciphertext") or "",
                    _decrypt(session, r, "response_ciphertext") or "",
                    _decrypt(session, r, "nominee_ciphertext") or "",
                )
            )
        tables.append(
            RecordTable(
                name="privacy_request_texts",
                columns=("id", "requester_name", "request", "notes", "response", "nominee"),
                rows=list(rows),
            )
        )
    return tables


def reencrypt_batch(
    session: Session, keyring: crypto.TenantKeyring, target_version: int, limit: int
) -> int:
    """DEK rotation (SEC-012): request texts under another key version are re-encrypted with
    the same associated data."""
    done = 0
    for row in repo.all_requests(session):
        values: dict[str, bytes] = {}
        for column in TEXT_COLUMNS:
            blob = getattr(row, column)
            if blob is None or ciphertext_key_version(bytes(blob)) == target_version:
                continue
            values[column] = crypto.reencrypt_value(
                session,
                bytes(blob),
                table=TABLE,
                column=column,
                row_id=row.id,
                key_version=target_version,
                keyring=keyring,
            )
        if values:
            repo.set_texts(session, row.id, values)
            done += 1
            if done >= limit:
                break
    return done


key_rotation.register_reencryptor("privacy_requests", reencrypt_batch)

exports.register_module_document(
    exports.SUBJECT_REPORT, permissions=(MANAGE,), builder=_report_document
)
exports.register_module_document(
    exports.PRIVACY_NOTICE, permissions=(READ,), builder=_notice_document
)

_PURGE = purging.PurgeTables(
    deleted=(
        "ops.privacy_request_links",
        "ops.privacy_request_events",
        "ops.privacy_requests",
        "ops.privacy_request_counters",
        "ops.privacy_notices",
    )
)


def tenant_data_counts(session: Session) -> dict[str, int]:
    return _PURGE.count(session)


def purge_tenant_data(session: Session) -> dict[str, int]:
    return _PURGE.delete(session)


tenancy.register_data_owner(
    tenancy.TenantDataOwner(name="privacy", count=tenant_data_counts, purge=purge_tenant_data)
)


__all__ = [
    "DECIDE",
    "MANAGE",
    "READ",
    "add_link",
    "build_report",
    "close_request",
    "contributors",
    "create_request",
    "erasure_plan",
    "execute_erasure",
    "export_records",
    "get_request",
    "letter_html",
    "list_notices",
    "list_templates",
    "notify_due",
    "preview_template",
    "record_classes",
    "record_notice",
    "release_report",
    "report_download",
    "request_notice_pdf",
    "search_requests",
    "subject_report",
    "update_request",
]
