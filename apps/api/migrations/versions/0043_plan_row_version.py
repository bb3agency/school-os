"""Edit counter on platform plans for If-Match on draft edits (FR-PLT-010; docs/16 §5.6).

Owner decision 2026-10-04: ``PATCH /platform/plans/{id}`` checks an optional ``If-Match`` like
every other platform edit (412 ``precondition_failed`` on a stale ETag). ``platform.plans.version``
is the catalogue version (``UNIQUE (code, version)``), not an edit counter, so a separate column
is added:

- ``platform.plans.row_version int NOT NULL DEFAULT 1 CHECK (row_version >= 1)``. Existing rows
  (the seeded catalogue and any drafts) start at 1. The service bumps it on each draft edit; the
  freeze trigger (0005, ``tg_plans_freeze``) already refuses any column change on a published or
  retired plan, so it never moves after publication.

Expand-only (invariant 12): a NOT NULL column with a default; code that does not know it keeps
working (inserts take the default). No grant changes: the existing table-level grants on
``platform.plans`` cover the new column.

Downgrade drops the column (lossy only for the edit counters; ETags start again at 1).

Revision ID: 0043_plan_row_version
Revises: 0042_apaar_id
Create Date: 2026-10-04
"""

from __future__ import annotations

from alembic import op

revision = "0043_plan_row_version"
down_revision = "0042_apaar_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE platform.plans
          ADD COLUMN row_version int NOT NULL DEFAULT 1
            CONSTRAINT plans_row_version CHECK (row_version >= 1);
        COMMENT ON COLUMN platform.plans.row_version IS
          'Edit counter for If-Match on draft edits (ETag); not the catalogue version.';
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE platform.plans DROP COLUMN IF EXISTS row_version")
