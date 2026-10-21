"""Public handles: issue, resolve, revoke, count views, purge (ADR-0047, ADR-0046; FR-PUB-002,
FR-PUB-005..007, FR-PUB-012; SEC-037, SEC-039).

The only module that writes ``core.public_handles``. Everything that issues, revokes or counts
runs in the school's own ``tenant_session`` (RLS: a school can never create or touch a handle
of another school). :func:`resolve` is the one cross-school step: it hashes the key and asks
``core.resolve_public_handle`` for ``(tenant_id, handle_id)``; the caller then opens that
school's ``tenant_session`` and re-reads the handle with :func:`load_handle`.

- Keys are never stored, returned twice or logged: :func:`issue_handle` returns a generated key
  exactly once (``IssuedHandle.key``) for the caller to put in a message or a printed code.
- Unknown, expired, revoked and suspended-school handles all look the same to a caller
  (``None`` here, the uniform 404 at the guard).
- Audit (FR-PUB-012, in the caller's transaction): ``public.link.issued``, ``public.link.viewed``
  (first view only; later views are counted on the row), ``public.link.revoked`` for parent
  links; ``public.handle.issued`` / ``public.handle.revoked`` for certificate codes and
  provider routes. Summaries carry ids, kinds and reason codes only.
- Expired parent handles are deleted ``purge_after_days`` after expiry by the daily job
  (``public.purge_handles``, FR-PUB-006), and with the school at offboarding (ADR-0029).
"""

from __future__ import annotations

import datetime as dt
import re
import uuid
from collections.abc import Collection
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.core import purge as purging
from app.core.db import context_free_session, tenant_session
from app.core.errors import Conflict, ServiceUnavailable, ValidationFailed
from app.core.ids import new_id
from app.core.logging import get_context, get_logger
from app.public import repository as repo
from app.public import tokens
from app.public.config import (
    HANDLE_KINDS,
    PARENT_KINDS,
    PARENT_TARGET_TYPES,
    HandleKind,
    ParentKind,
    is_parent_kind,
    rules,
)
from app.public.models import PublicHandle
from app.public.subjects import subject_report
from app.tenancy import service as tenancy

log = get_logger(__name__)

_CODE: Final = re.compile(r"^[a-z][a-z_]{1,39}$")
# Kinds whose key the service generates; the others are given by the caller (a certificate code
# made by app.certificates, a WABA id from the provider).
_GENERATED: Final = frozenset({*PARENT_KINDS, "razorpay_route", "school_disclosure"})


@dataclass(frozen=True, slots=True)
class IssuedHandle:
    """A new handle. ``key`` is the raw key when the service generated it (parent tokens,
    Razorpay routes): returned once, never stored, never logged; ``None`` otherwise."""

    handle_id: uuid.UUID
    kind: HandleKind
    key: str | None
    expires_at: dt.datetime | None


@dataclass(frozen=True, slots=True)
class ResolvedHandle:
    """What the resolver may say about a live handle: the school and the handle, nothing else."""

    tenant_id: uuid.UUID
    handle_id: uuid.UUID
    kind: HandleKind


@dataclass(frozen=True, slots=True)
class HandleView:
    """The handle re-read in its school's ``tenant_session`` (RLS)."""

    handle_id: uuid.UUID
    tenant_id: uuid.UUID
    kind: HandleKind
    target_type: str
    target_id: uuid.UUID
    guardian_id: uuid.UUID | None
    expires_at: dt.datetime | None
    view_count: int


class HandleKeyInUse(Conflict):
    code = "handle_key_in_use"


def _request_id() -> str | None:
    value = get_context().get("request_id")
    return value if isinstance(value, str) else None


def _audit(
    session: Session,
    *,
    action: str,
    handle_id: uuid.UUID,
    summary: dict[str, Any],
    system: bool,
) -> None:
    audit.record(
        session,
        action=action,
        resource_type="public_handle",
        resource_id=handle_id,
        summary=summary,
        actor_type="system" if system else "user",
        request_id=_request_id(),
    )


