"""Break-glass requests as seen by the control plane (docs/16 §5.15, 07 §6.4; SEC-029).

M0: operators can list and record requests. Emergency access (legal obligation or active
security incident, no school approval) needs two confirmations by two DIFFERENT operators who
hold ``platform.breakglass.emergency`` (service check + DB CHECK). Creating the temporary
tenant-side grant (``ops.break_glass_grants``) and the school approval flow ship in M1.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.core.db import platform_session
from app.core.errors import Conflict, NotFound
from app.core.ids import new_id
from app.platform import models as m
from app.platform import repository as repo
from app.platform.common import Actor, audit_platform, db_errors, now
from app.platform.schemas import BreakGlassIn, BreakGlassOut


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
