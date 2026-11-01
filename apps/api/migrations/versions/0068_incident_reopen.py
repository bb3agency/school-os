"""R3-2F (D48; ADR-0050; docs/16): a closed SchoolOS incident is locked; reopening it takes two
operators.

``platform.incidents`` gains the pending reopen request: ``reopen_requested_by``,
``reopen_requested_at`` and ``reopen_reason`` (categories and counts only, the summary's rule).
All three are set together or not at all (``incidents_reopen_whole``), only while the incident
is ``closed`` (``incidents_reopen_closed``), and the reason has the summary's CHECK
(``incidents_reopen_reason_check``). A different operator confirms the request (service and
:mod:`app.platform.two_person`); the confirmation sets the status back to ``open`` and clears the
request. Both steps are in the platform audit chain. No tenant table changes; no student data.

Downgrade: drops the three columns (a pending reopen request is lost; the audit chain keeps the
record of it).

Revision ID: 0068_incident_reopen
Revises: 0067_consents_breach
Create Date: 2026-11-01
"""

from __future__ import annotations

from alembic import op

revision = "0068_incident_reopen"
down_revision = "0067_consents_breach"
branch_labels = None
depends_on = None

UP = r"""
ALTER TABLE platform.incidents
  ADD COLUMN reopen_requested_by uuid REFERENCES platform.operators (id),
  ADD COLUMN reopen_requested_at timestamptz,
  ADD COLUMN reopen_reason text,
  ADD CONSTRAINT incidents_reopen_whole CHECK (
    (reopen_requested_by IS NULL) = (reopen_requested_at IS NULL)
    AND (reopen_requested_by IS NULL) = (reopen_reason IS NULL)),
  ADD CONSTRAINT incidents_reopen_closed CHECK (reopen_requested_by IS NULL OR status = 'closed'),
  ADD CONSTRAINT incidents_reopen_reason_check CHECK (reopen_reason IS NULL OR (
    char_length(reopen_reason) BETWEEN 10 AND 500
    AND reopen_reason !~ '[0-9]{6,}' AND position('@' IN reopen_reason) = 0));
"""

DOWN = r"""
ALTER TABLE platform.incidents
  DROP CONSTRAINT IF EXISTS incidents_reopen_reason_check,
  DROP CONSTRAINT IF EXISTS incidents_reopen_closed,
  DROP CONSTRAINT IF EXISTS incidents_reopen_whole,
  DROP COLUMN IF EXISTS reopen_reason,
  DROP COLUMN IF EXISTS reopen_requested_at,
  DROP COLUMN IF EXISTS reopen_requested_by;
"""


def upgrade() -> None:
    op.execute(UP)


def downgrade() -> None:
    op.execute(DOWN)
