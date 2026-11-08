"""WhatsApp daily cap counts claimed messages (audit 2026-11-08 BL-05; FR-MSG-011, FR-MSG-016).

The sender claims a queued message (``next_attempt_at`` = the claim lease) and calls Meta outside
the transaction. The daily cap counted only messages already ``sent``, so two runs of one school
at once could each pass the cap with a message in flight. ``msg.messages.claimed_until`` marks a
live claim: the cap counts template messages sent since IST midnight plus queued template
messages whose ``claimed_until`` is still ahead, and the check and claim run under a per-school
transaction advisory lock (application code, ``app/messaging/sends/repository.py``).

Expand only: one nullable column (no default, no rewrite) and its column ``UPDATE`` grant to
``sos_app`` (as ``next_attempt_at`` in 0061). RLS of the table is unchanged.

Downgrade: drops the column (claims are transient; nothing is lost).

Revision ID: 0083_audit_bl_fixes
Revises: 0079_audit_outbox_set_once
Create Date: 2026-11-08
"""

from __future__ import annotations

from alembic import op

revision = "0083_audit_bl_fixes"
down_revision = "0079_audit_outbox_set_once"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE msg.messages ADD COLUMN claimed_until timestamptz")
    op.execute("GRANT UPDATE (claimed_until) ON msg.messages TO sos_app")


def downgrade() -> None:
    op.execute("REVOKE UPDATE (claimed_until) ON msg.messages FROM sos_app")
    op.execute("ALTER TABLE msg.messages DROP COLUMN IF EXISTS claimed_until")
