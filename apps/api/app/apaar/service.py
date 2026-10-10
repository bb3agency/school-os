"""APAAR consent register public API (R1; ADR-0039; US-1901..US-1903; FR-APC-001..006, PRV-021).

Other modules call only these functions.

- **The register (FR-APC-001..003).** Per student, every decision a parent makes on the APAAR
  consent form is appended to ``sis.apaar_consents`` (``given``, ``refused``, ``pending``,
  ``withdrawn``) with who decided (relationship and, when on the record, the guardian), the date
  on the form, the form's language, the signed form as an evidence document (required for
  ``given``) and who recorded it when. Nothing is overwritten: the history is the register and
  the latest entry is the current state; a student with no entry is ``pending``. ``withdrawn``
  follows only a ``given``; once a parent decided, the state never goes back to ``pending``.
- **Refusal is a first-class answer (FR-APC-004).** The printed form offers refusal (Supreme
  Court order of 20 July 2026, *Abhishek Baxi v. Union of India*, as reported by a secondary
  source: ``config.yaml`` ``legal_basis``). Students whose parents refused are never pushed to an
  APAAR action: :func:`refused_student_ids` lets the data-quality checks skip the APAAR readiness
  rules (DQ-009, DQ-022) for them, and every list says "refused", not "missing".
- **Forms (FR-APC-004).** A4 print pages, one per student, in the school's chosen parent
  language (English or Telugu, owner decision D9; independent of ``SOS_TELUGU_ENABLED``). The
  form prints what the office holds (name, admission number, class, date of birth, PEN), never
  an Aadhaar number (invariant 4). Printing is audited (``apaar.consent_form.printed``).
- **Summary and follow-up (FR-APC-005).** Counts per section and the list of students with their
  state (``status=pending`` is the follow-up list).
- **Scope (SEC-015).** Every student is reached through ``app.students.service``: class teachers
  only their sections; anything else is 404.
- **Audit and logs (invariant 5, 7).** Every decision (``apaar.consent.recorded``), print and
  setting change is audited in the same transaction with ids, codes and counts only; notes,
  names and numbers are never logged or audited.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections import Counter
from collections.abc import Collection, Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any, Final
from zoneinfo import ZoneInfo

from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.apaar import repository as repo
from app.apaar import templates
from app.apaar.config import ApaarConfig, FormLanguage, load_config
from app.apaar.models import ApaarConsent
from app.apaar.schemas import (
    ApaarSettingsIn,
    ApaarSettingsOut,
    ConsentEntryOut,
    ConsentIn,
    ConsentRowOut,
    ConsentStatus,
    ConsentSummaryOut,
    SectionSummaryOut,
    StatusCounts,
    StudentConsentOut,
)
from app.audit import service as audit
from app.authz.context import UserContext
from app.authz.http import Page, decode_cursor, encode_cursor
from app.core import purge as purging
from app.core.errors import Conflict, NotFound, PreconditionFailed, ValidationFailed
from app.core.ids import new_id
from app.core.logging import get_context, get_logger
from app.core.records import RecordTable
from app.core.redaction import contains_full_aadhaar
from app.documents import service as documents
from app.identity import service as identity
from app.ops import service as ops
from app.students import service as students
from app.tenancy import service as tenancy

READ: Final = "apaar.consent.read"
RECORD: Final = "apaar.consent.record"
SETTINGS: Final = "tenant.settings.manage"
STUDENT_READ: Final = "student.read_basic"
PENDING: Final = "pending"
DQ_EVENT: Final = "student.values.changed"
DQ_KEY: Final = "apaar_consent"  # attribute_keys marker on the data-quality event
IST: Final = ZoneInfo("Asia/Kolkata")
AADHAAR_CODE: Final = "aadhaar_full_number_rejected"
AADHAAR_DETAIL: Final = "Don't enter Aadhaar numbers. Enter only the last 4 digits."
_FORM_KEYS: Final = ("full_name", "admission_no", "dob", "udise_pen")

log = get_logger(__name__)


class ConsentNotGiven(Conflict):
    status, code = 409, "consent_not_given"
    title = "Only a consent that was given can be withdrawn"


class ConsentAlreadyDecided(Conflict):
    status, code = 409, "consent_already_decided"
    title = "The parent already decided; record their new decision instead of pending"


def settings() -> ApaarConfig:
    return load_config()


@contextmanager
def _db_errors() -> Iterator[None]:
    try:
        yield
    except DBAPIError as exc:
        mapped = repo.translate_db_error(exc)
        if mapped is None:
            raise
        raise mapped from exc


def _error(field: str, code: str, message_key: str | None = None) -> dict[str, str]:
    return {"field": field, "code": code, "message_key": message_key or f"errors.{code}"}


def _audit(
    session: Session,
    *,
    action: str,
    resource_type: str,
    resource_id: uuid.UUID | None,
    summary: Mapping[str, Any],
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


def _reach_student(
    session: Session, ctx: UserContext, student_id: uuid.UUID, permissions: Sequence[str]
) -> None:
    """404 unless one of ``permissions`` (that the caller holds) reaches the student."""
    for permission in permissions:
        if not ctx.has(permission):
            continue
        try:
            students.ensure_in_scope(session, ctx, student_id, permission)
        except NotFound:
            continue
        return
    raise NotFound("Student not found")


def _entry_out(row: ApaarConsent, names: Mapping[uuid.UUID, str]) -> ConsentEntryOut:
    return ConsentEntryOut(
        id=row.id,
        seq=row.seq,
        status=row.status,
        relationship=row.relationship,
        guardian_id=row.guardian_id,
        decided_on=row.decided_on,
        form_language=row.form_language,
        evidence_document_id=row.evidence_document_id,
        note=row.note,
        recorded_by=row.recorded_by,
        recorded_by_name=names.get(row.recorded_by_membership),
        recorded_at=row.recorded_at,
    )


def _student_out(
    session: Session, student_id: uuid.UUID, rows: Sequence[ApaarConsent]
) -> StudentConsentOut:
    names = identity.member_display_names(session, {r.recorded_by_membership for r in rows})
    history = [_entry_out(r, names) for r in rows]
    current = history[0] if history else None
    return StudentConsentOut(
        student_id=student_id,
        status=current.status if current is not None else PENDING,
        current=current,
        history=history,
        version=current.seq if current is not None else 0,
    )


# --- one student ------------------------------------------------------------------------------


def get_consent(session: Session, ctx: UserContext, student_id: uuid.UUID) -> StudentConsentOut:
    """Current APAAR consent state and full history of one student (FR-APC-002; permission
    ``apaar.consent.read`` or ``apaar.consent.record``, in scope; else 404)."""
    _reach_student(session, ctx, student_id, (READ, RECORD))
    return _student_out(session, student_id, repo.history(session, student_id))


def _clean_note(note: str | None) -> str | None:
    if note is None or not note.strip():
        return None
    if contains_full_aadhaar(note):
        raise ValidationFailed(
            [_error("note", AADHAAR_CODE, "errors.aadhaar_last4_only")], detail=AADHAAR_DETAIL
        )
    if len(note) > settings().note_max_length:
        raise ValidationFailed([_error("note", "too_long")])
    return note


def _check_transition(previous: str | None, status: ConsentStatus) -> None:
    """``withdrawn`` follows only ``given``; ``pending`` only while nobody decided yet."""
    if status == "withdrawn" and previous != "given":
        raise ConsentNotGiven(
            "Only a consent that was given can be withdrawn. Record the parent's decision instead."
        )
    if status == PENDING and previous not in (None, PENDING):
        raise ConsentAlreadyDecided(
            "The parent already decided. Record their new decision (given or refused) instead."
        )


def _check_decision(
    session: Session, ctx: UserContext, student_id: uuid.UUID, data: ConsentIn
) -> tuple[str | None, uuid.UUID | None]:
    """Field rules of a decision (422 per field). Returns (relationship, guardian_id)."""
    problems: list[dict[str, str]] = []
    relationship: str | None = data.relationship
    guardian_id = data.guardian_id
    if guardian_id is not None:
        guardian = next(
            (g for g in students.list_guardians(session, ctx, student_id) if g.id == guardian_id),
            None,
        )
        if guardian is None:
            problems.append(_error("guardian_id", "not_found"))
        elif relationship is None:
            relationship = guardian.relationship
    if data.status != PENDING:
        if relationship is None:
            problems.append(_error("relationship", "missing"))
        if data.decided_on is None:
            problems.append(_error("decided_on", "missing"))
    if data.decided_on is not None:
        if data.decided_on > _today(session):
            problems.append(_error("decided_on", "date_in_future"))
        elif data.decided_on < settings().earliest_decision:
            problems.append(_error("decided_on", "date_too_early"))
    if data.status == "given" and data.evidence_document_id is None:
        # PRV-021: consent is recorded only with the signed form.
        problems.append(_error("evidence_document_id", "signed_form_required"))
    if data.evidence_document_id is not None and not (
        documents.is_visible(session, ctx, data.evidence_document_id)
        and documents.evidence_exists(session, data.evidence_document_id)
    ):
        problems.append(_error("evidence_document_id", "not_found"))
    if problems:
        raise ValidationFailed(problems)
    return relationship, guardian_id


def record_consent(
    session: Session,
    ctx: UserContext,
    student_id: uuid.UUID,
    data: ConsentIn,
    *,
    expected_version: int | None = None,
) -> StudentConsentOut:
    """Append a parent's decision to the register (FR-APC-001..003; permission
    ``apaar.consent.record`` in scope). ``expected_version`` (``If-Match``: the number of
    decisions read) guards against two people recording at once (412).

    Errors: 404 outside scope; 409 ``consent_not_given`` (withdrawn without a given consent),
    ``consent_already_decided`` (pending after a decision); 422 per field (``relationship`` /
    ``decided_on`` missing, ``signed_form_required``, ``evidence_document_id`` / ``guardian_id``
    ``not_found``, ``date_in_future``, ``aadhaar_full_number_rejected``). Audit
    ``apaar.consent.recorded`` (ids, statuses and codes only). A change to or from ``refused``
    re-checks the student's APAAR readiness findings (data-quality event)."""
    _reach_student(session, ctx, student_id, (RECORD,))
    students.lock_student_for_change(session, ctx, student_id)
    previous = repo.current(session, [student_id]).get(student_id)
    version = previous.seq if previous is not None else 0
    if expected_version is not None and expected_version != version:
        raise PreconditionFailed(
            "Someone else recorded a decision for this student just now. Reload and try again."
        )
    previous_status = previous.status if previous is not None else None
    _check_transition(previous_status, data.status)
    note = _clean_note(data.note)
    relationship, guardian_id = _check_decision(session, ctx, student_id, data)
    with _db_errors():
        row = repo.insert(
            session,
            id=new_id(),
            student_id=student_id,
            seq=version + 1,
            status=data.status,
            relationship=relationship,
            guardian_id=guardian_id,
            decided_on=data.decided_on,
            form_language=data.form_language,
            evidence_document_id=data.evidence_document_id,
            note=note,
            recorded_by=ctx.user_id,
            recorded_by_membership=ctx.membership_id,
        )
    _audit(
        session,
        action="apaar.consent.recorded",
        resource_type="student",
        resource_id=student_id,
        summary={
            "consent_id": row.id,
            "seq": row.seq,
            "status": row.status,
            "previous_status": previous_status or PENDING,
            "relationship": relationship,
            "guardian_linked": guardian_id is not None,
            "has_form": row.evidence_document_id is not None,
            "evidence_document_id": row.evidence_document_id,
            "form_language": row.form_language,
            "has_note": note is not None,
        },
    )
    if "refused" in (data.status, previous_status):
        # DQ-009 / DQ-022 skip students whose parents refused (FR-APC-006): re-check now.
        ops.enqueue_event(
            session, DQ_EVENT, {"student_ids": [student_id], "attribute_keys": [DQ_KEY]}
        )
    log.info("apaar.consent.recorded", resource_type="student", resource_id=student_id)
    return _student_out(session, student_id, repo.history(session, student_id))