def _family(kind: str) -> str:
    return "public.link" if is_parent_kind(kind) else "public.handle"


def _check_code(name: str, value: str) -> None:
    if not _CODE.fullmatch(value):
        raise ValueError(f"{name} must be a short lower-case code")


# --- issue ------------------------------------------------------------------------------------


def issue_handle(
    session: Session,
    *,
    kind: HandleKind,
    target_type: str,
    target_id: uuid.UUID,
    guardian_id: uuid.UUID | None = None,
    key: str | None = None,
    created_by: uuid.UUID | None = None,
    message_id: uuid.UUID | None = None,
) -> IssuedHandle:
    """Create a handle for the current school (``tenant_session``) and audit it.

    Parent kinds, ``razorpay_route`` and ``school_disclosure`` (ADR-0051: 22 characters, kept
    by ``app.disclosure`` so staff can copy the link again): the key is generated here and
    returned once in ``IssuedHandle.key`` (``key`` must be ``None``); parent links expire after
    the configured lifetime (30/90/7/7 days, D22) and name the guardian they were sent to (one
    token per guardian per message, FR-PUB-005). ``cert_verify`` and ``meta_waba``: the caller
    passes the key (a certificate code, a WABA id); they never expire. ``created_by`` is the
    member who caused the issue (``None`` for system jobs). Raises :class:`HandleKeyInUse`
    (409) when the key already belongs to a handle of that kind in any school (fails closed,
    nothing is revealed about which)."""
    if kind not in HANDLE_KINDS:
        raise ValueError(f"unknown public handle kind {kind!r}")
    _check_code("target_type", target_type)
    parent = is_parent_kind(kind)
    if guardian_id is not None and not parent:
        raise ValueError("only parent links name a guardian")
    if kind in _GENERATED:
        if key is not None:
            raise ValueError(f"{kind} keys are generated by the service")
        key = tokens.new_key(kind)
        returned: str | None = key
    else:
        if key is None or not tokens.is_well_formed(kind, key):
            raise ValidationFailed(
                [{"field": "key", "code": "invalid_format", "message_key": "errors.invalid"}]
            )
        returned = None
    expires_at: dt.datetime | None = None
    if parent:
        parent_kind: ParentKind = kind  # type: ignore[assignment]
        expires_at = repo.now(session) + rules().lifetime(parent_kind)
    handle_id = new_id()
    savepoint = session.begin_nested()
    try:
        row = repo.insert(
            session,
            id=handle_id,
            kind=kind,
            key_hash=tokens.key_hash(key),
            target_type=target_type,
            target_id=target_id,
            guardian_id=guardian_id,
            expires_at=expires_at,
            created_by=created_by,
        )
        savepoint.commit()
    except IntegrityError as exc:
        savepoint.rollback()
        if "public_handles_kind_key_hash_key" in str(exc.orig):
            raise HandleKeyInUse("This code is already in use. Try again.") from None
        raise
    summary: dict[str, Any] = {"kind": kind, "target_type": target_type, "target_id": target_id}
    if guardian_id is not None:
        summary["guardian_id"] = guardian_id
    if expires_at is not None:
        summary["expires_at"] = expires_at.isoformat(timespec="seconds")
    if message_id is not None:
        summary["message_id"] = message_id
    _audit(
        session,
        action=f"{_family(kind)}.issued",
        handle_id=row.id,
        summary=summary,
        system=created_by is None,
    )
    return IssuedHandle(handle_id=row.id, kind=kind, key=returned, expires_at=expires_at)


