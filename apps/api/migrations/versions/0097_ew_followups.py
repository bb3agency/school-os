"""FT-13 early-warning follow-ups and FT-14 records completeness (lead decision D86;
FR-EW-019..FR-EW-030, FR-ATT-016..FR-ATT-025, FR-MRK-016..FR-MRK-025; docs/05 §6.4).

No new table. Two existing tenant tables (RLS, composite FKs and the offboarding purge policy
unchanged) get one nullable column each:

- ``sis.insight_flags.review_on``: the next planned review of an open flag (the follow-up date a
  person set with the latest action). NULL when none is planned and always NULL once the flag is
  closed. ``sos_app`` may UPDATE it (column grant); what the rule saw stays read-only.
- ``sis.flag_actions.review_on``: the review date planned with that action (the log keeps the
  history of planned reviews). Actions stay append-only (no UPDATE grant on the column).

Also: the permission catalog gets ``insights.counts_read`` (management sees early-warning counts
only, never names), ``completeness.read`` and ``completeness.remind`` (attendance and marks
entry completeness per section). Role grants of existing schools change only with the
post-migration system-role sync (ADR-0022).

Expand-only (invariant 12). Downgrade drops both columns (lossy: the planned review dates) and
removes the catalog keys when no role holds them.

Revision ID: 0097_ew_followups
Revises: 0096_compliance_register
Create Date: 2026-11-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0097_ew_followups"
down_revision = "0096_compliance_register"
branch_labels = None
depends_on = None

UPGRADE_SQL = r"""
ALTER TABLE sis.insight_flags ADD COLUMN review_on date;
ALTER TABLE sis.insight_flags ADD CONSTRAINT insight_flags_review_after_raise
  CHECK (review_on IS NULL OR review_on >= raised_on);
ALTER TABLE sis.insight_flags ADD CONSTRAINT insight_flags_review_open
  CHECK (status <> 'closed' OR review_on IS NULL);
CREATE INDEX insight_flags_review_idx ON sis.insight_flags (tenant_id, review_on)
  WHERE review_on IS NOT NULL;
GRANT UPDATE (review_on) ON sis.insight_flags TO sos_app;

ALTER TABLE sis.flag_actions ADD COLUMN review_on date;
ALTER TABLE sis.flag_actions ADD CONSTRAINT flag_actions_review_after_action
  CHECK (review_on IS NULL OR review_on > acted_on);
"""

DOWNGRADE_SQL = r"""
REVOKE UPDATE (review_on) ON sis.insight_flags FROM sos_app;
DROP INDEX IF EXISTS sis.insight_flags_review_idx;
ALTER TABLE sis.insight_flags DROP CONSTRAINT IF EXISTS insight_flags_review_open;
ALTER TABLE sis.insight_flags DROP CONSTRAINT IF EXISTS insight_flags_review_after_raise;
ALTER TABLE sis.insight_flags DROP COLUMN IF EXISTS review_on;
ALTER TABLE sis.flag_actions DROP CONSTRAINT IF EXISTS flag_actions_review_after_action;
ALTER TABLE sis.flag_actions DROP COLUMN IF EXISTS review_on;
"""

NEW_PERMISSIONS: tuple[dict[str, object], ...] = (
    {
        "key": "insights.counts_read",
        "description": (
            "See the school's early-warning follow-up counts (raised, on time, overdue, "
            "outcomes); never names or flags"
        ),
        "sensitivity": "normal",
        "step_up": False,
        "is_platform": False,
    },
    {
        "key": "completeness.read",
        "description": (
            "See how complete attendance and marks entry is per section (counts) and whose "
            "entry it waits on"
        ),
        "sensitivity": "normal",
        "step_up": False,
        "is_platform": False,
    },
    {
        "key": "completeness.remind",
        "description": (
            "Send a section's class teacher an in-app reminder to finish attendance or marks "
            "entry (when reminders are switched on)"
        ),
        "sensitivity": "normal",
        "step_up": False,
        "is_platform": False,
    },
)

UPSERT = sa.text(
    """
    INSERT INTO core.permissions (key, description, sensitivity, step_up, is_platform)
    VALUES (:key, :description, :sensitivity, :step_up, :is_platform)
    ON CONFLICT (key) DO UPDATE SET
      description = EXCLUDED.description,
      sensitivity = EXCLUDED.sensitivity,
      step_up = EXCLUDED.step_up,
      is_platform = EXCLUDED.is_platform
    """
)


def upgrade() -> None:
    op.execute(UPGRADE_SQL)
    bind = op.get_bind()
    for row in NEW_PERMISSIONS:
        bind.execute(UPSERT, row)


def downgrade() -> None:
    bind = op.get_bind()
    for row in NEW_PERMISSIONS:
        savepoint = bind.begin_nested()
        try:
            bind.execute(sa.text("DELETE FROM core.permissions WHERE key = :k"), {"k": row["key"]})
            savepoint.commit()
        except sa.exc.IntegrityError:
            savepoint.rollback()
    op.execute(DOWNGRADE_SQL)
