"""Guard for break-glass (``platform_support``) sessions (docs/07 §6.4, US-103 AC2, T17).

Called by every ``require()`` guard once the caller's context is resolved. For a membership that
holds the temporary ``platform_support`` role:

1. **Read-only.** Only safe methods (GET/HEAD) pass; state-changing calls get 403
   ``breakglass_read_only`` even if a read permission guards them. Routes guarded only by the
   implicit ``session.authenticated`` (the caller's own session, profile and notifications) stay
   usable.
2. **Visible to the school.** Each guarded call is written to the school's own audit chain as
   ``breakglass.access`` with ``via_breakglass: true``, the route template, method, permission
   and the UUIDs in the path, in its own short transaction so the record survives even when the
   request later fails.
"""

from __future__ import annotations

import uuid
from typing import Any, Final

from fastapi import Request

from app.audit import service as audit
from app.authz.catalog import AUTHENTICATED
from app.authz.context import UserContext
from app.core.db import tenant_session
from app.core.errors import Forbidden

SAFE_METHODS: Final = frozenset({"GET", "HEAD"})
ACCESS_ACTION: Final = "breakglass.access"


def _route_template(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else "unknown"


def _path_ids(request: Request) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in request.path_params.items():
        try:
            out[str(key)] = str(uuid.UUID(str(value)))
        except ValueError:
            continue
    return out


def enforce(ctx: UserContext, request: Request, permission: str) -> None:
    """Refuse writes and record the access (no-op for ordinary memberships)."""
    if not ctx.via_breakglass:
        return
    allowed = request.method in SAFE_METHODS or permission == AUTHENTICATED
    ids = _path_ids(request)
    summary: dict[str, Any] = {
        "via_breakglass": True,
        "method": request.method,
        "route": _route_template(request)[:200],
        "permission": permission,
        "membership_id": ctx.membership_id,
        "outcome": "allowed" if allowed else "refused_read_only",
    }
    if ids:
        summary["path_ids"] = ids
    first = next(iter(ids.values()), None)
    with tenant_session(ctx.tenant_id, ctx.user_id) as session:
        audit.record(
            session,
            action=ACCESS_ACTION,
            resource_type="api_route",
            resource_id=uuid.UUID(first) if first else None,
            summary=summary,
            actor_type="user",
            actor_id=ctx.user_id,
            request_id=ctx.request_id,
        )
    if not allowed:
        raise Forbidden(
            "SchoolOS support access is read-only. Ask the school to make this change.",
            code="breakglass_read_only",
        )
