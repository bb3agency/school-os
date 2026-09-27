"""Session-level authz operations used by the ``/me`` routes (FR-IAM-005, FR-IAM-013, US-101).

- :func:`switch_active_tenant`: validate that the caller may work in a school (the BFF then sends
  it as ``X-Active-Tenant``); same checks as every request.
- :func:`record_login`: the BFF calls ``POST /me/login-event`` once after the OIDC callback. It
  resolves the membership and audits ``auth.login.succeeded`` or, when the school refuses the
  session (suspended school, MFA missing), ``auth.login.denied`` in that school's own chain.
  Rate-limited per user.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Final

from app.authz.context import UserContext
from app.authz.kv import KVUnavailable, kv_store
from app.authz.resolver import ME_ACTIVE_TENANT, ME_LOGIN_EVENT, AccessDenied, AuthzResolver
from app.core.errors import RateLimited
from app.core.logging import get_logger
from app.identity import service as identity
from app.identity.principal import Principal

LOGIN_EVENTS_PER_MINUTE: Final = 10
log = get_logger(__name__)


def _rate_limit(subject: str, *, limit: int, window_s: int) -> None:
    digest = hashlib.sha256(subject.encode()).hexdigest()[:32]
    try:
        count = kv_store().incr(f"sos:rl:login-event:{digest}", ttl_s=window_s)
    except KVUnavailable:
        log.warning("authz.rate_limit_unavailable")
        return  # fail open: the IdP throttles sign-ins itself (FR-IAM-005)
    if count > limit:
        raise RateLimited("Too many sign-in events. Wait a minute and try again.")


def switch_active_tenant(
    resolver: AuthzResolver,
    principal: Principal,
    tenant_id: uuid.UUID,
    *,
    request_id: str | None,
) -> tuple[UserContext, list[uuid.UUID]]:
    choices = resolver.choices(principal)
    ctx = resolver.context_for(
        principal,
        resolver.choose(choices, tenant_id),
        request_id=request_id,
        route=ME_ACTIVE_TENANT,
    )
    return ctx, [c.tenant_id for c in choices]


def _issuer_kind(principal: Principal) -> str | None:
    """``operator_support`` for SchoolOS support sign-ins (ADR-0023); staff: not recorded."""
    return "operator_support" if principal.kind == "support" else None


def record_login(
    resolver: AuthzResolver,
    principal: Principal,
    *,
    tenant_hint: uuid.UUID | None,
    request_id: str | None,
) -> UserContext:
    _rate_limit(principal.subject, limit=LOGIN_EVENTS_PER_MINUTE, window_s=60)
    choice = resolver.choose(resolver.choices(principal), tenant_hint)
    try:
        ctx = resolver.context_for(principal, choice, request_id=request_id, route=ME_LOGIN_EVENT)
    except AccessDenied as denied:
        identity.record_login_event(
            choice.tenant_id,
            choice.user_id,
            choice.membership_id,
            succeeded=False,
            reason=denied.code,
            request_id=request_id,
            session_id_present=principal.session_id is not None,
            issuer_kind=_issuer_kind(principal),
        )
        raise
    identity.record_login_event(
        ctx.tenant_id,
        ctx.user_id,
        ctx.membership_id,
        succeeded=True,
        request_id=request_id,
        session_id_present=principal.session_id is not None,
        issuer_kind=_issuer_kind(principal),
    )
    return ctx
