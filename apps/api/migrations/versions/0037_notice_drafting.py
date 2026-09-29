"""Parent notices drafted in the background (FR-NOTICE-003; docs/05 §6.3, docs/06 §4.10).

``POST /notices`` stores the notice at once and a worker drafts it with the AI, so the request no
longer waits for the model (the web BFF gives up after 30 s). ``ops.parent_notices``:

- ``status``: ``drafting -> draft | draft_failed``, then ``approved`` as before (a blank notice
  starts as ``draft``). ``draft_failed`` always carries its ``draft_error`` code; editing such a
  notice by hand makes it a ``draft``, "Try again" makes it ``drafting`` again.
- ``source_text``: the staff text a notice is drafted from, kept only while the notice is
  ``drafting`` or ``draft_failed`` (the worker reads it: task arguments carry IDs only) and
  cleared when the notice becomes a draft. Only ``staff_text`` notices have it; at most 4 000
  characters (the API's limit). A drafting notice without it (its text dropped by a downgrade)
  ends ``draft_failed`` with ``source_unavailable``.
- ``approved_complete``: every non-approved state may be empty (was: only ``draft``).
- ``sos_app`` may now update ``ai_drafted``, ``draft_error`` and ``source_text`` (the worker
  writes the draft's outcome; before, both were set once at insert).

Expand-only (invariant 12). Downgrade drops ``source_text`` (lossy: the staff text of notices
not yet drafted), revokes the new column grants and restores the 0034 status and completeness
checks as ``NOT VALID``: rows already ``drafting`` or ``draft_failed`` are kept as they are (the
migrator cannot rewrite them under FORCE RLS, as 0029_kb_v2), while new rows must satisfy the
old checks again. Walking up once more re-adds the full, validated checks.

Revision ID: 0037_notice_drafting
Revises: 0036_tally
Create Date: 2026-09-29
"""

from __future__ import annotations

from alembic import op

revision = "0037_notice_drafting"
down_revision = "0036_tally"
branch_labels = None
depends_on = None

TABLE = "ops.parent_notices"
COMPLETE = "(title_en <> '' AND body_en <> '' AND title_te <> '' AND body_te <> '')"

UPGRADE_SQL = rf"""
ALTER TABLE {TABLE} ADD COLUMN source_text text
  CONSTRAINT parent_notices_source_text_length CHECK (char_length(source_text) BETWEEN 1 AND 4000);
ALTER TABLE {TABLE} DROP CONSTRAINT parent_notices_status_check;
ALTER TABLE {TABLE} ADD CONSTRAINT parent_notices_status_check
  CHECK (status IN ('drafting','draft','draft_failed','approved'));
ALTER TABLE {TABLE} DROP CONSTRAINT parent_notices_approved_complete;
-- Both languages are filled before approval (FR-NOTICE-004); every other state may be empty.
ALTER TABLE {TABLE} ADD CONSTRAINT parent_notices_approved_complete
  CHECK (status <> 'approved' OR {COMPLETE});
-- A failed draft says why (FR-NOTICE-003).
ALTER TABLE {TABLE} ADD CONSTRAINT parent_notices_failed_has_error
  CHECK (status <> 'draft_failed' OR draft_error IS NOT NULL);
-- The staff text is kept only while the AI still has to draft from it.
ALTER TABLE {TABLE} ADD CONSTRAINT parent_notices_source_text_while_drafting
  CHECK (source_text IS NULL
         OR (source = 'staff_text' AND status IN ('drafting','draft_failed')));
GRANT UPDATE (ai_drafted, draft_error, source_text) ON {TABLE} TO sos_app;
"""

DOWNGRADE_SQL = rf"""
REVOKE UPDATE (ai_drafted, draft_error) ON {TABLE} FROM sos_app;
ALTER TABLE {TABLE} DROP CONSTRAINT parent_notices_source_text_while_drafting;
ALTER TABLE {TABLE} DROP CONSTRAINT parent_notices_failed_has_error;
ALTER TABLE {TABLE} DROP COLUMN source_text;
ALTER TABLE {TABLE} DROP CONSTRAINT parent_notices_approved_complete;
ALTER TABLE {TABLE} ADD CONSTRAINT parent_notices_approved_complete
  CHECK (status = 'draft' OR {COMPLETE}) NOT VALID;
ALTER TABLE {TABLE} DROP CONSTRAINT parent_notices_status_check;
ALTER TABLE {TABLE} ADD CONSTRAINT parent_notices_status_check
  CHECK (status IN ('draft','approved')) NOT VALID;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
