"""Operator authorization for control-plane routes (FR-PLT-028, SEC-027; docs/16 §6).

``require_platform("platform.<...>")`` is the FastAPI dependency every ``/api/v1/platform/*``
route declares. It

1. authenticates the operator token (separate OIDC client, MFA mandatory) through
   ``app.identity.principal.get_operator_principal`` (401 / 403 ``mfa_required``);
2. applies the per-operator and per-route rate limits (429, ``app.core.ratelimit``), and refuses
   while this operator subject is in sign-in backoff from this address;
3. maps the token subject to an active ``platform.operators`` row (an invited operator is
   activated on first MFA sign-in; unknown or deactivated -> 403, counted as a failed sign-in);
4. checks the permission against the role matrix in ``permissions.yaml`` (403);
5. requires step-up (MFA within 5 minutes) for permissions marked step_up (428).

The dependency object carries ``sos_permission`` so the route-enumeration test recognises it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, Request

from app.audit import service as audit
from app.core import ratelimit
from app.core.db import platform_session
from app.core.errors import Forbidden
from app.core.logging import get_logger
from app.identity.principal import Principal, get_operator_principal, require_recent_auth
from app.platform import models as m
from app.platform import repository as repo
from app.platform.permissions import catalog

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class OperatorContext:
    """The authenticated operator for one request. IDs only; no personal data."""

    operator_id: uuid.UUID
    roles: frozenset[str]
    permissions: frozenset[str]
    auth_time: datetime | None
    request_id: str | None

    def has(self, permission: str) -> bool:
        return permission in self.permissions

    @property
    def step_up_fresh(self) -> bool:
        if self.auth_time is None:
            return False
        age = datetime.now(UTC) - self.auth_time
        return timedelta(seconds=-30) <= age <= timedelta(seconds=catalog().step_up_max_age_seconds)


def _request_id(request: Request) -> str | None:
    value = getattr(request.state, "request_id", None)
    return value if isinstance(value, str) else None


def load_operator(principal: Principal, request_id: str | None) -> OperatorContext:
    """Resolve the operator row and roles for a verified operator principal."""
    with platform_session() as session:
        row = repo.operator_by_subject(session, principal.subject)
        if row is None or row["status"] == "deactivated":
            log.warning("platform.operator.denied", outcome="unknown_or_deactivated")
            raise Forbidden("This account is not an active SchoolOS operator.", code="not_operator")
        if row["status"] == "invited":
            # First sign-in with MFA (get_operator_principal already refused non-MFA tokens).
            repo.update_row(
                session,
                m.operators,
                row["id"],
                {"status": "active", "mfa_enrolled": True, "last_login_at": datetime.now(UTC)},
            )
            audit.record_platform(
                session,
                action="operator.activated",
                resource_type="operator",
                resource_id=row["id"],
                summary={"operator_id": str(row["id"])},
                actor_type="operator",
                actor_id=row["id"],
                request_id=request_id,
            )
        roles = frozenset(repo.operator_roles(session, row["id"]))
    return OperatorContext(
        operator_id=row["id"],
        roles=roles,
        permissions=catalog().permissions_for(roles),
        auth_time=principal.auth_time,
        request_id=request_id,
    )


class RequirePlatform:
    """Callable dependency; one instance per route declaration."""

    def __init__(self, permission: str, *, step_up: bool | None, any_of: tuple[str, ...]) -> None:
        cat = catalog()
        keys = (permission, *any_of)
        for key in keys:
            if key not in cat.permissions:
                raise ValueError(f"unknown platform permission {key}")
        self.permissions = keys
        # Route-enumeration contract (shared with authz.require): one catalog key per guard.
        self.sos_permission = permission
        self.sos_any_of = any_of
        self.sos_scope = None
        # None = follow the catalog; an explicit False is only used on read routes that
        # docs/16 §8 lists without the step-up marker.
        self.step_up = step_up if step_up is not None else cat.permissions[permission].step_up
        self.sos_step_up = self.step_up

    def __repr__(self) -> str:
        return f"require_platform({self.sos_permission!r}, step_up={self.step_up})"

    def __call__(
        self,
        request: Request,
        principal: Annotated[Principal, Depends(get_operator_principal)],
    ) -> OperatorContext:
        key = ratelimit.principal_key(principal.kind, principal.issuer, principal.subject)
        ratelimit.enforce(
            request, principal=key, tenant_id=None, layer="operator", subject_ip_block=True
        )
        try:
            ctx = load_operator(principal, _request_id(request))
        except Forbidden as refused:
            ratelimit.record_auth_failure(request, reason=refused.code, principal=key)
            raise
        if not ctx.permissions.intersection(self.permissions):
            log.info("platform.authz.denied", action=self.permissions[0], outcome="forbidden")
            raise Forbidden()
        if self.step_up:
            require_recent_auth(principal, timedelta(seconds=catalog().step_up_max_age_seconds))
        return ctx


def require_platform(
    permission: str, *, step_up: bool | None = None, any_of: tuple[str, ...] = ()
) -> RequirePlatform:
    """FastAPI dependency: ``Depends(require_platform("platform.tenants.read"))``.

    ``any_of`` lists alternative permissions (read screens reachable from two roles).
    Step-up follows the catalog for ``permission`` unless ``step_up`` is given explicitly.
    """
    return RequirePlatform(permission, step_up=step_up, any_of=any_of)