def issue_parent_link(
    session: Session,
    *,
    kind: ParentKind,
    target_id: uuid.UUID,
    guardian_id: uuid.UUID,
    message_id: uuid.UUID | None = None,
    created_by: uuid.UUID | None = None,
) -> IssuedHandle:
    """A parent link with the target type its page expects (``PARENT_TARGET_TYPES``; R2-3H
    pages, R2-3G sends): a notice, receipt, fee account or (opt-in) the guardian itself, always
    for one guardian (FR-PUB-005). The link is ``f"{SOS_PUBLIC_BASE_URL}/p/{issued.key}"``."""
    if kind not in PARENT_KINDS:
        raise ValueError(f"{kind!r} is not a parent link kind")
    if kind == "parent_optin" and target_id != guardian_id:
        raise ValueError("an opt-in link points at its own guardian")
    return issue_handle(
        session,
        kind=kind,
        target_type=PARENT_TARGET_TYPES[kind],
        target_id=target_id,
        guardian_id=guardian_id,
        created_by=created_by,
        message_id=message_id,
    )


# --- resolve (cross-school, definer function) -------------------------------------------------


def resolve(kind: HandleKind, key: str) -> ResolvedHandle | None:
    """The school and handle of a live key of ``kind``, or ``None`` for anything else (unknown,
    malformed, expired, revoked, or a school that is not active; receipts and certificate codes
    also resolve while the school is suspended). One indexed lookup through
    ``core.resolve_public_handle`` in a context-free session; a database failure is a 503,
    never a guess (ADR-0047 decision 5). The key itself is never logged."""
    return resolve_any((kind,), key)


def resolve_any(kinds: Collection[HandleKind], key: str | None) -> ResolvedHandle | None:
    """:func:`resolve` for a key that may be of any of ``kinds`` (a parent token does not say
    which kind of link it is). Every kind is asked in the same statement, so a miss always costs
    the same number of indexed lookups. Two live handles with the same key in different kinds
    (practically impossible for random 256-bit keys) resolve to nothing (fail closed)."""
    if not isinstance(key, str):
        return None
    wanted = [
        k for k in dict.fromkeys(kinds) if k in HANDLE_KINDS and tokens.is_well_formed(k, key)
    ]
    if not wanted:
        return None
    try:
        with context_free_session() as session:
            found = repo.resolve_any(session, wanted, tokens.key_hash(key))
    except DBAPIError:
        log.warning(
            "public.resolve_failed", resource_type="public_handle", error_code="database_error"
        )
        raise ServiceUnavailable() from None
    if len(found) != 1:
        return None
    tenant_id, handle_id, kind = found[0]
    resolved_kind: HandleKind = kind  # type: ignore[assignment]
    return ResolvedHandle(tenant_id=tenant_id, handle_id=handle_id, kind=resolved_kind)


def load_handle(
    session: Session, handle_id: uuid.UUID, kinds: Collection[str] | None = None
) -> HandleView | None:
    """The handle re-read under RLS in its school's ``tenant_session``; ``None`` when it is not
    this school's, not one of ``kinds``, revoked or expired (a revocation between the resolve
    and this read wins)."""
    row = repo.get(session, handle_id)
    if row is None or row.revoked_at is not None:
        return None
    if kinds is not None and row.kind not in kinds:
        return None
    if row.expires_at is not None and row.expires_at <= repo.now(session):
        return None
    return _view(row)


def _view(row: PublicHandle) -> HandleView:
    kind: HandleKind = row.kind  # type: ignore[assignment]
    return HandleView(
        handle_id=row.id,
        tenant_id=row.tenant_id,
        kind=kind,
        target_type=row.target_type,
        target_id=row.target_id,
        guardian_id=row.guardian_id,
        expires_at=row.expires_at,
        view_count=row.view_count,
    )


def record_view(session: Session, handle: HandleView) -> None:
    """Count one page view of a parent link; the first one is audited ``public.link.viewed``
    (FR-PUB-012, ADR-0046 decision 8). Link previews never get here (they carry no cookie)."""
    first = repo.count_view(session, handle.handle_id)
    if first:
        _audit(
            session,
            action="public.link.viewed",
            handle_id=handle.handle_id,
            summary={"kind": handle.kind, "target_type": handle.target_type},
            system=True,
        )


