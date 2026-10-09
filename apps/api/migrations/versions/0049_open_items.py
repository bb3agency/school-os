"""Workflow hardening from the 2026-10-05 app-logic and 2026-10-06 API-route audits.

Agent B helper "ob-workflow" block (the lead consolidates the 0049 blocks of several helpers and
re-points ``down_revision`` after 0048 at merge):

- **Change requests pin their evidence version** (audit 2026-10-05, hardening "Change
  requests"). ``sis.change_requests.evidence_version_id`` names the version of the evidence
  document that was current at submit. Composite FK ``(tenant_id, evidence_document_id,
  evidence_version_id)`` to ``kb.document_versions (tenant_id, document_id, id)``, so the pinned
  version always belongs to the evidence document of the same school. Nullable: requests
  submitted before this revision keep NULL and the service checks their latest ready version
  instead. ``sos_app`` keeps INSERT and SELECT on the table and still has no UPDATE on this column
  (the 0014 column grants), so a pinned version never changes.
- **Revert of an import keeps Tally party links** (audit 2026-10-05, hardening "Tally").
  ``ops.tally_party_links_student_fk`` was ``ON DELETE CASCADE``: reverting the import that
  created a student silently dropped the links a person had made to it. It is now ``NO
  ACTION``, so the revert's delete fails with a foreign-key violation that the students service
  already answers as ``409 import_has_dependents``. Offboarding is unaffected: the tally module
  is purged first (``offboarding.yaml``).
- **Exam names are unique per year whatever their case** (audit 2026-10-06, hardening "Exam
  names"). A unique index ``exams_name_per_year_ci`` on ``(tenant_id, academic_year_id,
  lower(name))``. Backward compatible: if a school already has two exams whose names differ
  only in case, the index is NOT created (a WARNING names no school or exam) and the existing
  case-sensitive constraint plus the service's case-insensitive check stay in force; the index
  is created by re-running this step after the duplicates are renamed.

Downgrade restores the cascade, drops the index and drops the pinned version column (the pins
are lost; approval then checks the latest ready version, as before this revision).
"""

from __future__ import annotations

from alembic import op

revision = "0049_open_items"
down_revision = "0047_security_decisions"
branch_labels = None
depends_on = None

# --- ob-workflow block (change-request evidence pin, tally links, exam names) -------------------

EVIDENCE_PIN_UP = (
    "ALTER TABLE sis.change_requests ADD COLUMN evidence_version_id uuid",
    "ALTER TABLE sis.change_requests ADD CONSTRAINT change_requests_evidence_version_fk "
    "FOREIGN KEY (tenant_id, evidence_document_id, evidence_version_id) "
    "REFERENCES kb.document_versions (tenant_id, document_id, id)",
)
EVIDENCE_PIN_DOWN = (
    "ALTER TABLE sis.change_requests DROP CONSTRAINT IF EXISTS change_requests_evidence_version_fk",
    "ALTER TABLE sis.change_requests DROP COLUMN IF EXISTS evidence_version_id",
)

TALLY_LINKS_UP = (
    "ALTER TABLE ops.tally_party_links DROP CONSTRAINT tally_party_links_student_fk",
    "ALTER TABLE ops.tally_party_links ADD CONSTRAINT tally_party_links_student_fk "
    "FOREIGN KEY (tenant_id, student_id) REFERENCES sis.students (tenant_id, id)",
)
TALLY_LINKS_DOWN = (
    "ALTER TABLE ops.tally_party_links DROP CONSTRAINT tally_party_links_student_fk",
    "ALTER TABLE ops.tally_party_links ADD CONSTRAINT tally_party_links_student_fk "
    "FOREIGN KEY (tenant_id, student_id) REFERENCES sis.students (tenant_id, id) "
    "ON DELETE CASCADE",
)

EXAM_NAMES_UP = (
    """
    DO $$
    BEGIN
      CREATE UNIQUE INDEX exams_name_per_year_ci
        ON sis.exams (tenant_id, academic_year_id, lower(name));
    EXCEPTION WHEN unique_violation THEN
      RAISE WARNING 'exams_name_per_year_ci not created: exam names that differ only in case '
        'exist; rename them and run this step again';
    END
    $$
    """,
)
EXAM_NAMES_DOWN = ("DROP INDEX IF EXISTS sis.exams_name_per_year_ci",)

# --- end of ob-workflow block -------------------------------------------------------------------


def upgrade() -> None:
    for statement in (*EVIDENCE_PIN_UP, *TALLY_LINKS_UP, *EXAM_NAMES_UP):
        op.execute(statement)


def downgrade() -> None:
    for statement in (*EXAM_NAMES_DOWN, *TALLY_LINKS_DOWN, *EVIDENCE_PIN_DOWN):
        op.execute(statement)
