"""E-mails are sent at most once per outbox event (audit 2026-11-09 YM-06; FR-NOT-001).

``notifications.send_email`` consumes ``notification.email.requested`` with ``acks_late``. The
Valkey event lease (``ops.TenantTask``) ends after two hours and fails open, so a worker that
died after the provider accepted an e-mail (or a lease lost with Valkey) let a redelivery send
the same e-mail again. Every e-mail is also an in-app notification or a resendable invitation,
so the lead chose at-most-once: the worker records its claim on the event in a committed step
before it calls the provider, and a later run of the same event skips the send.

``ops.outbox.consumed_at`` holds that claim. Expand only: one nullable column (no default, no
rewrite) and a column ``UPDATE`` grant to ``sos_app`` on that column alone; ``sos_app`` still
cannot change an event's type or payload, and RLS of the table is unchanged.

Downgrade: revokes the grant and drops the column (a claim only matters while its event may
still be redelivered; nothing else reads it).

Revision ID: 0084_audit_ym_fixes
Revises: 0081_audit_bl_fixes
Create Date: 2026-11-09
"""

from __future__ import annotations

from alembic import op

revision = "0084_audit_ym_fixes"
down_revision = "0081_audit_bl_fixes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE ops.outbox ADD COLUMN consumed_at timestamptz")
    op.execute("GRANT UPDATE (consumed_at) ON ops.outbox TO sos_app")


def downgrade() -> None:
    op.execute("REVOKE UPDATE (consumed_at) ON ops.outbox FROM sos_app")
    op.execute("ALTER TABLE ops.outbox DROP COLUMN IF EXISTS consumed_at")
