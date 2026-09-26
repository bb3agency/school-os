"""FastAPI dependencies: ``require()``, ``require_principal()``, ``get_user_context``, DB.

Every tenant route declares ``Depends(require("<permission>", scope=..., step_up=...))``
(CLAUDE.md §6.2, SEC-003). The dependency object carries ``sos_permission`` (and
``sos_step_up``/``sos_scope``) so the route-enumeration test can check every route against the
catalog. Unknown or platform permissions fail when the route module is imported.

Order of checks per request: authenticate (401) -> resolve membership (403/409) -> permission
(403) -> scope (403) -> step-up (428). The route then opens exactly one transaction with
``TenantDB`` (``tenant_session(ctx.tenant_id, ctx.user_id)``), which commits before the response
is sent (dependency scope ``"function"``) and rolls back on any error.
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
from app.authz.resolver import AuthzResolver
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

    def __call__(self, principal: Annotated[Principal, Depends(get_principal)]) -> Principal:
        return principal


def require_principal() -> PrincipalRequirement:
    return PrincipalRequirement()


def tenant_db(ctx: Annotated[UserContext, Depends(get_user_context)]) -> Iterator[Session]:
    """One ``tenant_session`` per request: commit on success, roll back on error."""
    with tenant_session(ctx.tenant_id, ctx.user_id) as session:
        yield session


TenantDB = Annotated[Session, Depends(tenant_db, scope="function")]
"""Use as a parameter type; the transaction commits before the response is sent."""
