"""Admission numbers unique per school ignoring case (BR-01, FR-STU-001; D53; docs/05 §14).

Imports already match ``adm/1`` and ``ADM/1`` as the same student; the database allowed both.

- Pre-check: if any school has admission numbers that differ only in case, the migration stops
  with ``admission numbers differ only in case: <n> school(s), <m> duplicate group(s)`` and
  changes nothing. The message carries counts only (no numbers, no school names or ids); the
  school resolves its duplicates (maker-checker change requests) and the migration is re-run.
  The count reads ``sis.students`` across schools as the table owner, so FORCE ROW LEVEL
  SECURITY is lifted for that one read-only query and restored in the same transaction (the
  0042/0049 pattern); RLS stays ENABLEd throughout.
- ``CREATE UNIQUE INDEX students_adm_no_ci ON sis.students (tenant_id, lower(admission_no))
  WHERE admission_no IS NOT NULL``. The app maps a violation of it to the same 409
  ``duplicate_admission_no`` as the exact index.
- Expand only: the exact index ``students_adm_no`` stays (the previous release maps only that
  name to ``duplicate_admission_no``); it is redundant now and is dropped in a later contract
  revision.

Downgrade: drops ``students_adm_no_ci`` (case variants are then accepted again).

Revision ID: 0073_admission_no_ci
Revises: 0072_outbox_retention
Create Date: 2026-11-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0073_admission_no_ci"
down_revision = "0072_outbox_retention"
branch_labels = None
depends_on = None

UNFORCE = "ALTER TABLE sis.students NO FORCE ROW LEVEL SECURITY"  # nosemgrep: sos-migration-rls-bypass -- owner counts case-variant duplicates read-only; FORCE is restored in the same transaction
REFORCE = "ALTER TABLE sis.students FORCE ROW LEVEL SECURITY"

DUPLICATES_SQL = """
SELECT count(DISTINCT tenant_id) AS schools, count(*) AS groups
  FROM (SELECT tenant_id
          FROM sis.students
         WHERE admission_no IS NOT NULL
         GROUP BY tenant_id, lower(admission_no)
        HAVING count(*) > 1) AS d
"""

UP = """
CREATE UNIQUE INDEX students_adm_no_ci ON sis.students (tenant_id, lower(admission_no))
  WHERE admission_no IS NOT NULL
"""

DOWN = "DROP INDEX IF EXISTS sis.students_adm_no_ci"


class CaseVariantAdmissionNumbers(RuntimeError):
    """Some schools hold admission numbers that differ only in case (counts only)."""


def upgrade() -> None:
    bind = op.get_bind()
    op.execute(UNFORCE)
    row = bind.execute(sa.text(DUPLICATES_SQL)).one()
    op.execute(REFORCE)
    if row.groups:
        raise CaseVariantAdmissionNumbers(
            f"admission numbers differ only in case: {row.schools} school(s), "
            f"{row.groups} duplicate group(s). Each school must resolve them (one number per "
            "student) before this migration can run; see the 0073_admission_no_ci docstring."
        )
    op.execute(UP)


def downgrade() -> None:
    op.execute(DOWN)
