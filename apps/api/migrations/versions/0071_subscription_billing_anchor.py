"""Billing day anchor for subscriptions (FR-PLT-015; docs/16 §10).

Periods used to roll with ``add_months(end, n)``, which clamps the day: a subscription that
started on the 31st moved to the 28th after February and stayed there. ``billing_anchor_day``
keeps the day of the month the subscription was first billed on; each period end is that day
clamped to the month's length (31 Jan -> 28 Feb -> 31 Mar -> 30 Apr).

- ``platform.subscriptions.billing_anchor_day smallint`` (NULL allowed: a NULL anchor rolls as
  before), CHECK 1..31.
- Backfill: the day of the subscription's first billed period (its earliest non-void invoice's
  ``period_start``; billing is in advance, so this is the original start, also for a trial that
  was activated later), else the day of ``current_period_start``. A subscription that already
  drifted moves back to its anchor day on its next roll.

No tenant table, RLS unchanged; no personal data.

Downgrade: drops the column (periods then roll with the clamped day again).

Revision ID: 0071_subscription_billing_anchor
Revises: 0069_incident_reopen_notice
Create Date: 2026-11-03
"""

from __future__ import annotations

from alembic import op

revision = "0071_subscription_billing_anchor"
down_revision = "0069_incident_reopen_notice"
branch_labels = None
depends_on = None

UP = r"""
ALTER TABLE platform.subscriptions
  ADD COLUMN billing_anchor_day smallint,
  ADD CONSTRAINT subscriptions_billing_anchor_day_check
    CHECK (billing_anchor_day BETWEEN 1 AND 31);

UPDATE platform.subscriptions s
   SET billing_anchor_day = EXTRACT(DAY FROM COALESCE(
         (SELECT min(i.period_start) FROM platform.invoices i
           WHERE i.subscription_id = s.id AND i.status <> 'void'),
         s.current_period_start))::smallint;
"""

DOWN = r"""
ALTER TABLE platform.subscriptions
  DROP CONSTRAINT IF EXISTS subscriptions_billing_anchor_day_check,
  DROP COLUMN IF EXISTS billing_anchor_day;
"""


def upgrade() -> None:
    op.execute(UP)


def downgrade() -> None:
    op.execute(DOWN)
