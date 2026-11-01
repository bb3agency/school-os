"""R3-2G track A (D49 part 3; ADR-0050, ADR-0054; docs/16 §5.15a): a reopened SchoolOS incident
reaches each affected school's breach register.

- ``platform.incidents`` counts its confirmed reopens: ``reopen_count`` (0 until the first
  reopen) and ``last_reopened_at`` (set together, ``incidents_reopened``).
- ``platform.incident_schools`` records which reopen each school last pulled:
  ``reopen_pulled`` (the reopen count the school stored) and ``reopen_pulled_at`` (set together,
  ``incident_schools_reopen_pulled``). A school whose ``reopen_pulled`` is behind its incident's
  ``reopen_count`` gets the notice again on its next pull.
- ``ops.breach_records`` keeps the last reopen the school stored: ``platform_reopen_seq`` and
  ``platform_reopened_at`` (set together; only on a ``processor_notice``). The app role may
  update just these two columns besides the ones 0067 granted. No new tenant table; RLS and
  policies are unchanged. Codes, counts and times only; no student data.

Downgrade: drops the six columns (the reopen history stays in both audit chains).

Revision ID: 0069_incident_reopen_notice
Revises: 0068_incident_reopen
Create Date: 2026-11-01
"""

from __future__ import annotations

from alembic import op

revision = "0069_incident_reopen_notice"
down_revision = "0068_incident_reopen"
branch_labels = None
depends_on = None

UP = r"""
ALTER TABLE platform.incidents
  ADD COLUMN reopen_count int NOT NULL DEFAULT 0,
  ADD COLUMN last_reopened_at timestamptz,
  ADD CONSTRAINT incidents_reopen_count_check CHECK (reopen_count >= 0),
  ADD CONSTRAINT incidents_reopened CHECK ((reopen_count = 0) = (last_reopened_at IS NULL));

ALTER TABLE platform.incident_schools
  ADD COLUMN reopen_pulled int NOT NULL DEFAULT 0,
  ADD COLUMN reopen_pulled_at timestamptz,
  ADD CONSTRAINT incident_schools_reopen_pulled_check CHECK (reopen_pulled >= 0),
  ADD CONSTRAINT incident_schools_reopen_pulled
    CHECK ((reopen_pulled = 0) = (reopen_pulled_at IS NULL));

ALTER TABLE ops.breach_records
  ADD COLUMN platform_reopen_seq int NOT NULL DEFAULT 0,
  ADD COLUMN platform_reopened_at timestamptz,
  ADD CONSTRAINT breach_records_reopen_seq_check CHECK (platform_reopen_seq >= 0),
  ADD CONSTRAINT breach_records_reopened
    CHECK ((platform_reopen_seq = 0) = (platform_reopened_at IS NULL)),
  ADD CONSTRAINT breach_records_reopen_origin
    CHECK (platform_reopen_seq = 0 OR origin = 'processor_notice');
GRANT UPDATE (platform_reopen_seq, platform_reopened_at) ON ops.breach_records TO sos_app;
"""

DOWN = r"""
REVOKE UPDATE (platform_reopen_seq, platform_reopened_at) ON ops.breach_records FROM sos_app;
ALTER TABLE ops.breach_records
  DROP CONSTRAINT IF EXISTS breach_records_reopen_origin,
  DROP CONSTRAINT IF EXISTS breach_records_reopened,
  DROP CONSTRAINT IF EXISTS breach_records_reopen_seq_check,
  DROP COLUMN IF EXISTS platform_reopened_at,
  DROP COLUMN IF EXISTS platform_reopen_seq;

ALTER TABLE platform.incident_schools
  DROP CONSTRAINT IF EXISTS incident_schools_reopen_pulled,
  DROP CONSTRAINT IF EXISTS incident_schools_reopen_pulled_check,
  DROP COLUMN IF EXISTS reopen_pulled_at,
  DROP COLUMN IF EXISTS reopen_pulled;

ALTER TABLE platform.incidents
  DROP CONSTRAINT IF EXISTS incidents_reopened,
  DROP CONSTRAINT IF EXISTS incidents_reopen_count_check,
  DROP COLUMN IF EXISTS last_reopened_at,
  DROP COLUMN IF EXISTS reopen_count;
"""


def upgrade() -> None:
    op.execute(UP)


def downgrade() -> None:
    op.execute(DOWN)
