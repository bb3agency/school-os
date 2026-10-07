"""Open audit items (wave 6).

# --- Agent B helper (wip/ob-platform-app): audit 2026-10-05 hardening "Announcements" ---------
A critical announcement needs a second operator's approval before schools see it (two-person,
like offboarding and emergency break-glass; owner decision 2026-10-07). Control-plane schema
only (no RLS, written only by ``sos_platform``, no student data):

- ``platform.announcements`` gains ``submitted_by``/``submitted_at`` (the operator who asked for
  the critical banner to go out, and when) and ``approved_by``/``approved_at`` (the second
  operator);
- the status set gains ``pending_approval`` (a critical announcement waiting for its second
  operator; never published to schools);
- ``announcements_critical_two_person``: a scheduled critical announcement carries an approval
  by an operator other than the one who submitted it. It is added ``NOT VALID`` so critical
  banners published before this revision stay readable and cancellable; every new or changed
  row is checked.

Downgrade: pending approvals become drafts (nothing was published), then the constraint, the
status value and the columns are dropped. Reversible without data loss for published rows.
# --- end Agent B helper block -------------------------------------------------------------------

Revision ID: 0049_open_items
Revises: 0047_security_decisions
Create Date: 2026-10-07
"""

from __future__ import annotations

from alembic import op

revision = "0049_open_items"
down_revision = "0047_security_decisions"
branch_labels = None
depends_on = None

# --- Agent B helper (wip/ob-platform-app): critical announcements are two-person -------------
ANNOUNCEMENTS_UP = """
ALTER TABLE platform.announcements
  ADD COLUMN submitted_by uuid REFERENCES platform.operators(id),
  ADD COLUMN submitted_at timestamptz,
  ADD COLUMN approved_by  uuid REFERENCES platform.operators(id),
  ADD COLUMN approved_at  timestamptz;
ALTER TABLE platform.announcements DROP CONSTRAINT announcements_status_check;
ALTER TABLE platform.announcements ADD CONSTRAINT announcements_status_check
  CHECK (status IN ('draft','pending_approval','scheduled','cancelled'));
ALTER TABLE platform.announcements ADD CONSTRAINT announcements_submitted
  CHECK ((submitted_by IS NULL) = (submitted_at IS NULL));
ALTER TABLE platform.announcements ADD CONSTRAINT announcements_approved
  CHECK ((approved_by IS NULL) = (approved_at IS NULL));
ALTER TABLE platform.announcements ADD CONSTRAINT announcements_pending_submitted
  CHECK (status <> 'pending_approval' OR (submitted_by IS NOT NULL AND approved_by IS NULL));
-- Two different operators put a critical banner in front of schools (SEC-029 pattern).
ALTER TABLE platform.announcements ADD CONSTRAINT announcements_critical_two_person
  CHECK (severity <> 'critical' OR status <> 'scheduled'
         OR (approved_by IS NOT NULL AND submitted_by IS NOT NULL
             AND approved_by <> submitted_by)) NOT VALID;
"""

ANNOUNCEMENTS_DOWN = """
UPDATE platform.announcements SET status = 'draft' WHERE status = 'pending_approval';
ALTER TABLE platform.announcements DROP CONSTRAINT announcements_critical_two_person;
ALTER TABLE platform.announcements DROP CONSTRAINT announcements_pending_submitted;
ALTER TABLE platform.announcements DROP CONSTRAINT announcements_approved;
ALTER TABLE platform.announcements DROP CONSTRAINT announcements_submitted;
ALTER TABLE platform.announcements DROP CONSTRAINT announcements_status_check;
ALTER TABLE platform.announcements ADD CONSTRAINT announcements_status_check
  CHECK (status IN ('draft','scheduled','cancelled'));
ALTER TABLE platform.announcements
  DROP COLUMN approved_at,
  DROP COLUMN approved_by,
  DROP COLUMN submitted_at,
  DROP COLUMN submitted_by;
"""
# --- end Agent B helper block -------------------------------------------------------------------


def upgrade() -> None:
    op.execute(ANNOUNCEMENTS_UP)


def downgrade() -> None:
    op.execute(ANNOUNCEMENTS_DOWN)