# --- lists, summary, follow-up ----------------------------------------------------------------


def _students_in_reach(
    session: Session,
    ctx: UserContext,
    *,
    section_id: uuid.UUID | None,
    class_id: uuid.UUID | None,
) -> list[uuid.UUID]:
    """Current-year students the caller reaches with ``apaar.consent.read``."""
    return students.list_students_in_scope(
        session,
        ctx,
        section_ids=[section_id] if section_id is not None else None,
        class_ids=[class_id] if class_id is not None else None,
        permissions=(READ,),
    )


def _rows(session: Session, ctx: UserContext, ids: Collection[uuid.UUID]) -> list[ConsentRowOut]:
    info = students.summaries(session, ctx, ids)
    placed = [sid for sid in ids if sid in info and info[sid].section_id is not None]
    current = repo.current(session, placed)
    apaar = students.canonical_values(session, placed, ["apaar_id"])
    rows = []
    for sid in placed:
        s = info[sid]
        c = current.get(sid)
        has_apaar = (v := apaar.get(sid, {}).get("apaar_id")) is not None and v.value is not None
        rows.append(
            ConsentRowOut(
                student_id=sid,
                display_name=s.display_name,
                admission_no=s.admission_no,
                class_section=s.class_section,
                section_id=s.section_id,
                status=c.status if c is not None else PENDING,
                decided_on=c.decided_on if c is not None else None,
                has_form=c is not None and c.evidence_document_id is not None,
                recorded_at=c.recorded_at if c is not None else None,
                version=c.seq if c is not None else 0,
                has_apaar_id=has_apaar,
            )
        )
    rows.sort(key=lambda r: (r.class_section or "", r.display_name or "", str(r.student_id)))
    return rows


