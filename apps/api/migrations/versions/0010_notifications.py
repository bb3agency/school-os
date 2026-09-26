"""In-app notifications (C11, FR-NOT-001; docs/05 §7).

- ``ops.notifications``: one row per recipient membership. ``template_key`` + ``params`` are
  rendered server-side in English or Telugu (``app/notifications/templates.yaml``); ``params``
  carry IDs, counts and codes only (validated like audit summaries), never names or free text.
- Tenant-owned: RLS ENABLE + FORCE with ``tenant_isolation``; the recipient is a composite FK to
  ``core.memberships`` (ADR-0013 §7).
- The app may only change ``read_at`` (column-level UPDATE grant); rows are otherwise immutable.
- Optional ``dedupe_key``: at most one notification per recipient and key (retries of the same
  event never notify twice).
- Retention: read notifications older than 90 days are purged daily (``notifications.purge_read``).

Requirements: FR-NOT-001, SEC-001.

Revision ID: 0010_notifications
Revises: 0007_accept_invitations (temporary; the lead relinks the M1 chain at merge)
Create Date: 2026-09-26
"""

from __future__ import annotations

from alembic import op

revision = "0010_notifications"
down_revision = "0007_accept_invitations"
branch_labels = None
depends_on = None

TABLE_SQL = r"""
CREATE TABLE ops.notifications (
  id                       uuid PRIMARY KEY,
  tenant_id                uuid NOT NULL REFERENCES core.tenants (id),
  recipient_membership_id  uuid NOT NULL,
  template_key             text NOT NULL CHECK (
                             template_key ~ '^[a-z_]+(\.[a-z_]+)+$'
                             AND char_length(template_key) <= 100),
  params                   jsonb NOT NULL DEFAULT '{}'::jsonb
                             CHECK (jsonb_typeof(params) = 'object'),
  resource_type            text CHECK (resource_type ~ '^[a-z][a-z0-9_]{0,63}$'),
  resource_id              uuid,
  dedupe_key               text CHECK (char_length(dedupe_key) BETWEEN 1 AND 200),
  created_at               timestamptz NOT NULL DEFAULT now(),
  read_at                  timestamptz,
  CONSTRAINT notifications_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT notifications_read_after_created CHECK (read_at IS NULL OR read_at >= created_at),
  CONSTRAINT notifications_resource_pair
    CHECK ((resource_type IS NULL) = (resource_id IS NULL)),
  CONSTRAINT notifications_recipient_fk FOREIGN KEY (tenant_id, recipient_membership_id)
    REFERENCES core.memberships (tenant_id, id) ON DELETE CASCADE
);
-- "My notifications", newest first (keyset pagination on created_at, id).
CREATE INDEX notifications_recipient_created
  ON ops.notifications (tenant_id, recipient_membership_id, created_at DESC, id DESC);
-- Unread badge.
CREATE INDEX notifications_recipient_unread
  ON ops.notifications (tenant_id, recipient_membership_id) WHERE read_at IS NULL;
-- Retention purge of read notifications.
CREATE INDEX notifications_read_at ON ops.notifications (tenant_id, read_at)
  WHERE read_at IS NOT NULL;
-- One notification per recipient and dedupe key.
CREATE UNIQUE INDEX notifications_dedupe
  ON ops.notifications (tenant_id, recipient_membership_id, dedupe_key)
  WHERE dedupe_key IS NOT NULL;
"""


def upgrade() -> None:
    op.execute(TABLE_SQL)
    op.execute("ALTER TABLE ops.notifications ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE ops.notifications FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON ops.notifications "
        "USING (tenant_id = core.current_tenant()) "
        "WITH CHECK (tenant_id = core.current_tenant())"
    )
    # Default privileges gave sos_app full DML; only read_at may change after insert.
    op.execute("REVOKE UPDATE ON ops.notifications FROM sos_app")
    op.execute("GRANT UPDATE (read_at) ON ops.notifications TO sos_app")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ops.notifications")
