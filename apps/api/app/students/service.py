"""Students public API: records, per-source values, canonical view, guardians, enrolments,
year-end promotions, search and sensitive reveal (M1: US-202 AC2, US-301..303; FR-TEN-011,
FR-STU-001..012; SEC-012, SEC-013, SEC-015).

Other modules call only these functions. Every function takes the caller's ``tenant_session``
(RLS: one school) and, for user-facing reads and writes, the caller's
:class:`~app.authz.context.UserContext`:

- **Scope (SEC-015).** Every object read goes through :func:`_visible_student`: a holder of a
  scoped grant (class teacher: own sections; subject teacher: own classes) sees only students
  actively enrolled in those sections in the *current* academic year; anything else, including
  other schools' ids, is ``NotFound`` (404, never 403, so existence is not revealed). The route
  checks the permission; the service checks the object.
- **Sensitive data (SEC-012, FR-STU-007).** C3 values are encrypted with the school's DEK
  (:mod:`app.students.crypto`), never written to ``value_text``/``value_norm``/the profile, and
  never logged. Responses omit them for callers without ``student.read_sensitive`` and show
  ``"••••"`` (masked) for holders; :func:`reveal_sensitive` returns one value and audits it.
- **Aadhaar (SEC-013, FR-STU-012).** Every text input is checked for a full Aadhaar number
  (422 ``aadhaar_full_number_rejected``, ``errors.aadhaar_last4_only``).
- **History (FR-STU-005).** Values are appended; the previous current value of the same
  (attribute, source) is superseded. A database trigger forbids editing recorded values.
- **Identity fields (BR-01, ADR-0010).** The admission-register value of an identity attribute
  can be recorded once (unverified) but never replaced or verified here: that is a change
  request (403 ``identity_change_required``). Only ``app.changes.service`` calls
  :func:`record_verified_identity_value` after an approval.
- **Audit (invariant 7).** Every write records an event in the same transaction; summaries hold
  ids, attribute keys, sources and codes, never values.
- **Outbox (FR-DQ-002).** Value, verification and enrolment writes queue one
  ``student.values.changed`` event per transaction (``{student_ids, attribute_keys}``, at most 100
  ids per event) for the incremental data-quality checks. Values recorded by an import batch are
  left to the import's own ``import.committed`` event.
- **Promotions (FR-TEN-011).** The students module owns them (enrolments live here; owner
  decision 2026-09-27): preview, commit (one transaction, recorded in ``sis.promotion_runs`` /
  ``sis.promotion_items``) and undo within 24 hours unless an enrolment it touched changed
  since (409 ``promotion_has_dependents``, the import-revert rule).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import itertools
import json
import uuid
from collections.abc import Collection, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Final, Literal
from zoneinfo import ZoneInfo

from sqlalchemy import event
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.authz.context import UserContext
from app.authz.http import Page, decode_cursor, encode_cursor
from app.core import purge as purging
from app.core.errors import (
    Conflict,
    Forbidden,
    NotFound,
    PreconditionFailed,
    ValidationFailed,
)
from app.core.ids import new_id
from app.core.languages import telugu_text
from app.core.logging import get_context, get_logger
from app.core.records import RecordTable
from app.core.textnorm import comparison_key
from app.identity import service as identity
from app.ops import service as ops
from app.students import crypto
from app.students import repository as repo
from app.students.canonical import Resolution, resolve
from app.students.definitions import (
    AADHAAR_DETAIL,
    DIGITS12_CODE,
    MASK,
    AttributeDef,
    CanonicalPolicy,
    CleanValue,
    aadhaar_display,
    aadhaar_error,
    digits12_value,
    error,
    is_full_aadhaar,
    normalize_phone,
    phone_display,
    reject_full_aadhaar,
    validate_value,
)
from app.students.models import AttributeDefinition, AttributeValue, Guardian, Student
from app.students.schemas import (
    AttributeOut,
    CanonicalOut,
    ClassSection,
    EnrollmentEnd,
    EnrollmentIn,
    EnrollmentOut,
    EnrollmentPatch,
    GuardianCreate,
    GuardianOut,
    GuardianPatch,
    PromotionCommitIn,
    PromotionCounts,
    PromotionGroupOut,
    PromotionIn,
    PromotionPreviewOut,
    PromotionProblemOut,
    PromotionRunOut,
    PromotionStudentOut,
    RevealIn,
    RevealOut,
    SearchFilters,
    StudentCreate,
    StudentMatch,
    StudentOut,
    StudentSummary,
    ValueIn,
    ValueOut,
    ValueRecorded,
)
from app.students.search import parse_query, search_config, translit_key
from app.tenancy import service as tenancy

READ: Final = "student.read_basic"
SENSITIVE: Final = "student.read_sensitive"
UPDATE: Final = "student.update_nonidentity"

APAAR_SEARCH_FIELD: Final = "apaar_id"
"""Search-body field and typed attribute for the exact APAAR ID search (FR-STU-016, ADR-0037)."""

VALUES_TABLE: Final = "sis.attribute_values"
VALUE_COLUMN: Final = "value_ciphertext"
GUARDIANS_TABLE: Final = "sis.guardians"
GUARDIAN_FIELDS: Final[dict[str, str]] = {
    "guardian_phone": "phone_ciphertext",
    "guardian_address": "address_ciphertext",
}
PHONE_INDEX_PURPOSE: Final = "guardian_phone"
PROFILE_KEYS: Final = ("full_name", "dob", "gender", "father_name", "mother_name")
IST: Final = ZoneInfo("Asia/Kolkata")

Verification = Literal["unverified", "verified", "rejected"]

VALUES_CHANGED_EVENT: Final = "student.values.changed"
ENROLLMENT_KEY: Final = "enrollment"  # attribute_keys marker for enrolment changes (DQ-007/012)
_CHANGED_INFO: Final = "sos_students_values_changed"
_EVENT_CHUNK: Final = 100

log = get_logger(__name__)


class IdentityChangeRequired(Forbidden):
    """Identity values change only through a change request (maker-checker, ADR-0010)."""

    code = "identity_change_required"

    def __init__(self) -> None:
        super().__init__(
            "This is an identity field. Submit a change request with evidence; "
            "a second person approves it."
        )


# --- plumbing --------------------------------------------------------------------------------


@contextmanager
def _db_errors() -> Iterator[None]:
    try:
        yield
    except DBAPIError as exc:
        mapped = repo.translate_db_error(exc)
        if mapped is None:
            raise
        raise mapped from exc


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


def _values_changed(
    session: Session, student_id: uuid.UUID, attribute_keys: Collection[str]
) -> None:
    """Remember a change; :func:`_emit_values_changed` queues the events at commit (coalesced)."""
    pending: dict[uuid.UUID, set[str]] | None = session.info.get(_CHANGED_INFO)
    if pending is None:
        pending = session.info[_CHANGED_INFO] = {}
        event.listen(session, "before_commit", _emit_values_changed, once=True)
        event.listen(session, "after_rollback", _forget_values_changed, once=True)
    pending.setdefault(student_id, set()).update(attribute_keys)


def _emit_values_changed(session: Session) -> None:
    pending: dict[uuid.UUID, set[str]] | None = session.info.pop(_CHANGED_INFO, None)
    if not pending:
        return
    ids = sorted(pending, key=str)
    for start in range(0, len(ids), _EVENT_CHUNK):
        chunk = ids[start : start + _EVENT_CHUNK]
        keys = sorted(set().union(*(pending[i] for i in chunk)))
        ops.enqueue_event(
            session, VALUES_CHANGED_EVENT, {"student_ids": chunk, "attribute_keys": keys}
        )


def _forget_values_changed(session: Session) -> None:
    session.info.pop(_CHANGED_INFO, None)


def _today() -> dt.date:
    return dt.datetime.now(IST).date()


@dataclass(frozen=True, slots=True)
class _Structure:
    """Current academic year with its sections and the class catalogue (labels, scope)."""

    year_id: uuid.UUID | None
    sections: dict[uuid.UUID, Any]
    classes: dict[uuid.UUID, Any]

    def label(self, section_id: uuid.UUID | None) -> str | None:
        section = self.sections.get(section_id) if section_id else None
        if section is None:
            return None
        klass = self.classes.get(section.class_id)
        code = klass.code if klass is not None else "?"
        return f"{code}-{section.name}"


def _structure(session: Session, year_id: uuid.UUID | None = None) -> _Structure:
    """The current academic year's structure, or ``year_id``'s (422 ``academic_year_id``
    ``not_found`` when this school has no such year)."""
    year: Any
    if year_id is None:
        year = tenancy.get_current_academic_year(session)
    else:
        try:
            year = tenancy.get_academic_year(session, year_id)
        except NotFound:
            raise ValidationFailed([error("academic_year_id", "not_found")]) from None
    sections = tenancy.list_sections(session, academic_year_id=year.id) if year else []
    classes = tenancy.list_classes(session)
    return _Structure(
        year_id=year.id if year else None,
        sections={s.id: s for s in sections},
        classes={c.id: c for c in classes},
    )


def _allowed_sections(
    ctx: UserContext, permission: str, structure: _Structure
) -> frozenset[uuid.UUID] | None:
    """Current-year sections the caller reaches with ``permission`` (``None`` = whole school)."""
    if not ctx.has(permission):
        return frozenset()
    grant = ctx.scope_for(permission)
    if grant.school_wide:
        return None
    return frozenset(
        s.id
        for s in structure.sections.values()
        if s.id in grant.section_ids or s.class_id in grant.class_ids
    )


def _in_scope(
    session: Session,
    ctx: UserContext,
    student_id: uuid.UUID,
    permission: str,
    structure: _Structure,
) -> bool:
    allowed = _allowed_sections(ctx, permission, structure)
    if allowed is None:
        return True
    if structure.year_id is None or not allowed:
        return False
    found = repo.students_in_sections(
        session, [student_id], academic_year_id=structure.year_id, section_ids=allowed
    )
    return student_id in found


def _visible_student(
    session: Session,
    ctx: UserContext,
    student_id: uuid.UUID,
    *,
    permission: str = READ,
    structure: _Structure,
    lock: bool = False,
) -> Student:
    """The student if the caller may reach it with ``permission`` (and basic read), else 404."""
    student = repo.get_student(session, student_id, lock=lock)
    if student is None:
        raise NotFound("Student not found")
    for perm in dict.fromkeys((READ, permission)):
        if not _in_scope(session, ctx, student_id, perm, structure):
            raise NotFound("Student not found")
    return student


def _definitions(session: Session) -> dict[str, AttributeDef]:
    """Global definitions first; a school's own attribute never shadows a global key."""
    rows = sorted(repo.list_definitions(session), key=lambda r: r.tenant_id is not None)
    out: dict[str, AttributeDef] = {}
    for row in rows:
        if row.key in out:
            continue
        out[row.key] = _definition(row)
    return dict(sorted(out.items(), key=lambda kv: (kv[1].sort_order, kv[0])))


def _definition(row: AttributeDefinition) -> AttributeDef:
    return AttributeDef(
        key=row.key,
        data_type=row.data_type,  # type: ignore[arg-type]
        classification=row.classification,  # type: ignore[arg-type]
        is_identity=row.is_identity,
        policy=CanonicalPolicy.from_json(row.canonical_policy),
        label_en=row.label_en,
        label_te=row.label_te,
        sort_order=row.sort_order,
        validation=dict(row.validation or {}),
        is_global=row.tenant_id is None,
    )


def _definition_for(defs: Mapping[str, AttributeDef], key: str, field: str) -> AttributeDef:
    definition = defs.get(key)
    if definition is None:
        raise ValidationFailed([error(field, "unknown_attribute")])
    return definition


def _decrypt(session: Session, row: AttributeValue) -> str:
    if row.value_ciphertext is None:
        raise crypto.CryptoError("value has no ciphertext")
    return crypto.decrypt_value(
        session, row.value_ciphertext, table=VALUES_TABLE, column=VALUE_COLUMN, row_id=row.id
    )


def _plain(session: Session, row: AttributeValue) -> str | None:
    """The stored value as a string (C3 decrypted); dates in ISO format."""
    if row.value_ciphertext is not None:
        return _decrypt(session, row)
    if row.value_date is not None:
        return row.value_date.isoformat()
    return row.value_text


def _compare_key(row: AttributeValue) -> str | None:
    """Comparison string for conflicts (C1/C2 only; C3 is never compared here)."""
    if row.value_ciphertext is not None:
        return None
    if row.value_norm is not None:
        return row.value_norm
    if row.value_date is not None:
        return row.value_date.isoformat()
    return row.value_text.upper() if row.value_text is not None else None


def _can_reveal(
    session: Session, ctx: UserContext, student_id: uuid.UUID, structure: _Structure
) -> bool:
    return ctx.has(SENSITIVE) and _in_scope(session, ctx, student_id, SENSITIVE, structure)


