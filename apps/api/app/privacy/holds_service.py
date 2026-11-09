"""Legal holds (FC-12; ADR-0059; FR-DPDP-039..FR-DPDP-052; docs/08 §7). Lead decision D84.

- **Scope (FR-DPDP-039).** One student, or one record class for the whole school (a key of
  ``record_classes.yaml`` holding personal data). Staff members are left out until a staff
  register exists.
- **Reason (FR-DPDP-040).** A code from ``holds.yaml`` and an optional free-text reference (a
  case, FIR or letter number). The reference is C3: encrypted with the school's DEK (AAD
  ``ops.legal_holds|reference_ciphertext|<id>``), shown only to holders of
  ``privacy.hold.manage``, never logged, audited, put in a notification or an access report.
- **Review date (FR-DPDP-041).** Required, today or later and at most ``review.max_months``
  ahead (12). A hold is never lifted automatically; reminders (FR-DPDP-049) go to the holders
  of ``privacy.hold.manage`` before the date and again while it is overdue.
- **Maker-checker (FR-DPDP-042).** Placing and lifting each take two different people with
  ``privacy.hold.manage`` (owner and principal) and step-up: one asks (``pending`` /
  ``lift_pending``), another approves (``active`` / ``lifted``). The asker may withdraw a
  pending placement; anyone with the permission may reject it or cancel a lift request. The
  database refuses the same person on both sides (CHECKs in 0083_legal_holds).
- **Effect.** Holds that are ``active`` or ``lift_pending`` stop retention and erasure, through
  ``app.privacy.service.hold_scope`` (the one public check; FR-DPDP-043..FR-DPDP-046).
- **Audit (FR-DPDP-047).** ``privacy.hold.requested``, ``.placed``, ``.rejected``,
  ``.lift_requested``, ``.lifted``, ``.lift_cancelled`` in the same transaction: ids, scope,
  record class and reason code only (never the reference).
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Mapping, Sequence
from typing import Any, Final
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.audit import service as audit
from app.authz.context import UserContext
from app.core.db import tenant_session
from app.core.errors import Conflict, Forbidden, NotFound, PreconditionFailed, ValidationFailed
from app.core.ids import new_id
from app.core.logging import get_context, get_logger
from app.core.records import RecordTable
from app.core.redaction import contains_full_aadhaar
from app.identity import service as identity
from app.notifications import service as notifications
from app.privacy import config as privacy_config
from app.privacy import holds_repository as repo
from app.privacy.holds_config import HoldsConfig, load_config
from app.privacy.holds_models import LegalHold
from app.privacy.holds_schemas import HoldCreate, HoldListOut, HoldOptionOut, HoldOut
from app.students import crypto
from app.students import rotation as key_rotation
from app.students import service as students

MANAGE: Final = "privacy.hold.manage"
TABLE: Final = "ops.legal_holds"
RESOURCE: Final = "legal_hold"
IST: Final = ZoneInfo("Asia/Kolkata")
MANAGERS = notifications.PermissionSelector(MANAGE)

log = get_logger(__name__)


class SelfApproval(Forbidden):
    code = "self_approval_forbidden"
    title = "Someone other than the person who asked must approve this"


def settings() -> HoldsConfig:
    return load_config()


def _error(field: str, code: str) -> ValidationFailed:
    return ValidationFailed([{"field": field, "code": code, "message_key": f"errors.{code}"}])


def _audit(
    session: Session, action: str, row: LegalHold, extra: Mapping[str, Any] | None = None
) -> None:
    request_id = get_context().get("request_id")
    audit.record(
        session,
        action=action,
        resource_type=RESOURCE,
        resource_id=row.id,
        summary={
            "scope": row.scope,
            "student_id": str(row.student_id) if row.student_id else None,
            "record_class": row.record_class,
            "reason_code": row.reason_code,
            **dict(extra or {}),
        },
        request_id=request_id if isinstance(request_id, str) else None,
    )


def _today(session: Session) -> dt.date:
    return repo.now(session).astimezone(IST).date()


def _add_months(day: dt.date, months: int) -> dt.date:
    month = day.month - 1 + months
    year = day.year + month // 12
    month = month % 12 + 1
    for last in (31, 30, 29, 28):
        try:
            return dt.date(year, month, min(day.day, last))
        except ValueError:
            continue
    raise AssertionError("unreachable")


def max_review_on(session: Session) -> dt.date:
    return _add_months(_today(session), settings().review.max_months)


def _holdable_classes() -> dict[str, str]:
    """Record classes a hold may name: every class that holds personal data."""
    return {
        key: cls.label
        for key, cls in privacy_config.record_classes().classes.items()
        if cls.erasure != "no_personal_data"
    }


# --- texts --------------------------------------------------------------------------------------


def _encrypt(session: Session, hold_id: uuid.UUID, value: str | None) -> bytes | None:
    if not value:
        return None
    blob, _ = crypto.encrypt_value(
        session, value, table=TABLE, column=repo.CIPHERTEXT, row_id=hold_id
    )
    return blob


def _decrypt(session: Session, row: LegalHold) -> str | None:
    if row.reference_ciphertext is None:
        return None
    try:
        return crypto.decrypt_value(
            session,
            bytes(row.reference_ciphertext),
            table=TABLE,
            column=repo.CIPHERTEXT,
            row_id=row.id,
        )
    except crypto.CryptoError:  # pragma: no cover - tampered or moved ciphertext
        log.error("privacy.hold_reference_unreadable", resource_id=row.id, error_code="crypto")
        return None


# --- shaping ------------------------------------------------------------------------------------


def _out(
    session: Session,
    ctx: UserContext,
    rows: Sequence[LegalHold],
) -> list[HoldOut]:
    today = _today(session)
    cfg = settings()
    classes = _holdable_classes()
    student_ids = {r.student_id for r in rows if r.student_id is not None}
    summaries = students.summaries(session, ctx, student_ids) if student_ids else {}
    users = {
        u
        for r in rows
        for u in (r.placed_by, r.approved_by, r.lift_requested_by, r.lifted_by)
        if u is not None
    }
    names = {u: m[1] for u, m in identity.members_for_users(session, users).items()}
    out = []
    for r in rows:
        if r.student_id is not None:
            s = summaries.get(r.student_id)
            label = s.display_name if s is not None and s.display_name else "Student"
            if s is not None and s.admission_no:
                label = f"{label} ({s.admission_no})"
        else:
            label = classes.get(r.record_class or "", r.record_class or "")
        effective = r.status in repo.EFFECTIVE
        asker = r.placed_by if r.status == "pending" else r.lift_requested_by
        out.append(
            HoldOut(
                id=r.id,
                scope="student" if r.scope == "student" else "record_class",
                student_id=r.student_id,
                subject_label=label,
                record_class=r.record_class,
                reason_code=r.reason_code,
                reason_label=cfg.reason_codes.get(r.reason_code, r.reason_code),
                reference=_decrypt(session, r),
                review_on=r.review_on,
                review_overdue=effective and r.review_on < today,
                status=r.status,
                effective=effective,
                placed_by_name=names.get(r.placed_by),
                placed_at=r.placed_at,
                approved_by_name=names.get(r.approved_by) if r.approved_by else None,
                approved_at=r.approved_at,
                lift_requested_by_name=(
                    names.get(r.lift_requested_by) if r.lift_requested_by else None
                ),
                lift_requested_at=r.lift_requested_at,
                lifted_by_name=names.get(r.lifted_by) if r.lifted_by else None,
                lifted_at=r.lifted_at,
                rejected_at=r.rejected_at,
                can_approve=r.status in ("pending", "lift_pending") and asker != ctx.user_id,
                version=r.version,
            )
        )
    return out


# --- reads --------------------------------------------------------------------------------------


def list_holds(session: Session, ctx: UserContext) -> HoldListOut:
    """Every open hold (earliest review first), then recent lifted and rejected ones, with the
    reason codes and record classes the place dialog offers (``privacy.hold.manage``)."""
    cfg = settings()
    return HoldListOut(
        items=_out(session, ctx, repo.list_holds(session)),
        reason_codes=[HoldOptionOut(code=k, label=v) for k, v in cfg.reason_codes.items()],
        record_classes=[HoldOptionOut(code=k, label=v) for k, v in _holdable_classes().items()],
        max_review_on=max_review_on(session),
    )


def _get(session: Session, hold_id: uuid.UUID, *, lock: bool = False) -> LegalHold:
    row = repo.get(session, hold_id, lock=lock)
    if row is None:
        raise NotFound("Legal hold not found")
    return row


def get_hold(session: Session, ctx: UserContext, hold_id: uuid.UUID) -> HoldOut:
    return _out(session, ctx, [_get(session, hold_id)])[0]


# --- writes -------------------------------------------------------------------------------------


def _check_review(session: Session, review_on: dt.date) -> None:
    if review_on < _today(session):
        raise _error("review_on", "date_in_past")
    if review_on > max_review_on(session):
        raise _error("review_on", "review_too_far")


def place(session: Session, ctx: UserContext, data: HoldCreate) -> HoldOut:
    """Ask to place a hold (FR-DPDP-039..FR-DPDP-042; step-up). It takes effect when another
    holder of ``privacy.hold.manage`` approves it. Audit ``privacy.hold.requested``."""
    if data.reason_code not in settings().reason_codes:
        raise _error("reason_code", "unknown_reason_code")
    if data.reference and contains_full_aadhaar(data.reference):
        raise _error("reference", "aadhaar_full_number_rejected")
    _check_review(session, data.review_on)
    if data.scope == "student":
        if data.student_id is None or data.record_class is not None:
            raise _error("student_id", "student_required")
        try:
            students.ensure_in_scope(session, ctx, data.student_id)
        except NotFound as exc:
            raise _error("student_id", "not_found") from exc
    else:
        if data.record_class is None or data.student_id is not None:
            raise _error("record_class", "record_class_required")
        if data.record_class not in _holdable_classes():
            raise _error("record_class", "unknown_record_class")
    hold_id = new_id()
    row = repo.insert(
        session,
        id=hold_id,
        tenant_id=repo.current_tenant_id(session),
        scope=data.scope,
        student_id=data.student_id,
        record_class=data.record_class,
        reason_code=data.reason_code,
        reference_ciphertext=_encrypt(session, hold_id, data.reference),
        review_on=data.review_on,
        status="pending",
        placed_by=ctx.user_id,
    )
    _audit(session, "privacy.hold.requested", row, {"has_reference": bool(data.reference)})
    log.info("privacy.hold.requested", resource_type=RESOURCE, resource_id=row.id)
    return get_hold(session, ctx, row.id)


def _transition(
    session: Session,
    ctx: UserContext,
    hold_id: uuid.UUID,
    expected_version: int,
    *,
    allowed_from: tuple[str, ...],
    values: Mapping[str, Any],
    action: str,
) -> HoldOut:
    row = _get(session, hold_id, lock=True)
    if row.version != expected_version:
        raise PreconditionFailed("Someone else changed this hold. Reload it.")
    if row.status not in allowed_from:
        raise Conflict("This hold is not waiting for this step.", code="hold_wrong_state")
    updated = repo.update_hold(session, row, values, expected_version=expected_version)
    if updated is None:  # pragma: no cover - the row is locked above
        raise PreconditionFailed("Someone else changed this hold. Reload it.")
    _audit(session, action, updated)
    log.info(action, resource_type=RESOURCE, resource_id=updated.id)
    return get_hold(session, ctx, updated.id)


def approve(
    session: Session, ctx: UserContext, hold_id: uuid.UUID, expected_version: int
) -> HoldOut:
    """Approve a pending placement: the hold becomes active (another person than the one who
    asked; step-up). Audit ``privacy.hold.placed``."""
    row = _get(session, hold_id)
    if row.placed_by == ctx.user_id:
        raise SelfApproval()
    now = repo.now(session)
    return _transition(
        session,
        ctx,
        hold_id,
        expected_version,
        allowed_from=("pending",),
        values={"status": "active", "approved_by": ctx.user_id, "approved_at": now},
        action="privacy.hold.placed",
    )


def reject(
    session: Session, ctx: UserContext, hold_id: uuid.UUID, expected_version: int
) -> HoldOut:
    """Reject (or, by the asker, withdraw) a pending placement. Audit ``privacy.hold.rejected``."""
    now = repo.now(session)
    return _transition(
        session,
        ctx,
        hold_id,
        expected_version,
        allowed_from=("pending",),
        values={"status": "rejected", "rejected_by": ctx.user_id, "rejected_at": now},
        action="privacy.hold.rejected",
    )


def request_lift(
    session: Session, ctx: UserContext, hold_id: uuid.UUID, expected_version: int
) -> HoldOut:
    """Ask to lift an active hold; it keeps holding until another person approves. Audit
    ``privacy.hold.lift_requested``."""
    now = repo.now(session)
    return _transition(
        session,
        ctx,
        hold_id,
        expected_version,
        allowed_from=("active",),
        values={
            "status": "lift_pending",
            "lift_requested_by": ctx.user_id,
            "lift_requested_at": now,
        },
        action="privacy.hold.lift_requested",
    )


def approve_lift(
    session: Session, ctx: UserContext, hold_id: uuid.UUID, expected_version: int
) -> HoldOut:
    """Approve a lift (another person than the one who asked; step-up): retention and erasure
    apply again from the next run. Audit ``privacy.hold.lifted``."""
    row = _get(session, hold_id)
    if row.lift_requested_by == ctx.user_id:
        raise SelfApproval()
    now = repo.now(session)
    return _transition(
        session,
        ctx,
        hold_id,
        expected_version,
        allowed_from=("lift_pending",),
        values={"status": "lifted", "lifted_by": ctx.user_id, "lifted_at": now},
        action="privacy.hold.lifted",
    )


def cancel_lift(
    session: Session, ctx: UserContext, hold_id: uuid.UUID, expected_version: int
) -> HoldOut:
    """Cancel a lift request: the hold stays active. Audit ``privacy.hold.lift_cancelled``."""
    return _transition(
        session,
        ctx,
        hold_id,
        expected_version,
        allowed_from=("lift_pending",),
        values={"status": "active", "lift_requested_by": None, "lift_requested_at": None},
        action="privacy.hold.lift_cancelled",
    )


# --- the check (used by app.privacy.service.hold_scope) ----------------------------------------


def effective(session: Session, record_class: str) -> tuple[bool, frozenset[uuid.UUID]]:
    return repo.effective(session, record_class)


def student_held(session: Session, student_id: uuid.UUID) -> bool:
    return repo.student_held(session, student_id)


def held_classes(session: Session) -> set[str]:
    return repo.held_classes(session)


# --- review reminders (FR-DPDP-049) ------------------------------------------------------------


def send_review_reminders(tenant_id: uuid.UUID, *, today: dt.date | None = None) -> int:
    """Daily: remind the holders of ``privacy.hold.manage`` of holds whose review date is near
    or past (in-app; ids and counts only). Returns how many holds were reminded."""
    review = settings().review
    sent = 0
    with tenant_session(tenant_id) as session:
        day = today or _today(session)
        for row in repo.due_for_reminder(
            session,
            day,
            before_days=review.remind_days_before,
            every_days=review.overdue_every_days,
        ):
            days_left = (row.review_on - day).days
            notifications.notify(
                session,
                tenant_id=tenant_id,
                recipients=MANAGERS,
                template_key="privacy.hold.review_due",
                params={
                    "hold_id": row.id,
                    "days": abs(days_left),
                    "state": "overdue" if days_left < 0 else "due",
                },
                resource_id=row.id,
                dedupe_key=f"privacy.hold.review_due:{row.id}:{day.isoformat()}",
            )
            repo.mark_reminded(session, row.id, day)
            sent += 1
    return sent


# --- DEK rotation, full export ------------------------------------------------------------------


def reencrypt_batch(
    session: Session, keyring: crypto.TenantKeyring, target_version: int, limit: int
) -> int:
    """References under another key version are re-encrypted to ``target_version``."""
    done = 0
    for hold_id, blob in repo.stale_ciphertexts(session, target_version, limit):
        new_blob = crypto.reencrypt_value(
            session,
            blob,
            table=TABLE,
            column=repo.CIPHERTEXT,
            row_id=hold_id,
            key_version=target_version,
            keyring=keyring,
        )
        repo.set_ciphertext(session, hold_id, new_blob)
        done += 1
    return done


key_rotation.register_reencryptor("privacy_holds", reencrypt_batch)


def export_records(session: Session) -> list[RecordTable]:
    """Worker only: the school's holds for its full data export (reference left out)."""
    return repo.export_tables(session)


__all__ = [
    "MANAGE",
    "SelfApproval",
    "approve",
    "approve_lift",
    "cancel_lift",
    "effective",
    "export_records",
    "get_hold",
    "held_classes",
    "list_holds",
    "max_review_on",
    "place",
    "reencrypt_batch",
    "reject",
    "request_lift",
    "send_review_reminders",
    "student_held",
]