def _offset(cursor: str | None) -> int:
    decoded = decode_cursor(cursor)
    if decoded is None:
        return 0
    offset = decoded.get("o")
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise ValidationFailed([_error("cursor", "invalid", "errors.invalid_cursor")])
    return offset


def list_consents(
    session: Session,
    ctx: UserContext,
    *,
    section_id: uuid.UUID | None = None,
    class_id: uuid.UUID | None = None,
    status: ConsentStatus | None = None,
    limit: int = 50,
    cursor: str | None = None,
) -> Page[ConsentRowOut]:
    """Students of the current academic year with their consent state, by class and name
    (FR-APC-005; permission ``apaar.consent.read``; class teachers: their sections).
    ``status=pending`` is the follow-up list (no decision yet, or form sent and awaited)."""
    offset = _offset(cursor)
    rows = _rows(
        session, ctx, _students_in_reach(session, ctx, section_id=section_id, class_id=class_id)
    )
    if status is not None:
        rows = [r for r in rows if r.status == status]
    page = rows[offset : offset + limit]
    more = len(rows) > offset + limit
    return Page[ConsentRowOut](
        data=page, next_cursor=encode_cursor({"o": offset + limit}) if more else None
    )


def _counts(statuses: Sequence[str]) -> StatusCounts:
    c = Counter(statuses)
    return StatusCounts(
        total=len(statuses),
        given=c["given"],
        refused=c["refused"],
        pending=c[PENDING],
        withdrawn=c["withdrawn"],
    )


