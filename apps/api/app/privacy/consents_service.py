"""Consents for optional purposes (R3-1B; ADR-0049; US-2701..US-2703; FR-DPDP-020..026, PRV-006).

Other modules call only these functions (R3-1A's ``app.privacy.service`` re-exports
:func:`consent_state` and :func:`record_withdrawal_from_request` at merge).

- **Purposes (FR-DPDP-020).** The school's optional purposes (``sis.consent_purposes``), enabled
  from the suggestions in ``consent_purposes.yaml`` or added by the school
  (``tenant.settings.manage``, step-up). A purpose's text is frozen once a decision refers to its
  current ``form_version``: editing it makes the next version (the DB trigger refuses anything
  else); earlier answers stay with their version.
- **Decisions (FR-DPDP-021, FR-DPDP-022).** Append-only ``sis.consents`` per student and purpose
  with the APAAR register's rules: ``given`` only with the signed form (422
  ``signed_form_required``), ``withdrawn`` only after ``given`` (409 ``consent_not_given``), no
  return to ``pending`` (409 ``consent_already_decided``), ``If-Match`` on the number of decisions
  read; notes refuse Aadhaar-like numbers and are never logged or audited.
- **Forms (FR-DPDP-023).** A4 forms per student or section with "I agree" and "I do not agree"
  side by side for every active purpose, in English while ``SOS_TELUGU_ENABLED`` is off (D47),
  otherwise in the asked or the school's parent language; also a PDF through
  ``app.exports`` (kind ``consent_forms``).
- **State for other modules (FR-DPDP-024).** :func:`consent_state` returns ``given`` only when
  the latest decision is ``given`` and the purpose is still active; anything else is
  ``not_given``. Nothing in R3 uses it automatically.
- **One view (FR-DPDP-025).** :func:`consent_view` shows the optional purposes, the APAAR
  decision (``apaar.service``) and the guardians' WhatsApp opt-ins (``messaging.service``), each
  read-only from its own register and only for readers of that register.
- **Scope (FR-DPDP-026, SEC-015).** Every student is reached through ``students.service`` with
  ``consent.read`` / ``consent.record`` (class teachers: their sections); anything else is 404.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections import Counter
from collections.abc import Collection, Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any, Final, Literal
from zoneinfo import ZoneInfo

from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.apaar import service as apaar
from app.audit import service as audit
from app.authz.context import UserContext
from app.core import languages
from app.core import purge as purging
from app.core.errors import Conflict, NotFound, PreconditionFailed, ValidationFailed
from app.core.ids import new_id
from app.core.logging import get_context, get_logger
from app.core.pdf import PrintDocument
from app.core.records import RecordTable
from app.core.redaction import contains_full_aadhaar
from app.documents import service as documents
from app.identity import service as identity
from app.messaging import service as messaging
from app.privacy import consents_repository as repo
from app.privacy import consents_templates as templates
from app.privacy.consents_config import ConsentConfig, FormLanguage, load_config
from app.privacy.consents_models import Consent, ConsentPurpose
from app.privacy.consents_schemas import (
    AnswerCounts,
    ApaarStateOut,
    ConsentState,
    ConsentViewOut,
    GuardianOptinsBrief,
    OptinCategoryOut,
    PurposeAnswer,
    PurposeConsentEntryOut,
    PurposeConsentIn,
    PurposeConsentSummaryOut,
    PurposeCreate,
    PurposeListOut,
    PurposeOut,
    PurposeSectionCountsOut,
    PurposeStateOut,
    PurposeSuggestionOut,
    PurposeSummaryOut,
    PurposeUpdate,
    StudentConsentsOut,
    WhatsappStateOut,
)
from app.privacy.consents_subjects import subject_report
from app.students import service as students
from app.tenancy import service as tenancy

READ: Final = "consent.read"
RECORD: Final = "consent.record"
SETTINGS: Final = "tenant.settings.manage"
APAAR_READERS: Final = ("apaar.consent.read", "apaar.consent.record")
OPTIN_READERS: Final = ("message.read", "messaging.optin.record")
PENDING: Final = "pending"
NOT_ANSWERED: Final = "not_answered"
IST: Final = ZoneInfo("Asia/Kolkata")
AADHAAR_CODE: Final = "aadhaar_full_number_rejected"
AADHAAR_DETAIL: Final = "Don't enter Aadhaar numbers. Enter only the last 4 digits."

log = get_logger(__name__)


class ConsentNotGiven(Conflict):
    status, code = 409, "consent_not_given"
    title = "Only a consent that was given can be withdrawn"


class ConsentAlreadyDecided(Conflict):
    status, code = 409, "consent_already_decided"
    title = "The family already decided; record their new decision instead of pending"


def settings() -> ConsentConfig:
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


# --- purposes -----------------------------------------------------------------------------------


def _purpose_out(row: ConsentPurpose, answered: int, current_answered: bool) -> PurposeOut:
    return PurposeOut(
        id=row.id,
        key=row.key,
        label_en=row.label_en,
        label_te=row.label_te,
        description=row.description,
        form_version=row.form_version,
        active=row.active,
        suggested_key=row.suggested_key,
        answered=answered,
        current_version_answered=current_answered,
        version=row.version,
    )


def _one_purpose_out(session: Session, row: ConsentPurpose) -> PurposeOut:
    versions = repo.answered_versions(session, row.id)
    count = repo.answered_counts(session).get(row.id, 0)
    return _purpose_out(row, count, row.form_version in versions)


def list_purposes(session: Session) -> PurposeListOut:
    """The school's purposes (active first) and the suggestions it may enable (FR-DPDP-020;
    ``consent.read``, ``consent.record`` or ``tenant.settings.manage``)."""
    rows = repo.list_purposes(session)
    counts = repo.answered_counts(session)
    data = []
    for row in rows:
        versions = repo.answered_versions(session, row.id) if counts.get(row.id) else set()
        data.append(_purpose_out(row, counts.get(row.id, 0), row.form_version in versions))
    data.sort(key=lambda p: (not p.active, p.label_en.casefold()))
    used = {r.suggested_key for r in rows if r.suggested_key}
    suggestions = [
        PurposeSuggestionOut(
            key=key,
            label_en=s.label_en,
            label_te=s.label_te,
            description=s.description,
            enabled=key in used,
        )
        for key, s in settings().suggestions.items()
    ]
    return PurposeListOut(data=data, suggestions=suggestions)


def _check_active_limit(session: Session) -> None:
    limit = settings().max_active_purposes
    if len(repo.list_purposes(session, active_only=True)) >= limit:
        raise Conflict(
            f"A school can ask about at most {limit} purposes at a time. Switch one off first.",
            code="too_many_purposes",
        )


def create_purpose(session: Session, ctx: UserContext, data: PurposeCreate) -> PurposeOut:
    """Enable a suggested purpose or add the school's own (``tenant.settings.manage``, step-up;
    FR-DPDP-020). 409 ``purpose_exists`` for a key already used, ``too_many_purposes`` above the
    limit; 422 ``unknown_suggestion``. Audit ``consent.purpose.created`` (suggestion, version)."""
    if data.suggested_key is not None:
        suggestion = settings().suggestions.get(data.suggested_key)
        if suggestion is None:
            raise ValidationFailed([_error("suggested_key", "unknown_suggestion")])
        key = data.key or data.suggested_key
        label_en = data.label_en or suggestion.label_en
        label_te = data.label_te or suggestion.label_te
        description = data.description or suggestion.description
    elif data.key is not None and data.label_en is not None and data.description is not None:
        key, label_en, label_te, description = (
            data.key,
            data.label_en,
            data.label_te,
            data.description,
        )
    else:  # pragma: no cover - PurposeCreate refuses this shape
        raise ValidationFailed([_error("key", "missing")])
    if repo.purpose_by_key(session, key) is not None:
        raise Conflict("A purpose with this key already exists.", code="purpose_exists")
    _check_active_limit(session)
    with _db_errors():
        row = repo.insert_purpose(
            session,
            id=new_id(),
            key=key,
            label_en=label_en,
            label_te=label_te,
            description=description,
            suggested_key=data.suggested_key,
            created_by=ctx.user_id,
        )
    _audit(
        session,
        action="consent.purpose.created",
        resource_type="consent_purpose",
        resource_id=row.id,
        summary={
            "suggested_key": row.suggested_key,
            "form_version": row.form_version,
        },
    )
    return _purpose_out(row, 0, False)


def update_purpose(
    session: Session,
    ctx: UserContext,
    purpose_id: uuid.UUID,
    data: PurposeUpdate,
    *,
    expected_version: int,
) -> PurposeOut:
    """Edit a purpose's text or switch it on or off (``tenant.settings.manage``, step-up;
    ``If-Match``). A text change on a purpose whose current version families answered makes
    the next ``form_version`` (ADR-0049 decision 1). Audit ``consent.purpose.updated`` (field
    names, versions, never the text)."""
    row = repo.get_purpose(session, purpose_id, lock=True)
    if row is None:
        raise NotFound("Purpose not found")
    if row.version != expected_version:
        raise PreconditionFailed("Someone else changed this purpose. Reload it.")
    values: dict[str, Any] = {}
    if data.label_en is not None and data.label_en != row.label_en:
        values["label_en"] = data.label_en
    if data.clear_label_te and row.label_te is not None:
        values["label_te"] = None
    elif data.label_te is not None and data.label_te != row.label_te:
        values["label_te"] = data.label_te
    if data.description is not None and data.description != row.description:
        values["description"] = data.description
    text_changed = sorted(values)
    if data.active is not None and data.active != row.active:
        if data.active:
            _check_active_limit(session)
        values["active"] = data.active
    if not values:
        return _one_purpose_out(session, row)
    previous_version = row.form_version
    if text_changed and row.form_version in repo.answered_versions(session, row.id):
        values["form_version"] = row.form_version + 1
    with _db_errors():
        row = repo.update_purpose(session, purpose_id, **values)
    _audit(
        session,
        action="consent.purpose.updated",
        resource_type="consent_purpose",
        resource_id=row.id,
        summary={
            "fields": sorted(values),
            "form_version": row.form_version,
            "previous_form_version": previous_version,
            "active": row.active,
        },
    )
    return _one_purpose_out(session, row)


# --- one student --------------------------------------------------------------------------------


def _entry_out(row: Consent, names: Mapping[uuid.UUID, str]) -> PurposeConsentEntryOut:
    return PurposeConsentEntryOut(
        id=row.id,
        purpose_id=row.purpose_id,
        form_version=row.form_version,
        seq=row.seq,
        status=row.status,
        relationship=row.relationship,
        guardian_id=row.guardian_id,
        decided_on=row.decided_on,
        form_language=row.form_language,
        evidence_document_id=row.evidence_document_id,
        note=row.note,
        privacy_request_id=row.privacy_request_id,
        recorded_by=row.recorded_by,
        recorded_by_name=names.get(row.recorded_by_membership),
        recorded_at=row.recorded_at,
    )


def _state(purpose: ConsentPurpose, latest: Consent | None) -> ConsentState:
    """FR-DPDP-024: given only when the latest decision is given (for the current or an earlier
    version of a purpose that is still active)."""
    if not purpose.active or latest is None or latest.status != "given":
        return "not_given"
    return "given" if latest.form_version <= purpose.form_version else "not_given"


def _purpose_states(
    purposes: Sequence[ConsentPurpose],
    rows: Sequence[Consent],
    names: Mapping[uuid.UUID, str],
    *,
    with_history: bool,
) -> list[PurposeStateOut]:
    by_purpose: dict[uuid.UUID, list[Consent]] = {}
    for r in rows:
        by_purpose.setdefault(r.purpose_id, []).append(r)
    out = []
    for p in purposes:
        history = sorted(by_purpose.get(p.id, []), key=lambda r: r.seq, reverse=True)
        if not p.active and not history:
            continue
        latest = history[0] if history else None
        answer: PurposeAnswer = latest.status if latest is not None else NOT_ANSWERED  # type: ignore[assignment]
        out.append(
            PurposeStateOut(
                purpose_id=p.id,
                key=p.key,
                label_en=p.label_en,
                active=p.active,
                form_version=p.form_version,
                answer=answer,
                state=_state(p, latest),
                current=_entry_out(latest, names) if latest is not None else None,
                history=[_entry_out(r, names) for r in history] if with_history else [],
            )
        )
    return out


def _student_out(session: Session, student_id: uuid.UUID) -> StudentConsentsOut:
    rows = repo.history(session, student_id)
    names = identity.member_display_names(session, {r.recorded_by_membership for r in rows})
    purposes = repo.list_purposes(session)
    return StudentConsentsOut(
        student_id=student_id,
        purposes=_purpose_states(purposes, rows, names, with_history=True),
        version=len(rows),
    )


def get_student_consents(
    session: Session, ctx: UserContext, student_id: uuid.UUID
) -> StudentConsentsOut:
    """Every purpose's current answer and history for one student (FR-DPDP-021; ``consent.read``
    or ``consent.record`` in scope, else 404)."""
    _reach_student(session, ctx, student_id, (READ, RECORD))
    return _student_out(session, student_id)


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


def _check_transition(previous: str | None, status: str) -> None:
    if status == "withdrawn" and previous != "given":
        raise ConsentNotGiven(
            "Only a consent that was given can be withdrawn. Record the family's decision instead."
        )
    if status == PENDING and previous not in (None, PENDING):
        raise ConsentAlreadyDecided(
            "The family already decided. Record their new decision (agreed or not) instead."
        )


def _check_decision(  # noqa: PLR0912 - one rule per branch
    session: Session,
    ctx: UserContext,
    student_id: uuid.UUID,
    data: PurposeConsentIn,
    current_on: dt.date | None,
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
        elif current_on is not None and data.decided_on < current_on:
            problems.append(_error("decided_on", "before_current_decision"))
    if data.status == "given" and data.evidence_document_id is None:
        problems.append(_error("evidence_document_id", "signed_form_required"))
    if data.evidence_document_id is not None and not (
        documents.is_visible(session, ctx, data.evidence_document_id)
        and documents.evidence_exists(session, data.evidence_document_id)
    ):
        problems.append(_error("evidence_document_id", "not_found"))
    if problems:
        raise ValidationFailed(problems)
    return relationship, guardian_id


def _active_purpose(session: Session, purpose_id: uuid.UUID) -> ConsentPurpose:
    purpose = repo.get_purpose(session, purpose_id)
    if purpose is None:
        raise ValidationFailed([_error("purpose_id", "not_found")])
    return purpose


def record_consent(
    session: Session,
    ctx: UserContext,
    student_id: uuid.UUID,
    data: PurposeConsentIn,
    *,
    expected_version: int | None = None,
    privacy_request_id: uuid.UUID | None = None,
) -> StudentConsentsOut:
    """Append a family's decision on one purpose (FR-DPDP-021, FR-DPDP-022; ``consent.record``
    in scope). ``expected_version`` (``If-Match``: the number of the student's decisions read)
    guards against two people recording at once (412).

    Errors: 404 outside scope; 409 ``consent_not_given``, ``consent_already_decided``,
    ``purpose_inactive`` (a new ``given`` or ``pending`` on a purpose switched off); 422 per field
    (``signed_form_required``, ``relationship`` / ``decided_on`` missing, ``not_found``,
    ``date_in_future``, ``before_current_decision``, ``aadhaar_full_number_rejected``). Audit
    ``consent.recorded`` (ids and statuses; never the note, nor the purpose key)."""
    _reach_student(session, ctx, student_id, (RECORD,))
    students.lock_student_for_change(session, ctx, student_id)
    purpose = _active_purpose(session, data.purpose_id)
    if not purpose.active and data.status in ("given", PENDING):
        raise Conflict(
            "This purpose is switched off. Switch it on in Consent purposes first.",
            code="purpose_inactive",
        )
    version = len(repo.history(session, student_id))
    if expected_version is not None and expected_version != version:
        raise PreconditionFailed(
            "Someone else recorded a decision for this student just now. Reload and try again."
        )
    previous = repo.current(session, [student_id], data.purpose_id).get(
        (student_id, data.purpose_id)
    )
    previous_status = previous.status if previous is not None else None
    _check_transition(previous_status, data.status)
    note = _clean_note(data.note)
    relationship, guardian_id = _check_decision(
        session, ctx, student_id, data, previous.decided_on if previous is not None else None
    )
    with _db_errors():
        row = repo.insert(
            session,
            id=new_id(),
            student_id=student_id,
            purpose_id=purpose.id,
            form_version=purpose.form_version,
            seq=(previous.seq if previous is not None else 0) + 1,
            status=data.status,
            relationship=relationship,
            guardian_id=guardian_id,
            decided_on=data.decided_on,
            form_language=data.form_language,
            evidence_document_id=data.evidence_document_id,
            note=note,
            privacy_request_id=privacy_request_id,
            recorded_by=ctx.user_id,
            recorded_by_membership=ctx.membership_id,
        )
    _audit(
        session,
        action="consent.recorded",
        resource_type="student",
        resource_id=student_id,
        summary={
            "consent_id": row.id,
            "purpose_id": purpose.id,
            "form_version": row.form_version,
            "seq": row.seq,
            "status": row.status,
            "previous_status": previous_status or NOT_ANSWERED,
            "relationship": relationship,
            "guardian_linked": guardian_id is not None,
            "has_form": row.evidence_document_id is not None,
            "evidence_document_id": row.evidence_document_id,
            "privacy_request_id": privacy_request_id,
            "has_note": note is not None,
        },
    )
    log.info("consent.recorded", resource_type="student", resource_id=student_id)
    return _student_out(session, student_id)


def record_withdrawal_from_request(
    session: Session,
    ctx: UserContext,
    student_id: uuid.UUID,
    purpose_id: uuid.UUID,
    *,
    privacy_request_id: uuid.UUID,
    relationship: Literal["father", "mother", "guardian"],
    decided_on: dt.date,
) -> StudentConsentsOut:
    """A withdrawal asked through the data-principal register (ADR-0049 decision 6; FR-DPDP-026):
    recorded here as ``withdrawn`` and linked to the request (R3-1A calls this from
    ``app.privacy`` with the caller's ``consent.record``)."""
    data = PurposeConsentIn(
        purpose_id=purpose_id,
        status="withdrawn",
        relationship=relationship,
        decided_on=decided_on,
    )
    return record_consent(session, ctx, student_id, data, privacy_request_id=privacy_request_id)


# --- the one consent view (FR-DPDP-025) --------------------------------------------------------


def _apaar_state(session: Session, ctx: UserContext, student_id: uuid.UUID) -> ApaarStateOut | None:
    if not any(ctx.has(p) for p in APAAR_READERS):
        return None
    try:
        _reach_student(session, ctx, student_id, APAAR_READERS)
    except NotFound:
        return None
    entry = apaar.current_decision(session, student_id)
    if entry is None:
        return ApaarStateOut(status=PENDING, decided_on=None, has_form=False, recorded_at=None)
    return ApaarStateOut(
        status=entry.status,
        decided_on=entry.decided_on,
        has_form=entry.evidence_document_id is not None,
        recorded_at=entry.recorded_at,
    )


def _whatsapp_state(
    session: Session, ctx: UserContext, student_id: uuid.UUID
) -> WhatsappStateOut | None:
    if not any(ctx.has(p) for p in OPTIN_READERS):
        return None
    try:
        _reach_student(session, ctx, student_id, OPTIN_READERS)
    except NotFound:
        return None
    if not messaging.available():
        return WhatsappStateOut(available=False, guardians=[])
    guardians = students.guardians_of_students(session, [student_id])
    states = messaging.optin_states(session, [g.guardian_id for g in guardians])
    return WhatsappStateOut(
        available=True,
        guardians=[
            GuardianOptinsBrief(
                guardian_id=g.guardian_id,
                full_name=g.full_name,
                relationship=g.relationship,
                categories=[
                    OptinCategoryOut(category=c.category, status=c.status, decided_on=c.decided_on)
                    for c in states.get(g.guardian_id, [])
                ],
            )
            for g in guardians
        ],
    )


def consent_view(session: Session, ctx: UserContext, student_id: uuid.UUID) -> ConsentViewOut:
    """The one read-only consent view of a student (FR-DPDP-025; ``consent.read`` in scope, else
    404). APAAR and WhatsApp parts appear only for readers of those registers; nothing changes."""
    _reach_student(session, ctx, student_id, (READ,))
    rows = repo.history(session, student_id)
    names = identity.member_display_names(session, {r.recorded_by_membership for r in rows})
    return ConsentViewOut(
        student_id=student_id,
        purposes=_purpose_states(repo.list_purposes(session), rows, names, with_history=False),
        apaar=_apaar_state(session, ctx, student_id),
        whatsapp=_whatsapp_state(session, ctx, student_id),
    )


# --- summary (FR-DPDP-026) ----------------------------------------------------------------------


def _counts(answers: Sequence[str]) -> AnswerCounts:
    c = Counter(answers)
    return AnswerCounts(
        total=len(answers),
        given=c["given"],
        refused=c["refused"],
        pending=c[PENDING],
        withdrawn=c["withdrawn"],
        not_answered=c[NOT_ANSWERED],
    )


def _reached_students(
    session: Session, ctx: UserContext, section_id: uuid.UUID | None
) -> list[uuid.UUID]:
    reach: set[uuid.UUID] = set()
    for permission in (READ, RECORD):
        if ctx.has(permission):
            reach.update(
                students.list_students_in_scope(
                    session,
                    ctx,
                    section_ids=[section_id] if section_id is not None else None,
                    permissions=(permission,),
                )
            )
    return sorted(reach)


def summary(
    session: Session,
    ctx: UserContext,
    *,
    section_id: uuid.UUID | None = None,
    purpose_id: uuid.UUID | None = None,
) -> PurposeConsentSummaryOut:
    """Given / refused / pending / withdrawn / not answered per active purpose and section of the
    current year, for the students the caller reaches (``consent.read`` or ``consent.record``;
    class teachers: their sections). Counts only."""
    ids = _reached_students(session, ctx, section_id)
    info = students.summaries(session, ctx, ids)
    placed = [sid for sid in ids if sid in info and info[sid].section_id is not None]
    purposes = [
        p
        for p in repo.list_purposes(session, active_only=True)
        if purpose_id is None or p.id == purpose_id
    ]
    current = repo.current(session, placed)
    out = []
    for p in purposes:
        by_section: dict[uuid.UUID, list[str]] = {}
        labels: dict[uuid.UUID, str] = {}
        answers: list[str] = []
        for sid in placed:
            row = current.get((sid, p.id))
            answer = row.status if row is not None else NOT_ANSWERED
            answers.append(answer)
            sec = info[sid].section_id
            if sec is None:  # pragma: no cover - placed students have a section
                continue
            by_section.setdefault(sec, []).append(answer)
            labels[sec] = info[sid].class_section or ""
        sections = sorted(
            (
                PurposeSectionCountsOut(section_id=s, class_section=labels[s], counts=_counts(a))
                for s, a in by_section.items()
            ),
            key=lambda x: x.class_section,
        )
        out.append(
            PurposeSummaryOut(
                purpose_id=p.id,
                key=p.key,
                label_en=p.label_en,
                totals=_counts(answers),
                sections=sections,
            )
        )
    return PurposeConsentSummaryOut(purposes=out)


# --- printed forms (FR-DPDP-023) ----------------------------------------------------------------


def _form_language(session: Session, language: FormLanguage | None) -> FormLanguage:
    """English while Telugu is switched off (ADR-0036, lead decision D47), whatever was asked
    or the school's parent language says; with the switch on, the asked language, else the
    school's parent language (ADR-0044)."""
    if not languages.telugu_enabled():
        return "en"
    if language is not None:
        return language
    return "te" if tenancy.parent_language(session) == "te" else "en"


def _form_purposes(session: Session, language: FormLanguage) -> list[templates.FormPurpose]:
    rows = repo.list_purposes(session, active_only=True)
    if not rows:
        raise Conflict(
            "Switch on at least one consent purpose before printing forms.", code="no_purposes"
        )
    return [
        templates.FormPurpose(
            label=(r.label_te if language == "te" and r.label_te else r.label_en),
            description=r.description,
            form_version=r.form_version,
        )
        for r in rows
    ]


def _form_students(
    session: Session, ctx: UserContext, ids: Sequence[uuid.UUID]
) -> list[templates.FormStudent]:
    info = students.summaries(session, ctx, ids)
    return [
        templates.FormStudent(
            name=info[sid].display_name,
            admission_no=info[sid].admission_no,
            class_section=info[sid].class_section,
        )
        for sid in ids
        if sid in info
    ]


def form_targets(
    session: Session,
    ctx: UserContext,
    *,
    student_id: uuid.UUID | None = None,
    section_id: uuid.UUID | None = None,
    only_unanswered: bool = False,
    language: FormLanguage | None = None,
) -> tuple[FormLanguage, list[uuid.UUID]]:
    """The students one print covers (one student, or a section of the current year in
    class-list order, optionally only those with an active purpose not answered yet) and the
    form's language. ``consent.read`` or ``consent.record`` in scope (404 outside); 409
    ``no_forms`` / ``too_many_forms`` / ``no_purposes``. Nothing is printed or audited here."""
    if (student_id is None) == (section_id is None):
        raise ValidationFailed(
            [_error("student_id", "one_of", "errors.consent.form_target")],
            detail="Choose one student or one section.",
        )
    lang = _form_language(session, language)
    _form_purposes(session, lang)
    if student_id is not None:
        _reach_student(session, ctx, student_id, (READ, RECORD))
        return lang, [student_id]
    if section_id is None:  # pragma: no cover - checked above
        raise NotFound("Section not found")
    try:
        tenancy.get_section(session, section_id)
    except NotFound:
        raise NotFound("Section not found") from None
    ids = _reached_students(session, ctx, section_id)
    if not ids and not _section_in_reach(session, ctx, section_id):
        raise NotFound("Section not found")
    info = students.summaries(session, ctx, ids)
    ordered = sorted(
        (sid for sid in ids if sid in info),
        key=lambda s: (info[s].display_name or "", str(s)),
    )
    if only_unanswered:
        active = [p.id for p in repo.list_purposes(session, active_only=True)]
        current = repo.current(session, ordered)
        ordered = [
            s
            for s in ordered
            if any(
                (row := current.get((s, pid))) is None or row.status == PENDING for pid in active
            )
        ]
    cfg = settings()
    if not ordered:
        raise Conflict("No student in this section matches.", code="no_forms")
    if len(ordered) > cfg.max_forms_per_print:
        raise Conflict(
            f"Print at most {cfg.max_forms_per_print} forms at a time.", code="too_many_forms"
        )
    return lang, ordered


def _section_in_reach(session: Session, ctx: UserContext, section_id: uuid.UUID) -> bool:
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


def print_forms(
    session: Session,
    ctx: UserContext,
    *,
    student_id: uuid.UUID | None,
    section_id: uuid.UUID | None,
    only_unanswered: bool,
    language: FormLanguage | None,
) -> str:
    """The printable consent forms (FR-DPDP-023) as one A4 HTML page per student. Audit
    ``consent.form.printed`` (count, language, scope)."""
    lang, ids = form_targets(
        session,
        ctx,
        student_id=student_id,
        section_id=section_id,
        only_unanswered=only_unanswered,
        language=language,
    )
    page = templates.render_forms(
        settings(),
        language=lang,
        school=tenancy.get_tenant(session).name,
        students=_form_students(session, ctx, ids),
        purposes=_form_purposes(session, lang),
    )
    _audit(
        session,
        action="consent.form.printed",
        resource_type="student" if student_id is not None else "section",
        resource_id=student_id or section_id,
        summary={
            "count": len(ids),
            "language": lang,
            "only_unanswered": only_unanswered,
            "form_layout_version": settings().form_layout_version,
        },
    )
    return page


def forms_document(
    session: Session,
    ctx: UserContext,
    student_ids: Sequence[uuid.UUID],
    *,
    language: FormLanguage,
) -> tuple[PrintDocument, int]:
    """Worker (export kind ``consent_forms``): the forms of ``student_ids`` for printing to PDF,
    in the given order and limited to the students the caller still reaches. Never an Aadhaar
    number (invariant 4). Returns the page and the number of forms."""
    cfg = settings()
    language = "te" if language == "te" and languages.telugu_enabled() else "en"  # D47
    reach = set(_reached_students(session, ctx, None))
    ids = [i for i in student_ids if i in reach][: cfg.max_forms_per_print]
    page = templates.render_forms(
        cfg,
        language=language,
        school=tenancy.get_tenant(session).name,
        students=_form_students(session, ctx, ids),
        purposes=_form_purposes(session, language),
        for_pdf=True,
    )
    return PrintDocument(page, telugu_font=language == "te"), len(ids)


# --- for other modules (FR-DPDP-024) ------------------------------------------------------------


def consent_state(session: Session, student_id: uuid.UUID, purpose_key: str) -> ConsentState:
    """``given`` only when the latest decision on the purpose is ``given`` and the purpose is
    still active; anything else (no decision, pending, refused, withdrawn, unknown or switched-off
    purpose) is ``not_given`` (FR-DPDP-024). ``student_id`` must come from the caller's own
    scoped reads. The only way a module checks a purpose (ADR-0049 decision 5)."""
    purpose = repo.purpose_by_key(session, purpose_key)
    if purpose is None:
        return "not_given"
    latest = repo.current(session, [student_id], purpose.id).get((student_id, purpose.id))
    return _state(purpose, latest)


def student_decisions(
    session: Session, student_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, list[PurposeConsentEntryOut]]:
    """Every decision of the given students, newest first per purpose, without names (the
    access report of R3-1A; ``student_ids`` from the caller's own scoped reads)."""
    out: dict[uuid.UUID, list[PurposeConsentEntryOut]] = {}
    for sid in student_ids:
        out[sid] = [_entry_out(r, {}) for r in repo.history(session, sid)]
    return out


def export_records(session: Session) -> list[RecordTable]:
    """Worker only: purposes and decisions for the school's full data export (``app.admin``;
    FR-DPDP-018). Notes are C2 school records and go with the register."""
    return repo.export_tables(session)


# --- offboarding purge (FR-PLT-005, ADR-0029) ---------------------------------------------------
_PURGE = purging.PurgeTables(deleted=("sis.consents", "sis.consent_purposes"))


def tenant_data_counts(session: Session) -> dict[str, int]:
    """Rows of the current school in this module's tables (offboarding inventory)."""
    return _PURGE.count(session)


def purge_tenant_data(session: Session) -> dict[str, int]:
    """Delete the current school's rows of this module (offboarding only; ADR-0029)."""
    return _PURGE.delete(session)


tenancy.register_data_owner(
    tenancy.TenantDataOwner(
        name="privacy_consents", count=tenant_data_counts, purge=purge_tenant_data
    )
)


__all__ = [
    "READ",
    "RECORD",
    "SETTINGS",
    "ConsentAlreadyDecided",
    "ConsentNotGiven",
    "consent_state",
    "consent_view",
    "create_purpose",
    "export_records",
    "form_targets",
    "forms_document",
    "get_student_consents",
    "list_purposes",
    "print_forms",
    "purge_tenant_data",
    "record_consent",
    "record_withdrawal_from_request",
    "settings",
    "student_decisions",
    "subject_report",
    "summary",
    "tenant_data_counts",
    "update_purpose",
]
