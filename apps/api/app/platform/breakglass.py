"""Break-glass requests as seen by the control plane (docs/16 §5.15, 07 §6.4; SEC-029).

M0: operators can list and record requests. Emergency access (legal obligation or active
security incident, no school approval) needs two confirmations by two DIFFERENT operators who
hold ``platform.breakglass.emergency`` (service check + DB CHECK).

M1 (US-103, FR-OPS-004): the school side (``app.breakglass``) PULLS this school's open requests
(:func:`requests_for_school`) into its own ``ops.break_glass_grants``, decides there (approval
with step-up, denial, revocation, expiry) and reports each outcome back with
:func:`record_school_outcome`, which updates the request status and writes the control-plane
audit chain. The control plane never writes tenant tables.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Final

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import platform_session
from app.core.errors import Conflict, NotFound
from app.core.ids import new_id
from app.core.logging import get_logger
from app.platform import models as m
from app.platform import repository as repo
from app.platform.common import SYSTEM, Actor, audit_platform, db_errors, now
from app.platform.schemas import BreakGlassIn, BreakGlassOut

log = get_logger(__name__)

# Status changes the school side may report (request status -> allowed next statuses).
_SCHOOL_TRANSITIONS: Final[dict[str, frozenset[str]]] = {
    "requested": frozenset({"active", "denied", "expired"}),
    "approved": frozenset({"active", "expired", "revoked"}),  # emergency (two operators)
    "active": frozenset({"expired", "revoked"}),
}


class SchoolBreakGlassRequest(BaseModel):
    """What the school needs to decide on a request: the request and who would get access.

    ``operator_*`` identify the requesting operator (an adult SchoolOS employee): the school
    sees who asks, and an approval gives that identity (``operator_issuer`` = the operator
    pool, ``operator_subject``; ADR-0023) a temporary membership.
    """

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    requested_by: uuid.UUID
    reason_code: str
    reason: str
    scope: dict[str, Any]
    duration_minutes: int
    emergency: bool
    status: str
    created_at: dt.datetime
    emergency_confirmed_at: dt.datetime | None
    operator_subject: str | None
    operator_issuer: str
    operator_display_name: str
    operator_email: str | None
    operator_status: str


def _school_requests(
    tenant_id: uuid.UUID, request_id: uuid.UUID | None = None
) -> list[SchoolBreakGlassRequest]:
    r, o = m.breakglass_requests, m.operators
    stmt = (
        select(
            r.c.id,
            r.c.tenant_id,
            r.c.requested_by,
            r.c.reason_code,
            r.c.reason,
            r.c.scope,
            r.c.duration_minutes,
            r.c.emergency,
            r.c.status,
            r.c.created_at,
            r.c.emergency_confirmed_at,
            o.c.idp_subject.label("operator_subject"),
            o.c.display_name.label("operator_display_name"),
            o.c.email.label("operator_email"),
            o.c.status.label("operator_status"),
        )
        .join(o, o.c.id == r.c.requested_by)
        .where(r.c.tenant_id == tenant_id)
        .order_by(r.c.created_at, r.c.id)
    )
    if request_id is not None:
        stmt = stmt.where(r.c.id == request_id)
    else:
        stmt = stmt.where(r.c.status.in_(("requested", "approved"))).limit(200)
    # Operators sign in to schools through the support client of the operator pool.
    issuer = get_settings().resolved_support_issuer
    with platform_session() as s:
        return [
            SchoolBreakGlassRequest.model_validate({**dict(row), "operator_issuer": issuer})
            for row in s.execute(stmt).mappings()
        ]


def requests_for_school(tenant_id: uuid.UUID) -> list[SchoolBreakGlassRequest]:
    """This school's requests awaiting it: ``requested`` (needs the school's approval) and
    ``approved`` emergency requests (two operators confirmed; the school is told)."""
    return _school_requests(tenant_id)


def request_for_school(
    tenant_id: uuid.UUID, request_id: uuid.UUID
) -> SchoolBreakGlassRequest | None:
    """One request of this school (another school's ID answers ``None``)."""
    found = _school_requests(tenant_id, request_id)
    return found[0] if found else None


def record_school_outcome(
    tenant_id: uuid.UUID, request_id: uuid.UUID, status: str, *, grant_id: uuid.UUID
) -> bool:
    """Mirror the school's decision or the grant's end onto the request (idempotent).

    Returns True when the status changed (and ``breakglass.<status>`` was written to the
    control-plane audit chain); False when it already had that status, the request is not this
    school's, or the transition is not allowed.
    """
    with platform_session() as s, db_errors():
        row = repo.get(s, m.breakglass_requests, request_id, for_update=True)
        if row is None or row["tenant_id"] != tenant_id or row["status"] == status:
            return False
        if status not in _SCHOOL_TRANSITIONS.get(row["status"], frozenset()):
            log.warning(
                "breakglass.outcome_ignored",
                resource_type="breakglass_request",
                resource_id=request_id,
                outcome=status,
            )
            return False
        repo.update_row(
            s, m.breakglass_requests, request_id, {"status": status, "updated_at": now()}
        )
        audit_platform(
            s,
            SYSTEM,
            f"breakglass.{status}",
            "breakglass_request",
            request_id,
            {"grant_id": grant_id, "status": status, "source": "school"},
            tenant_id=tenant_id,
        )
        return True


def record_session_started(
    tenant_id: uuid.UUID,
    request_id: uuid.UUID,
    *,
    grant_id: uuid.UUID,
    session_ref: str | None,
) -> bool:
    """Write ``breakglass.session_started`` to the control-plane chain (ADR-0023 §4).

    Called by the school side when the requesting operator starts a support session in the
    school app. IDs only (no token, no names). Returns False when the request is not this
    school's (nothing is written)."""
    with platform_session() as s, db_errors():
        row = repo.get(s, m.breakglass_requests, request_id)
        if row is None or row["tenant_id"] != tenant_id:
            return False
        summary: dict[str, Any] = {"grant_id": grant_id, "source": "school"}
        if session_ref:
            summary["session_ref"] = session_ref
        audit_platform(
            s,
            Actor(row["requested_by"]),
            "breakglass.session_started",
            "breakglass_request",
            request_id,
            summary,
            tenant_id=tenant_id,
        )
        return True


def list_requests(tenant_id: uuid.UUID | None = None) -> list[BreakGlassOut]:
    with platform_session() as s:
        stmt = select(m.breakglass_requests).order_by(m.breakglass_requests.c.created_at.desc())
        if tenant_id:
            stmt = stmt.where(m.breakglass_requests.c.tenant_id == tenant_id)
        return [
            BreakGlassOut.model_validate(dict(r)) for r in s.execute(stmt.limit(200)).mappings()
        ]


def create_request(actor: Actor, data: BreakGlassIn) -> BreakGlassOut:
    with platform_session() as s, db_errors():
        if repo.get_by(s, m.deployments, m.deployments.c.tenant_id == data.tenant_id) is None:
            raise NotFound("School not found")
        row = repo.insert_row(
            s,
            m.breakglass_requests,
            {
                "id": new_id(),
                "tenant_id": data.tenant_id,
                "requested_by": actor.operator_id,
                "reason_code": data.reason_code,
                "reason": data.reason,
                "scope": {k: str(v) for k, v in data.scope.items()},
                "duration_minutes": data.duration_minutes,
                "emergency": data.emergency,
                "status": "requested",
            },
        )
        audit_platform(
            s,
            actor,
            "breakglass.requested",
            "breakglass_request",
            row["id"],
            {
                "reason_code": data.reason_code,
                "emergency": data.emergency,
                "duration_minutes": data.duration_minutes,
            },
            tenant_id=data.tenant_id,
        )
        return BreakGlassOut.model_validate(dict(row))


def emergency_confirm(actor: Actor, request_id: uuid.UUID) -> BreakGlassOut:
    """First call records confirmer 1; a second, different operator completes approval."""
    with platform_session() as s, db_errors():
        row = repo.get(s, m.breakglass_requests, request_id, for_update=True)
        if row is None:
            raise NotFound("Request not found")
        if not row["emergency"] or row["status"] != "requested":
            raise Conflict(
                "Only a pending emergency request can be confirmed.", code="invalid_state"
            )
        if row["emergency_confirmed_by_1"] is None:
            values: dict[str, object] = {"emergency_confirmed_by_1": actor.operator_id}
            step = 1
        elif row["emergency_confirmed_by_1"] == actor.operator_id:
            raise Conflict(
                "A different operator must give the second confirmation.", code="same_operator"
            )
        else:
            values = {
                "emergency_confirmed_by_2": actor.operator_id,
                "emergency_confirmed_at": now(),
                "status": "approved",
            }
            step = 2
        row = repo.update_row(s, m.breakglass_requests, request_id, values)
        audit_platform(
            s,
            actor,
            "breakglass.emergency_confirmed",
            "breakglass_request",
            request_id,
            {"confirmation": step},
            tenant_id=row["tenant_id"],
        )
        return BreakGlassOut.model_validate(dict(row))
