"""FastAPI dependencies: ``require()``, ``require_any()``, ``require_principal()``,
``get_user_context``, DB.

Every tenant route declares ``Depends(require("<permission>", scope=..., step_up=...))``
(CLAUDE.md §6.2, SEC-003), or ``Depends(require_any("<permission>", "<alternative>", ...))``
for a read that any one of several permissions may use. The dependency object carries
``sos_permission`` (and ``sos_step_up``/``sos_scope``, plus ``sos_any_of`` for ``require_any``)
so the route-enumeration test can check every route against the catalog; ``require_any`` takes
``step_up=True`` when every listed permission is a step-up permission. Unknown or platform
permissions fail when the route module is imported.

Order of checks per request: authenticate (401) -> resolve membership (403/409) -> rate limits
per person, school and route (429, ``app.core.ratelimit.enforce``; the per-IP layer ran in the
middleware) -> permission (403) -> scope (403) -> step-up (428). Refused calls count too.
The route then opens exactly one transaction with ``TenantDB``
(``tenant_session(ctx.tenant_id, ctx.user_id)``), which commits before the response is sent
(dependency scope ``"function"``) and rolls back on any error.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from functools import lru_cache
from typing import Annotated, Final, Literal

from fastapi import Depends, Request
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.authz import breakglass_guard
from app.authz.catalog import AUTHENTICATED, CatalogError, tenant_permission
from app.authz.context import UserContext
from app.authz.resolver import AuthzResolver, RouteKey
from app.core import ratelimit
from app.core.db import tenant_session
from app.core.errors import BadRequest, Forbidden
from app.core.logging import bind_context
from app.identity.principal import Principal, get_principal, require_recent_auth

ACTIVE_TENANT_HEADER: Final = "X-Active-Tenant"
ScopeRule = Literal["school"]


@lru_cache(maxsize=1)
def get_resolver() -> AuthzResolver:
    return AuthzResolver()


def tenant_hint(request: Request) -> uuid.UUID | None:
    raw = request.headers.get(ACTIVE_TENANT_HEADER)
    if raw is None or raw == "":
        return None
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise BadRequest(
            "X-Active-Tenant must be a school id.", code="invalid_active_tenant"
        ) from exc


def request_id_of(request: Request) -> str | None:
    value = getattr(request.state, "request_id", None)
    return value if isinstance(value, str) else None


def limit(
    request: Request, principal: Principal, tenant_id: uuid.UUID | None, *, sign_in: bool = False
) -> None:
    """Rate-limit layers 2-4 for this caller (docs/09 §2.7). ``sign_in`` also refuses while the
    person is in sign-in backoff from this address (ASVS 2.2.1)."""
    ratelimit.enforce(
        request,
        principal=ratelimit.principal_key(principal.kind, principal.issuer, principal.subject),
        tenant_id=tenant_id,
        layer="user",
        subject_ip_block=sign_in,
    )


def route_of(request: Request) -> RouteKey | None:
    """The matched route template (e.g. ``/api/v1/tenant/billing``); ``None`` if unknown.

    Used only for the suspended-school allowlist (``resolver.SUSPENDED_SCHOOL_ALLOWLIST``);
    an unknown route is refused there (fails closed)."""
    path = getattr(request.scope.get("route"), "path", None)
    return RouteKey(request.method, path) if isinstance(path, str) else None


async def get_user_context(
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
    resolver: Annotated[AuthzResolver, Depends(get_resolver)],
) -> UserContext:
    """Resolve once per request (FastAPI caches it) and bind tenant/user into the log context.

    ``async`` so the log-context binding is visible to the (threadpool) route handler; the
    database work runs in the threadpool.
    """
    ctx = await run_in_threadpool(
        resolver.resolve,
        principal,
        tenant_hint=tenant_hint(request),
        request_id=request_id_of(request),
        route=route_of(request),
    )
    bind_context(tenant_id=ctx.tenant_id, user_id=ctx.user_id)
    return ctx


class Requirement:
    """Callable dependency returned by :func:`require`."""

    def __init__(self, permission: str, *, scope: ScopeRule | None, step_up: bool) -> None:
        pdef = tenant_permission(permission)
        if step_up and not pdef.step_up:
            raise CatalogError(f"{permission} is not a step-up permission")
        if scope not in (None, "school"):
            raise CatalogError(f"unknown scope rule {scope!r}")
        self.sos_permission = permission
        self.sos_scope = scope
        self.sos_step_up = step_up

    def __repr__(self) -> str:
        p, s, u = self.sos_permission, self.sos_scope, self.sos_step_up
        return f"require({p!r}, scope={s!r}, step_up={u})"

    def __call__(
        self,
        request: Request,
        ctx: Annotated[UserContext, Depends(get_user_context)],
        principal: Annotated[Principal, Depends(get_principal)],
    ) -> UserContext:
        limit(request, principal, ctx.tenant_id)
        if not ctx.has(self.sos_permission):
            raise Forbidden()
        if self.sos_scope == "school" and not ctx.scope_for(self.sos_permission).school_wide:
            raise Forbidden()
        if self.sos_step_up:
            require_recent_auth(principal)
        # Break-glass sessions: read-only and every call recorded for the school (07 §6.4).
        breakglass_guard.enforce(ctx, request, self.sos_permission)
        return ctx


def require(
    permission: str, *, scope: ScopeRule | None = None, step_up: bool = False
) -> Requirement:
    """Route guard: the caller's active membership must hold ``permission``.

    ``scope="school"`` additionally refuses holders whose grant is limited to classes/sections
    (07 §6.2 "S"); without it, scoped holders pass and the handler filters objects with
    ``app.authz.scope``. ``step_up=True`` requires MFA within 5 minutes (428).
    """
    return Requirement(permission, scope=scope, step_up=step_up)


class AnyOfRequirement(Requirement):
    """Callable dependency returned by :func:`require_any`.

    Satisfied by ``sos_permission`` or any of ``sos_any_of``; no scope rule. With
    ``step_up=True`` (every listed permission must be a step-up permission in the catalog) the
    caller also needs MFA within 5 minutes (428), whichever permission they hold. The
    route-enumeration and authorization-matrix tests read ``sos_any_of`` and ``sos_step_up``.
    """

    def __init__(self, permission: str, *, any_of: tuple[str, ...], step_up: bool = False) -> None:
        super().__init__(permission, scope=None, step_up=step_up)
        for alternative in any_of:
            adef = tenant_permission(alternative)  # unknown or platform permissions fail at import
            if step_up and not adef.step_up:
                raise CatalogError(f"{alternative} is not a step-up permission")
        self.sos_any_of = any_of

    def __repr__(self) -> str:
        listed = ", ".join(map(repr, (self.sos_permission, *self.sos_any_of)))
        return (
            f"require_any({listed}, step_up=True)" if self.sos_step_up else f"require_any({listed})"
        )

    def __call__(
        self,
        request: Request,
        ctx: Annotated[UserContext, Depends(get_user_context)],
        principal: Annotated[Principal, Depends(get_principal)],
    ) -> UserContext:
        limit(request, principal, ctx.tenant_id)
        held = next((p for p in (self.sos_permission, *self.sos_any_of) if ctx.has(p)), None)
        if held is None:
            raise Forbidden()
        if self.sos_step_up:
            require_recent_auth(principal)
        # Break-glass: read-only, recorded under the first listed permission the caller holds.
        breakglass_guard.enforce(ctx, request, held)
        return ctx


def require_any(permission: str, *alternatives: str, step_up: bool = False) -> AnyOfRequirement:
    """Route guard: the caller's active membership must hold ``permission`` or one of
    ``alternatives`` (e.g. a read screen shared by the maker and the checker).

    Unlike :func:`require` it has no scope rule: scoped holders pass and the service filters
    objects by scope (404 outside it). Use it only where the service checks the particular
    permission again per object or per request (e.g. ``export.board`` for board profiles).
    ``step_up=True`` requires MFA within 5 minutes (428) and is allowed only when every listed
    permission is a step-up permission (e.g. creating any pre-check export, ADR-0021).
    """
    return AnyOfRequirement(permission, any_of=alternatives, step_up=step_up)


class PrincipalRequirement:
    """Authenticated caller without a resolved school (choosing the active school, login event).

    Carries ``sos_permission = "session.authenticated"`` like every member's implicit grant.
    """

    sos_permission = AUTHENTICATED
    sos_scope = None
    sos_step_up = False
    sos_tenantless = True

    def __repr__(self) -> str:
        return "require_principal()"

    def __call__(
        self, request: Request, principal: Annotated[Principal, Depends(get_principal)]
    ) -> Principal:
        # No school yet: per person and per route (the sign-in budgets), plus sign-in backoff.
        limit(request, principal, None, sign_in=True)
        return principal


def require_principal() -> PrincipalRequirement:
    return PrincipalRequirement()


def tenant_db(ctx: Annotated[UserContext, Depends(get_user_context)]) -> Iterator[Session]:
    """One ``tenant_session`` per request: commit on success, roll back on error."""
    with tenant_session(ctx.tenant_id, ctx.user_id) as session:
        yield session


TenantDB = Annotated[Session, Depends(tenant_db, scope="function")]
"""Use as a parameter type; the transaction commits before the response is sent."""
