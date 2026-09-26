"""Authenticated principal: FastAPI dependencies for TB2 (docs/07 §3, docs/04 §5 step 2).

Every API request must carry BOTH
  * ``X-Service-Token``: the BFF service token (``service_token.py``), and
  * ``Authorization: Bearer <access token>``: the user's OIDC access token (``tokens.py``).
Missing or invalid -> 401 problem+json; IdP unreachable -> 503.

This module answers "who is calling", nothing more. Tenant/membership resolution (docs/04 §5
step 3) needs ``core.resolve_login`` and lives in ``authz``, which implements
:class:`PrincipalResolver`. Step-up (SEC-005, docs/07 §5.2) is :func:`require_recent_auth`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Final, Literal, Protocol, TypeVar
from uuid import UUID

from fastapi import Depends, Request

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
    get_tenant_token_verifier,
)

PrincipalKind = Literal["user", "operator"]
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


def _authenticate(
    request: Request, tokens: TokenVerifier, service: ServiceTokenVerifier
) -> VerifiedToken:
    service_token = request.headers.get(SERVICE_TOKEN_HEADER)
    if not service_token:
        raise Unauthenticated(_MISSING_SERVICE_TOKEN)
    access_token = _bearer_token(request)
    # Service token first: an HMAC check, no network, so junk never reaches the JWKS cache.
    service.verify(service_token)
    return tokens.verify(access_token)


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
) -> Principal:
    """School staff (tenant user pool / app client)."""
    return _principal(_authenticate(request, tokens, service), "user")


def get_operator_principal(
    request: Request,
    tokens: Annotated[TokenVerifier, Depends(get_platform_token_verifier)],
    service: Annotated[ServiceTokenVerifier, Depends(get_service_token_verifier)],
) -> Principal:
    """Platform operators (separate pool/app client). MFA is mandatory for every operator
    (FR-IAM-002, contract §5), so a token without an MFA signal is refused outright."""
    principal = _principal(_authenticate(request, tokens, service), "operator")
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