def _value_out(
    session: Session, row: AttributeValue, definition: AttributeDef | None, *, masked: bool
) -> ValueOut:
    sensitive = definition is None or definition.sensitive or row.value_ciphertext is not None
    return ValueOut(
        id=row.id,
        attribute_key=row.attribute_key,
        source=row.source,
        value=MASK if (sensitive and masked) else (None if sensitive else _plain(session, row)),
        masked=sensitive,
        verification_status=row.verification_status,
        verified_by=row.verified_by,
        verified_at=row.verified_at,
        recorded_by=row.recorded_by,
        recorded_at=row.recorded_at,
        evidence_document_id=row.evidence_document_id,
        import_batch_id=row.import_batch_id,
        change_request_id=row.change_request_id,
        superseded_by=row.superseded_by,
        current=row.superseded_by is None,
    )


def _group_current(
    rows: Sequence[AttributeValue],
) -> dict[uuid.UUID, dict[str, dict[str, AttributeValue]]]:
    out: dict[uuid.UUID, dict[str, dict[str, AttributeValue]]] = {}
    for row in rows:
        out.setdefault(row.student_id, {}).setdefault(row.attribute_key, {})[row.source] = row
    return out


def _resolve(definition: AttributeDef, current: Mapping[str, AttributeValue]) -> Resolution[Any]:
    compare = None if definition.sensitive else {s: _compare_key(v) for s, v in current.items()}
    return resolve(definition.policy, current, compare=compare)


def _source_rank(definition: AttributeDef, source: str) -> tuple[int, str]:
    order = definition.policy.precedence + definition.policy.show_conflicts_from
    return (order.index(source) if source in order else len(order), source)


# --- attribute catalog -------------------------------------------------------------------------


def attribute_catalog(session: Session) -> list[AttributeOut]:
    """Global + school attributes with classification, identity flag and labels (EN/TE;
    ``label_te`` is empty while Telugu is hidden, ADR-0036)."""
    return [
        AttributeOut(
            key=d.key,
            data_type=d.data_type,
            classification=d.classification,
            is_identity=d.is_identity,
            label_en=d.label_en,
            label_te=telugu_text(d.label_te) or "",  # empty while Telugu is hidden (ADR-0036)
            sort_order=d.sort_order,
            allowed_sources=list(d.allowed_sources) if d.allowed_sources else None,
            allowed_values=[str(v) for v in d.validation.get("values", ())] or None,
            precedence=list(d.policy.precedence),
            is_global=d.is_global,
        )
        for d in _definitions(session).values()
    ]


@dataclass(frozen=True, slots=True)
class AttributeRules:
    """How :func:`record_value` validates one attribute (FR-STU-006, the ``validation`` of
    ``sis.attribute_definitions``), for modules that check input before recording it (imports
    show row-level errors). The student service still re-validates every value it records."""

    key: str
    data_type: str
    classification: str
    is_identity: bool
    is_name: bool
    allowed_sources: tuple[str, ...] | None  # None = every source
    allowed_values: tuple[str, ...] | None  # enum values
    max_length: int | None  # after cleaning (NFC, single spaces); None for dates
    pattern: str | None  # full-match regular expression
    not_future: bool  # dates


def attribute_rules(session: Session) -> list[AttributeRules]:
    """Validation rules of the global and school attributes (same order as the catalog)."""
    return [
        AttributeRules(
            key=d.key,
            data_type=d.data_type,
            classification=d.classification,
            is_identity=d.is_identity,
            is_name=d.is_name,
            allowed_sources=d.allowed_sources,
            allowed_values=d.allowed_values,
            max_length=d.max_length,
            pattern=d.pattern,
            not_future=d.not_future,
        )
        for d in _definitions(session).values()
    ]


# --- profile projection (same transaction as every write) -------------------------------------


def _refresh_projection(
    session: Session, student: Student, defs: Mapping[str, AttributeDef]
) -> Student:
    """Rebuild the C2 profile row and the admission number from the canonical values."""
    keys = [k for k in (*PROFILE_KEYS, "admission_no") if k in defs and not defs[k].sensitive]
    grouped = _group_current(repo.current_values(session, [student.id], keys)).get(student.id, {})
    canon: dict[str, AttributeValue | None] = {
        k: _resolve(defs[k], grouped.get(k, {})).value for k in keys
    }

    def text_of(key: str) -> str | None:
        row = canon.get(key)
        return row.value_text if row is not None else None

    def norm_of(key: str) -> str | None:
        row = canon.get(key)
        return row.value_norm if row is not None else None

    full_name = text_of("full_name")
    spellings = [full_name] + [v.value_text for v in grouped.get("full_name", {}).values()]
    dob_row = canon.get("dob")
    admission_no = text_of("admission_no")
    full_norm = norm_of("full_name")
    father, mother = norm_of("father_name"), norm_of("mother_name")
    doc = " ".join(p for p in (full_norm, father, mother, admission_no) if p)
    repo.upsert_profile(
        session,
        search_doc=doc,
        tenant_id=student.tenant_id,
        student_id=student.id,
        full_name=full_name,
        full_name_norm=full_norm,
        full_name_translit=translit_key(spellings),
        dob=dob_row.value_date if dob_row is not None else None,
        gender=text_of("gender"),
        father_name_norm=father,
        mother_name_norm=mother,
        current_section_id=repo.latest_active_section(session, student.id),
        status=student.status,
    )
    if admission_no != student.admission_no:
        with _db_errors():
            updated = repo.update_student(
                session, student.id, values={"admission_no": admission_no}
            )
        if updated is not None:
            student = updated
    return student


def _touch(session: Session, student: Student, defs: Mapping[str, AttributeDef]) -> Student:
    """Refresh the projection and bump the student's version (ETag) once per write."""
    student = _refresh_projection(session, student, defs)
    updated = repo.update_student(session, student.id, values={})
    return updated if updated is not None else student


# --- reads -------------------------------------------------------------------------------------


def _student_out(
    session: Session,
    ctx: UserContext,
    student: Student,
    structure: _Structure,
    defs: Mapping[str, AttributeDef],
) -> StudentOut:
    revealable = _can_reveal(session, ctx, student.id, structure)
    grouped = _group_current(repo.current_values(session, [student.id])).get(student.id, {})
    canonical: dict[str, CanonicalOut] = {}
    values: dict[str, list[ValueOut]] = {}
    for key, definition in defs.items():
        if definition.sensitive and not revealable:
            continue  # US-301 AC3: hidden without student.read_sensitive
        current = grouped.get(key, {})
        if not current and not definition.is_identity:
            continue
        res = _resolve(definition, current)
        chosen: AttributeValue | None = res.value
        canonical[key] = CanonicalOut(
            value=(
                None
                if chosen is None
                else (MASK if definition.sensitive else _plain(session, chosen))
            ),
            source=chosen.source if chosen is not None else None,
            verified=chosen is not None and chosen.verification_status == "verified",
            provisional=res.provisional,
            masked=definition.sensitive,
            conflicts=list(res.conflicts),
        )
        if current:
            ordered = sorted(current.values(), key=lambda v: _source_rank(definition, v.source))
            values[key] = [_value_out(session, v, definition, masked=True) for v in ordered]
    enrollment = None
    if structure.year_id is not None:
        active = repo.active_enrollment(session, student.id, structure.year_id)
        section = structure.sections.get(active.section_id) if active is not None else None
        if active is not None and section is not None:
            enrollment = ClassSection(
                section_id=active.section_id,
                class_id=section.class_id,
                academic_year_id=active.academic_year_id,
                label=structure.label(active.section_id) or "",
                roll_no=active.roll_no,
            )
    return StudentOut(
        id=student.id,
        status=student.status,
        admission_no=student.admission_no,
        version=student.version,
        created_at=student.created_at,
        updated_at=student.updated_at,
        enrollment=enrollment,
        canonical=canonical,
        values=values,
        sensitive_revealable=revealable,
    )


def get_profile(session: Session, ctx: UserContext, student_id: uuid.UUID) -> StudentOut:
    """Canonical profile + current per-source values (US-301 AC1-AC3); 404 outside scope."""
    structure = _structure(session)
    student = _visible_student(session, ctx, student_id, structure=structure)
    return _student_out(session, ctx, student, structure, _definitions(session))


def value_history(
    session: Session, ctx: UserContext, student_id: uuid.UUID, attribute_key: str | None = None
) -> list[ValueOut]:
    """Full history (newest first) of one attribute or of all attributes (FR-STU-005)."""
    structure = _structure(session)
    _visible_student(session, ctx, student_id, structure=structure)
    defs = _definitions(session)
    if attribute_key is not None:
        _definition_for(defs, attribute_key, "attribute")
    revealable = _can_reveal(session, ctx, student_id, structure)
    out: list[ValueOut] = []
    for row in repo.value_history(session, student_id, attribute_key):
        definition = defs.get(row.attribute_key)
        sensitive = definition is None or definition.sensitive or row.value_ciphertext is not None
        if sensitive and not revealable:
            continue
        out.append(_value_out(session, row, definition, masked=True))
    return out


# --- create and record -----------------------------------------------------------------------


def _check_evidence(evidence_document_id: uuid.UUID | None) -> None:
    """Evidence documents live in ``kb.documents`` (M1 documents module); until that module's
    ``evidence_exists`` is available the id is stored as given (composite FK added in 0009)."""


def _identity_guard(
    definition: AttributeDef,
    source: str,
    verification: Verification,
    existing: AttributeValue | None,
) -> None:
    if not definition.is_identity:
        return
    if verification != "unverified":
        raise IdentityChangeRequired()
    if source == definition.policy.anchor and existing is not None:
        raise IdentityChangeRequired()


def _same_value(session: Session, row: AttributeValue, clean: CleanValue) -> bool:
    try:
        return _plain(session, row) == clean.plain
    except crypto.CryptoError:
        return False


def _insert_value(
    session: Session,
    ctx: UserContext,
    student: Student,
    definition: AttributeDef,
    *,
    source: str,
    clean: CleanValue,
    previous: AttributeValue | None,
    verification: Verification,
    evidence_document_id: uuid.UUID | None,
    import_batch_id: uuid.UUID | None,
    change_request_id: uuid.UUID | None,
    confidence: Decimal | None,
) -> AttributeValue:
    value_id = new_id()
    columns: dict[str, Any]
    if definition.sensitive:
        blob, version = crypto.encrypt_value(
            session, clean.plain, table=VALUES_TABLE, column=VALUE_COLUMN, row_id=value_id
        )
        columns = {"value_ciphertext": blob, "key_version": version}
    else:
        columns = {"value_text": clean.text, "value_date": clean.date, "value_norm": clean.norm}
    decided = verification != "unverified"
    with _db_errors():
        if previous is not None:
            # The FK to the successor is deferred; av_current needs the old row retired first.
            repo.mark_superseded(session, previous.id, value_id)
        return repo.insert_value(
            session,
            id=value_id,
            tenant_id=student.tenant_id,
            student_id=student.id,
            attribute_key=definition.key,
            source=source,
            confidence=confidence,
            verification_status=verification,
            verified_by=ctx.user_id if decided else None,
            verified_at=repo.now(session) if decided else None,
            evidence_document_id=evidence_document_id,
            import_batch_id=import_batch_id,
            change_request_id=change_request_id,
            recorded_by=ctx.user_id,
            **columns,
        )


def _section_or_422(session: Session, section_id: uuid.UUID) -> Any:
    try:
        return tenancy.get_section(session, section_id)
    except NotFound:
        raise ValidationFailed([error("section_id", "not_found")]) from None


def _enrolment_target(session: Session, section_id: uuid.UUID) -> Any:
    """The section a student is being placed in, locked with its class and year ``FOR SHARE``
    until the transaction ends (a concurrent archive waits, then fails on its active-enrolment
    guard). 422 ``section_id``/``not_found`` for an unknown section; 409 ``structure_archived``
    when the section, its class or its academic year is archived (FR-TEN-010)."""
    try:
        return tenancy.lock_enrolment_targets(session, [section_id])[section_id]
    except NotFound:
        raise ValidationFailed([error("section_id", "not_found")]) from None


