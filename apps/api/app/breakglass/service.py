"""Break-glass support access, school side (US-103, FR-OPS-004, SEC-021; docs/07 §6.4, T17).

Flow
1. An operator raises a request in the control plane (reason, scope, 15 min .. 8 h).
2. :func:`sync_school` PULLS the school's open requests (``app.platform.service``, read as
   ``sos_platform``) into ``ops.break_glass_grants`` (status ``requested``), audits
   ``breakglass.requested`` in the school's chain (actor ``platform``) and notifies every holder
   of ``breakglass.approve``. The control plane never writes tenant tables.
3. An owner/principal approves with step-up (:func:`approve`): in the school's own transaction
   the operator's account is created/found with the approver as inviter
   (``identity.open_breakglass_membership``; no new definer function) and gets a temporary
   ``platform_support`` membership (read-only, MFA, scoped to the request scope) that expires
   with the grant. :func:`deny` refuses; :func:`revoke` ends access at any time.
4. :func:`sweep_school` (every minute) ends grants whose window passed (the membership already
   stopped working at ``expires_at``: sign-in resolution refuses expired memberships) and
   expires requests left unanswered for 24 h.
5. Emergency requests (legal obligation / security incident) confirmed by two different
   operators are opened without school approval when pulled, and the owner/principal are
   notified immediately (``breakglass.emergency``). Only an operator who already has a SchoolOS
   account can be given a membership this way; otherwise the grant stays ``approved`` without
   access (fail closed) until it expires.

Every decision is audited in the same transaction in the school's chain and reported to the
control plane after commit (:func:`report_outcomes`, retried by the sweep), which writes the
control-plane chain. Every call made with a ``platform_support`` membership is recorded by the
authz guard as ``breakglass.access`` (``via_breakglass: true``).

Signing in (ADR-0023 option C): the operator signs in to the school app with the support app
client of the operator pool (MFA, fresh sign-in); the membership belongs to the identity
``(operator pool issuer, operator subject)``, never to a staff account with the same subject.
:func:`start_support_session` (step-up) opens the session: it checks the grant, writes
``breakglass.session_started`` to the school's chain and, in the same request, to the
control-plane chain. :func:`support_grant_active` is checked on every support request.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Any

import yaml
from sqlalchemy import RowMapping, event
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.authz.context import UserContext
from app.authz.http import Page, decode_cursor, encode_cursor
from app.authz.resolver import register_support_grant_check
from app.breakglass import repository as repo
from app.breakglass.schemas import GrantOut, SupportSessionOut
from app.core.config import DeploymentMode, get_settings
from app.core.db import tenant_session
from app.core.errors import (
    Conflict,
    DomainError,
    Forbidden,
    NotFound,
    ServiceUnavailable,
    ValidationFailed,
)
from app.core.ids import new_id
from app.core.logging import get_logger
from app.identity import service as identity
from app.identity.principal import Principal, require_recent_auth
from app.notifications import service as notifications
from app.platform import service as control_plane

log = get_logger(__name__)
APPROVERS = notifications.PermissionSelector("breakglass.approve")
RESOURCE = "breakglass_grant"


@lru_cache(maxsize=1)
def _config() -> dict[str, Any]:
    raw = yaml.safe_load(
        resources.files("app.breakglass").joinpath("config.yaml").read_text("utf-8")
    )
    if not isinstance(raw, dict):
        raise ValueError("breakglass/config.yaml: expected a mapping")
    return raw


def pending_ttl() -> dt.timedelta:
    hours = _config()["pending_request_ttl_hours"]
    if not isinstance(hours, int) or hours < 1:
        raise ValueError("pending_request_ttl_hours must be a positive int")
    return dt.timedelta(hours=hours)


def sweep_interval_seconds() -> int:
    value = _config()["sweep_interval_seconds"]
    if not isinstance(value, int) or value < 10:
        raise ValueError("sweep_interval_seconds must be an int >= 10")
    return value


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _control_plane_here() -> bool:
    """Requests live in the shared deployment's control plane; a dedicated host has none (its
    requests would arrive with the heartbeat, not built yet)."""
    return get_settings().deployment_mode is DeploymentMode.SHARED


def _out(row: Mapping[Any, Any]) -> GrantOut:
    return GrantOut.model_validate(dict(row))


def _membership_scopes(scope: Mapping[str, Any]) -> list[tuple[str, uuid.UUID | None]]:
    """The request scope as membership scopes: a section and/or class, else the whole school."""
    out: list[tuple[str, uuid.UUID | None]] = []
    for key, kind in (("section_id", "section"), ("class_id", "class")):
        value = scope.get(key)
        if value is None:
            continue
        try:
            out.append((kind, uuid.UUID(str(value))))
        except ValueError as exc:
            raise ValidationFailed(
                [{"field": "scope", "code": "invalid", "message_key": "errors.invalid"}]
            ) from exc
    return out or [("school", None)]


def _scope_summary(scope: Mapping[str, Any]) -> dict[str, Any]:
    return {str(k): v for k, v in list(scope.items())[:10]}


# --- pulling requests from the control plane ------------------------------------------------


@dataclass(frozen=True, slots=True)
class SyncResult:
    received: int = 0
    emergency: int = 0
    reported: int = 0


def _receive_request(
    session: Session, tenant_id: uuid.UUID, req: control_plane.SchoolBreakGlassRequest
) -> bool:
    grant_id = new_id()
    row = repo.insert_if_new(
        session,
        {
            "id": grant_id,
            "tenant_id": tenant_id,
            "platform_user_id": req.requested_by,
            "platform_request_id": req.id,
            "reason": req.reason,
            "reason_code": req.reason_code,
            "scope": req.scope,
            "duration_minutes": req.duration_minutes,
            "emergency": False,
            "status": "requested",
            "operator_display_name": req.operator_display_name,
            "requested_at": req.created_at,
            "platform_status_synced": "requested",
        },
    )
    if row is None:
        return False
    audit.record(
        session,
        action="breakglass.requested",
        resource_type=RESOURCE,
        resource_id=grant_id,
        summary={
            "platform_request_id": req.id,
            "reason_code": req.reason_code,
            "duration_minutes": req.duration_minutes,
            "scope": _scope_summary(req.scope),
            "emergency": False,
        },
        actor_type="platform",
        actor_id=req.requested_by,
    )
    notifications.notify(
        session,
        tenant_id=tenant_id,
        recipients=APPROVERS,
        template_key="breakglass.requested",
        params={
            "grant_id": grant_id,
            "minutes": req.duration_minutes,
            "reason_code": req.reason_code,
        },
        resource_id=grant_id,
        dedupe_key=f"breakglass.requested:{grant_id}",
    )
    return True


def _try_open_emergency_membership(
    session: Session,
    grant: Mapping[Any, Any],
    req: control_plane.SchoolBreakGlassRequest,
) -> uuid.UUID | None:
    """Emergency path: open the membership without a school approver, or fail closed."""
    if req.operator_status != "active" or not req.operator_subject:
        return None
    try:
        with session.begin_nested():
            _, membership_id = identity.open_breakglass_membership(
                session,
                None,
                subject=req.operator_subject,
                issuer=req.operator_issuer,
                display_name=req.operator_display_name,
                email=req.operator_email,
                expires_at=grant["expires_at"],
                scopes=_membership_scopes(grant["scope"]),
            )
    except DomainError as exc:
        log.warning(
            "breakglass.emergency_membership_refused",
            resource_type=RESOURCE,
            resource_id=grant["id"],
            error_code=exc.code,
        )
        return None
    repo.update_grant(session, grant["id"], {"status": "active", "membership_id": membership_id})
    return membership_id


def _receive_emergency(
    session: Session, tenant_id: uuid.UUID, req: control_plane.SchoolBreakGlassRequest
) -> bool:
    now = _now()
    starts = min(req.emergency_confirmed_at or now, now)
    expires = starts + dt.timedelta(minutes=req.duration_minutes)
    grant_id = new_id()
    row = repo.insert_if_new(
        session,
        {
            "id": grant_id,
            "tenant_id": tenant_id,
            "platform_user_id": req.requested_by,
            "platform_request_id": req.id,
            "reason": req.reason,
            "reason_code": req.reason_code,
            "scope": req.scope,
            "duration_minutes": req.duration_minutes,
            "emergency": True,
            "status": "approved" if expires > now else "expired",
            "starts_at": starts,
            "expires_at": expires,
            "decided_at": starts,
            "operator_display_name": req.operator_display_name,
            "requested_at": req.created_at,
            "platform_status_synced": "approved",
        },
    )
    if row is None:
        return False
    membership_id = (
        _try_open_emergency_membership(session, row, req) if row["status"] == "approved" else None
    )
    audit.record(
        session,
        action="breakglass.emergency_opened",
        resource_type=RESOURCE,
        resource_id=grant_id,
        summary={
            "platform_request_id": req.id,
            "reason_code": req.reason_code,
            "duration_minutes": req.duration_minutes,
            "scope": _scope_summary(req.scope),
            "emergency": True,
            "operator_confirmations": 2,
            "membership_id": membership_id,
            "access_opened": membership_id is not None,
        },
        actor_type="platform",
        actor_id=req.requested_by,
    )
    notifications.notify(
        session,
        tenant_id=tenant_id,
        recipients=APPROVERS,
        template_key="breakglass.emergency",
        params={
            "grant_id": grant_id,
            "minutes": req.duration_minutes,
            "reason_code": req.reason_code,
        },
        resource_id=grant_id,
        dedupe_key=f"breakglass.emergency:{grant_id}",
    )
    return True


def _retry_emergency_memberships(
    session: Session, tenant_id: uuid.UUID, requests: Mapping[uuid.UUID, Any]
) -> None:
    for grant in repo.list_emergency_without_membership(session, _now()):
        req = requests.get(grant["platform_request_id"])
        if req is None:
            continue
        membership_id = _try_open_emergency_membership(session, grant, req)
        if membership_id is not None:
            audit.record(
                session,
                action="breakglass.emergency_access_opened",
                resource_type=RESOURCE,
                resource_id=grant["id"],
                summary={"membership_id": membership_id, "via_breakglass": True},
                actor_type="system",
            )


def sync_school(tenant_id: uuid.UUID) -> SyncResult:
    """Pull this school's open requests from the control plane and report outcomes back."""
    if not _control_plane_here():
        return SyncResult()
    requests = control_plane.breakglass_requests_for_school(tenant_id)
    received = emergency = 0
    with tenant_session(tenant_id) as session:
        for req in requests:
            if req.emergency:
                if req.status == "approved" and _receive_emergency(session, tenant_id, req):
                    emergency += 1
            elif req.status == "requested" and _receive_request(session, tenant_id, req):
                received += 1
        _retry_emergency_memberships(session, tenant_id, {r.id: r for r in requests if r.emergency})
    if received or emergency:
        log.info("breakglass.requests_received", count=received + emergency)
    return SyncResult(received=received, emergency=emergency, reported=report_outcomes(tenant_id))


