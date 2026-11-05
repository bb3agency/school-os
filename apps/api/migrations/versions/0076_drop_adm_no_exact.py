"""Contract step after D53: drop the exact admission-number index (lead decision D64; BR-01,
FR-STU-001; docs/05 §14).

``0073_admission_no_ci`` added ``students_adm_no_ci`` on ``sis.students (tenant_id,
lower(admission_no)) WHERE admission_no IS NOT NULL``. Every pair of rows the exact index
``students_adm_no`` (``(tenant_id, admission_no)``, same predicate) refuses is also refused by
the case-insensitive one, so the exact index is redundant: this revision drops it.

- No code names the exact index in ``ON CONFLICT``: student writes are plain INSERT/UPDATE and
  the app maps a unique violation of either index name to the 409 ``duplicate_admission_no``
  (``students.repository._DUPLICATE_CODES``). The release that ships this revision still maps
  ``students_adm_no`` so a downgraded database answers the same.
- No query needs the exact index: the student search compares ``upper(admission_no)``, which
  it never served, and a scan by ``tenant_id`` can use the CI index's leading column.

Downgrade: recreates ``students_adm_no`` as in 0008. It cannot fail on data: while
``students_adm_no_ci`` exists, no two rows of one school share an admission number in any case,
so none can share it exactly.

Revision ID: 0076_drop_adm_no_exact
Revises: 0075_breach_retention
Create Date: 2026-11-05
"""

from __future__ import annotations

from alembic import op

revision = "0076_drop_adm_no_exact"
down_revision = "0075_breach_retention"
branch_labels = None
depends_on = None

UP = "DROP INDEX IF EXISTS sis.students_adm_no"

DOWN = """
CREATE UNIQUE INDEX IF NOT EXISTS students_adm_no ON sis.students (tenant_id, admission_no)
  WHERE admission_no IS NOT NULL
"""


def upgrade() -> None:
    op.execute(UP)


def downgrade() -> None:
    op.execute(DOWN)