def create_student(
    session: Session,
    ctx: UserContext,
    data: StudentCreate,
    *,
    import_batch_id: uuid.UUID | None = None,
) -> StudentOut:
    """Create a student with its first values (docs/09; permission ``student.create``).

    Every value names its source. ``import_batch_id`` (imports only, never from the API) tags
    the values with their batch; the batch's own ``import.committed`` event then covers them
    instead of ``student.values.changed``. Identity values are recorded unverified, so the canonical
    identity values stay provisional until verified through a change request. Optional
    ``section_id`` enrols the student. Audit: ``student.created`` (+ ``enrollment.created``).
    """
    defs = _definitions(session)
    cleaned: list[tuple[AttributeDef, str, CleanValue, uuid.UUID | None]] = []
    seen: set[tuple[str, str]] = set()
    for i, item in enumerate(data.values):
        definition = _definition_for(defs, item.attribute_key, f"values.{i}.attribute_key")
        clean = validate_value(definition, item.source, item.value, field_name=f"values.{i}.value")
        if (item.attribute_key, item.source) in seen:
            raise ValidationFailed([error(f"values.{i}", "duplicate_value")])
        seen.add((item.attribute_key, item.source))
        _check_evidence(item.evidence_document_id)
        cleaned.append((definition, item.source, clean, item.evidence_document_id))
    if not any(d.key == "full_name" for d, *_ in cleaned):
        raise ValidationFailed([error("values", "full_name_required")])
    section = _enrolment_target(session, data.section_id) if data.section_id else None
    tenant_id = repo.current_tenant_id(session)
    with _db_errors():
        student = repo.insert_student(
            session, student_id=new_id(), tenant_id=tenant_id, status=data.status
        )
    for definition, source, clean, evidence in cleaned:
        _insert_value(
            session,
            ctx,
            student,
            definition,
            source=source,
            clean=clean,
            previous=None,
            verification="unverified",
            evidence_document_id=evidence,
            import_batch_id=import_batch_id,
            change_request_id=None,
            confidence=None,
        )
    enrollment_id = None
    if section is not None:
        enrollment_id = _enrol(
            session, ctx, student, section, roll_no=data.roll_no, started_on=None
        ).id
    student = _refresh_projection(session, student, defs)
    changed = {d.key for d, *_ in cleaned}
    if import_batch_id is None:
        _values_changed(session, student.id, changed | ({ENROLLMENT_KEY} if section else set()))
    _audit(
        session,
        action="student.created",
        resource_type="student",
        resource_id=student.id,
        summary={
            "attribute_keys": sorted({d.key for d, *_ in cleaned}),
            "sources": sorted({s for _, s, *_ in cleaned}),
            "value_count": len(cleaned),
            "section_id": section.id if section is not None else None,
            "enrollment_id": enrollment_id,
        },
    )
    log.info("student.created", resource_type="student", resource_id=student.id)
    return _student_out(session, ctx, student, _structure(session), defs)


def record_value(  # noqa: PLR0917 - signature fixed by the M1 build contract
    session: Session,
    ctx: UserContext,
    student_id: uuid.UUID,
    attribute_key: str,
    source: str,
    value: str,
    *,
    evidence_document_id: uuid.UUID | None = None,
    verification: Verification = "unverified",
    import_batch_id: uuid.UUID | None = None,
    change_request_id: uuid.UUID | None = None,
    confidence: Decimal | None = None,
    expected_version: int | None = None,
    permission: str = READ,
) -> ValueRecorded:
    """Record one observed value from ``source`` (FR-STU-002/003/005); supersedes the previous
    current value of the same attribute and source, or returns it unchanged if identical (so
    re-running an import is harmless).

    Callers: ``POST /students/{id}/values`` (``student.update_nonidentity``, passed as
    ``permission`` so its scope applies too), imports and the extraction queue (their own
    route permissions; object scope via ``student.read_basic``). Refused with 403
    ``identity_change_required``: replacing the admission-register value of an identity
    attribute, and any ``verified`` or ``rejected`` identity value. ``expected_version``
    (If-Match) guards lost updates (412). Audit: ``student.value.recorded``.
    """
    structure = _structure(session)
    student = _visible_student(
        session, ctx, student_id, permission=permission, structure=structure, lock=True
    )
    if expected_version is not None and student.version != expected_version:
        raise PreconditionFailed("The student was changed by someone else. Reload and try again.")
    defs = _definitions(session)
    definition = _definition_for(defs, attribute_key, "attribute_key")
    clean = validate_value(definition, source, value)
    _check_evidence(evidence_document_id)
    previous = repo.current_value(session, student_id, attribute_key, source)
    if (
        previous is not None
        and previous.verification_status in (verification, "verified")
        and evidence_document_id in (None, previous.evidence_document_id)
        and _same_value(session, previous, clean)
    ):
        return ValueRecorded(
            id=previous.id,
            student_id=student_id,
            attribute_key=attribute_key,
            source=source,
            superseded=None,
            student_version=student.version,
        )
    _identity_guard(definition, source, verification, previous)
    row = _insert_value(
        session,
        ctx,
        student,
        definition,
        source=source,
        clean=clean,
        previous=previous,
        verification=verification,
        evidence_document_id=evidence_document_id,
        import_batch_id=import_batch_id,
        change_request_id=change_request_id,
        confidence=confidence,
    )
    student = _touch(session, student, defs)
    if import_batch_id is None:
        _values_changed(session, student_id, [attribute_key])
    _audit(
        session,
        action="student.value.recorded",
        resource_type="student",
        resource_id=student_id,
        summary={
            "value_id": row.id,
            "attribute_key": attribute_key,
            "source": source,
            "verification": verification,
            "superseded_value_id": previous.id if previous is not None else None,
            "import_batch_id": import_batch_id,
            "change_request_id": change_request_id,
            "has_evidence": evidence_document_id is not None,
        },
    )
    return ValueRecorded(
        id=row.id,
        student_id=student_id,
        attribute_key=attribute_key,
        source=source,
        superseded=previous.id if previous is not None else None,
        student_version=student.version,
    )


def record_value_in(
    session: Session,
    ctx: UserContext,
    student_id: uuid.UUID,
    data: ValueIn,
    *,
    expected_version: int | None = None,
) -> ValueRecorded:
    """API adapter for :func:`record_value` (body :class:`ValueIn`)."""
    return record_value(
        session,
        ctx,
        student_id,
        data.attribute_key,
        data.source,
        data.value,
        evidence_document_id=data.evidence_document_id,
        expected_version=expected_version,
        permission=UPDATE,
    )


def record_verified_identity_value(
    session: Session,
    ctx: UserContext,
    student_id: uuid.UUID,
    attribute_key: str,
    value: str,
    *,
    change_request_id: uuid.UUID,
    evidence_document_id: uuid.UUID,
    source: str = "admission_register",
) -> ValueRecorded:
    """INTERNAL: record an approved identity correction as a new *verified* value.

    Reserved for ``app.changes.service`` after a change request is approved (maker-checker,
    ADR-0010, FR-CR-*): ``ctx`` is the approver, ``change_request_id`` and
    ``evidence_document_id`` are required and stored on the value. The previous value stays in
    history (superseded). No other module may call this function.
    Audit: ``student.value.recorded`` with ``verification = verified`` and the request id.
    """
    structure = _structure(session)
    student = _visible_student(
        session, ctx, student_id, permission=READ, structure=structure, lock=True
    )
    defs = _definitions(session)
    definition = _definition_for(defs, attribute_key, "attribute_key")
    if not definition.is_identity:
        raise ValidationFailed([error("attribute_key", "not_identity_attribute")])
    clean = validate_value(definition, source, value)
    previous = repo.current_value(session, student_id, attribute_key, source)
    row = _insert_value(
        session,
        ctx,
        student,
        definition,
        source=source,
        clean=clean,
        previous=previous,
        verification="verified",
        evidence_document_id=evidence_document_id,
        import_batch_id=None,
        change_request_id=change_request_id,
        confidence=None,
    )
    student = _touch(session, student, defs)
    _values_changed(session, student_id, [attribute_key])
    _audit(
        session,
        action="student.value.recorded",
        resource_type="student",
        resource_id=student_id,
        summary={
            "value_id": row.id,
            "attribute_key": attribute_key,
            "source": source,
            "verification": "verified",
            "superseded_value_id": previous.id if previous is not None else None,
            "change_request_id": change_request_id,
            "has_evidence": True,
        },
    )
    return ValueRecorded(
        id=row.id,
        student_id=student_id,
        attribute_key=attribute_key,
        source=source,
        superseded=previous.id if previous is not None else None,
        student_version=student.version,
    )


def validate_identity_value(
    session: Session,
    attribute_key: str,
    source: str,
    value: str,
    *,
    field_name: str = "value",
) -> tuple[AttributeDef, CleanValue]:
    """Validate a proposed value of an IDENTITY attribute without recording it (read-only).

    For ``app.changes.service`` before it stores a change request (FR-CR-001): the same rules as
    :func:`record_verified_identity_value` will apply on approval (source allowed, format,
    dates, full-Aadhaar rejection). 422 ``unknown_attribute`` / ``not_identity_attribute`` on
    ``attribute_key``.
    """
    definition = _definition_for(_definitions(session), attribute_key, "attribute_key")
    if not definition.is_identity:
        raise ValidationFailed([error("attribute_key", "not_identity_attribute")])
    return definition, validate_value(definition, source, value, field_name=field_name)


def verify_value(
    session: Session,
    ctx: UserContext,
    student_id: uuid.UUID,
    value_id: uuid.UUID,
    status: Literal["verified", "rejected"] = "verified",
) -> ValueOut:
    """Mark the current value of a NON-identity attribute verified or rejected.

    Identity values are verified only through change requests (403 identity_change_required);
    a superseded value answers 409 ``value_superseded``. Audit: ``student.value.verified``.
    """
    structure = _structure(session)
    student = _visible_student(
        session, ctx, student_id, permission=UPDATE, structure=structure, lock=True
    )
    row = repo.get_value(session, value_id)
    if row is None or row.student_id != student_id:
        raise NotFound("Value not found")
    defs = _definitions(session)
    definition = defs.get(row.attribute_key)
    if definition is None or definition.is_identity:
        raise IdentityChangeRequired()
    if row.superseded_by is not None:
        raise Conflict(
            "A newer value was recorded. Verify the current one.", code="value_superseded"
        )
    with _db_errors():
        row = repo.set_verification(
            session, value_id, status=status, by=ctx.user_id, at=repo.now(session)
        )
    _touch(session, student, defs)
    _values_changed(session, student_id, [row.attribute_key])
    _audit(
        session,
        action="student.value.verified",
        resource_type="student",
        resource_id=student_id,
        summary={
            "value_id": value_id,
            "attribute_key": row.attribute_key,
            "source": row.source,
            "status": status,
        },
    )
    return _value_out(session, row, definition, masked=True)


def update_student_status(
    session: Session,
    ctx: UserContext,
    student_id: uuid.UUID,
    status: str,
    *,
    expected_version: int,
) -> StudentOut:
    """Change the record status (active, left, graduated, provisional); ``If-Match`` required.

    Audit: ``student.status_changed`` with {from, to}.
    """
    structure = _structure(session)
    student = _visible_student(
        session, ctx, student_id, permission=UPDATE, structure=structure, lock=True
    )
    previous = student.status
    with _db_errors():
        updated = repo.update_student(
            session, student_id, values={"status": status}, expected_version=expected_version
        )
    if updated is None:
        raise PreconditionFailed("The student was changed by someone else. Reload and try again.")
    defs = _definitions(session)
    updated = _refresh_projection(session, updated, defs)
    _audit(
        session,
        action="student.status_changed",
        resource_type="student",
        resource_id=student_id,
        summary={"from": previous, "to": status},
    )
    return _student_out(session, ctx, updated, structure, defs)


# --- enrolments ------------------------------------------------------------------------------


def _enrol(
    session: Session,
    ctx: UserContext,
    student: Student,
    section: Any,
    *,
    roll_no: str | None,
    started_on: dt.date | None,
) -> EnrollmentOut:
    existing = repo.active_enrollment(session, student.id, section.academic_year_id, lock=True)
    if existing is not None and existing.section_id == section.id:
        raise Conflict("The student is already in this section.", code="already_enrolled")
    start = started_on or _today()
    with _db_errors():
        if existing is not None:
            repo.end_enrollment(session, existing.id, status="transferred", ended_on=start)
        row = repo.insert_enrollment(
            session,
            id=new_id(),
            tenant_id=student.tenant_id,
            student_id=student.id,
            section_id=section.id,
            academic_year_id=section.academic_year_id,
            roll_no=roll_no,
            status="active",
            started_on=start,
            created_by=ctx.user_id,
        )
    if existing is not None:
        _audit(
            session,
            action="enrollment.transferred",
            resource_type="enrollment",
            resource_id=existing.id,
            summary={
                "student_id": student.id,
                "from_section_id": existing.section_id,
                "to_section_id": section.id,
                "new_enrollment_id": row.id,
            },
        )
    _audit(
        session,
        action="enrollment.created",
        resource_type="enrollment",
        resource_id=row.id,
        summary={
            "student_id": student.id,
            "section_id": section.id,
            "academic_year_id": section.academic_year_id,
        },
    )
    return EnrollmentOut.model_validate(row)


def enrol(
    session: Session, ctx: UserContext, student_id: uuid.UUID, data: EnrollmentIn
) -> EnrollmentOut:
    """Enrol in a section (its academic year); an active enrolment in that year is ended as
    ``transferred``. Permission ``student.update_nonidentity``. 409 ``structure_archived`` when
    the section, its class or its year is archived (the rows stay share-locked until commit,
    so a concurrent archive fails instead). Audit: ``enrollment.created``
    (+ ``enrollment.transferred``)."""
    structure = _structure(session)
    student = _visible_student(
        session, ctx, student_id, permission=UPDATE, structure=structure, lock=True
    )
    section = _enrolment_target(session, data.section_id)
    # The target section must be in reach too, as for PATCH (SEC-015).
    allowed = _allowed_sections(ctx, UPDATE, structure)
    if allowed is not None and section.id not in allowed:
        raise ValidationFailed([error("section_id", "not_found")])
    out = _enrol(session, ctx, student, section, roll_no=data.roll_no, started_on=data.started_on)
    _touch(session, student, _definitions(session))
    _values_changed(session, student_id, [ENROLLMENT_KEY])
    return out