# --- reporting outcomes to the control plane ------------------------------------------------


def report_outcomes(tenant_id: uuid.UUID) -> int:
    """Report decided/ended grants to the control plane (idempotent; retried by the sweep)."""
    if not _control_plane_here():
        return 0
    with tenant_session(tenant_id) as session:
        pending = [
            (r["id"], r["platform_request_id"], r["status"]) for r in repo.list_unreported(session)
        ]
    reported = 0
    for grant_id, request_id, status in pending:
        try:
            control_plane.record_breakglass_outcome(
                tenant_id, request_id, status, grant_id=grant_id
            )
        except (SQLAlchemyError, DomainError) as exc:
            log.warning(
                "breakglass.report_failed",
                resource_type=RESOURCE,
                resource_id=grant_id,
                error_type=type(exc).__name__,
            )
            continue
        with tenant_session(tenant_id) as session:
            repo.mark_reported(session, grant_id, status)
        reported += 1
    return reported


def _report_after_commit(session: Session, tenant_id: uuid.UUID) -> None:
    def _after_commit(_: Session) -> None:
        try:
            report_outcomes(tenant_id)
        except (SQLAlchemyError, DomainError) as exc:  # the sweep retries
            log.warning("breakglass.report_failed", error_type=type(exc).__name__)

    event.listen(session, "after_commit", _after_commit, once=True)


