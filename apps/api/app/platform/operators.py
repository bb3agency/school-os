"""Operators and platform roles (FR-PLT-028; docs/16 §5.16).

Rules: invite with roles (the account activates on first MFA sign-in); an operator cannot
change their own roles or deactivate themselves; at least one active ``platform_owner`` must
remain. All changes are audited in the platform chain.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy.orm import Session

from app.core.db import platform_session
from app.core.errors import Conflict, NotFound
from app.core.ids import new_id
from app.platform import models as m
from app.platform import repository as repo
from app.platform.common import Actor, audit_platform, clamp_limit, db_errors, now, parse_cursor
from app.platform.schemas import OperatorInvite, OperatorOut


def _out(session: Session, row: Mapping[Any, Any]) -> OperatorOut:
    data = dict(row)
    data["roles"] = sorted(repo.operator_roles(session, row["id"]))
    return OperatorOut.model_validate(data)


def list_operators(
    limit: int = 50, cursor: str | None = None
) -> tuple[list[OperatorOut], str | None]:
    limit = clamp_limit(limit)
    with platform_session() as s:
        rows = repo.list_rows(s, m.operators, limit=limit, cursor=parse_cursor(cursor))
        roles = repo.roles_by_operator(s, [r["id"] for r in rows[:limit]])
        items = [
            OperatorOut.model_validate({**dict(r), "roles": roles.get(r["id"], [])})
            for r in rows[:limit]
        ]
    return items, (str(rows[limit - 1]["id"]) if len(rows) > limit else None)


def get_operator(operator_id: uuid.UUID) -> OperatorOut:
    with platform_session() as s:
        row = repo.get(s, m.operators, operator_id)
        if row is None:
            raise NotFound("Operator not found")
        return _out(s, row)


def invite_operator(actor: Actor, data: OperatorInvite) -> OperatorOut:
    with platform_session() as s, db_errors():
        row = repo.insert_row(
            s,
            m.operators,
            {
                "id": new_id(),
                "idp_subject": data.idp_subject,
                "email": data.email,
                "display_name": data.display_name,
                "status": "invited",
                "mfa_enrolled": False,
                "invited_by": actor.operator_id,
            },
        )
        roles = sorted(set(data.roles))
        repo.set_operator_roles(s, row["id"], roles, actor.operator_id)
        audit_platform(s, actor, "operator.invited", "operator", row["id"], {"roles": list(roles)})
        return _out(s, row)


def set_roles(actor: Actor, operator_id: uuid.UUID, roles: Sequence[str]) -> OperatorOut:
    if operator_id == actor.operator_id:
        raise Conflict("You cannot change your own roles.", code="own_roles")
    wanted = sorted(set(roles))
    with platform_session() as s, db_errors():
        owners = repo.active_owner_ids(s, lock=True)
        row = repo.get(s, m.operators, operator_id, for_update=True)
        if row is None:
            raise NotFound("Operator not found")
        before = sorted(repo.operator_roles(s, operator_id))
        if operator_id in owners and "platform_owner" not in wanted and len(owners) <= 1:
            raise Conflict("At least one active platform owner must remain.", code="last_owner")
        repo.set_operator_roles(s, operator_id, wanted, actor.operator_id)
        audit_platform(
            s,
            actor,
            "operator.roles_changed",
            "operator",
            operator_id,
            {"before": before, "after": wanted},
        )
        return _out(s, row)


def deactivate(actor: Actor, operator_id: uuid.UUID) -> OperatorOut:
    if operator_id == actor.operator_id:
        raise Conflict("You cannot deactivate yourself.", code="own_account")
    with platform_session() as s, db_errors():
        owners = repo.active_owner_ids(s, lock=True)
        row = repo.get(s, m.operators, operator_id, for_update=True)
        if row is None:
            raise NotFound("Operator not found")
        if row["status"] == "deactivated":
            return _out(s, row)
        if operator_id in owners and len(owners) <= 1:
            raise Conflict("At least one active platform owner must remain.", code="last_owner")
        row = repo.update_row(
            s, m.operators, operator_id, {"status": "deactivated", "deactivated_at": now()}
        )
        audit_platform(s, actor, "operator.deactivated", "operator", operator_id, {})
        return _out(s, row)


def bootstrap_owner(*, subject: str, email: str, display_name: str) -> uuid.UUID:
    """First deploy only: create the first active ``platform_owner`` (refused if one exists)."""
    with platform_session() as s, db_errors():
        if repo.active_owner_ids(s, lock=True):
            raise Conflict("An active platform owner already exists.", code="owner_exists")
        row = repo.insert_row(
            s,
            m.operators,
            {
                "id": new_id(),
                "idp_subject": subject,
                "email": email,
                "display_name": display_name,
                "status": "active",
                # The operator user pool enforces MFA for every account (ADR-0018).
                "mfa_enrolled": True,
            },
        )
        repo.set_operator_roles(s, row["id"], ["platform_owner"], None)
        audit_platform(
            s,
            Actor(None),
            "operator.bootstrapped",
            "operator",
            row["id"],
            {"roles": ["platform_owner"]},
        )
        return uuid.UUID(str(row["id"]))
