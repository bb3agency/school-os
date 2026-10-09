"""Narrow the app role's UPDATE grants to workflow columns; no DELETE on the metering ledger.

Round-one data-layer audit (docs/security/audit-2026-10-04-data-layer.md), hardening notes 1
and 2 (SEC-002, invariant 1 defence in depth):

- ``core.memberships``: ``sos_app`` held table-wide UPDATE, so a bug or an injected statement
  could move a membership to another user or school (``user_id``, ``tenant_id``) or rewrite
  ``created_by``. The app changes only ``status``, ``expires_at`` and ``version``
  (``identity.repository``); ``updated_at`` is set by the trigger. ``mfa_required`` is not
  written by the app and is left out on purpose (it must not be switched off by the app role).
- ``ops.break_glass_grants``: the app writes only the decision and lifecycle columns
  (``breakglass.repository.update_grant`` / ``mark_reported``); the request itself (operator,
  reason, scope, duration, emergency flag) is fixed once inserted.
- ``audit.chain_heads`` and ``audit.chain_verifications`` (0047): every column except
  ``tenant_id``. The 0046 head guard still checks the values.
- ``kb.llm_calls``: DELETE revoked. It is the metering ledger; offboarding deletes it as
  ``sos_purger`` (0032).

The table-wide grant is revoked and the column grants added in one transaction, so the app
never runs without the columns it needs. Backward compatible: the previous code writes only
these columns. The downgrade restores the table-wide grants.

Revision ID: 0048_narrow_app_grants
Revises: 0047_security_decisions
Create Date: 2026-10-07
"""

from __future__ import annotations

from alembic import op

revision = "0048_narrow_app_grants"
down_revision = "0047_security_decisions"
branch_labels = None
depends_on = None

UPDATE_COLUMNS: dict[str, tuple[str, ...]] = {
    "core.memberships": ("status", "expires_at", "version", "updated_at"),
    "ops.break_glass_grants": (
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
    ),
    "audit.chain_heads": ("last_seq", "last_hash", "updated_at"),
    "audit.chain_verifications": (
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
    ),
}


def upgrade() -> None:
    for table, columns in UPDATE_COLUMNS.items():
        # Revoking the table-wide privilege also drops every column privilege of that kind, so
        # revoke first, then grant the columns (one transaction: no window without them).
        op.execute(f"REVOKE UPDATE ON {table} FROM sos_app")
        op.execute(f"GRANT UPDATE ({', '.join(columns)}) ON {table} TO sos_app")
    op.execute("REVOKE DELETE ON kb.llm_calls FROM sos_app")


def downgrade() -> None:
    op.execute("GRANT DELETE ON kb.llm_calls TO sos_app")
    for table in UPDATE_COLUMNS:
        op.execute(f"GRANT UPDATE ON {table} TO sos_app")