# --- school decisions (owner/principal, breakglass.approve with step-up) ----------------------


def _locked(session: Session, grant_id: uuid.UUID) -> RowMapping:
    row = repo.get(session, grant_id, for_update=True)
    if row is None:
        raise NotFound("Support access request not found")
    return row


def _pending(session: Session, grant_id: uuid.UUID) -> RowMapping:
    row = _locked(session, grant_id)
    if row["emergency"] or row["status"] != "requested":
        raise Conflict("This request is no longer waiting for a decision.", code="invalid_state")
    requested = row["requested_at"] or row["created_at"]
    if requested + pending_ttl() <= _now():
        raise Conflict(
            "This request is too old. Ask SchoolOS support to send a new one.",
            code="request_expired",
        )
    return row


def approve(session: Session, ctx: UserContext, grant_id: uuid.UUID) -> GrantOut:
    """Approve a pending request: access starts now and ends after the requested duration.

    Permission ``breakglass.approve`` with step-up (route). The request must still be pending in
    the control plane and its operator active. The approver can never be the person who gets
    access (service check + DB CHECK). Audit ``breakglass.approved`` (+ membership events);
    notification ``breakglass.approved`` to every approver; reported to the control plane.
    """
    grant = _pending(session, grant_id)
    req = control_plane.breakglass_request_for_school(ctx.tenant_id, grant["platform_request_id"])
    if req is None or req.status != "requested":
        raise Conflict(
            "SchoolOS support withdrew or changed this request.", code="request_withdrawn"
        )
    if req.operator_status != "active" or not req.operator_subject:
        raise Conflict(
            "The SchoolOS person who asked can no longer sign in. Deny this request.",
            code="operator_inactive",
        )
    now = _now()
    expires = now + dt.timedelta(minutes=int(grant["duration_minutes"]))
    _, membership_id = identity.open_breakglass_membership(
        session,
        ctx,
        subject=req.operator_subject,
        issuer=req.operator_issuer,
        display_name=req.operator_display_name,
        email=req.operator_email,
        expires_at=expires,
        scopes=_membership_scopes(grant["scope"]),
    )
    row = repo.update_grant(
        session,
        grant_id,
        {
            "status": "active",
            "starts_at": now,
            "expires_at": expires,
            "decided_at": now,
            "approved_by_membership": ctx.membership_id,
            "membership_id": membership_id,
        },
    )
    audit.record(
        session,
        action="breakglass.approved",
        resource_type=RESOURCE,
        resource_id=grant_id,
        summary={
            "platform_request_id": grant["platform_request_id"],
            "membership_id": membership_id,
            "duration_minutes": grant["duration_minutes"],
            "scope": _scope_summary(grant["scope"]),
        },
        actor_type="user",
        actor_id=ctx.user_id,
        request_id=ctx.request_id,
    )
    notifications.notify(
        session,
        tenant_id=ctx.tenant_id,
        recipients=APPROVERS,
        template_key="breakglass.approved",
        params={"grant_id": grant_id, "minutes": grant["duration_minutes"]},
        resource_id=grant_id,
        dedupe_key=f"breakglass.approved:{grant_id}",
    )
    _report_after_commit(session, ctx.tenant_id)
    return _out(row)