def summary(
    session: Session,
    ctx: UserContext,
    *,
    section_id: uuid.UUID | None = None,
    class_id: uuid.UUID | None = None,
) -> ConsentSummaryOut:
    """Given / refused / pending / withdrawn per section (FR-APC-005; permission
    ``apaar.consent.read``; class teachers: their sections). Counts only."""
    rows = _rows(
        session, ctx, _students_in_reach(session, ctx, section_id=section_id, class_id=class_id)
    )
    by_section: dict[uuid.UUID, list[ConsentRowOut]] = {}
    for r in rows:
        if r.section_id is not None:
            by_section.setdefault(r.section_id, []).append(r)
    sections = [
        SectionSummaryOut(
            section_id=sid,
            class_section=members[0].class_section or "",
            counts=_counts([m.status for m in members]),
        )
        for sid, members in by_section.items()
    ]
    sections.sort(key=lambda s: s.class_section)
    return ConsentSummaryOut(totals=_counts([r.status for r in rows]), sections=sections)


# --- printed forms ----------------------------------------------------------------------------


def _form_language(session: Session, language: FormLanguage | None) -> FormLanguage:
    if language is not None:
        return language
    row = repo.get_settings(session)
    chosen: FormLanguage = "te" if row is not None and row.form_language == "te" else "en"
    return chosen