def list_enrollments(
    session: Session, ctx: UserContext, student_id: uuid.UUID
) -> list[EnrollmentOut]:
    """Every enrolment of a student (any year and status), newest first
    (``student.read_basic``; scoped holders only for students in their sections, else 404)."""
    structure = _structure(session)
    _visible_student(session, ctx, student_id, structure=structure)
    return [EnrollmentOut.model_validate(e) for e in repo.enrollments_of(session, student_id)]


def _owned_enrollment(
    session: Session, ctx: UserContext, student_id: uuid.UUID, enrollment_id: uuid.UUID
) -> tuple[Student, Any]:
    """The student (in the caller's update scope) and one of its enrolments, locked; else 404."""
    structure = _structure(session)
    student = _visible_student(
        session, ctx, student_id, permission=UPDATE, structure=structure, lock=True
    )
    enrollment = repo.get_enrollment(session, student_id, enrollment_id, lock=True)
    if enrollment is None:
        raise NotFound("Enrolment not found")
    return student, enrollment


def update_enrollment(
    session: Session,
    ctx: UserContext,
    student_id: uuid.UUID,
    enrollment_id: uuid.UUID,
    data: EnrollmentPatch,
    *,
    expected_version: int,
) -> EnrollmentOut:
    """Correct an enrolment's roll number and/or move an active enrolment to another section of
    the same class and academic year (``If-Match``: enrolment version; 412 when stale).
    Permission ``student.update_nonidentity``; a scoped holder may only move a student into a
    section they reach; an archived target answers 409 ``structure_archived``.
    Audit: ``enrollment.updated`` (field names and section ids)."""
    fields = data.model_fields_set
    student, enrollment = _owned_enrollment(session, ctx, student_id, enrollment_id)
    values: dict[str, Any] = {}
    if "roll_no" in fields:
        values["roll_no"] = data.roll_no
    if "section_id" in fields:
        if data.section_id is None:
            raise ValidationFailed([error("section_id", "missing")])
        if data.section_id != enrollment.section_id:
            if enrollment.status != "active":
                raise Conflict(
                    "Only an active enrolment can move to another section.",
                    code="enrollment_not_active",
                )
            current = _section_or_422(session, enrollment.section_id)
            target = _enrolment_target(session, data.section_id)
            if (target.academic_year_id, target.class_id) != (
                current.academic_year_id,
                current.class_id,
            ):
                raise ValidationFailed([error("section_id", "different_class_or_year")])
            allowed = _allowed_sections(ctx, UPDATE, _structure(session))
            if allowed is not None and target.id not in allowed:
                raise ValidationFailed([error("section_id", "not_found")])
            values["section_id"] = target.id
    if not values:
        if enrollment.version != expected_version:
            raise PreconditionFailed(
                "The enrolment was changed by someone else. Reload and try again."
            )
        return EnrollmentOut.model_validate(enrollment)
    with _db_errors():
        updated = repo.update_enrollment(
            session, enrollment_id, expected_version=expected_version, values=values
        )
    if updated is None:
        raise PreconditionFailed("The enrolment was changed by someone else. Reload and try again.")
    _audit(
        session,
        action="enrollment.updated",
        resource_type="enrollment",
        resource_id=enrollment_id,
        summary={
            "student_id": student_id,
            "fields": sorted(values),
            "from_section_id": enrollment.section_id if "section_id" in values else None,
            "to_section_id": values.get("section_id"),
        },
    )
    _touch(session, student, _definitions(session))
    if "section_id" in values:
        _values_changed(session, student_id, [ENROLLMENT_KEY])
    return EnrollmentOut.model_validate(updated)


def end_enrollment(
    session: Session,
    ctx: UserContext,
    student_id: uuid.UUID,
    enrollment_id: uuid.UUID,
    data: EnrollmentEnd,
    *,
    expected_version: int,
) -> EnrollmentOut:
    """Close an active enrolment as ``completed`` or ``transferred`` on ``ended_on`` (default
    today), ``If-Match``: enrolment version. The student's record status is not changed (use
    ``PATCH /students/{id}`` for ``left``). Permission ``student.update_nonidentity``.
    Audit: ``enrollment.ended`` ({status, section_id, academic_year_id})."""
    student, enrollment = _owned_enrollment(session, ctx, student_id, enrollment_id)
    if enrollment.status != "active":
        raise Conflict("This enrolment is already closed.", code="enrollment_not_active")
    ended_on = data.ended_on or _today()
    if enrollment.started_on is not None and ended_on < enrollment.started_on:
        raise ValidationFailed([error("ended_on", "before_start")])
    with _db_errors():
        updated = repo.update_enrollment(
            session,
            enrollment_id,
            expected_version=expected_version,
            values={"status": data.status, "ended_on": ended_on},
        )
    if updated is None:
        raise PreconditionFailed("The enrolment was changed by someone else. Reload and try again.")
    _audit(
        session,
        action="enrollment.ended",
        resource_type="enrollment",
        resource_id=enrollment_id,
        summary={
            "student_id": student_id,
            "status": data.status,
            "section_id": enrollment.section_id,
            "academic_year_id": enrollment.academic_year_id,
        },
    )
    _touch(session, student, _definitions(session))
    _values_changed(session, student_id, [ENROLLMENT_KEY])
    return EnrollmentOut.model_validate(updated)


@dataclass(frozen=True, slots=True)
class Withdrawal:
    """What :func:`withdraw_for_transfer_certificate` changed (IDs and codes only)."""

    enrollment_ids: tuple[uuid.UUID, ...]
    previous_status: str
    status: str


def withdraw_for_transfer_certificate(
    session: Session,
    student_id: uuid.UUID,
    *,
    left_on: dt.date,
    certificate_id: uuid.UUID,
) -> Withdrawal:
    """End the student's active enrolments (``transferred`` on ``left_on``) and set the record
    status to ``left`` (unless already ``left`` or ``graduated``), for a transfer certificate
    being issued in the same transaction (FR-CERT-005, US-1102 AC3).

    For ``app.certificates`` only: the caller has checked its own permission
    (``certificate.approve`` / ``certificate.issue``) and the student's scope; this function
    applies no permission of its own. A leaving date before an active enrolment's start answers
    422 ``leaving_date_before_enrolment``. Audit ``student.withdrawn`` ({certificate_id,
    enrollment_ids, from, to}); the enrolment change is queued for DQ like any enrolment write.
    """
    student = repo.get_student(session, student_id, lock=True)
    if student is None:
        raise NotFound("Student not found")
    active = [e for e in repo.enrollments_of(session, student_id) if e.status == "active"]
    for enrollment in active:
        if enrollment.started_on is not None and left_on < enrollment.started_on:
            raise ValidationFailed([error("leaving_date", "leaving_date_before_enrolment")])
    ended: list[uuid.UUID] = []
    with _db_errors():
        for enrollment in active:
            updated = repo.update_enrollment(
                session,
                enrollment.id,
                expected_version=enrollment.version,
                values={"status": "transferred", "ended_on": left_on},
            )
            if updated is None:  # pragma: no cover - the student row is locked
                raise PreconditionFailed("The enrolment was changed meanwhile. Try again.")
            ended.append(enrollment.id)
    previous = student.status
    target = previous if previous in ("left", "graduated") else "left"
    defs = _definitions(session)
    if target != previous:
        with _db_errors():
            changed = repo.update_student(session, student_id, values={"status": target})
        if changed is None:  # pragma: no cover - row locked above
            raise NotFound("Student not found")
        student = changed
    _touch(session, student, defs)
    _audit(
        session,
        action="student.withdrawn",
        resource_type="student",
        resource_id=student_id,
        summary={
            "certificate_id": certificate_id,
            "enrollment_ids": ended,
            "from": previous,
            "to": target,
        },
    )
    if ended:
        _values_changed(session, student_id, [ENROLLMENT_KEY])
    return Withdrawal(enrollment_ids=tuple(ended), previous_status=previous, status=target)


def enrolment_histories(
    session: Session, student_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, list[EnrollmentOut]]:
    """Every enrolment (any year and status) of many students, oldest first, for registers and
    certificates (``app.certificates``). ``student_ids`` MUST come from
    :func:`list_students_in_scope` or a student the caller already reached."""
    out: dict[uuid.UUID, list[EnrollmentOut]] = {sid: [] for sid in student_ids}
    for row in repo.enrollments_of_many(session, list(student_ids)):
        out.setdefault(row.student_id, []).append(EnrollmentOut.model_validate(row))
    return out


# --- search and lists --------------------------------------------------------------------------


def _offset(cursor: str | None, max_offset: int) -> int:
    decoded = decode_cursor(cursor)
    if decoded is None:
        return 0
    offset = decoded.get("o")
    if not isinstance(offset, int) or isinstance(offset, bool) or not 0 <= offset <= max_offset:
        raise ValidationFailed([error("cursor", "invalid", "errors.invalid_cursor")])
    return offset


def _intersect(
    current: frozenset[uuid.UUID] | None, other: frozenset[uuid.UUID]
) -> frozenset[uuid.UUID]:
    return other if current is None else current & other


def search(
    session: Session,
    ctx: UserContext,
    filters: SearchFilters,
    *,
    limit: int = 50,
    cursor: str | None = None,
) -> Page[StudentSummary]:
    """Scoped, ranked search by partial name (EN/TE), admission number, class/section tokens
    (``9b``, ``IX-B``) and parent names (FR-STU-010, US-302). Scope: current-year enrolments in
    the caller's sections for scoped holders (US-302 AC2). ``filters.academic_year_id`` picks
    another year: its enrolments give the class and section, and scoped holders reach that
    year's sections exactly as they reach the current year's.

    ``filters.apaar_id`` (FR-STU-016, ADR-0037): exact match on the typed ``apaar_id``
    attribute only, current values that are verified or recorded (not rejected); same scope.
    The value is never logged or audited (PRV-020)."""
    if filters.query and is_full_aadhaar(filters.query):
        raise ValidationFailed([aadhaar_error("query")], detail=AADHAAR_DETAIL)
    apaar: str | None = None
    if filters.apaar_id is not None:
        apaar = digits12_value(filters.apaar_id)
        if apaar is None:
            raise ValidationFailed([error(APAAR_SEARCH_FIELD, DIGITS12_CODE)])
    cfg = search_config()
    offset = _offset(cursor, cfg.max_offset)
    structure = _structure(session, filters.academic_year_id)
    allowed = _allowed_sections(ctx, READ, structure)
    parsed = parse_query(filters.query or "")
    sections: frozenset[uuid.UUID] | None = None
    if parsed.has_structure:
        codes = {c.id: c.code.upper() for c in structure.classes.values()}
        sections = frozenset(
            s.id
            for s in structure.sections.values()
            if codes.get(s.class_id) in parsed.class_codes
            or (codes.get(s.class_id), s.name.upper()) in parsed.sections
        )
    if filters.section_id is not None:
        sections = _intersect(sections, frozenset({filters.section_id}))
    if filters.class_id is not None:
        in_class = frozenset(
            s.id for s in structure.sections.values() if s.class_id == filters.class_id
        )
        sections = _intersect(sections, in_class)
    empty = Page[StudentSummary](data=[], next_cursor=None)
    if (allowed is not None and not allowed) or (sections is not None and not sections):
        return empty
    rows = repo.search(
        session,
        repo.SearchSpec(
            academic_year_id=structure.year_id,
            allowed_sections=allowed,
            section_filter=sections,
            status=filters.status,
            admission_no=filters.admission_no,
            name_key=parsed.name_key,
            name_phonetic=parsed.name_phonetic,
            admission_terms=parsed.admission_terms,
            threshold=cfg.name_threshold,
            parent_weight=cfg.parent_weight,
            admission_bonus=cfg.admission_exact_bonus,
            offset=offset,
            limit=limit,
            apaar_key=APAAR_SEARCH_FIELD if apaar is not None else None,
            apaar_id=apaar,
        ),
    )
    ranked = bool(parsed.name_key or parsed.admission_terms)
    unranked_field = APAAR_SEARCH_FIELD if apaar is not None else None
    items = [
        StudentSummary(
            id=r.id,
            display_name=r.full_name,
            admission_no=r.admission_no,
            status=r.status,
            class_section=structure.label(r.section_id),
            section_id=r.section_id,
            match=StudentMatch(
                field=r.match_field if ranked else unranked_field,
                score=round(float(r.score), 3) if ranked else None,
            ),
        )
        for r in rows[:limit]
    ]
    more = len(rows) > limit
    return Page[StudentSummary](
        data=items, next_cursor=encode_cursor({"o": offset + limit}) if more else None
    )


