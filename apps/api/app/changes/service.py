"""Change requests public API: maker-checker for identity fields (US-601; FR-CR-001..005;
SEC-014; BR-01, BR-04; ADR-0010).

- **Submit (FR-CR-001).** ``student.identity_change.request``: an identity attribute
  (``is_identity``) of a student the caller reaches, the new value (validated with the
  attribute's rules by ``students.validate_identity_value``; full Aadhaar numbers refused), a
  reason (10..1000 characters) and an evidence document that exists, is usable (not
  quarantined), is visible to the caller and has purpose ``evidence``. One pending request per
  student, attribute and source. The value being corrected is snapshotted with its id, and the
  evidence version current at submit is pinned (``evidence_version_id``).
- **Approve (FR-CR-002/003).** ``student.identity_change.approve`` + MFA within 5 minutes, by
  someone other than the requester (checked here AND by a database CHECK), on a pending,
  unexpired request whose old value is still the current one (checked with the student row
  locked), whose maker is still an active member and whose pinned evidence version passed its
  virus scan and is visible to the approver, with ``If-Match``. In the same
  transaction ``students.record_verified_identity_value`` records the new *verified* value (the
  old one stays in history, superseded), the request is closed, both are audited and
  ``change_request.approved`` goes to the outbox (DQ re-evaluates the student's findings).
- **Reject (FR-CR-004)** needs a reason; **cancel** is for the requester; pending requests
  **expire** after ``expiry_days`` (daily task).
- **Memo (FR-CR-005).** A print-ready bilingual HTML page (:mod:`app.changes.memo`).
- **Sensitive values (SEC-012).** Values of C3 attributes are stored only as ciphertext (tenant
  DEK, AAD bound to this table, column and row) and always masked in API responses; the memo
  shows them only to holders of ``student.read_sensitive`` (audited).
- **Scope (SEC-015).** Requests are reached through the student: a scoped holder sees only
  requests of students in their sections/classes; anything else is 404.
- Audit summaries, outbox payloads, notification params and logs hold ids and codes only.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Collection, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Any, Final

import yaml
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.authz.context import UserContext
from app.authz.http import Page, decode_cursor, encode_cursor
from app.changes import memo as memo_page
from app.changes import repository as repo
from app.changes.models import ChangeRequest
from app.changes.schemas import ApproveIn, ChangeRequestCreate, ChangeRequestOut, RejectIn
from app.core import purge as purging
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
from app.core.records import RecordTable
from app.core.redaction import contains_full_aadhaar
from app.documents import service as documents
from app.identity import service as identity
from app.identity.principal import STEP_UP_MAX_AGE
from app.notifications import service as notifications
from app.ops import service as ops
from app.students import crypto
from app.students import service as students
from app.tenancy import service as tenancy

REQUEST: Final = "student.identity_change.request"
APPROVE: Final = "student.identity_change.approve"
READ: Final = "student.read_basic"
SENSITIVE: Final = "student.read_sensitive"
TABLE: Final = "sis.change_requests"
NEW_CT: Final = "new_value_ciphertext"
OLD_CT: Final = "old_value_ciphertext"
MASK: Final = "••••"
AADHAAR_CODE: Final = "aadhaar_full_number_rejected"
AADHAAR_DETAIL: Final = "Don't enter Aadhaar numbers. Enter only the last 4 digits."
_EXPIRY_BATCH: Final = 500
_USER_PAGE: Final = 200

log = get_logger(__name__)


# --- errors (docs/09 change requests) ----------------------------------------------------------


class SelfApprovalForbidden(Forbidden):
    code = "self_approval_forbidden"

    def __init__(self) -> None:
        super().__init__("You submitted this request, so someone else must approve or reject it.")


class NotRequester(Forbidden):
    code = "not_requester"

    def __init__(self) -> None:
        super().__init__("Only the person who submitted this request can cancel it.")


class RequestNotPending(Conflict):
    code = "request_not_pending"

    def __init__(self) -> None:
        super().__init__("This request was already decided. Reload to see its status.")


class RequestExpired(Conflict):
    code = "request_expired"

    def __init__(self) -> None:
        super().__init__("This request has expired. Submit a new request if it is still needed.")


class RequestOutdated(Conflict):
    code = "request_outdated"

    def __init__(self) -> None:
        super().__init__(
            "The value was changed after this request was made. Reject it and submit a new one."
        )


class RequesterInactive(Conflict):
    code = "requester_inactive"

    def __init__(self) -> None:
        super().__init__(
            "The person who submitted this request is no longer an active member of the school. "
            "Reject it; someone active must submit the correction again."
        )


class EvidenceNotReady(Conflict):
    code = "evidence_not_ready"

    def __init__(self) -> None:
        super().__init__(
            "The evidence file is still being checked for viruses. Try again in a few minutes."
        )


class EvidenceNotUsable(Conflict):
    code = "evidence_not_usable"

    def __init__(self) -> None:
        super().__init__(
            "The evidence file attached to this request failed the virus check or is no longer "
            "available. Reject the request; it must be submitted again with a new scan."
        )


class EvidenceNotVisible(Conflict):
    code = "evidence_not_visible"

    def __init__(self) -> None:
        super().__init__(
            "You can't open the evidence attached to this request, so you can't approve it. "
            "Ask someone who can open it to decide, or reject the request."
        )


class EvidenceRequired(ValidationFailed):
    code = "evidence_required"

    def __init__(self, reason: str) -> None:
        super().__init__(
            [
                {
                    "field": "evidence_document_id",
                    "code": reason,
                    "message_key": f"errors.{reason}",
                }
            ],
            detail="Attach an evidence document (such as a birth certificate scan) that you can "
            "open and that was uploaded as evidence.",
        )


class NotIdentityAttribute(ValidationFailed):
    code = "not_identity_attribute"

    def __init__(self) -> None:
        super().__init__(
            [
                {
                    "field": "attribute_key",
                    "code": "not_identity_attribute",
                    "message_key": "errors.not_identity_attribute",
                }
            ],
            detail="Only identity fields need a change request. Edit this field directly.",
        )


# --- configuration -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Settings:
    expiry_days: int
    text_min_length: int
    text_max_length: int


@lru_cache(maxsize=1)
def settings() -> Settings:
    raw: dict[str, Any] = yaml.safe_load(
        resources.files("app.changes").joinpath("config.yaml").read_text("utf-8")
    )
    out = Settings(
        expiry_days=int(raw["expiry_days"]),
        text_min_length=int(raw["text_min_length"]),
        text_max_length=int(raw["text_max_length"]),
    )
    if not 1 <= out.expiry_days <= 365 or not 1 <= out.text_min_length <= out.text_max_length:
        raise ValueError("app/changes/config.yaml: invalid settings")
    return out


# --- plumbing ----------------------------------------------------------------------------------


def _now(session: Session) -> dt.datetime:
    """Transaction time (tests move the clock by patching this function)."""
    return repo.now(session)


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
    resource_id: uuid.UUID,
    summary: Mapping[str, Any],
    system: bool = False,
) -> None:
    request_id = get_context().get("request_id")
    audit.record(
        session,
        action=action,
        resource_type="change_request",
        resource_id=resource_id,
        summary=summary,
        actor_type="system" if system else "user",
        request_id=request_id if isinstance(request_id, str) else None,
    )


def _error(field: str, code: str, message_key: str | None = None) -> dict[str, str]:
    return {"field": field, "code": code, "message_key": message_key or f"errors.{code}"}


def _clean_text(field: str, value: str | None, *, required: bool) -> str | None:
    """Reason / decision note: trimmed, 10..1000 characters, never a full Aadhaar number."""
    if value is None or not value.strip():
        if required:
            raise ValidationFailed([_error(field, "missing")])
        return None
    text = value.strip()
    if contains_full_aadhaar(text):
        raise ValidationFailed(
            [_error(field, AADHAAR_CODE, "errors.aadhaar_last4_only")], detail=AADHAAR_DETAIL
        )
    cfg = settings()
    if len(text) < cfg.text_min_length:
        raise ValidationFailed([_error(field, "too_short")])
    if len(text) > cfg.text_max_length:
        raise ValidationFailed([_error(field, "too_long")])
    return text


def _require_recent_mfa(ctx: UserContext) -> None:
    """Defence in depth for FR-CR-002 (the route already demands step-up): MFA-backed sign-in
    within 5 minutes of the wall clock, else 428 ``step_up_required``."""
    if not ctx.mfa or ctx.auth_time is None:
        raise StepUpRequired()
    age = dt.datetime.now(dt.UTC) - ctx.auth_time
    if age > STEP_UP_MAX_AGE or age < -dt.timedelta(seconds=30):
        raise StepUpRequired()


def _reach(
    session: Session, ctx: UserContext, permissions: Iterable[str]
) -> frozenset[uuid.UUID] | None:
    """Students the caller reaches through any of ``permissions`` (and ``student.read_basic``);
    ``None`` means the whole school."""
    held = [p for p in dict.fromkeys(permissions) if ctx.has(p)]
    if not held:
        return frozenset()
    read_school = ctx.has(READ) and ctx.scope_for(READ).school_wide
    reached: set[uuid.UUID] = set()
    for permission in held:
        grant = ctx.scope_for(permission)
        if grant.school_wide:
            if read_school:
                return None
            reached.update(students.list_students_in_scope(session, ctx))
            continue
        if grant.section_ids:
            reached.update(
                students.list_students_in_scope(session, ctx, section_ids=grant.section_ids)
            )
        if grant.class_ids:
            reached.update(students.list_students_in_scope(session, ctx, class_ids=grant.class_ids))
    return frozenset(reached)


def _load(
    session: Session,
    ctx: UserContext,
    request_id: uuid.UUID,
    *,
    permissions: Iterable[str],
    lock: bool = False,
) -> ChangeRequest:
    """The request if the caller reaches its student through ``permissions``; else 404."""
    row = repo.get_request(session, request_id, lock=lock)
    if row is None:
        raise NotFound("Change request not found")
    reach = _reach(session, ctx, permissions)
    if reach is not None and row.student_id not in reach:
        raise NotFound("Change request not found")
    return row


def _labels(session: Session) -> dict[str, tuple[str, str]]:
    return {a.key: (a.label_en, a.label_te) for a in students.attribute_catalog(session)}


def _decrypt(session: Session, row: ChangeRequest, column: str) -> str:
    blob = row.new_value_ciphertext if column == NEW_CT else row.old_value_ciphertext
    if blob is None:
        raise crypto.CryptoError("no ciphertext")
    return crypto.decrypt_value(session, blob, table=TABLE, column=column, row_id=row.id)


def _is_sensitive(row: ChangeRequest) -> bool:
    return row.new_value_ciphertext is not None


def _plain_new(session: Session, row: ChangeRequest) -> str:
    if row.new_value_ciphertext is not None:
        return _decrypt(session, row, NEW_CT)
    if row.new_value_date is not None:
        return row.new_value_date.isoformat()
    if row.new_value_text is None:  # CHECK change_requests_one_new_value
        raise RuntimeError("change request without a value")
    return row.new_value_text


def _plain_old(session: Session, row: ChangeRequest) -> str | None:
    if row.old_value_ciphertext is not None:
        return _decrypt(session, row, OLD_CT)
    if row.old_value_date is not None:
        return row.old_value_date.isoformat()
    return row.old_value_text


def _out(
    session: Session,
    ctx: UserContext,
    row: ChangeRequest,
    labels: Mapping[str, tuple[str, str]],
    now: dt.datetime,
) -> ChangeRequestOut:
    sensitive = _is_sensitive(row)
    label_en, label_te = labels.get(row.attribute_key, (row.attribute_key, row.attribute_key))
    is_open = row.status == "pending" and row.expires_at > now
    mine = row.requested_by == ctx.membership_id
    return ChangeRequestOut(
        id=row.id,
        student_id=row.student_id,
        attribute_key=row.attribute_key,
        attribute_label_en=label_en,
        attribute_label_te=label_te,
        target_source=row.target_source,
        old_value_id=row.old_value_id,
        old_value=(MASK if row.old_value_id else None) if sensitive else _plain_old(session, row),
        new_value=MASK if sensitive else _plain_new(session, row),
        masked=sensitive,
        reason=row.reason,
        evidence_document_id=row.evidence_document_id,
        status=row.status,
        requested_by=row.requested_by,
        requested_at=row.requested_at,
        decided_by=row.decided_by,
        decided_at=row.decided_at,
        decision_note=row.decision_note,
        applied_value_id=row.applied_value_id,
        expires_at=row.expires_at,
        version=row.version,
        can_decide=is_open and ctx.has(APPROVE) and not mine,
        can_cancel=is_open and ctx.has(REQUEST) and mine,
    )


def _notify(
    session: Session,
    row: ChangeRequest,
    template_key: str,
    recipients: notifications.Recipients,
) -> None:
    notifications.notify(
        session,
        tenant_id=row.tenant_id,
        recipients=recipients,
        template_key=template_key,
        params={"change_request_id": str(row.id), "student_id": str(row.student_id)},
        resource_id=row.id,
        dedupe_key=f"{template_key}:{row.id}",
    )


def _event(session: Session, event_type: str, row: ChangeRequest) -> None:
    ops.enqueue_event(
        session,
        event_type,
        {
            "change_request_id": str(row.id),
            "student_id": str(row.student_id),
            "attribute_key": row.attribute_key,
        },
    )


def _check_evidence(session: Session, ctx: UserContext, document_id: uuid.UUID) -> None:
    """Evidence must be visible to the requester, uploaded as ``evidence`` and usable."""
    if not documents.is_visible(session, ctx, document_id):
        raise EvidenceRequired("evidence_not_found")
    if documents.get_document(session, ctx, document_id).purpose != "evidence":
        raise EvidenceRequired("evidence_wrong_purpose")
    if not documents.evidence_exists(session, document_id):
        raise EvidenceRequired("evidence_not_usable")


def _check_evidence_for_approval(session: Session, ctx: UserContext, row: ChangeRequest) -> None:
    """The approver decides on the file the requester attached: the pinned version must be
    clean and the approver must be able to open the document (audit 2026-10-05)."""
    state = documents.evidence_state(
        session, ctx, row.evidence_document_id, row.evidence_version_id
    )
    if state == "not_visible":
        raise EvidenceNotVisible()
    if state == "pending":
        raise EvidenceNotReady()
    if state != "ready":
        raise EvidenceNotUsable()


def _current_value(
    session: Session, student_id: uuid.UUID, attribute_key: str, source: str
) -> students.SourceValue | None:
    values = students.source_values(session, [student_id], [attribute_key], include_sensitive=True)
    return values.get(student_id, {}).get(attribute_key, {}).get(source)


def _check_open(row: ChangeRequest, now: dt.datetime) -> None:
    if row.status != "pending":
        raise RequestNotPending()
    if row.expires_at <= now:
        raise RequestExpired()


def _check_version(row: ChangeRequest, expected_version: int) -> None:
    if row.version != expected_version:
        raise PreconditionFailed("This request was changed by someone else. Reload and try again.")


# --- submit ------------------------------------------------------------------------------------


def submit(session: Session, ctx: UserContext, data: ChangeRequestCreate) -> ChangeRequestOut:
    """Submit an identity correction (permission ``student.identity_change.request``).

    Errors: 404 student outside scope; 422 ``not_identity_attribute``, ``evidence_required``,
    ``validation_error`` (value rules, reason length, full Aadhaar); 409
    ``duplicate_pending_request``. Audit ``change_request.submitted``; outbox
    ``change_request.submitted``; notification to every approver.
    """
    reason = _clean_text("reason", data.reason, required=True) or ""
    students.get_profile(session, ctx, data.student_id)  # 404 outside the school or READ scope
    reach = _reach(session, ctx, [REQUEST])
    if reach is not None and data.student_id not in reach:
        raise NotFound("Student not found")
    try:
        definition, clean = students.validate_identity_value(
            session, data.attribute_key, data.target_source, data.raw_value, field_name="new_value"
        )
    except ValidationFailed as exc:
        if any(e.get("code") == "not_identity_attribute" for e in exc.errors):
            raise NotIdentityAttribute() from None
        raise
    _check_evidence(session, ctx, data.evidence_document_id)
    evidence_version_id = documents.evidence_version(session, data.evidence_document_id)
    old = _current_value(session, data.student_id, data.attribute_key, data.target_source)
    request_id = new_id()
    columns: dict[str, Any] = {}
    if definition.sensitive:
        blob, version = crypto.encrypt_value(
            session, clean.plain, table=TABLE, column=NEW_CT, row_id=request_id
        )
        columns.update(new_value_ciphertext=blob, key_version=version)
        if old is not None and old.value is not None:
            old_blob, _ = crypto.encrypt_value(
                session, old.value, table=TABLE, column=OLD_CT, row_id=request_id
            )
            columns["old_value_ciphertext"] = old_blob
    elif definition.data_type == "date":
        columns["new_value_date"] = clean.date
        if old is not None and old.value is not None:
            columns["old_value_date"] = dt.date.fromisoformat(old.value)
    else:
        columns["new_value_text"] = clean.plain
        if old is not None:
            columns["old_value_text"] = old.value
    now = _now(session)
    with _db_errors():
        row = repo.insert_request(
            session,
            id=request_id,
            tenant_id=repo.current_tenant_id(session),
            student_id=data.student_id,
            attribute_key=data.attribute_key,
            target_source=data.target_source,
            old_value_id=old.value_id if old is not None else None,
            reason=reason,
            evidence_document_id=data.evidence_document_id,
            evidence_version_id=evidence_version_id,
            status="pending",
            requested_by=ctx.membership_id,
            requested_at=now,
            expires_at=now + dt.timedelta(days=settings().expiry_days),
            **columns,
        )
    _audit(
        session,
        action="change_request.submitted",
        resource_id=row.id,
        summary={
            "student_id": row.student_id,
            "attribute_key": row.attribute_key,
            "target_source": row.target_source,
            "old_value_id": row.old_value_id,
            "evidence_document_id": row.evidence_document_id,
            "evidence_version_id": row.evidence_version_id,
            "sensitive": definition.sensitive,
        },
    )
    _event(session, "change_request.submitted", row)
    _notify(session, row, "change_request.submitted", notifications.PermissionSelector(APPROVE))
    log.info("change_request.submitted", resource_type="change_request", resource_id=row.id)
    return _out(session, ctx, row, _labels(session), now)


# --- reads -------------------------------------------------------------------------------------


def _after(cursor: str | None) -> tuple[dt.datetime, uuid.UUID] | None:
    raw = decode_cursor(cursor)
    if raw is None:
        return None
    try:
        created = dt.datetime.fromisoformat(str(raw["t"]))
        last = uuid.UUID(str(raw["i"]))
    except (KeyError, ValueError):
        raise ValidationFailed([_error("cursor", "invalid", "errors.invalid_cursor")]) from None
    if created.tzinfo is None:
        raise ValidationFailed([_error("cursor", "invalid", "errors.invalid_cursor")])
    return created, last


def list_requests(
    session: Session,
    ctx: UserContext,
    *,
    status: str | None = None,
    student_id: uuid.UUID | None = None,
    limit: int = 50,
    cursor: str | None = None,
) -> Page[ChangeRequestOut]:
    """Requests of students the caller reaches (request or approve permission), newest first."""
    reach = _reach(session, ctx, [REQUEST, APPROVE])
    rows = repo.list_requests(
        session,
        student_ids=reach,
        status=status,
        student_id=student_id,
        after=_after(cursor),
        limit=limit + 1,
    )
    page, more = rows[:limit], len(rows) > limit
    labels, now = _labels(session), _now(session)
    items = [_out(session, ctx, r, labels, now) for r in page]
    next_cursor = (
        encode_cursor({"t": page[-1].requested_at.isoformat(), "i": str(page[-1].id)})
        if more and page
        else None
    )
    return Page[ChangeRequestOut](data=items, next_cursor=next_cursor)


def get_request(session: Session, ctx: UserContext, request_id: uuid.UUID) -> ChangeRequestOut:
    """One request (request or approve permission; 404 outside scope or school)."""
    row = _load(session, ctx, request_id, permissions=[REQUEST, APPROVE])
    return _out(session, ctx, row, _labels(session), _now(session))


# --- decisions ---------------------------------------------------------------------------------


def approve(
    session: Session,
    ctx: UserContext,
    request_id: uuid.UUID,
    data: ApproveIn,
    *,
    expected_version: int,
) -> ChangeRequestOut:
    """Approve and apply a correction (FR-CR-002/003); see the module docstring for the rules.

    Errors: 404; 403 ``self_approval_forbidden``; 428 ``step_up_required``; 412; 409
    ``request_not_pending`` / ``request_expired`` / ``request_outdated`` /
    ``requester_inactive`` (the maker is no longer an active member) / ``evidence_not_ready``
    (the pinned evidence version is still being scanned) / ``evidence_not_usable`` (it failed
    the scan) / ``evidence_not_visible`` (the approver cannot open the evidence).
    """
    row = _load(session, ctx, request_id, permissions=[APPROVE], lock=True)
    if row.requested_by == ctx.membership_id:
        raise SelfApprovalForbidden()
    now = _now(session)
    _require_recent_mfa(ctx)
    _check_version(row, expected_version)
    _check_open(row, now)
    note = _clean_text("note", data.note, required=False)
    if not identity.is_active_member(session, row.requested_by):
        raise RequesterInactive()
    _check_evidence_for_approval(session, ctx, row)
    # Lock the student first, as every value write does, so the old value cannot change
    # between this check and the new value (audit 2026-10-05).
    students.lock_student_for_change(session, ctx, row.student_id)
    current = _current_value(session, row.student_id, row.attribute_key, row.target_source)
    if (current.value_id if current is not None else None) != row.old_value_id:
        raise RequestOutdated()
    recorded = students.record_verified_identity_value(
        session,
        ctx,
        row.student_id,
        row.attribute_key,
        _plain_new(session, row),
        change_request_id=row.id,
        evidence_document_id=row.evidence_document_id,
        source=row.target_source,
    )
    with _db_errors():
        row = repo.update_request(
            session,
            row.id,
            {
                "status": "approved",
                "decided_by": ctx.membership_id,
                "decided_at": now,
                "decision_note": note,
                "applied_value_id": recorded.id,
            },
        )
    _audit(
        session,
        action="change_request.approved",
        resource_id=row.id,
        summary={
            "student_id": row.student_id,
            "attribute_key": row.attribute_key,
            "target_source": row.target_source,
            "value_id": recorded.id,
            "superseded_value_id": recorded.superseded,
            "evidence_document_id": row.evidence_document_id,
        },
    )
    _event(session, "change_request.approved", row)
    _notify(session, row, "change_request.approved", [row.requested_by])
    log.info("change_request.approved", resource_type="change_request", resource_id=row.id)
    return _out(session, ctx, row, _labels(session), now)


def reject(
    session: Session,
    ctx: UserContext,
    request_id: uuid.UUID,
    data: RejectIn,
    *,
    expected_version: int,
) -> ChangeRequestOut:
    """Reject with a reason (FR-CR-004). Same guards as :func:`approve` (the requester cancels
    instead). Audit ``change_request.rejected``; outbox; notification to the requester."""
    row = _load(session, ctx, request_id, permissions=[APPROVE], lock=True)
    if row.requested_by == ctx.membership_id:
        raise SelfApprovalForbidden()
    now = _now(session)
    _require_recent_mfa(ctx)
    _check_version(row, expected_version)
    _check_open(row, now)
    note = _clean_text("reason", data.reason, required=True)
    with _db_errors():
        row = repo.update_request(
            session,
            row.id,
            {
                "status": "rejected",
                "decided_by": ctx.membership_id,
                "decided_at": now,
                "decision_note": note,
            },
        )
    _audit(
        session,
        action="change_request.rejected",
        resource_id=row.id,
        summary={
            "student_id": row.student_id,
            "attribute_key": row.attribute_key,
            "target_source": row.target_source,
        },
    )
    _event(session, "change_request.rejected", row)
    _notify(session, row, "change_request.rejected", [row.requested_by])
    log.info("change_request.rejected", resource_type="change_request", resource_id=row.id)
    return _out(session, ctx, row, _labels(session), now)


def cancel(
    session: Session, ctx: UserContext, request_id: uuid.UUID, *, expected_version: int
) -> ChangeRequestOut:
    """The requester withdraws a pending request. Audit ``change_request.cancelled``; outbox
    ``change_request.cancelled`` (DQ releases findings linked to it)."""
    row = _load(session, ctx, request_id, permissions=[REQUEST], lock=True)
    if row.requested_by != ctx.membership_id:
        raise NotRequester()
    now = _now(session)
    _check_version(row, expected_version)
    _check_open(row, now)
    with _db_errors():
        row = repo.update_request(session, row.id, {"status": "cancelled", "decided_at": now})
    _audit(
        session,
        action="change_request.cancelled",
        resource_id=row.id,
        summary={"student_id": row.student_id, "attribute_key": row.attribute_key},
    )
    _event(session, "change_request.cancelled", row)
    log.info("change_request.cancelled", resource_type="change_request", resource_id=row.id)
    return _out(session, ctx, row, _labels(session), now)


def expire_due(session: Session, *, at: dt.datetime | None = None) -> int:
    """Mark pending requests past ``expires_at`` as expired (daily task, one school per session).

    Audit ``change_request.expired`` (system actor), a notification to the requester and
    outbox ``change_request.expired`` (DQ releases findings linked to it)."""
    when = at or _now(session)
    total = 0
    while True:
        rows = repo.lock_due_for_expiry(session, when, limit=_EXPIRY_BATCH)
        for due in rows:
            row = repo.update_request(session, due.id, {"status": "expired", "decided_at": when})
            _audit(
                session,
                action="change_request.expired",
                resource_id=row.id,
                summary={"student_id": row.student_id, "attribute_key": row.attribute_key},
                system=True,
            )
            _notify(session, row, "change_request.expired", [row.requested_by])
            _event(session, "change_request.expired", row)
        total += len(rows)
        if len(rows) < _EXPIRY_BATCH:
            return total


def request_student(session: Session, request_id: uuid.UUID) -> uuid.UUID | None:
    """The student a request of the current school is about, or ``None`` (other modules
    validate links to a request, e.g. a DQ finding resolved by it). IDs only; no permission
    check, so callers must already hold their own permission on the linking resource."""
    row = repo.get_request(session, request_id)
    return row.student_id if row is not None else None


@dataclass(frozen=True, slots=True)
class RequestLink:
    """What another module needs to validate a link to a request (IDs and codes only)."""

    student_id: uuid.UUID
    attribute_key: str
    status: str


def request_link(session: Session, request_id: uuid.UUID) -> RequestLink | None:
    """The student, attribute and status of a request of the current school, or ``None`` (e.g.
    DQ resolves a finding only with an approved request about its student and field). No
    permission check, as for :func:`request_student`."""
    row = repo.get_request(session, request_id)
    if row is None:
        return None
    return RequestLink(student_id=row.student_id, attribute_key=row.attribute_key, status=row.status)


def pending_attribute_keys(
    session: Session, student_id: uuid.UUID, attribute_keys: Collection[str]
) -> set[str]:
    """Which of ``attribute_keys`` have a correction waiting for approval for the student (a
    certificate that prints such a field waits for the decision; audit 2026-10-05). IDs and
    keys only; no permission check: the caller has checked its own scope on the student."""
    return repo.pending_keys(session, student_id, attribute_keys, _now(session))


# --- correction memo ---------------------------------------------------------------------------


def _display_names(session: Session, membership_ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    names: dict[uuid.UUID, str] = {}
    after: uuid.UUID | None = None
    while membership_ids - names.keys():
        users, after = identity.list_users(session, limit=_USER_PAGE, after=after)
        names.update({u.membership_id: u.display_name for u in users})
        if after is None:
            break
    return names


def _evidence_label(session: Session, ctx: UserContext, row: ChangeRequest) -> str:
    reference = f"ref. {str(row.evidence_document_id)[:8]}"
    try:
        doc = documents.get_document(session, ctx, row.evidence_document_id)
    except NotFound:
        return f"Document {reference}"
    return f"{doc.title} ({doc.doc_type}, {reference})"


def memo(session: Session, ctx: UserContext, request_id: uuid.UUID) -> str:
    """The correction memo HTML (FR-CR-005; request or approve permission, in scope).

    C3 values are shown only to holders of ``student.read_sensitive`` for this student. Audit
    ``change_request.memo_viewed`` (with whether sensitive values were shown)."""
    row = _load(session, ctx, request_id, permissions=[REQUEST, APPROVE])
    profile = students.get_profile(session, ctx, row.student_id)
    sensitive = _is_sensitive(row)
    reveal = False
    if sensitive and ctx.has(SENSITIVE):
        reach = _reach(session, ctx, [SENSITIVE])
        reveal = reach is None or row.student_id in reach
    if sensitive and not reveal:
        old_value = MASK if row.old_value_id else "—"
        new_value = MASK
    else:
        old_value = _plain_old(session, row) or "—"
        new_value = _plain_new(session, row)
    names = _display_names(
        session, {row.requested_by} | ({row.decided_by} if row.decided_by else set())
    )
    label_en, label_te = _labels(session).get(
        row.attribute_key, (row.attribute_key, row.attribute_key)
    )
    full_name = profile.canonical.get("full_name")
    page = memo_page.render(
        memo_page.MemoData(
            school_name=tenancy.get_tenant(session).name,
            reference=f"CR-{str(row.id)[:8].upper()}",
            request_id=str(row.id),
            student_name=(full_name.value if full_name and full_name.value else "—"),
            admission_no=profile.admission_no or "—",
            class_section=profile.enrollment.label if profile.enrollment else "—",
            attribute_en=label_en,
            attribute_te=label_te,
            source=row.target_source,
            old_value=old_value,
            new_value=new_value,
            reason=row.reason,
            evidence=_evidence_label(session, ctx, row),
            requested_by=names.get(row.requested_by, "—"),
            requested_at=row.requested_at,
            status=row.status,
            decided_by=names.get(row.decided_by, "—") if row.decided_by else "—",
            decided_at=row.decided_at,
            decision_note=row.decision_note or "—",
            printed_at=_now(session),
        )
    )
    _audit(
        session,
        action="change_request.memo_viewed",
        resource_id=row.id,
        summary={"student_id": row.student_id, "sensitive_shown": sensitive and reveal},
    )
    return page


EXPORT_COLUMNS: Final = (
    "id",
    "student_id",
    "attribute_key",
    "target_source",
    "old_value_id",
    "old_value",
    "new_value",
    "value_state",
    "reason",
    "evidence_document_id",
    "status",
    "requested_by_membership",
    "requested_at",
    "decided_by_membership",
    "decided_at",
    "decision_note",
    "applied_value_id",
    "expires_at",
    "updated_at",
    "version",
)


def export_records(
    session: Session, *, include_sensitive: bool, withheld: Collection[str]
) -> list[RecordTable]:
    """Worker only: every change request of the current school for its full data export
    (``app.admin``; the caller checked ``tenant.export_all`` and, for ``include_sensitive``,
    school-wide ``student.read_sensitive``, and audits the export). Restricted (C3) old and new
    values are ``••••`` (``value_state`` ``masked``) unless ``include_sensitive``; attributes in
    ``withheld`` never carry a value (``withheld``)."""
    rows: list[tuple[object, ...]] = []
    for row in repo.all_requests(session):
        old: str | None
        new: str | None
        if row.attribute_key in withheld:
            old, new, state = None, None, "withheld"
        elif _is_sensitive(row) and not include_sensitive:
            old, new, state = (MASK if row.old_value_id else None), MASK, "masked"
        else:
            old, new, state = _plain_old(session, row), _plain_new(session, row), "value"
        rows.append(
            (
                row.id,
                row.student_id,
                row.attribute_key,
                row.target_source,
                row.old_value_id,
                old,
                new,
                state,
                row.reason,
                row.evidence_document_id,
                row.status,
                row.requested_by,
                row.requested_at,
                row.decided_by,
                row.decided_at,
                row.decision_note,
                row.applied_value_id,
                row.expires_at,
                row.updated_at,
                row.version,
            )
        )
    notes = () if include_sensitive else ("c3_masked",)
    return [RecordTable(name="change_requests", columns=EXPORT_COLUMNS, rows=rows, notes=notes)]


__all__ = [
    "APPROVE",
    "REQUEST",
    "EvidenceRequired",
    "NotIdentityAttribute",
    "NotRequester",
    "RequestExpired",
    "RequestNotPending",
    "RequestOutdated",
    "SelfApprovalForbidden",
    "Settings",
    "approve",
    "cancel",
    "expire_due",
    "export_records",
    "get_request",
    "list_requests",
    "memo",
    "purge_tenant_data",
    "reject",
    "request_student",
    "settings",
    "submit",
    "tenant_data_counts",
]


# --- offboarding purge (FR-PLT-005, ADR-0029) ------------------------------------------------
# Change requests (maker-checker history).
# Registered with app.tenancy at import; the offboarding job counts them as sos_app and deletes
# them as sos_purger (children before parents) inside the school's tenant_session.
_PURGE = purging.PurgeTables(
    deleted=("sis.change_requests",),
)


def tenant_data_counts(session: Session) -> dict[str, int]:
    """Rows of the current school in this module's tables (offboarding inventory)."""
    return _PURGE.count(session)


def purge_tenant_data(session: Session) -> dict[str, int]:
    """Delete the current school's rows of this module (offboarding only: the database allows it
    only as ``sos_purger`` for a school in ``offboarding``)."""
    return _PURGE.delete(session)


tenancy.register_data_owner(
    tenancy.TenantDataOwner(name="changes", count=tenant_data_counts, purge=purge_tenant_data)
)