def _form_students(
    session: Session, ctx: UserContext, ids: Sequence[uuid.UUID]
) -> list[templates.FormStudent]:
    info = students.summaries(session, ctx, ids)
    values = students.canonical_values(session, [i for i in ids if i in info], _FORM_KEYS)
    out: list[templates.FormStudent] = []
    for sid in ids:
        if sid not in info:
            continue
        per = values.get(sid, {})

        def value(key: str, per: Mapping[str, Any] = per) -> str | None:
            v = per.get(key)
            return v.value if v is not None else None

        dob = value("dob")
        out.append(
            templates.FormStudent(
                name=value("full_name") or info[sid].display_name,
                admission_no=value("admission_no") or info[sid].admission_no,
                class_section=info[sid].class_section,
                dob=dt.date.fromisoformat(dob) if dob else None,
                pen=value("udise_pen"),
            )
        )
    return out


def _page(session: Session, language: FormLanguage, forms: Sequence[templates.FormStudent]) -> str:
    return templates.render_forms(
        settings(), language=language, school=tenancy.get_tenant(session).name, students=forms
    )


def student_form(
    session: Session, ctx: UserContext, student_id: uuid.UUID, *, language: FormLanguage | None
) -> str:
    """The printable consent form of one student (FR-APC-004; permission
    ``apaar.consent.read`` or ``apaar.consent.record``, in scope). Audit
    ``apaar.consent_form.printed``."""
    _reach_student(session, ctx, student_id, (READ, RECORD))
    lang = _form_language(session, language)
    forms = _form_students(session, ctx, [student_id])
    if not forms:
        raise NotFound("Student not found")
    page = _page(session, lang, forms)
    _audit(
        session,
        action="apaar.consent_form.printed",
        resource_type="student",
        resource_id=student_id,
        summary={"count": 1, "language": lang, "form_version": settings().form_version},
    )
    return page


def section_forms(
    session: Session,
    ctx: UserContext,
    section_id: uuid.UUID,
    *,
    status: ConsentStatus | None,
    language: FormLanguage | None,
) -> str:
    """Printable consent forms for a section of the current year, one page per student, in
    class-list order; ``status`` prints only those students (e.g. ``pending`` to send again)
    (FR-APC-004; permission ``apaar.consent.read`` or ``apaar.consent.record``; class teachers:
    their sections; a section outside the caller's reach is 404). 409 ``too_many_forms`` above
    ``max_forms_per_print``; 409 ``no_forms`` when nobody matches. Audit
    ``apaar.consent_form.printed`` (section and count)."""
    try:
        tenancy.get_section(session, section_id)
    except NotFound:
        raise NotFound("Section not found") from None
    reach: set[uuid.UUID] = set()
    for permission in (READ, RECORD):
        if ctx.has(permission):
            reach.update(
                students.list_students_in_scope(
                    session, ctx, section_ids=[section_id], permissions=(permission,)
                )
            )
    if not reach and not _section_in_reach(session, ctx, section_id):
        raise NotFound("Section not found")
    rows = _rows(session, ctx, reach)
    if status is not None:
        rows = [r for r in rows if r.status == status]
    cfg = settings()
    if not rows:
        raise Conflict("No student in this section matches.", code="no_forms")
    if len(rows) > cfg.max_forms_per_print:
        raise Conflict(
            f"Print at most {cfg.max_forms_per_print} forms at a time.", code="too_many_forms"
        )
    lang = _form_language(session, language)
    page = _page(session, lang, _form_students(session, ctx, [r.student_id for r in rows]))
    _audit(
        session,
        action="apaar.consent_form.printed",
        resource_type="section",
        resource_id=section_id,
        summary={
            "count": len(rows),
            "language": lang,
            "status_filter": status,
            "form_version": cfg.form_version,
        },
    )
    return page