def list_students_in_scope(
    session: Session,
    ctx: UserContext,
    *,
    section_ids: Collection[uuid.UUID] | None = None,
    class_ids: Collection[uuid.UUID] | None = None,
    permissions: Collection[str] = (),
) -> list[uuid.UUID]:
    """Student ids the caller may read (``student.read_basic``), optionally limited to
    current-year sections/classes. For dq and exports: pass the result to
    :func:`canonical_values` / :func:`source_values`.

    ``permissions``: further permissions whose scope must ALSO reach the student (e.g.
    ``insights.read`` and ``student.read_sensitive`` for restricted insights, M5); the result is
    the intersection of every grant's current-year sections."""
    structure = _structure(session)
    allowed = _allowed_sections(ctx, READ, structure)
    for permission in dict.fromkeys(permissions):
        if permission == READ:
            continue
        extra = _allowed_sections(ctx, permission, structure)
        if extra is not None:
            allowed = _intersect(allowed, extra)
    wanted: frozenset[uuid.UUID] | None = None
    if section_ids is not None:
        wanted = frozenset(section_ids)
    if class_ids is not None:
        classes = set(class_ids)
        wanted = _intersect(
            wanted,
            frozenset(s.id for s in structure.sections.values() if s.class_id in classes),
        )
    if allowed is None and wanted is None:
        return repo.all_student_ids(session)
    if structure.year_id is None:
        return []
    combined = wanted if allowed is None else _intersect(wanted, allowed)
    return repo.student_ids_in_sections(
        session, academic_year_id=structure.year_id, section_ids=combined
    )


def summaries(
    session: Session, ctx: UserContext, student_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, StudentSummary]:
    """Display fields (name, admission number, status, current class-section) of the given
    students the caller may read (``student.read_basic`` and its scope, as in :func:`search`);
    ids outside the scope or unknown are left out. For modules that keep student ids of their
    own (Tally ledger links, FR-TALLY-006). C2 only, never a C3 value."""
    if not student_ids or not ctx.has(READ):
        return {}
    structure = _structure(session)
    allowed = _allowed_sections(ctx, READ, structure)
    wanted = set(student_ids)
    if allowed is not None:
        if structure.year_id is None or not allowed:
            return {}
        wanted = repo.students_in_sections(
            session, wanted, academic_year_id=structure.year_id, section_ids=allowed
        )
    return {
        r.id: StudentSummary(
            id=r.id,
            display_name=r.full_name,
            admission_no=r.admission_no,
            status=r.status,
            class_section=structure.label(r.current_section_id),
            section_id=r.current_section_id,
            match=StudentMatch(field=None, score=None),
        )
        for r in repo.summary_rows(session, wanted)
    }


@dataclass(frozen=True, slots=True)
class CanonicalValue:
    value: str | None
    source: str | None
    verified: bool
    provisional: bool
    conflicts: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SourceValue:
    value_id: uuid.UUID
    value: str | None
    norm: str | None
    verification_status: str
    recorded_at: dt.datetime
    evidence_document_id: uuid.UUID | None


def canonical_values(
    session: Session,
    student_ids: Collection[uuid.UUID],
    attribute_keys: Collection[str],
    *,
    include_sensitive: bool = False,
) -> dict[uuid.UUID, dict[str, CanonicalValue]]:
    """Canonical values for many students (dq, exports). ``student_ids`` MUST come from
    :func:`list_students_in_scope`; C3 attributes are skipped unless ``include_sensitive`` (the
    caller then needs ``student.read_sensitive`` and must never log or export them unmasked
    without its own audit)."""
    defs = _definitions(session)
    keys = [k for k in attribute_keys if k in defs and (include_sensitive or not defs[k].sensitive)]
    grouped = _group_current(repo.current_values(session, list(student_ids), keys))
    out: dict[uuid.UUID, dict[str, CanonicalValue]] = {}
    for sid in student_ids:
        per: dict[str, CanonicalValue] = {}
        for key in keys:
            res = _resolve(defs[key], grouped.get(sid, {}).get(key, {}))
            chosen = res.value
            per[key] = CanonicalValue(
                value=_plain(session, chosen) if chosen is not None else None,
                source=chosen.source if chosen is not None else None,
                verified=chosen is not None and chosen.verification_status == "verified",
                provisional=res.provisional,
                conflicts=res.conflicts,
            )
        out[sid] = per
    return out


def source_values(
    session: Session,
    student_ids: Collection[uuid.UUID],
    attribute_keys: Collection[str],
    *,
    include_sensitive: bool = False,
) -> dict[uuid.UUID, dict[str, dict[str, SourceValue]]]:
    """Current value per source (dq comparisons such as register vs Aadhaar-as-printed).

    Same contract as :func:`canonical_values` for ``student_ids`` and C3 attributes.
    """
    defs = _definitions(session)
    keys = [k for k in attribute_keys if k in defs and (include_sensitive or not defs[k].sensitive)]
    rows = repo.current_values(session, list(student_ids), keys)
    out: dict[uuid.UUID, dict[str, dict[str, SourceValue]]] = {}
    for row in rows:
        plain = _plain(session, row)
        norm = row.value_norm
        if norm is None and plain is not None and defs[row.attribute_key].is_name:
            norm = comparison_key(plain)
        out.setdefault(row.student_id, {}).setdefault(row.attribute_key, {})[row.source] = (
            SourceValue(
                value_id=row.id,
                value=plain,
                norm=norm,
                verification_status=row.verification_status,
                recorded_at=row.recorded_at,
                evidence_document_id=row.evidence_document_id,
            )
        )
    return out


@dataclass(frozen=True, slots=True)
class ActiveEnrolment:
    enrollment_id: uuid.UUID
    student_id: uuid.UUID
    section_id: uuid.UUID
    academic_year_id: uuid.UUID
    roll_no: str | None = None  # exports: class lists and "ready to enter" sheets


def active_enrolments(
    session: Session, student_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, list[ActiveEnrolment]]:
    """Active enrolments in any academic year for many students (dq: DQ-007 age band,
    DQ-012 enrolled twice; exports: section and roll number). ``student_ids`` MUST come from
    :func:`list_students_in_scope`."""
    out: dict[uuid.UUID, list[ActiveEnrolment]] = {}
    for row in repo.active_enrollments_of(session, list(student_ids)):
        out.setdefault(row.student_id, []).append(
            ActiveEnrolment(
                enrollment_id=row.id,
                student_id=row.student_id,
                section_id=row.section_id,
                academic_year_id=row.academic_year_id,
                roll_no=row.roll_no,
            )
        )
    return out


def ensure_in_scope(
    session: Session, ctx: UserContext, student_id: uuid.UUID, *permissions: str
) -> None:
    """404 unless the caller reaches ``student_id`` with ``student.read_basic`` AND every one of
    ``permissions`` (object-level check for other modules' student-scoped reads and writes,
    e.g. attendance, notes and flags of M5; SEC-015)."""
    structure = _structure(session)
    _visible_student(session, ctx, student_id, structure=structure)
    for permission in dict.fromkeys(permissions):
        if not _in_scope(session, ctx, student_id, permission, structure):
            raise NotFound("Student not found")


def current_placements(
    session: Session, student_ids: Collection[uuid.UUID] | None = None
) -> dict[uuid.UUID, uuid.UUID]:
    """Student -> section of their active enrolment in the current academic year (system reads,
    e.g. the early-warning rules of M5). ``student_ids`` None = every such student of the
    school; students without one (left, not enrolled) are absent from the result."""
    structure = _structure(session)
    if structure.year_id is None:
        return {}
    ids = (
        list(student_ids)
        if student_ids is not None
        else repo.student_ids_in_sections(
            session, academic_year_id=structure.year_id, section_ids=None
        )
    )
    return {
        row.student_id: row.section_id
        for row in repo.active_enrollments_of(session, ids)
        if row.academic_year_id == structure.year_id
    }


@dataclass(frozen=True, slots=True)
class StructureInUse:
    """Academic years, classes and sections with at least one active enrolment."""

    year_ids: frozenset[uuid.UUID]
    class_ids: frozenset[uuid.UUID]
    section_ids: frozenset[uuid.UUID]


def structure_in_use(session: Session) -> StructureInUse:
    """Which structure rows have active enrolments (the archive guard's rule, FR-TEN-010), for
    the structure screens. Yes/no only: no counts, names or student ids."""
    placements = repo.active_placements(session)
    sections = frozenset(s for _, s in placements)
    classes = frozenset(s.class_id for s in tenancy.list_sections(session) if s.id in sections)
    return StructureInUse(
        year_ids=frozenset(y for y, _ in placements), class_ids=classes, section_ids=sections
    )


def student_ids_for_import_batch(session: Session, batch_id: uuid.UUID) -> list[uuid.UUID]:
    """Students with a value recorded by import batch ``batch_id`` (dq runs for a batch).
    Callers intersect the result with :func:`list_students_in_scope`."""
    return repo.student_ids_with_batch(session, batch_id)


# --- import revert (FR-IMP-005; called by app.imports only) ------------------------------------

ANCHOR_SOURCE: Final = "admission_register"  # BR-01: the legal anchor of identity values


def _import_has_dependents() -> Conflict:
    return Conflict(
        "Records from this import were changed after it was added, so it cannot be reverted. "
        "Correct the records instead.",
        code="import_has_dependents",
    )


@dataclass(frozen=True, slots=True)
class ImportRevertPlan:
    """What reverting one import batch changes (built by :func:`plan_import_revert`)."""

    batch_id: uuid.UUID
    created: tuple[uuid.UUID, ...]  # students the batch created: removed
    withdraw: tuple[uuid.UUID, ...]  # batch values with no predecessor: marked rejected
    replaced: tuple[uuid.UUID, ...]  # batch values that replaced one: the predecessor returns


@dataclass(frozen=True, slots=True)
class ImportRevertResult:
    students_removed: int
    values_withdrawn: int
    values_restored: int


def plan_import_revert(
    session: Session, batch_id: uuid.UUID, created_students: Mapping[uuid.UUID, int | None]
) -> ImportRevertPlan:
    """Check that import batch ``batch_id`` can still be undone (FR-IMP-005); read-only.

    ``created_students`` maps each student the batch created to the version the batch left it
    at. Refused (409 ``import_has_dependents``) when anything was recorded on top of the batch:
    a student it created was changed since (version), one of its values was superseded, or a
    value it replaced is a verified or admission-register identity value (BR-01: those change
    only through a change request, never by a revert).
    """
    versions = repo.student_versions(session, list(created_students))
    if any(versions.get(sid) != version for sid, version in created_students.items()):
        raise _import_has_dependents()
    values = repo.batch_values(session, batch_id)
    if any(v.superseded_by is not None for v in values):
        raise _import_has_dependents()
    priors = repo.superseded_by_any(session, [v.id for v in values])
    defs = _definitions(session)
    for prior in priors:
        definition = defs.get(prior.attribute_key)
        if definition is None or (
            definition.is_identity
            and (
                prior.source in (ANCHOR_SOURCE, definition.policy.anchor)
                or prior.verification_status != "unverified"
            )
        ):
            raise _import_has_dependents()
    replaced = {p.superseded_by for p in priors if p.superseded_by is not None}
    return ImportRevertPlan(
        batch_id=batch_id,
        created=tuple(created_students),
        withdraw=tuple(v.id for v in values if v.id not in replaced),
        replaced=tuple(sorted(replaced, key=str)),
    )


def revert_import(session: Session, ctx: UserContext, plan: ImportRevertPlan) -> ImportRevertResult:
    """Undo an import batch checked by :func:`plan_import_revert`, in the caller's transaction.

    The caller (``app.imports``) must already have moved the batch to ``reverting`` in this
    transaction: the database lets a student be deleted only then. In order: values the batch
    added to existing students are withdrawn (marked ``rejected``; history kept), any value they
    replaced becomes current again (re-recorded with :func:`record_value`, same source, evidence
    and verification), the profile and version of every other student that lost a value are
    refreshed, and the students the batch created are removed with their values, enrolment,
    profile and derived data (``ON DELETE CASCADE``). Other records pointing at those students
    (e.g. change requests) refuse the delete: 409 ``import_has_dependents``.

    Audit (same transaction): ``student.values.withdrawn`` per existing student,
    ``student.value.recorded`` per restored value, ``student.removed`` per removed student.
    Values keep their batch tag, so the batch's own ``import.reverted`` event (queued by the
    caller) drives the data-quality re-run instead of ``student.values.changed``.
    """
    withdrawn = repo.reject_current_values(session, plan.withdraw, ctx.user_id)
    created = set(plan.created)
    lost: dict[uuid.UUID, list[AttributeValue]] = {}
    for row in withdrawn:
        if row.student_id not in created:
            lost.setdefault(row.student_id, []).append(row)
    for student_id, rows in lost.items():
        _audit(
            session,
            action="student.values.withdrawn",
            resource_type="student",
            resource_id=student_id,
            summary={
                "import_batch_id": plan.batch_id,
                "attribute_keys": sorted({r.attribute_key for r in rows}),
                "value_count": len(rows),
                "reason": "import_reverted",
            },
        )
    priors = repo.superseded_by_any(session, plan.replaced)
    for prior in priors:
        record_value(
            session,
            ctx,
            prior.student_id,
            prior.attribute_key,
            prior.source,
            _plain(session, prior) or "",
            evidence_document_id=prior.evidence_document_id,
            verification=prior.verification_status,  # type: ignore[arg-type]
        )
    restored = {p.student_id for p in priors}
    if lost.keys() - restored:
        defs = _definitions(session)
        for student_id in sorted(lost.keys() - restored, key=str):
            student = repo.get_student(session, student_id, lock=True)
            if student is not None:
                _touch(session, student, defs)
    try:
        removed = repo.delete_students(session, plan.created)
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) == "23503":
            raise _import_has_dependents() from exc
        raise
    for student_id in plan.created:
        _audit(
            session,
            action="student.removed",
            resource_type="student",
            resource_id=student_id,
            summary={"import_batch_id": plan.batch_id, "reason": "import_reverted"},
        )
    return ImportRevertResult(
        students_removed=removed,
        values_withdrawn=len(withdrawn),
        values_restored=len(priors),
    )


