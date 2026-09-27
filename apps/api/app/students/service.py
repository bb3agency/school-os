"""Students public API: records, per-source values, canonical view, guardians, enrolments,
search and sensitive reveal (M1: US-301..303; FR-STU-001..012; SEC-012, SEC-013, SEC-015).

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
"""

from __future__ import annotations

import datetime as dt
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
from app.core.errors import (
    Conflict,
    Forbidden,
    NotFound,
    PreconditionFailed,
    ValidationFailed,
)
from app.core.ids import new_id
from app.core.logging import get_context, get_logger
from app.core.textnorm import comparison_key
from app.ops import service as ops
from app.students import crypto
from app.students import repository as repo
from app.students.canonical import Resolution, resolve
from app.students.definitions import (
    AADHAAR_DETAIL,
    MASK,
    AttributeDef,
    CanonicalPolicy,
    CleanValue,
    aadhaar_display,
    aadhaar_error,
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
    EnrollmentIn,
    EnrollmentOut,
    GuardianCreate,
    GuardianOut,
    GuardianPatch,
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


def _structure(session: Session) -> _Structure:
    year = tenancy.get_current_academic_year(session)
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
    """Global + school attributes with classification, identity flag and labels (EN/TE)."""
    return [
        AttributeOut(
            key=d.key,
            data_type=d.data_type,
            classification=d.classification,
            is_identity=d.is_identity,
            label_en=d.label_en,
            label_te=d.label_te,
            sort_order=d.sort_order,
            allowed_sources=list(d.allowed_sources) if d.allowed_sources else None,
            allowed_values=[str(v) for v in d.validation.get("values", ())] or None,
            precedence=list(d.policy.precedence),
            is_global=d.is_global,
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
    section = _section_or_422(session, data.section_id) if data.section_id else None
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
    ``transferred``. Permission ``student.update_nonidentity``. Audit: ``enrollment.created``
    (+ ``enrollment.transferred``)."""
    structure = _structure(session)
    student = _visible_student(
        session, ctx, student_id, permission=UPDATE, structure=structure, lock=True
    )
    section = _section_or_422(session, data.section_id)
    out = _enrol(session, ctx, student, section, roll_no=data.roll_no, started_on=data.started_on)
    _touch(session, student, _definitions(session))
    _values_changed(session, student_id, [ENROLLMENT_KEY])
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
    the caller's sections for scoped holders (US-302 AC2)."""
    if filters.query and is_full_aadhaar(filters.query):
        raise ValidationFailed([aadhaar_error("query")], detail=AADHAAR_DETAIL)
    cfg = search_config()
    offset = _offset(cursor, cfg.max_offset)
    structure = _structure(session)
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
        ),
    )
    ranked = bool(parsed.name_key or parsed.admission_terms)
    items = [
        StudentSummary(
            id=r.id,
            display_name=r.full_name,
            admission_no=r.admission_no,
            status=r.status,
            class_section=structure.label(r.section_id),
            section_id=r.section_id,
            match=StudentMatch(
                field=r.match_field if ranked else None,
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
) -> list[uuid.UUID]:
    """Student ids the caller may read (``student.read_basic``), optionally limited to
    current-year sections/classes. For dq and exports: pass the result to
    :func:`canonical_values` / :func:`source_values`."""
    structure = _structure(session)
    allowed = _allowed_sections(ctx, READ, structure)
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


def active_enrolments(
    session: Session, student_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, list[ActiveEnrolment]]:
    """Active enrolments in any academic year for many students (dq: DQ-007 age band,
    DQ-012 enrolled twice). ``student_ids`` MUST come from :func:`list_students_in_scope`."""
    out: dict[uuid.UUID, list[ActiveEnrolment]] = {}
    for row in repo.active_enrollments_of(session, list(student_ids)):
        out.setdefault(row.student_id, []).append(
            ActiveEnrolment(
                enrollment_id=row.id,
                student_id=row.student_id,
                section_id=row.section_id,
                academic_year_id=row.academic_year_id,
            )
        )
    return out


def student_ids_for_import_batch(session: Session, batch_id: uuid.UUID) -> list[uuid.UUID]:
    """Students with a value recorded by import batch ``batch_id`` (dq runs for a batch).
    Callers intersect the result with :func:`list_students_in_scope`."""
    return repo.student_ids_with_batch(session, batch_id)


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