def deny(session: Session, ctx: UserContext, grant_id: uuid.UUID) -> GrantOut:
    """Refuse a pending request. Audit ``breakglass.denied``; reported to the control plane."""
    grant = _locked(session, grant_id)
    if grant["emergency"] or grant["status"] != "requested":
        raise Conflict("This request is no longer waiting for a decision.", code="invalid_state")
    now = _now()
    row = repo.update_grant(
        session,
        grant_id,
        {"status": "denied", "decided_at": now, "denied_by_membership": ctx.membership_id},
    )
    audit.record(
        session,
        action="breakglass.denied",
        resource_type=RESOURCE,
        resource_id=grant_id,
        summary={"platform_request_id": grant["platform_request_id"]},
        actor_type="user",
        actor_id=ctx.user_id,
        request_id=ctx.request_id,
    )
    notifications.notify(
        session,
        tenant_id=ctx.tenant_id,
        recipients=APPROVERS,
        template_key="breakglass.denied",
        params={"grant_id": grant_id},
        resource_id=grant_id,
        dedupe_key=f"breakglass.denied:{grant_id}",
    )
    _report_after_commit(session, ctx.tenant_id)
    return _out(row)


def revoke(session: Session, ctx: UserContext, grant_id: uuid.UUID) -> GrantOut:
    """End support access now (approved or active grants, including emergency ones).

    The temporary membership is removed in the same transaction, so the operator's next call is
    refused. Audit ``breakglass.revoked``; notification; reported to the control plane.
    """
    grant = _locked(session, grant_id)
    if grant["status"] not in ("approved", "active"):
        raise Conflict("This support access is not open.", code="invalid_state")
    if grant["membership_id"] is not None:
        identity.close_breakglass_membership(session, ctx, grant["membership_id"], reason="revoked")
    now = _now()
    row = repo.update_grant(
        session,
        grant_id,
        {"status": "revoked", "revoked_at": now, "revoked_by_membership": ctx.membership_id},
    )
    audit.record(
        session,
        action="breakglass.revoked",
        resource_type=RESOURCE,
        resource_id=grant_id,
        summary={
            "platform_request_id": grant["platform_request_id"],
            "membership_id": grant["membership_id"],
        },
        actor_type="user",
        actor_id=ctx.user_id,
        request_id=ctx.request_id,
    )
    notifications.notify(
        session,
        tenant_id=ctx.tenant_id,
        recipients=APPROVERS,
        template_key="breakglass.revoked",
        params={"grant_id": grant_id},
        resource_id=grant_id,
        dedupe_key=f"breakglass.revoked:{grant_id}",
    )
    _report_after_commit(session, ctx.tenant_id)
    return _out(row)


