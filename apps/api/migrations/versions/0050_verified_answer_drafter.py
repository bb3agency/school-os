"""Verified answers remember who drafted them (owner decision 2026-10-09; FR-KB-030).

A verified answer needs a reviewer other than the person who drafted it. ``verified_by`` is
overwritten by every review, so the drafter is kept in its own column:
``kb.verified_answers.drafted_by`` is the membership id of the person who created the answer,
or of the reviewer who last changed its text or citations.

Expand only: the column is nullable and has no default. Answers stored before this revision
keep NULL and the service treats their ``verified_by`` (the person who stands behind the answer
now) as the drafter, so no row is rewritten and no FORCE is lifted. ``sos_app`` already has
table-level INSERT, SELECT and UPDATE on the table, which cover the new column.

Downgrade drops the column (the rule then has no drafter to compare; the service is rolled back
with it).

Revision ID: 0050_verified_answer_drafter
Revises: 0049_open_items
Create Date: 2026-10-09
"""

from __future__ import annotations

from alembic import op

revision = "0050_verified_answer_drafter"
down_revision = "0049_open_items"
branch_labels = None
depends_on = None

UP = "ALTER TABLE kb.verified_answers ADD COLUMN drafted_by uuid"
DOWN = "ALTER TABLE kb.verified_answers DROP COLUMN IF EXISTS drafted_by"


def upgrade() -> None:
    op.execute(UP)


def downgrade() -> None:
    op.execute(DOWN)