def _section_in_reach(session: Session, ctx: UserContext, section_id: uuid.UUID) -> bool:
    """An empty section the caller may still see (school-wide holders, or a scoped holder's own
    section): ``no_forms`` instead of 404."""
    for permission in (READ, RECORD):
        if not ctx.has(permission):
            continue
        grant = ctx.scope_for(permission)
        if grant.school_wide or section_id in grant.section_ids:
            return True
        if grant.class_ids:
            section = tenancy.get_section(session, section_id)
            if section.class_id in grant.class_ids:
                return True
    return False


# --- settings ---------------------------------------------------------------------------------


def get_settings(session: Session) -> ApaarSettingsOut:
    """The school's parent-form language (``en`` until set; version 0 before the first save)."""
    row = repo.get_settings(session)
    if row is None:
        return ApaarSettingsOut(form_language="en", version=0)
    return ApaarSettingsOut(form_language=row.form_language, version=row.version)


def update_settings(
    session: Session, ctx: UserContext, data: ApaarSettingsIn, *, expected_version: int
) -> ApaarSettingsOut:
    """Set the language of the printed parent form (owner decision D9; permission
    ``tenant.settings.manage``, step-up; ``If-Match``: 0 for the first save). Audit
    ``apaar.settings.updated``."""
    row = repo.get_settings(session, lock=True)
    current = row.version if row is not None else 0
    if expected_version != current:
        raise PreconditionFailed("The setting was changed by someone else. Reload it.")
    before = row.form_language if row is not None else "en"
    if row is None:
        row = repo.insert_settings(
            session, id=new_id(), form_language=data.form_language, updated_by=ctx.user_id
        )
    elif row.form_language != data.form_language:
        row = repo.update_settings(
            session, row.id, form_language=data.form_language, updated_by=ctx.user_id
        )
    else:
        return ApaarSettingsOut(form_language=row.form_language, version=row.version)
    _audit(
        session,
        action="apaar.settings.updated",
        resource_type="apaar_settings",
        resource_id=row.id,
        summary={"form_language": data.form_language, "previous_form_language": before},
    )
    return ApaarSettingsOut(form_language=row.form_language, version=row.version)


# --- for other modules ------------------------------------------------------------------------


def consent_statuses(session: Session, student_ids: Collection[uuid.UUID]) -> dict[uuid.UUID, str]:
    """Current consent status of each given student that has a decision (others: pending).
    ``student_ids`` must come from the caller's own scoped reads (dq, exports)."""
    return repo.statuses(session, student_ids)


def refused_student_ids(
    session: Session, student_ids: Collection[uuid.UUID]
) -> frozenset[uuid.UUID]:
    """Students whose parents refused (or withdrew) APAAR consent: never pushed to an APAAR
    action (FR-APC-006). ``student_ids`` must come from the caller's own scoped reads."""
    return frozenset(
        sid
        for sid, s in repo.statuses(session, student_ids).items()
        if s in ("refused", "withdrawn")
    )


def export_records(session: Session) -> list[RecordTable]:
    """Worker only: the consent register and setting for the school's full data export
    (``app.admin``; the caller checked ``tenant.export_all`` and audits the export). Notes are
    C2 school records and go with the register."""
    return repo.export_tables(session)


# --- offboarding purge (FR-PLT-005, ADR-0029) ----------------------------------------------------
_PURGE = purging.PurgeTables(deleted=("sis.apaar_consents", "sis.apaar_consent_settings"))


def tenant_data_counts(session: Session) -> dict[str, int]:
    """Rows of the current school in this module's tables (offboarding inventory)."""
    return _PURGE.count(session)


def purge_tenant_data(session: Session) -> dict[str, int]:
    """Delete the current school's rows of this module (offboarding only; ADR-0029)."""
    return _PURGE.delete(session)


tenancy.register_data_owner(
    tenancy.TenantDataOwner(name="apaar", count=tenant_data_counts, purge=purge_tenant_data)
)


__all__ = [
    "READ",
    "RECORD",
    "SETTINGS",
    "ConsentAlreadyDecided",
    "ConsentNotGiven",
    "consent_statuses",
    "export_records",
    "get_consent",
    "get_settings",
    "list_consents",
    "purge_tenant_data",
    "record_consent",
    "refused_student_ids",
    "section_forms",
    "student_form",
    "summary",
    "tenant_data_counts",
    "update_settings",
]