# --- support sign-in (ADR-0023 option C) ------------------------------------------------------


SESSION_STARTED = "breakglass.session_started"


def _session_ref(session_id: str | None) -> str | None:
    """A short, non-reversible reference to the IdP session (never the raw ID or a token)."""
    if not session_id:
        return None
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:16]


def support_grant_active(tenant_id: uuid.UUID, membership_id: uuid.UUID) -> bool:
    """True while ``membership_id`` belongs to an active grant whose window is open now.

    Checked by the authz resolver on every request of a support principal (ADR-0023), on top
    of the membership's own expiry and ``core.resolve_login``'s filter."""
    with tenant_session(tenant_id) as session:
        return repo.active_grant_for_membership(session, membership_id, _now()) is not None


def start_support_session(
    session: Session, ctx: UserContext, principal: Principal, platform_request_id: uuid.UUID
) -> SupportSessionOut:
    """Start a SchoolOS support session for the grant of ``platform_request_id`` (ADR-0023 §4).

    Only a support principal whose resolved membership is that grant's membership, with a
    sign-in within 5 minutes (428 ``step_up_required``). Another school's or another operator's
    request answers 404. Audit ``breakglass.session_started`` (grant, operator, membership,
    session reference; no token) in the school's chain in this transaction and, on the shared
    tier, in the control-plane chain before this transaction commits (a failure there refuses
    the session, 503).
    """
    if principal.kind != "support" or not ctx.via_breakglass:
        raise Forbidden("Only SchoolOS support starts a support session.", code="breakglass_only")
    require_recent_auth(principal)
    grant = repo.get_by_request(session, platform_request_id)
    if grant is None or grant["membership_id"] != ctx.membership_id:
        raise NotFound("Support access request not found")
    now = _now()
    if grant["status"] != "active" or grant["expires_at"] is None or grant["expires_at"] <= now:
        raise Conflict("This support access is not open.", code="breakglass_grant_inactive")
    session_ref = _session_ref(principal.session_id)
    summary: dict[str, Any] = {
        "platform_request_id": platform_request_id,
        "membership_id": ctx.membership_id,
        "operator_id": grant["platform_user_id"],
        "issuer_kind": "operator_support",
        "via_breakglass": True,
    }
    if session_ref:
        summary["session_ref"] = session_ref
    audit.record(
        session,
        action=SESSION_STARTED,
        resource_type=RESOURCE,
        resource_id=grant["id"],
        summary=summary,
        actor_type="user",
        actor_id=ctx.user_id,
        request_id=ctx.request_id,
    )
    if _control_plane_here():
        try:
            recorded = control_plane.record_breakglass_session_started(
                ctx.tenant_id, platform_request_id, grant_id=grant["id"], session_ref=session_ref
            )
        except (SQLAlchemyError, DomainError) as exc:
            log.warning(
                "breakglass.session_report_failed",
                resource_type=RESOURCE,
                resource_id=grant["id"],
                error_type=type(exc).__name__,
            )
            raise ServiceUnavailable(
                "Support access could not be started right now. Try again in a minute."
            ) from exc
        if not recorded:
            log.warning(
                "breakglass.session_report_skipped", resource_type=RESOURCE, resource_id=grant["id"]
            )
    return SupportSessionOut(
        grant_id=grant["id"],
        platform_request_id=platform_request_id,
        tenant_id=ctx.tenant_id,
        expires_at=grant["expires_at"],
        scope=dict(grant["scope"] or {}),
    )


