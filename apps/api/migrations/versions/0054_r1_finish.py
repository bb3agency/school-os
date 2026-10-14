"""R1 leftovers: print jobs for parent verification slips and APAAR consent forms as PDF
(FR-DQ-047, FR-APC-007; ADR-0040 and ADR-0039 follow-ups).

The worker prints the slips and the consent forms to PDF through the export pipeline
(``ops.exports`` and ``ops.export_files``: private bucket, SSE-KMS, presigned downloads, audit,
deletion after the retention period). Two new export kinds:

- ``readiness_slips``: names its readiness profile (``profile_key`` and ``profile_version``),
  like the pre-checks;
- ``apaar_consent_forms``: names no profile.

Both carry no column list and exactly one file, a PDF. The language CHECK is unchanged (consent
forms use ``en`` or ``te``, the school's parent-form language). The table keeps its RLS policy,
FORCE, grants and frozen-request trigger; only CHECK constraints change.

Downgrade deletes the export rows of the two kinds (their file rows go with them by the
cascade; the stored PDFs are removed by the bucket's export lifecycle rule) and restores the
original constraints.

Revision ID: 0054_r1_finish
Revises: 0053_readiness
Create Date: 2026-10-14
"""

from __future__ import annotations

from alembic import op

revision = "0054_r1_finish"
down_revision = "0053_readiness"
branch_labels = None
depends_on = None

UPGRADE_SQL = r"""
ALTER TABLE ops.exports DROP CONSTRAINT exports_kind_check;
ALTER TABLE ops.exports ADD CONSTRAINT exports_kind_check CHECK (kind IN (
  'board_precheck','portal_precheck','student_list','readiness_slips','apaar_consent_forms'));

ALTER TABLE ops.exports DROP CONSTRAINT exports_kind_shape;
-- Pre-checks and slips name a DQ profile; student lists name their columns; consent forms
-- name neither.
ALTER TABLE ops.exports ADD CONSTRAINT exports_kind_shape CHECK (
  (kind = 'student_list' AND profile_key IS NULL AND profile_version IS NULL
     AND columns IS NOT NULL AND cardinality(columns) BETWEEN 1 AND 60)
  OR (kind IN ('board_precheck','portal_precheck','readiness_slips')
     AND profile_key IS NOT NULL AND profile_version IS NOT NULL AND columns IS NULL)
  OR (kind = 'apaar_consent_forms'
     AND profile_key IS NULL AND profile_version IS NULL AND columns IS NULL));

-- Print jobs make exactly one PDF.
ALTER TABLE ops.exports ADD CONSTRAINT exports_print_formats CHECK (
  kind NOT IN ('readiness_slips','apaar_consent_forms') OR formats = ARRAY['pdf']::text[]);
"""

# The owner lifts FORCE for the one DELETE (rows of every school); FORCE is restored before the
# constraints change, in the same transaction.
UNFORCE = "ALTER TABLE ops.exports NO FORCE ROW LEVEL SECURITY"  # nosemgrep: sos-migration-rls-bypass -- owner deletes the print-job rows of every school on downgrade; FORCE is restored in the same transaction
REFORCE = "ALTER TABLE ops.exports FORCE ROW LEVEL SECURITY"
DELETE_PRINT_JOBS = (
    "DELETE FROM ops.exports WHERE kind IN ('readiness_slips','apaar_consent_forms')"
)

DOWNGRADE_SQL = r"""
ALTER TABLE ops.exports DROP CONSTRAINT exports_print_formats;

ALTER TABLE ops.exports DROP CONSTRAINT exports_kind_shape;
ALTER TABLE ops.exports ADD CONSTRAINT exports_kind_shape CHECK (
  (kind = 'student_list' AND profile_key IS NULL AND profile_version IS NULL
     AND columns IS NOT NULL AND cardinality(columns) BETWEEN 1 AND 60)
  OR (kind <> 'student_list' AND profile_key IS NOT NULL AND profile_version IS NOT NULL
     AND columns IS NULL));

ALTER TABLE ops.exports DROP CONSTRAINT exports_kind_check;
ALTER TABLE ops.exports ADD CONSTRAINT exports_kind_check
  CHECK (kind IN ('board_precheck','portal_precheck','student_list'));
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(UNFORCE)
    op.execute(DELETE_PRINT_JOBS)
    op.execute(REFORCE)
    op.execute(DOWNGRADE_SQL)