# --- sensitive reveal --------------------------------------------------------------------------


def reveal_sensitive(
    session: Session, ctx: UserContext, student_id: uuid.UUID, data: RevealIn
) -> RevealOut:
    """Return one C3 value in clear (``student.read_sensitive``, in scope) and audit it
    (``student.sensitive_revealed``: attribute key, value/guardian id; never the value)."""
    structure = _structure(session)
    _visible_student(session, ctx, student_id, permission=SENSITIVE, structure=structure)
    if data.attribute_key in GUARDIAN_FIELDS:
        return _reveal_guardian(session, student_id, data)
    defs = _definitions(session)
    definition = _definition_for(defs, data.attribute_key, "attribute_key")
    if not definition.sensitive:
        raise ValidationFailed([error("attribute_key", "not_sensitive")])
    row: AttributeValue | None
    if data.value_id is not None:
        row = repo.get_value(session, data.value_id)
        if row is None or row.student_id != student_id or row.attribute_key != definition.key:
            raise NotFound("Value not found")
    else:
        current = _group_current(repo.current_values(session, [student_id], [definition.key]))
        row = _resolve(definition, current.get(student_id, {}).get(definition.key, {})).value
        if row is None:
            raise NotFound("No value recorded")
    value = _plain(session, row)
    _audit(
        session,
        action="student.sensitive_revealed",
        resource_type="student",
        resource_id=student_id,
        summary={"attribute_key": definition.key, "source": row.source, "value_id": row.id},
    )
    display = aadhaar_display(value) if definition.data_type == "digits4" and value else value
    return RevealOut(
        attribute_key=definition.key,
        source=row.source,
        value=value,
        display=display,
        value_id=row.id,
        guardian_id=None,
    )


def _reveal_guardian(session: Session, student_id: uuid.UUID, data: RevealIn) -> RevealOut:
    if data.guardian_id is None:
        raise ValidationFailed([error("guardian_id", "missing")])
    if repo.get_link(session, student_id, data.guardian_id) is None:
        raise NotFound("Guardian not found")
    guardian = repo.ensure_found(repo.get_guardian(session, data.guardian_id), "Guardian")
    column = GUARDIAN_FIELDS[data.attribute_key]
    blob: bytes | None = getattr(guardian, column)
    if blob is None:
        raise NotFound("No value recorded")
    value = crypto.decrypt_value(
        session, blob, table=GUARDIANS_TABLE, column=column, row_id=guardian.id
    )
    _audit(
        session,
        action="student.sensitive_revealed",
        resource_type="student",
        resource_id=student_id,
        summary={"attribute_key": data.attribute_key, "guardian_id": guardian.id},
    )
    display = phone_display(value) if data.attribute_key == "guardian_phone" else value
    return RevealOut(
        attribute_key=data.attribute_key,
        source=None,
        value=value,
        display=display,
        value_id=None,
        guardian_id=guardian.id,
    )


# --- guardians ---------------------------------------------------------------------------------


def _guardian_out(
    guardian: Guardian, relationship: str, is_primary: bool, *, reveal: bool
) -> GuardianOut:
    has_phone = guardian.phone_ciphertext is not None
    has_address = guardian.address_ciphertext is not None
    return GuardianOut(
        id=guardian.id,
        full_name=guardian.full_name,
        relationship=relationship,
        is_primary=is_primary,
        has_phone=has_phone if reveal else False,
        has_address=has_address if reveal else False,
        phone=MASK if (reveal and has_phone) else None,
        address=MASK if (reveal and has_address) else None,
        masked=reveal and (has_phone or has_address),
        version=guardian.version,
    )


def list_guardians(session: Session, ctx: UserContext, student_id: uuid.UUID) -> list[GuardianOut]:
    """Guardians of a student (``student.read_basic``); phone/address masked and shown only to
    callers who may reveal them."""
    structure = _structure(session)
    _visible_student(session, ctx, student_id, structure=structure)
    reveal = _can_reveal(session, ctx, student_id, structure)
    return [
        _guardian_out(g, link.relationship, link.is_primary, reveal=reveal)
        for g, link in repo.guardians_of(session, student_id)
    ]


def _encrypt_guardian_field(
    session: Session, guardian_id: uuid.UUID, column: str, value: str
) -> tuple[bytes, int]:
    return crypto.encrypt_value(
        session, value, table=GUARDIANS_TABLE, column=column, row_id=guardian_id
    )


def _guardian_secret_columns(
    session: Session,
    guardian_id: uuid.UUID,
    *,
    phone: str | None,
    address: str | None,
    set_phone: bool,
    set_address: bool,
) -> dict[str, Any]:
    columns: dict[str, Any] = {}
    versions: list[int] = []
    if set_phone:
        if phone is None:
            columns.update(phone_ciphertext=None, phone_blind_index=None)
        else:
            blob, version = _encrypt_guardian_field(session, guardian_id, "phone_ciphertext", phone)
            digest, _ = crypto.blind_index(
                session, phone, purpose=PHONE_INDEX_PURPOSE, key_version=version
            )
            columns.update(phone_ciphertext=blob, phone_blind_index=digest)
            versions.append(version)
    if set_address:
        if address is None:
            columns["address_ciphertext"] = None
        else:
            blob, version = _encrypt_guardian_field(
                session, guardian_id, "address_ciphertext", address
            )
            columns["address_ciphertext"] = blob
            versions.append(version)
    if versions:
        columns["key_version"] = max(versions)
    return columns


def add_guardian(
    session: Session, ctx: UserContext, student_id: uuid.UUID, data: GuardianCreate
) -> GuardianOut:
    """Add a new guardian or link an existing one (siblings share guardians; FR-STU-008).

    Phone and address are C3: encrypted, phone with a blind index for lookups. Permission
    ``student.update_nonidentity``. Audit: ``guardian.created`` and ``guardian.linked``.
    """
    reject_full_aadhaar({"full_name": data.full_name, "phone": data.phone, "address": data.address})
    structure = _structure(session)
    student = _visible_student(
        session, ctx, student_id, permission=UPDATE, structure=structure, lock=True
    )
    if data.guardian_id is not None:
        if data.full_name or data.phone or data.address:
            raise ValidationFailed([error("guardian_id", "link_or_create")])
        guardian = repo.get_guardian(session, data.guardian_id)
        if guardian is None:
            raise ValidationFailed([error("guardian_id", "not_found")])
        created = False
    else:
        if not data.full_name:
            raise ValidationFailed([error("full_name", "missing")])
        phone = normalize_phone(data.phone) if data.phone else None
        guardian_id = new_id()
        secrets = _guardian_secret_columns(
            session,
            guardian_id,
            phone=phone,
            address=data.address,
            set_phone=phone is not None,
            set_address=data.address is not None,
        )
        with _db_errors():
            guardian = repo.insert_guardian(
                session,
                id=guardian_id,
                tenant_id=student.tenant_id,
                full_name=data.full_name,
                full_name_norm=comparison_key(data.full_name),
                **secrets,
            )
        created = True
    with _db_errors():
        if data.is_primary:
            repo.clear_primary(session, student_id)
        repo.link_guardian(
            session,
            tenant_id=student.tenant_id,
            student_id=student_id,
            guardian_id=guardian.id,
            relationship=data.relationship,
            is_primary=data.is_primary,
        )
    if created:
        _audit(
            session,
            action="guardian.created",
            resource_type="guardian",
            resource_id=guardian.id,
            summary={
                "student_id": student_id,
                "fields": sorted(
                    f
                    for f, present in (
                        ("full_name", True),
                        ("phone", guardian.phone_ciphertext is not None),
                        ("address", guardian.address_ciphertext is not None),
                    )
                    if present
                ),
            },
        )
    _audit(
        session,
        action="guardian.linked",
        resource_type="guardian",
        resource_id=guardian.id,
        summary={
            "student_id": student_id,
            "relationship": data.relationship,
            "is_primary": data.is_primary,
        },
    )
    _touch(session, student, _definitions(session))
    reveal = _can_reveal(session, ctx, student_id, structure)
    return _guardian_out(guardian, data.relationship, data.is_primary, reveal=reveal)


def update_guardian(
    session: Session,
    ctx: UserContext,
    student_id: uuid.UUID,
    guardian_id: uuid.UUID,
    data: GuardianPatch,
    *,
    expected_version: int,
) -> GuardianOut:
    """Change a guardian's name, phone or address (``If-Match``: guardian version) and/or the
    relationship/primary flag for this student. Audit: ``guardian.updated`` (field names)."""
    fields = data.model_fields_set
    reject_full_aadhaar(
        {k: getattr(data, k) for k in fields if k in ("full_name", "phone", "address")}
    )
    structure = _structure(session)
    student = _visible_student(
        session, ctx, student_id, permission=UPDATE, structure=structure, lock=True
    )
    link = repo.get_link(session, student_id, guardian_id)
    if link is None:
        raise NotFound("Guardian not found")
    if "full_name" in fields and data.full_name is None:
        raise ValidationFailed([error("full_name", "missing")])
    phone = normalize_phone(data.phone) if data.phone else None
    values: dict[str, Any] = {}
    if data.full_name is not None:
        values.update(full_name=data.full_name, full_name_norm=comparison_key(data.full_name))
    values.update(
        _guardian_secret_columns(
            session,
            guardian_id,
            phone=phone,
            address=data.address,
            set_phone="phone" in fields,
            set_address="address" in fields,
        )
    )
    with _db_errors():
        guardian = repo.update_guardian(
            session, guardian_id, expected_version=expected_version, values=values
        )
    if guardian is None:
        raise PreconditionFailed("The guardian was changed by someone else. Reload and try again.")
    link_values: dict[str, Any] = {}
    if data.relationship is not None:
        link_values["relationship"] = data.relationship
    if data.is_primary is not None:
        link_values["is_primary"] = data.is_primary
    with _db_errors():
        if data.is_primary:
            repo.clear_primary(session, student_id)
        if link_values:
            repo.update_link(session, student_id, guardian_id, link_values)
    link = repo.ensure_found(repo.get_link(session, student_id, guardian_id), "Guardian")
    _audit(
        session,
        action="guardian.updated",
        resource_type="guardian",
        resource_id=guardian_id,
        summary={"student_id": student_id, "fields": sorted(fields)},
    )
    _touch(session, student, _definitions(session))
    reveal = _can_reveal(session, ctx, student_id, structure)
    return _guardian_out(guardian, link.relationship, link.is_primary, reveal=reveal)


def remove_guardian(
    session: Session,
    ctx: UserContext,
    student_id: uuid.UUID,
    guardian_id: uuid.UUID,
    *,
    expected_version: int,
) -> None:
    """Unlink a guardian from a student (``If-Match``: guardian version; 412 when stale).

    A guardian still linked to another student (siblings) stays for them; one linked to nobody
    any more is deleted with its encrypted phone and address (data minimisation, docs/08).
    Permission ``student.update_nonidentity``. Audit: ``guardian.unlinked`` (and
    ``guardian.deleted``).
    """
    structure = _structure(session)
    student = _visible_student(
        session, ctx, student_id, permission=UPDATE, structure=structure, lock=True
    )
    link = repo.get_link(session, student_id, guardian_id)
    guardian = repo.get_guardian(session, guardian_id) if link is not None else None
    if link is None or guardian is None:
        raise NotFound("Guardian not found")
    if guardian.version != expected_version:
        raise PreconditionFailed("The guardian was changed by someone else. Reload and try again.")
    repo.unlink_guardian(session, student_id, guardian_id)
    _audit(
        session,
        action="guardian.unlinked",
        resource_type="guardian",
        resource_id=guardian_id,
        summary={
            "student_id": student_id,
            "relationship": link.relationship,
            "was_primary": link.is_primary,
        },
    )
    if not repo.guardian_student_ids(session, guardian_id):
        repo.delete_guardian(session, guardian_id)
        _audit(
            session,
            action="guardian.deleted",
            resource_type="guardian",
            resource_id=guardian_id,
            summary={"student_id": student_id, "reason": "no_linked_students"},
        )
    _touch(session, student, _definitions(session))


# --- promotions (FR-TEN-011, US-202 AC2; owner decisions 2026-09-27) ----------------------------

PROMOTE: Final = "tenant.structure.manage"
UNDO_WINDOW: Final = dt.timedelta(hours=24)
_SKIP_STATUSES: Final = frozenset({"left", "graduated"})


@dataclass(frozen=True, slots=True)
class _PlannedStudent:
    student_id: uuid.UUID
    enrollment_id: uuid.UUID
    enrollment_version: int
    from_section_id: uuid.UUID
    student_status: str
    outcome: str  # promoted | held_back | graduated | skipped
    to_section_id: uuid.UUID | None
    target_class_id: uuid.UUID | None
    reason: str | None


