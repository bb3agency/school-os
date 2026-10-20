"""0062_messaging_finish: the database keeps a withdrawn notice withdrawn (R2-3J; FR-NOTICE-009;
invariant 12). Synthetic rows written by the admin engine in school A of the shared world."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

pytestmark = pytest.mark.db


def _notice(admin: Engine, tenant_id: uuid.UUID, user_id: uuid.UUID, status: str) -> uuid.UUID:
    notice_id = uuid.uuid4()
    approved = status != "draft"
    with admin.begin() as c:
        c.execute(
            text(
                "INSERT INTO ops.parent_notices (id, tenant_id, source, status, title_en, "
                "body_en, title_te, body_te, created_by, approved_by, approved_at) VALUES "
                "(:i, :t, 'blank', :s, 'Sports day', 'Sports day is on 14/11/2026.', "
                "'Sports day', 'Sports day is on 14/11/2026.', :u, :ab, :at)"
            ),
            {
                "i": notice_id,
                "t": tenant_id,
                "s": status,
                "u": user_id,
                "ab": user_id if approved else None,
                "at": dt.datetime.now(dt.UTC) if approved else None,
            },
        )
    return notice_id


def _withdraw(admin: Engine, notice_id: uuid.UUID, user_id: uuid.UUID, **extra: Any) -> None:
    values = {
        "status": "withdrawn",
        "withdrawn_by": user_id,
        "withdrawn_at": dt.datetime.now(dt.UTC),
        "withdraw_reason": "The date in this notice was wrong",
        **extra,
    }
    sets = ", ".join(f"{k} = :{k}" for k in values)
    with admin.begin() as c:
        c.execute(
            text(f"UPDATE ops.parent_notices SET {sets} WHERE id = :id"),
            {**values, "id": notice_id},
        )


def test_FR_NOTICE_009_withdrawn_is_final_and_only_from_approved(
    world: Any, admin_engine: Engine
) -> None:
    owner = world.a.people["owner"]
    approved = _notice(admin_engine, world.a.tenant_id, owner.user_id, "approved")
    _withdraw(admin_engine, approved, owner.user_id)
    # Withdrawn stays withdrawn, with its who, when and why.
    with pytest.raises(DBAPIError), admin_engine.begin() as c:
        c.execute(
            text(
                "UPDATE ops.parent_notices SET status = 'approved', withdrawn_by = NULL, "
                "withdrawn_at = NULL, withdraw_reason = NULL WHERE id = :i"
            ),
            {"i": approved},
        )
    with pytest.raises(DBAPIError):
        _withdraw(admin_engine, approved, owner.user_id, withdraw_reason="Another reason given")
    # A draft cannot become withdrawn.
    draft = _notice(admin_engine, world.a.tenant_id, owner.user_id, "draft")
    with pytest.raises(DBAPIError):
        _withdraw(admin_engine, draft, owner.user_id)
    # Withdrawn needs its reason (10..500 characters) and who withdrew it.
    other = _notice(admin_engine, world.a.tenant_id, owner.user_id, "approved")
    with pytest.raises(DBAPIError):
        _withdraw(admin_engine, other, owner.user_id, withdraw_reason="short")
    with pytest.raises(DBAPIError):
        _withdraw(admin_engine, other, owner.user_id, withdrawn_by=None)


def test_FR_NOTICE_009_app_role_may_write_the_withdrawal_columns(admin_engine: Engine) -> None:
    with admin_engine.connect() as c:
        granted = {
            row[0]
            for row in c.execute(
                text(
                    "SELECT column_name FROM information_schema.column_privileges "
                    "WHERE table_schema = 'ops' AND table_name = 'parent_notices' "
                    "AND grantee = 'sos_app' AND privilege_type = 'UPDATE'"
                )
            )
        }
    assert {"withdrawn_by", "withdrawn_at", "withdraw_reason", "status"} <= granted