# --- revoke -----------------------------------------------------------------------------------


def _revoke_rows(session: Session, rows: list[PublicHandle], reason: str, *, by_user: bool) -> int:
    _check_code("reason", reason)
    if not rows:
        return 0
    changed = repo.mark_revoked(session, [r.id for r in rows], reason)
    for row in rows:
        _audit(
            session,
            action=f"{_family(row.kind)}.revoked",
            handle_id=row.id,
            summary={"kind": row.kind, "reason": reason},
            system=not by_user,
        )
    return changed


def revoke(session: Session, handle_id: uuid.UUID, *, reason: str, by_user: bool = True) -> bool:
    """Revoke one handle of the current school (idempotent: ``False`` when it was already
    revoked or is not this school's). ``reason`` is a short code (``manual``,
    ``payments_disconnected``, ...). A revocation is final (database trigger)."""
    row = repo.get(session, handle_id, lock=True)
    if row is None or row.revoked_at is not None:
        return False
    return _revoke_rows(session, [row], reason, by_user=by_user) == 1


def revoke_for_guardian(
    session: Session, guardian_id: uuid.UUID, *, reason: str, by_user: bool = True
) -> int:
    """Revoke every open parent link of a guardian (FR-PUB-007: unlinked or deleted guardian,
    phone change, opt-out). Returns how many were revoked."""
    rows = repo.open_for_guardian(session, guardian_id, PARENT_KINDS)
    return _revoke_rows(session, rows, reason, by_user=by_user)


def revoke_for_target(
    session: Session,
    target_type: str,
    target_id: uuid.UUID,
    *,
    reason: str,
    kinds: Collection[HandleKind] | None = None,
    by_user: bool = True,
) -> int:
    """Revoke every open handle of one target (a withdrawn notice, a disconnected provider
    account). Returns how many were revoked."""
    _check_code("target_type", target_type)
    rows = repo.open_for_target(session, target_type, target_id, kinds or HANDLE_KINDS)
    return _revoke_rows(session, rows, reason, by_user=by_user)


# --- purge ------------------------------------------------------------------------------------


def purge_expired_handles(tenant_id: uuid.UUID) -> int:
    """Delete the school's handles that expired more than ``purge_after_days`` ago (FR-PUB-006),
    in batches, each in its own transaction. Returns the number deleted (ids and counts only)."""
    cfg = rules()
    total = 0
    while True:
        with tenant_session(tenant_id) as session:
            before = repo.now(session) - dt.timedelta(days=cfg.purge_after_days)
            deleted = repo.purge_expired(session, before, cfg.purge_batch_size)
        total += deleted
        if deleted < cfg.purge_batch_size:
            return total


# --- offboarding purge (FR-PLT-005, ADR-0029) -------------------------------------------------

_PURGE = purging.PurgeTables(deleted=("core.public_handles",))


def tenant_data_counts(session: Session) -> dict[str, int]:
    """Rows of the current school in this module's tables (offboarding inventory)."""
    return _PURGE.count(session)


def purge_tenant_data(session: Session) -> dict[str, int]:
    """Delete the current school's handles (offboarding only; ADR-0029)."""
    return _PURGE.delete(session)


tenancy.register_data_owner(
    tenancy.TenantDataOwner(name="public", count=tenant_data_counts, purge=purge_tenant_data)
)


__all__ = [
    "HandleKeyInUse",
    "HandleView",
    "IssuedHandle",
    "ResolvedHandle",
    "issue_handle",
    "issue_parent_link",
    "load_handle",
    "purge_expired_handles",
    "purge_tenant_data",
    "record_view",
    "resolve",
    "resolve_any",
    "revoke",
    "revoke_for_guardian",
    "revoke_for_target",
    "subject_report",
    "tenant_data_counts",
]