@dataclass(frozen=True, slots=True)
class _Plan:
    from_year: Any
    to_year: Any
    students: tuple[_PlannedStudent, ...]
    labels: dict[uuid.UUID, str]
    fingerprint: str

    def counts(self) -> PromotionCounts:
        tally = dict.fromkeys(("promoted", "held_back", "graduated", "skipped"), 0)
        for s in self.students:
            tally[s.outcome] += 1
        return PromotionCounts(**tally)

    def problems(self) -> list[PromotionProblemOut]:
        grouped: dict[tuple[uuid.UUID, uuid.UUID], int] = {}
        for s in self.students:
            if s.reason == "no_target_section" and s.target_class_id is not None:
                key = (s.from_section_id, s.target_class_id)
                grouped[key] = grouped.get(key, 0) + 1
        return [
            PromotionProblemOut(
                code="no_target_section",
                from_section_id=from_id,
                from_label=self.labels.get(from_id, "?"),
                target_class_id=class_id,
                count=count,
            )
            for (from_id, class_id), count in sorted(grouped.items(), key=lambda kv: str(kv[0]))
        ]


def _promotion_error(field_name: str, code: str) -> ValidationFailed:
    return ValidationFailed([error(field_name, code)])


def _promotion_plan(
    session: Session, year_id: uuid.UUID, data: PromotionIn, *, lock: bool
) -> _Plan:
    """Compute what a promotion of ``year_id`` into ``data.to_academic_year_id`` would do.

    Read-only. Rules (owner decisions 2026-09-27): every active enrolment of the source year is
    considered; a student whose record status is ``left`` or ``graduated`` is skipped (nothing
    changes); a student already actively enrolled in the target year is skipped; a held-back
    student goes to the same class again; the others go to the next class by ``sort_order``, and
    students of the last class graduate (no new enrolment). The target section is the
    ``section_map`` entry for (source section, target class), else the target year's section of
    that class with the same name; without one the student cannot be placed (problem
    ``no_target_section``).
    """
    from_year, to_year = _promotion_years(session, year_id, data.to_academic_year_id)
    # Archived classes and sections are never targets (US-202): the last active class graduates.
    classes = sorted(
        tenancy.list_classes(session, include_archived=False), key=lambda c: (c.sort_order, c.code)
    )
    next_class = {a.id: b.id for a, b in itertools.pairwise(classes)}
    class_code = {c.id: c.code for c in tenancy.list_classes(session)}
    sources = {s.id: s for s in tenancy.list_sections(session, academic_year_id=from_year.id)}
    targets = {
        s.id: s
        for s in tenancy.list_sections(session, academic_year_id=to_year.id, include_archived=False)
    }
    by_name = {(s.class_id, s.name.casefold()): s.id for s in targets.values()}
    labels = {
        s.id: f"{class_code.get(s.class_id, '?')}-{s.name}"
        for s in (*sources.values(), *targets.values())
    }
    overrides = _section_overrides(data, sources, targets)
    rows = repo.year_enrollments(session, from_year.id, lock=lock)
    enrolled = {e.student_id for e, _ in rows}
    held = set(data.held_back_student_ids)
    for i, sid in enumerate(data.held_back_student_ids):
        if sid not in enrolled:
            raise _promotion_error(f"held_back_student_ids.{i}", "not_in_year")
    already = repo.students_active_in_year(session, enrolled, to_year.id)
    planned: list[_PlannedStudent] = []
    for enrollment, status in rows:
        source = sources.get(enrollment.section_id)
        outcome, reason, target_class, to_section = "skipped", None, None, None
        if status in _SKIP_STATUSES:
            reason = status
        elif enrollment.student_id in already:
            reason = "already_enrolled"
        elif source is not None:
            if enrollment.student_id in held:
                outcome, target_class = "held_back", source.class_id
            else:
                target_class = next_class.get(source.class_id)
                outcome = "promoted" if target_class is not None else "graduated"
            if target_class is not None:
                to_section = overrides.get((source.id, target_class)) or by_name.get(
                    (target_class, source.name.casefold())
                )
                if to_section is None:
                    reason = "no_target_section"
        planned.append(
            _PlannedStudent(
                student_id=enrollment.student_id,
                enrollment_id=enrollment.id,
                enrollment_version=enrollment.version,
                from_section_id=enrollment.section_id,
                student_status=status,
                outcome=outcome,
                to_section_id=to_section,
                target_class_id=target_class,
                reason=reason,
            )
        )
    return _Plan(
        from_year=from_year,
        to_year=to_year,
        students=tuple(planned),
        labels=labels,
        fingerprint=_plan_fingerprint(from_year.id, to_year.id, planned),
    )


def _promotion_years(
    session: Session, year_id: uuid.UUID, to_year_id: uuid.UUID
) -> tuple[Any, Any]:
    from_year = tenancy.get_academic_year(session, year_id)  # 404 for unknown/other school
    try:
        to_year = tenancy.get_academic_year(session, to_year_id)
    except NotFound:
        raise _promotion_error("to_academic_year_id", "not_found") from None
    if to_year.id == from_year.id:
        raise _promotion_error("to_academic_year_id", "same_year")
    if to_year.starts_on <= from_year.starts_on:
        raise _promotion_error("to_academic_year_id", "not_later")
    return from_year, to_year


def _section_overrides(
    data: PromotionIn, sources: Mapping[uuid.UUID, Any], targets: Mapping[uuid.UUID, Any]
) -> dict[tuple[uuid.UUID, uuid.UUID], uuid.UUID]:
    """``section_map`` as {(source section, target class): target section}, validated."""
    overrides: dict[tuple[uuid.UUID, uuid.UUID], uuid.UUID] = {}
    for i, entry in enumerate(data.section_map):
        if entry.from_section_id not in sources:
            raise _promotion_error(f"section_map.{i}.from_section_id", "not_found")
        target = targets.get(entry.to_section_id)
        if target is None:
            raise _promotion_error(f"section_map.{i}.to_section_id", "not_found")
        key = (entry.from_section_id, target.class_id)
        if key in overrides:
            raise _promotion_error(f"section_map.{i}", "duplicate")
        overrides[key] = target.id
    return overrides


