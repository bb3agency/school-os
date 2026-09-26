"""Break-glass workflow columns on ``ops.break_glass_grants`` (US-103, FR-OPS-004, SEC-021;
docs/07 §6.4).

The school keeps its own copy of each support-access request that the control plane raised for
it (pulled by the tenant side; the control plane never writes tenant tables). The grant row
records the school decision, the temporary ``platform_support`` membership and its window.

Added columns (all nullable or defaulted, so existing rows stay valid):
- ``reason_code``, ``duration_minutes`` (15..480), ``emergency`` copied from the request;
- ``operator_display_name`` (who asked; an adult operator, shown to the school);
- ``requested_at``, ``decided_at``;
- ``membership_id`` (the temporary membership), ``denied_by_membership``,
  ``revoked_by_membership`` (composite FKs to ``core.memberships``);
- ``platform_status_synced``: last status reported back to the control plane.

Constraints: one grant per control-plane request; the approver is never the temporary
membership itself (no self-approval); emergency grants have no school approver; a request copied
from the control plane carries its duration. Grants are records: the app may not delete them.

Revision ID: 0011_breakglass
Revises: 0010_notifications
Create Date: 2026-09-26
"""

from __future__ import annotations

from alembic import op

revision = "0011_breakglass"
down_revision = "0010_notifications"
branch_labels = None
depends_on = None

STATUSES = "'requested','approved','active','expired','revoked','denied'"

UPGRADE_SQL = rf"""
ALTER TABLE ops.break_glass_grants
  ADD COLUMN reason_code text NOT NULL DEFAULT 'support_request'
    CONSTRAINT break_glass_grants_reason_code CHECK (reason_code IN
      ('support_request','security_incident','legal_obligation')),
  ADD COLUMN duration_minutes int
    CONSTRAINT break_glass_grants_duration CHECK (duration_minutes BETWEEN 15 AND 480),
  ADD COLUMN emergency boolean NOT NULL DEFAULT false,
  ADD COLUMN operator_display_name text
    CONSTRAINT break_glass_grants_operator_display_name
      CHECK (char_length(operator_display_name) BETWEEN 1 AND 200),
  ADD COLUMN requested_at timestamptz,
  ADD COLUMN decided_at timestamptz,
  ADD COLUMN membership_id uuid,
  ADD COLUMN denied_by_membership uuid,
  ADD COLUMN revoked_by_membership uuid,
  ADD COLUMN platform_status_synced text
    CONSTRAINT break_glass_grants_platform_status_synced
      CHECK (platform_status_synced IN ({STATUSES}));

ALTER TABLE ops.break_glass_grants
  ADD CONSTRAINT break_glass_grants_platform_request_key UNIQUE (tenant_id, platform_request_id),
  ADD CONSTRAINT break_glass_grants_membership_fk FOREIGN KEY (tenant_id, membership_id)
    REFERENCES core.memberships (tenant_id, id),
  ADD CONSTRAINT break_glass_grants_denied_by_fk FOREIGN KEY (tenant_id, denied_by_membership)
    REFERENCES core.memberships (tenant_id, id),
  ADD CONSTRAINT break_glass_grants_revoked_by_fk FOREIGN KEY (tenant_id, revoked_by_membership)
    REFERENCES core.memberships (tenant_id, id),
  -- 07 §6.3 pattern: the person who receives access never approves it.
  ADD CONSTRAINT break_glass_grants_no_self_approval
    CHECK (approved_by_membership IS NULL OR membership_id IS NULL
           OR approved_by_membership <> membership_id),
  -- Emergency access is confirmed by two operators, never by the school (07 §6.4).
  ADD CONSTRAINT break_glass_grants_emergency_unapproved
    CHECK (NOT emergency OR approved_by_membership IS NULL),
  ADD CONSTRAINT break_glass_grants_request_has_duration
    CHECK (platform_request_id IS NULL OR duration_minutes IS NOT NULL),
  ADD CONSTRAINT break_glass_grants_denied_by_when_denied
    CHECK (denied_by_membership IS NULL OR status = 'denied');

CREATE INDEX break_glass_grants_tenant_status
  ON ops.break_glass_grants (tenant_id, status, expires_at);
CREATE INDEX break_glass_grants_tenant_created
  ON ops.break_glass_grants (tenant_id, created_at DESC, id DESC);

-- Grants are part of the school's record of who had access: never deleted by the app.
REVOKE DELETE ON ops.break_glass_grants FROM sos_app;
"""

DOWNGRADE_SQL = r"""
GRANT DELETE ON ops.break_glass_grants TO sos_app;
DROP INDEX IF EXISTS ops.break_glass_grants_tenant_created;
DROP INDEX IF EXISTS ops.break_glass_grants_tenant_status;
ALTER TABLE ops.break_glass_grants
  DROP CONSTRAINT IF EXISTS break_glass_grants_denied_by_when_denied,
  DROP CONSTRAINT IF EXISTS break_glass_grants_request_has_duration,
  DROP CONSTRAINT IF EXISTS break_glass_grants_emergency_unapproved,
  DROP CONSTRAINT IF EXISTS break_glass_grants_no_self_approval,
  DROP CONSTRAINT IF EXISTS break_glass_grants_revoked_by_fk,
  DROP CONSTRAINT IF EXISTS break_glass_grants_denied_by_fk,
  DROP CONSTRAINT IF EXISTS break_glass_grants_membership_fk,
  DROP CONSTRAINT IF EXISTS break_glass_grants_platform_request_key;
ALTER TABLE ops.break_glass_grants
  DROP COLUMN IF EXISTS platform_status_synced,
  DROP COLUMN IF EXISTS revoked_by_membership,
  DROP COLUMN IF EXISTS denied_by_membership,
  DROP COLUMN IF EXISTS membership_id,
  DROP COLUMN IF EXISTS decided_at,
  DROP COLUMN IF EXISTS requested_at,
  DROP COLUMN IF EXISTS operator_display_name,
  DROP COLUMN IF EXISTS emergency,
  DROP COLUMN IF EXISTS duration_minutes,
  DROP COLUMN IF EXISTS reason_code;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
