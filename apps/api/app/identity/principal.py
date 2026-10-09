"""Authenticated principal: FastAPI dependencies for TB2 (docs/07 §3, docs/04 §5 step 2).

Every API request must carry BOTH
  * ``X-Service-Token``: the BFF service token (``service_token.py``), and
  * ``Authorization: Bearer <access token>``: the user's OIDC access token (``tokens.py``).
Missing or invalid -> 401 problem+json; IdP unreachable -> 503.

This module answers "who is calling", nothing more. Tenant/membership resolution (docs/04 §5
step 3) needs ``core.resolve_login`` and lives in ``authz``, which implements
:class:`PrincipalResolver`. Step-up (SEC-005, docs/07 §5.2) is :func:`require_recent_auth`.

Tenant routes accept two token families (ADR-0023 option C): staff-pool tokens (kind ``user``)
and, only when ``SOS_SUPPORT_OIDC_AUDIENCE`` is set, tokens of the dedicated support app client
of the OPERATOR pool (kind ``support``; MFA always). The operator admin client is never accepted
here, and the support client is never accepted on ``/api/v1/platform/*``. What a support
principal may reach is decided by membership resolution: only unexpired ``platform_support``
memberships with an active break-glass grant.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Final, Literal, Protocol, TypeVar
from uuid import UUID

from fastapi import Depends, Request

from app.core import ratelimit
from app.core.errors import Forbidden, StepUpRequired, Unauthenticated
from app.identity.service_token import (
    SERVICE_TOKEN_HEADER,
    ServiceTokenVerifier,
    get_service_token_verifier,
)
from app.identity.tokens import (
    TokenVerifier,
    VerifiedToken,
    get_platform_token_verifier,
    get_support_token_verifier,
    get_tenant_token_verifier,
    unverified_issuer,
)

PrincipalKind = Literal["user", "operator", "support"]
STEP_UP_MAX_AGE: Final = timedelta(minutes=5)
_MISSING_SERVICE_TOKEN = "Requests must come through the SchoolOS web app"  # noqa: S105
_MISSING_BEARER = "Sign in required"


@dataclass(frozen=True, slots=True)
class Principal:
    """Who is calling. ``subject`` is the IdP's opaque ``sub`` (an ID, not personal data)."""

    subject: str
    issuer: str
    kind: PrincipalKind
    auth_time: datetime | None
    mfa: bool
    session_id: str | None
    expires_at: datetime


T_co = TypeVar("T_co", covariant=True)


class PrincipalResolver(Protocol[T_co]):
    """Implemented by ``authz``: map a principal to an active membership / user context.

    Intended flow (README): ``core.resolve_login(principal.subject)`` (SECURITY DEFINER) returns
    the user's active memberships; pick ``tenant_hint`` (the BFF's active tenant) or the only
    membership; reject suspended tenants/users with 401/403; then open ``tenant_session``.
    """

    def resolve(self, principal: Principal, *, tenant_hint: UUID | None) -> T_co: ...


def _bearer_token(request: Request) -> str:
    header = request.headers.get("authorization")
    if header is None:
        raise Unauthenticated(_MISSING_BEARER)
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token or " " in token.strip() or token != token.strip():
        raise Unauthenticated(_MISSING_BEARER)
    return token


def _checked_bearer(request: Request, service: ServiceTokenVerifier) -> str:
    """The bearer access token (unverified) once the BFF service token has been checked."""
    service_token = request.headers.get(SERVICE_TOKEN_HEADER)
    if not service_token:
        raise Unauthenticated(_MISSING_SERVICE_TOKEN)
    access_token = _bearer_token(request)
    # Service token first: an HMAC check, no network, so junk never reaches the JWKS cache.
    service.verify(service_token)
    return access_token


def _authenticate(
    request: Request, tokens: TokenVerifier, service: ServiceTokenVerifier
) -> VerifiedToken:
    return tokens.verify(_checked_bearer(request, service))


def _failed(request: Request, refused: Unauthenticated) -> None:
    """A rejected service or access token (``security.auth.failed``; counted per client IP for
    the backoff in the rate-limit middleware). An expired access token is routine (the BFF
    refreshes it) and does not count."""
    if refused.code != "token_expired":
        ratelimit.record_auth_failure(request, reason="token_rejected")


def _principal(token: VerifiedToken, kind: PrincipalKind) -> Principal:
    return Principal(
        subject=token.subject,
        issuer=token.issuer,
        kind=kind,
        auth_time=token.auth_time,
        mfa=token.mfa,
        session_id=token.session_id,
        expires_at=token.expires_at,
    )


def get_principal(
    request: Request,
    tokens: Annotated[TokenVerifier, Depends(get_tenant_token_verifier)],
    service: Annotated[ServiceTokenVerifier, Depends(get_service_token_verifier)],
    support: Annotated[TokenVerifier | None, Depends(get_support_token_verifier)],
) -> Principal:
    """Callers of tenant routes: school staff (tenant user pool / app client), or SchoolOS
    support during break-glass (support app client of the operator pool, ADR-0023).

    The unverified ``iss`` only picks the verifier; that verifier checks signature, issuer,
    audience/client and lifetime. Support principals must carry the MFA claim (the operator
    pool enforces MFA; the API checks it again)."""
    try:
        access_token = _checked_bearer(request, service)
        if (
            support is not None
            and support.issuer != tokens.issuer
            and unverified_issuer(access_token) == support.issuer
        ):
            principal = _principal(support.verify(access_token), "support")
        else:
            return _principal(tokens.verify(access_token), "user")
    except Unauthenticated as refused:
        _failed(request, refused)
        raise
    if not principal.mfa:
        raise Forbidden("SchoolOS support must sign in with MFA", code="mfa_required")
    return principal


def get_operator_principal(
    request: Request,
    tokens: Annotated[TokenVerifier, Depends(get_platform_token_verifier)],
    service: Annotated[ServiceTokenVerifier, Depends(get_service_token_verifier)],
) -> Principal:
    """Platform operators (separate pool/app client). MFA is mandatory for every operator
    (FR-IAM-002, contract §5), so a token without an MFA signal is refused outright."""
    try:
        principal = _principal(_authenticate(request, tokens, service), "operator")
    except Unauthenticated as refused:
        _failed(request, refused)
        raise
    if not principal.mfa:
        raise Forbidden("Operators must sign in with MFA", code="mfa_required")
    return principal


def require_recent_auth(
    principal: Principal,
    max_age: timedelta = STEP_UP_MAX_AGE,
    *,
    now: datetime | None = None,
) -> None:
    """Step-up (SEC-005): MFA-backed sign-in within ``max_age`` or 428 ``step_up_required``.

    The BFF reacts to 428 by sending the user back through the IdP with ``prompt=login``
    (Cognito managed login supports it), which yields a fresh ``auth_time``.
    """
    current = now or datetime.now(UTC)
    auth_time = principal.auth_time
    if not principal.mfa or auth_time is None:
        raise StepUpRequired()
    age = current - auth_time
    if age > max_age or age < -timedelta(seconds=30):
        raise StepUpRequired()


def recent_auth(max_age: timedelta = STEP_UP_MAX_AGE) -> Callable[[Principal], Principal]:
    """Dependency factory: ``Depends(recent_auth())`` on routes that need step-up."""

    def dependency(principal: Annotated[Principal, Depends(get_principal)]) -> Principal:
        require_recent_auth(principal, max_age)
        return principal

    return dependency
