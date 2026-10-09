"""The app role may change only the workflow columns it needs (data-layer hardening 1 and 2).

Round-one data-layer audit (docs/security/audit-2026-10-04-data-layer.md), hardening notes:

1. ``sos_app`` held table-wide UPDATE on ``core.memberships`` (including ``id``, ``tenant_id``,
   ``user_id``, ``created_by``), ``ops.break_glass_grants`` (nearly every column) and
   ``audit.chain_heads`` (including ``tenant_id``); ``audit.chain_verifications`` (0047) had the
   same shape. Migration ``0048_narrow_app_grants`` narrows each to the columns the app writes.
2. ``sos_app`` held DELETE on ``kb.llm_calls``, the metering ledger, with no app path using it.

Catalog checks (``information_schema``) plus one behavioural check per table as ``sos_app``.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import ProgrammingError

pytestmark = pytest.mark.db

EXPECTED_UPDATE: dict[str, set[str]] = {
    "core.memberships": {"status", "expires_at", "version", "updated_at"},
    "ops.break_glass_grants": {
        "status",
        "starts_at",
        "expires_at",
        "decided_at",
        "approved_by_membership",
        "denied_by_membership",
        "membership_id",
        "revoked_at",
        "revoked_by_membership",
        "platform_status_synced",
        "updated_at",
    },
    "audit.chain_heads": {"last_seq", "last_hash", "updated_at"},
    "audit.chain_verifications": {
        "verified_at",
        "mode",
        "source",
        "ok",
        "checked",
        "first_bad_seq",
        "reason",
        "checkpoint_seq",
        "checkpoint_hash",
        "checkpoint_at",
        "last_full_at",
        "requested_at",
        "requested_by",
        "requested_full",
        "updated_at",
    },
}


def _update_columns(engine: Engine, table: str) -> set[str]:
    schema, name = table.split(".")
    with engine.connect() as c:
        rows: Iterable[str] = c.execute(
            text(
                "SELECT column_name FROM information_schema.column_privileges "
                "WHERE table_schema = :s AND table_name = :n AND grantee = 'sos_app' "
                "AND privilege_type = 'UPDATE'"
            ),
            {"s": schema, "n": name},
        ).scalars()
        return set(rows)


def _table_privileges(engine: Engine, table: str) -> set[str]:
    schema, name = table.split(".")
    with engine.connect() as c:
        rows: Iterable[str] = c.execute(
            text(
                "SELECT privilege_type FROM information_schema.role_table_grants "
                "WHERE table_schema = :s AND table_name = :n AND grantee = 'sos_app'"
            ),
            {"s": schema, "n": name},
        ).scalars()
        return set(rows)


@pytest.mark.parametrize("table", sorted(EXPECTED_UPDATE))
def test_SEC_002_app_role_updates_only_workflow_columns(admin_engine: Engine, table: str) -> None:
    assert "UPDATE" not in _table_privileges(admin_engine, table), "no table-wide UPDATE"
    assert _update_columns(admin_engine, table) == EXPECTED_UPDATE[table]


def test_SEC_002_app_role_cannot_delete_metering_ledger(admin_engine: Engine) -> None:
    assert _table_privileges(admin_engine, "kb.llm_calls") == {"SELECT", "INSERT"}


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE core.memberships SET tenant_id = tenant_id WHERE false",
        "UPDATE core.memberships SET user_id = user_id WHERE false",
        "UPDATE core.memberships SET created_by = created_by WHERE false",
        "UPDATE ops.break_glass_grants SET scope = scope WHERE false",
        "UPDATE ops.break_glass_grants SET tenant_id = tenant_id WHERE false",
        "UPDATE audit.chain_heads SET tenant_id = tenant_id WHERE false",
        "UPDATE audit.chain_verifications SET tenant_id = tenant_id WHERE false",
        "DELETE FROM kb.llm_calls WHERE false",
    ],
)
def test_SEC_002_app_role_is_refused_outside_its_columns(
    app_engine: Engine, statement: str
) -> None:
    with app_engine.connect() as c:
        c.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(uuid.uuid4())})
        with pytest.raises(ProgrammingError, match="permission denied"):
            c.execute(text(statement))
        c.rollback()