# --- reads -----------------------------------------------------------------------------------


def _after(cursor: str | None) -> tuple[dt.datetime, uuid.UUID] | None:
    value = decode_cursor(cursor)
    if value is None:
        return None
    try:
        created = dt.datetime.fromisoformat(str(value["t"]))
        return created, uuid.UUID(str(value["i"]))
    except (KeyError, ValueError) as exc:
        raise ValidationFailed(
            [{"field": "cursor", "code": "invalid", "message_key": "errors.invalid_cursor"}]
        ) from exc


def list_grants(
    session: Session, *, limit: int, cursor: str | None = None, status: str | None = None
) -> Page[GrantOut]:
    """Requests and grants of this school, newest first."""
    rows = repo.list_page(session, limit=limit + 1, after=_after(cursor), status=status)
    page = rows[:limit]
    nxt = None
    if len(rows) > limit and page:
        nxt = encode_cursor({"t": page[-1]["created_at"].isoformat(), "i": str(page[-1]["id"])})
    return Page[GrantOut](data=[_out(r) for r in page], next_cursor=nxt)


def get_grant(session: Session, grant_id: uuid.UUID) -> GrantOut:
    row = repo.get(session, grant_id)
    if row is None:
        raise NotFound("Support access request not found")
    return _out(row)


def refresh_requests(tenant_id: uuid.UUID) -> None:
    """Best-effort pull before showing the list (the worker also pulls every minute)."""
    try:
        sync_school(tenant_id)
    except (SQLAlchemyError, DomainError) as exc:
        log.warning("breakglass.sync_failed", error_type=type(exc).__name__)


# --- expiry (worker) -------------------------------------------------------------------------


def sweep_school(tenant_id: uuid.UUID, *, now: dt.datetime | None = None) -> dict[str, int]:
    """End grants whose window has passed and expire unanswered requests (system actor)."""
    at = now or _now()
    expired = stale = 0
    with tenant_session(tenant_id) as session:
        for grant in repo.list_due(session, at):
            if grant["membership_id"] is not None:
                identity.close_breakglass_membership(
                    session, None, grant["membership_id"], reason="expired"
                )
            repo.update_grant(session, grant["id"], {"status": "expired"})
            audit.record(
                session,
                action="breakglass.expired",
                resource_type=RESOURCE,
                resource_id=grant["id"],
                summary={
                    "platform_request_id": grant["platform_request_id"],
                    "membership_id": grant["membership_id"],
                },
                actor_type="system",
            )
            notifications.notify(
                session,
                tenant_id=tenant_id,
                recipients=APPROVERS,
                template_key="breakglass.expired",
                params={"grant_id": grant["id"]},
                resource_id=grant["id"],
                dedupe_key=f"breakglass.expired:{grant['id']}",
            )
            expired += 1
        for grant in repo.list_stale_requests(session, at - pending_ttl()):
            repo.update_grant(session, grant["id"], {"status": "expired"})
            audit.record(
                session,
                action="breakglass.request_expired",
                resource_type=RESOURCE,
                resource_id=grant["id"],
                summary={"platform_request_id": grant["platform_request_id"]},
                actor_type="system",
            )
            stale += 1
    if expired or stale:
        log.info("breakglass.expired", count=expired + stale)
    return {"expired": expired, "stale_requests": stale, "reported": report_outcomes(tenant_id)}


# The authz resolver checks the grant of every support request (ADR-0023).
register_support_grant_check(support_grant_active)

__all__ = [
    "SyncResult",
    "approve",
    "deny",
    "get_grant",
    "list_grants",
    "pending_ttl",
    "refresh_requests",
    "report_outcomes",
    "revoke",
    "start_support_session",
    "support_grant_active",
    "sweep_interval_seconds",
    "sweep_school",
    "sync_school",
]