def _plan_fingerprint(
    from_year_id: uuid.UUID, to_year_id: uuid.UUID, planned: Sequence[_PlannedStudent]
) -> str:
    """SHA-256 over what the plan does (ids, enrolment versions, outcomes, target sections)."""
    material = json.dumps(
        {
            "from": str(from_year_id),
            "to": str(to_year_id),
            "students": sorted(
                [
                    str(p.student_id),
                    str(p.enrollment_id),
                    str(p.enrollment_version),
                    p.outcome,
                    str(p.to_section_id or ""),
                ]
                for p in planned
            ),
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(material.encode()).hexdigest()


def _preview_out(plan: _Plan) -> PromotionPreviewOut:
    groups: dict[tuple[uuid.UUID, str, uuid.UUID | None], int] = {}
    for s in plan.students:
        key = (s.from_section_id, s.outcome, s.to_section_id)
        groups[key] = groups.get(key, 0) + 1
    problems = plan.problems()
    counts = plan.counts()
    movers = counts.promoted + counts.held_back + counts.graduated
    return PromotionPreviewOut(
        from_academic_year_id=plan.from_year.id,
        to_academic_year_id=plan.to_year.id,
        counts=counts,
        groups=[
            PromotionGroupOut(
                from_section_id=from_id,
                from_label=plan.labels.get(from_id, "?"),
                outcome=outcome,
                to_section_id=to_id,
                to_label=plan.labels.get(to_id) if to_id else None,
                count=count,
            )
            for (from_id, outcome, to_id), count in sorted(
                groups.items(), key=lambda kv: (plan.labels.get(kv[0][0], ""), kv[0][1])
            )
        ],
        problems=problems,
        students=[
            PromotionStudentOut(
                student_id=s.student_id,
                enrollment_id=s.enrollment_id,
                from_section_id=s.from_section_id,
                outcome=s.outcome,
                to_section_id=s.to_section_id,
                reason=s.reason,
            )
            for s in plan.students
        ],
        plan_fingerprint=plan.fingerprint,
        can_commit=not problems and movers > 0,
    )


def preview_promotion(
    session: Session, ctx: UserContext, year_id: uuid.UUID, data: PromotionIn
) -> PromotionPreviewOut:
    """What promoting ``year_id`` into ``data.to_academic_year_id`` would do; writes nothing
    (permission ``tenant.structure.manage``, school-wide). Unknown years or another school's
    year: 404. Ids only (no names): the screen joins them with the student list."""
    return _preview_out(_promotion_plan(session, year_id, data, lock=False))


def _run_names(session: Session, runs: Sequence[Any]) -> dict[uuid.UUID, str]:
    """Display names of who committed/undid ``runs`` (members of this school only)."""
    users = {u for r in runs for u in (r.committed_by, r.undone_by) if u is not None}
    return {u: ref[1] for u, ref in identity.members_for_users(session, users).items()}


def _run_out(
    run: Any, now: dt.datetime, names: Mapping[uuid.UUID, str] | None = None
) -> PromotionRunOut:
    names = names or {}
    undo_until = run.committed_at + UNDO_WINDOW
    return PromotionRunOut(
        id=run.id,
        from_academic_year_id=run.from_academic_year_id,
        to_academic_year_id=run.to_academic_year_id,
        status=run.status,
        counts=PromotionCounts(
            promoted=run.promoted_count,
            held_back=run.held_back_count,
            graduated=run.graduated_count,
            skipped=run.skipped_count,
        ),
        plan_fingerprint=run.plan_fingerprint,
        committed_by=run.committed_by,
        committed_by_name=names.get(run.committed_by) if run.committed_by else None,
        committed_at=run.committed_at,
        undo_until=undo_until,
        can_undo=run.status == "committed" and now < undo_until,
        undone_by=run.undone_by,
        undone_by_name=names.get(run.undone_by) if run.undone_by else None,
        undone_at=run.undone_at,
        version=run.version,
    )


def list_promotions(
    session: Session, ctx: UserContext, year_id: uuid.UUID
) -> list[PromotionRunOut]:
    """Promotions out of ``year_id``, newest first, with whether each can still be undone."""
    year = tenancy.get_academic_year(session, year_id)
    now = repo.now(session)
    runs = repo.promotions_from(session, year.id)
    names = _run_names(session, runs)
    return [_run_out(r, now, names) for r in runs]


def commit_promotion(
    session: Session, ctx: UserContext, year_id: uuid.UUID, data: PromotionCommitIn
) -> PromotionRunOut:
    """Apply the promotion plan in the caller's transaction (all or nothing).

    The plan is recomputed with the year's active enrolments locked. Refused with 409
    ``promotion_already_committed`` while an earlier promotion of this year is not undone,
    409 ``promotion_plan_changed`` when ``plan_fingerprint`` no longer matches, 409
    ``nothing_to_promote`` when no student moves, and 422 ``no_target_section`` (field
    ``section_map``) while a student cannot be placed; 409 ``structure_archived`` when a target
    section (or its class or year) is archived. Old enrolments are closed as
    ``completed`` on the source year's last day; new ones start on the target year's first day
    (roll numbers are not carried over); graduates get record status ``graduated``.

    Audit: one ``promotion.committed`` event (run id, year ids and counts; the per-student ids
    are in ``sis.promotion_items``). Outbox: ``student.values.changed`` (enrolment marker) for
    every moved student, for the incremental data-quality checks.
    """
    if repo.committed_promotion(session, year_id, lock=True) is not None:
        raise Conflict(
            "This year's students were already promoted. Undo that promotion first.",
            code="promotion_already_committed",
        )
    plan = _promotion_plan(session, year_id, data, lock=True)
    if data.plan_fingerprint is not None and data.plan_fingerprint != plan.fingerprint:
        raise Conflict(
            "Enrolments changed since the preview. Preview again and check the new plan.",
            code="promotion_plan_changed",
        )
    if plan.problems():
        raise ValidationFailed(
            [error("section_map", "no_target_section")],
            detail="Some students have no section in the new year. Add the sections or map them.",
        )
    movers = [s for s in plan.students if s.outcome != "skipped"]
    if not movers:
        raise Conflict("No student in this year can be promoted.", code="nothing_to_promote")
    # Lock the target sections (and their class and year) against a concurrent archive; an
    # archived target answers 409 structure_archived (FR-TEN-010).
    tenancy.lock_enrolment_targets(
        session, {s.to_section_id for s in movers if s.to_section_id is not None}
    )
    tenant_id = repo.current_tenant_id(session)
    ended_on = plan.from_year.ends_on
    closed = repo.close_enrollments(session, [s.enrollment_id for s in movers], ended_on=ended_on)
    if len(closed) != len(movers):
        raise Conflict(
            "Enrolments changed while promoting. Preview again.", code="promotion_plan_changed"
        )
    new_ids: dict[uuid.UUID, uuid.UUID] = {}
    rows: list[dict[str, Any]] = []
    for s in movers:
        if s.to_section_id is None:
            continue
        new_ids[s.student_id] = new_id()
        rows.append(
            {
                "id": new_ids[s.student_id],
                "tenant_id": tenant_id,
                "student_id": s.student_id,
                "section_id": s.to_section_id,
                "academic_year_id": plan.to_year.id,
                "roll_no": None,
                "status": "active",
                "started_on": plan.to_year.starts_on,
                "created_by": ctx.user_id,
            }
        )
    with _db_errors():
        opened = repo.insert_enrollments(session, rows)
    graduates = [s.student_id for s in movers if s.outcome == "graduated"]
    repo.set_student_status(session, graduates, "graduated")
    repo.bump_student_versions(session, [s.student_id for s in movers if s.outcome != "graduated"])
    moved_ids = [s.student_id for s in movers]
    repo.refresh_placements(session, moved_ids)
    counts = plan.counts()
    run_id = new_id()
    with _db_errors():
        run = repo.insert_promotion(
            session,
            {
                "id": run_id,
                "tenant_id": tenant_id,
                "from_academic_year_id": plan.from_year.id,
                "to_academic_year_id": plan.to_year.id,
                "status": "committed",
                "promoted_count": counts.promoted,
                "held_back_count": counts.held_back,
                "graduated_count": counts.graduated,
                "skipped_count": counts.skipped,
                "plan_fingerprint": plan.fingerprint,
                "committed_by": ctx.user_id,
            },
            [
                {
                    "tenant_id": tenant_id,
                    "run_id": run_id,
                    "student_id": s.student_id,
                    "outcome": s.outcome,
                    "from_enrollment_id": s.enrollment_id,
                    "from_enrollment_version": closed[s.enrollment_id],
                    "to_enrollment_id": new_ids.get(s.student_id),
                    "to_enrollment_version": (
                        opened[new_ids[s.student_id]] if s.student_id in new_ids else None
                    ),
                    "previous_student_status": s.student_status,
                }
                for s in movers
            ],
        )
    for sid in moved_ids:
        _values_changed(session, sid, [ENROLLMENT_KEY])
    _audit(
        session,
        action="promotion.committed",
        resource_type="promotion",
        resource_id=run.id,
        summary={
            "from_academic_year_id": plan.from_year.id,
            "to_academic_year_id": plan.to_year.id,
            "promoted_count": counts.promoted,
            "held_back_count": counts.held_back,
            "graduated_count": counts.graduated,
            "skipped_count": counts.skipped,
            "section_map_count": len(data.section_map),
        },
    )
    log.info("promotion.committed", resource_type="promotion", resource_id=run.id)
    return _run_out(run, repo.now(session), _run_names(session, [run]))


def _promotion_has_dependents() -> Conflict:
    return Conflict(
        "Some enrolments from this promotion were changed after it was committed, so it cannot "
        "be undone. Correct those students one by one instead.",
        code="promotion_has_dependents",
    )


def undo_promotion(session: Session, ctx: UserContext, year_id: uuid.UUID) -> PromotionRunOut:
    """Undo the committed promotion of ``year_id`` within 24 hours (FR-TEN-011).

    Refused with 409 ``no_promotion`` when there is none, 409 ``promotion_undo_expired`` after
    24 hours, and 409 ``promotion_has_dependents`` when anything was recorded on top of it: an
    enrolment it closed or opened changed since (version or status), or a graduate's record
    status changed, and 409 ``structure_archived`` when an enrolment it would reopen is in
    archived structure. Otherwise, in the caller's transaction: the enrolments it opened are
    removed, the ones it closed are active again, graduates get their previous status back.

    Audit: ``promotion.undone`` (run id and counts). Outbox: ``student.values.changed`` for
    every affected student.
    """
    year = tenancy.get_academic_year(session, year_id)
    run = repo.committed_promotion(session, year.id, lock=True)
    if run is None:
        raise Conflict("There is no promotion of this year to undo.", code="no_promotion")
    if repo.now(session) >= run.committed_at + UNDO_WINDOW:
        raise Conflict(
            "Promotions can be undone only within 24 hours. Correct students one by one instead.",
            code="promotion_undo_expired",
        )
    items = repo.promotion_items(session, run.id)
    old_ids = [i.from_enrollment_id for i in items]
    new_ids = [i.to_enrollment_id for i in items if i.to_enrollment_id is not None]
    states = repo.enrollment_states(session, [*old_ids, *new_ids], lock=True)
    statuses = repo.student_statuses(session, [i.student_id for i in items], lock=True)
    for item in items:
        if states.get(item.from_enrollment_id) != ("completed", item.from_enrollment_version):
            raise _promotion_has_dependents()
        if item.to_enrollment_version is not None and (
            item.to_enrollment_id is None
            or states.get(item.to_enrollment_id) != ("active", item.to_enrollment_version)
        ):
            raise _promotion_has_dependents()
        if item.outcome == "graduated" and statuses.get(item.student_id) != "graduated":
            raise _promotion_has_dependents()
    if repo.students_active_in_year(session, [i.student_id for i in items], year.id):
        raise _promotion_has_dependents()  # re-enrolled in the old year since the commit
    # The reopened enrolments must not land in archived structure (409 structure_archived);
    # their sections stay locked against a concurrent archive (FR-TEN-010).
    tenancy.lock_enrolment_targets(session, repo.enrollment_section_ids(session, old_ids))
    removed = repo.delete_enrollments(session, new_ids)
    reopened = repo.reopen_enrollments(session, old_ids)
    by_status: dict[str, list[uuid.UUID]] = {}
    for item in items:
        if item.outcome == "graduated":
            by_status.setdefault(item.previous_student_status, []).append(item.student_id)
    for status, ids in by_status.items():
        repo.set_student_status(session, ids, status)
    student_ids = [i.student_id for i in items]
    repo.bump_student_versions(session, [i.student_id for i in items if i.outcome != "graduated"])
    repo.refresh_placements(session, student_ids)
    run = repo.mark_promotion_undone(session, run.id, undone_by=ctx.user_id)
    for sid in student_ids:
        _values_changed(session, sid, [ENROLLMENT_KEY])
    _audit(
        session,
        action="promotion.undone",
        resource_type="promotion",
        resource_id=run.id,
        summary={
            "from_academic_year_id": run.from_academic_year_id,
            "to_academic_year_id": run.to_academic_year_id,
            "enrollments_removed": removed,
            "enrollments_reopened": reopened,
            "graduations_reverted": sum(len(v) for v in by_status.values()),
        },
    )
    log.info("promotion.undone", resource_type="promotion", resource_id=run.id)
    return _run_out(run, repo.now(session), _run_names(session, [run]))


# --- full data export (app.admin; FR-ADM-001, US-1201) -----------------------------------------

VALUE_STATE_VALUE: Final = "value"
VALUE_STATE_MASKED: Final = "masked"
VALUE_STATE_WITHHELD: Final = "withheld"
VALUE_COLUMNS: Final = (
    "id",
    "student_id",
    "attribute_key",
    "classification",
    "source",
    "value",
    "value_state",
    "current",
    "superseded_by",
    "verification_status",
    "verified_by",
    "verified_at",
    "confidence",
    "evidence_document_id",
    "import_batch_id",
    "change_request_id",
    "recorded_by",
    "recorded_at",
)
GUARDIAN_COLUMNS: Final = (
    "id",
    "full_name",
    "phone",
    "phone_state",
    "address",
    "address_state",
    "created_at",
    "updated_at",
    "version",
)


def _export_value(
    session: Session,
    row: AttributeValue,
    definition: AttributeDef | None,
    *,
    include_sensitive: bool,
    withheld: Collection[str],
) -> tuple[str | None, str]:
    """(value, state) of one recorded value for the full export."""
    if row.attribute_key in withheld:
        return None, VALUE_STATE_WITHHELD
    sensitive = definition is None or definition.sensitive or row.value_ciphertext is not None
    if sensitive and not include_sensitive:
        return MASK, VALUE_STATE_MASKED
    value = _plain(session, row)
    if value is not None and definition is not None and definition.data_type == "digits4":
        value = aadhaar_display(value)  # PRV-014: the only way an Aadhaar reference is shown
    return value, VALUE_STATE_VALUE


def _export_guardian_field(
    session: Session, guardian: Guardian, column: str, *, include_sensitive: bool
) -> tuple[str | None, str | None]:
    blob: bytes | None = getattr(guardian, column)
    if blob is None:
        return None, None
    if not include_sensitive:
        return MASK, VALUE_STATE_MASKED
    value = crypto.decrypt_value(
        session, blob, table=GUARDIANS_TABLE, column=column, row_id=guardian.id
    )
    return value, VALUE_STATE_VALUE


def export_records(
    session: Session, *, include_sensitive: bool, withheld: Collection[str]
) -> list[RecordTable]:
    """Worker only: every student record table of the current school for its full data export
    (``app.admin``; the caller checked ``tenant.export_all`` and, for ``include_sensitive``,
    school-wide ``student.read_sensitive``, and audits the export).

    Tables: ``students``, ``student_values`` (every recorded value from every source, history
    included, with ``value_state``), ``enrollments``, ``guardians``, ``student_guardians``,
    ``promotion_runs``, ``promotion_items``. Restricted (C3) values, guardian phone numbers and
    addresses are ``••••`` (state ``masked``) unless ``include_sensitive``; attributes in
    ``withheld`` (the Aadhaar-as-printed fields) never carry a value (state ``withheld``);
    ``aadhaar_last4`` is shown only as ``XXXX XXXX 1234`` (PRV-014). Full Aadhaar numbers are
    never stored, so none can be exported (the archive writer masks any Aadhaar-like number
    anyway). Nothing is logged or audited here."""
    defs = _definitions(session)
    students, enrollments, links, runs, items = repo.export_plain_tables(session)
    values: list[tuple[object, ...]] = []
    for row in repo.all_values(session):
        definition = defs.get(row.attribute_key)
        value, state = _export_value(
            session, row, definition, include_sensitive=include_sensitive, withheld=withheld
        )
        values.append(
            (
                row.id,
                row.student_id,
                row.attribute_key,
                definition.classification if definition is not None else "C3",
                row.source,
                value,
                state,
                row.superseded_by is None,
                row.superseded_by,
                row.verification_status,
                row.verified_by,
                row.verified_at,
                row.confidence,
                row.evidence_document_id,
                row.import_batch_id,
                row.change_request_id,
                row.recorded_by,
                row.recorded_at,
            )
        )
    guardians: list[tuple[object, ...]] = []
    for g in repo.all_guardians(session):
        phone, phone_state = _export_guardian_field(
            session, g, "phone_ciphertext", include_sensitive=include_sensitive
        )
        address, address_state = _export_guardian_field(
            session, g, "address_ciphertext", include_sensitive=include_sensitive
        )
        guardians.append(
            (
                g.id,
                g.full_name,
                phone,
                phone_state,
                address,
                address_state,
                g.created_at,
                g.updated_at,
                g.version,
            )
        )
    masked = () if include_sensitive else ("c3_masked",)
    withheld_note = ("aadhaar_as_printed_withheld",) if withheld else ()
    return [
        students,
        RecordTable(
            name="student_values", columns=VALUE_COLUMNS, rows=values, notes=masked + withheld_note
        ),
        enrollments,
        RecordTable(name="guardians", columns=GUARDIAN_COLUMNS, rows=guardians, notes=masked),
        links,
        runs,
        items,
    ]


def sensitive_export_fields(session: Session, *, withheld: Collection[str]) -> list[str]:
    """The restricted (C3) fields a full export with ``include_sensitive`` shows in clear: the
    school's C3 attribute keys (minus ``withheld``) and the guardian phone and address. Names
    only, for the audit event (docs/07 §8, ADR-0021)."""
    keys = [k for k, d in _definitions(session).items() if d.sensitive and k not in withheld]
    return [*keys, *GUARDIAN_FIELDS]


# --- offboarding purge (FR-PLT-005, ADR-0029) ------------------------------------------------
# Students, their values and profiles, guardians, enrolments, promotions and the school's
# own attribute definitions (global definitions have no tenant and stay).
# Registered with app.tenancy at import; the offboarding job counts them as sos_app and deletes
# them as sos_purger (children before parents) inside the school's tenant_session.
_PURGE = purging.PurgeTables(
    deleted=(
        "sis.promotion_runs",
        "sis.student_guardians",
        "sis.guardians",
        "sis.enrollments",
        "sis.students",
        "sis.attribute_definitions",
    ),
    cascaded=("sis.promotion_items", "sis.student_profiles", "sis.attribute_values"),
)


def tenant_data_counts(session: Session) -> dict[str, int]:
    """Rows of the current school in this module's tables (offboarding inventory)."""
    return _PURGE.count(session)


def purge_tenant_data(session: Session) -> dict[str, int]:
    """Delete the current school's rows of this module (offboarding only: the database allows it
    only as ``sos_purger`` for a school in ``offboarding``)."""
    return _PURGE.delete(session)


tenancy.register_data_owner(
    tenancy.TenantDataOwner(name="students", count=tenant_data_counts, purge=purge_tenant_data)
)


def forget_destroyed_keys(tenant_id: uuid.UUID) -> None:
    """After crypto-shredding, drop the school's unwrapped keys cached in this process."""
    crypto.get_keyring().forget(tenant_id)


if forget_destroyed_keys not in tenancy.KEYS_DESTROYED_HOOKS:
    tenancy.KEYS_DESTROYED_HOOKS.append(forget_destroyed_keys)
