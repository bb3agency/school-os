"""Open security audit items, wave 6 (owner decisions of 2026-10-07).

- A-01 (audit-2026-10-05-app-logic): a DQ **blocker** that a run no longer finds because a value
  was written without evidence is no longer ``resolved``/``auto_cleared``; it becomes
  ``needs_confirmation``. That status counts as unresolved everywhere (certificates, exports,
  pre-check lists), so the record cannot be printed until a ``dq.findings.waive`` holder with a
  fresh MFA sign-in confirms the correction through ``POST /dq/findings/{id}/resolve`` (audited
  ``dq.finding.resolved``). Expand only: one more value in the status CHECK.
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
- **Critical announcements are two-person** (audit 2026-10-05, hardening "Announcements").
  A critical announcement needs a second operator's approval before schools see it (two-person,
  like offboarding and emergency break-glass; owner decision 2026-10-07). Control-plane schema
  only (no RLS, written only by ``sos_platform``, no student data):

  - ``platform.announcements`` gains ``submitted_by``/``submitted_at`` (the operator who asked for
    the critical banner to go out, and when) and ``approved_by``/``approved_at`` (the second
    operator);
  - the status set gains ``pending_approval`` (a critical announcement waiting for its second
    operator; never published to schools);
  - ``announcements_critical_two_person``: a scheduled critical announcement carries an approval
    by an operator other than the one who submitted it. It is added ``NOT VALID`` so critical
    banners published before this revision stay readable and cancellable; every new or changed
    row is checked.

Downgrade, in reverse order:
- announcements: pending approvals become drafts (nothing was published), then the
  constraints, the status value and the columns are dropped;
- the exam-name index is dropped, the Tally link cascade is restored, and the pinned evidence
  version column is dropped (approval then checks the latest ready version);
- DQ rows waiting for confirmation go back to ``open`` (they keep blocking, which fails safe),
  then the status CHECK is restored.

Revision ID: 0049_open_items
Revises: 0048_narrow_app_grants
Create Date: 2026-10-07
"""

from __future__ import annotations

from alembic import op

revision = "0049_open_items"
down_revision = "0048_narrow_app_grants"
branch_labels = None
depends_on = None

# --- A-01: DQ findings waiting for a confirmation ------------------------------------------------

_A01_UP = """
ALTER TABLE sis.dq_findings DROP CONSTRAINT dq_findings_status_check;
ALTER TABLE sis.dq_findings ADD CONSTRAINT dq_findings_status_check
  CHECK (status IN ('open','resolved','waived','reopened','needs_confirmation'));
"""

_A01_DOWN = """
ALTER TABLE sis.dq_findings DROP CONSTRAINT dq_findings_status_check;
ALTER TABLE sis.dq_findings ADD CONSTRAINT dq_findings_status_check
  CHECK (status IN ('open','resolved','waived','reopened'));
"""

# Every school's waiting rows go back to open. The owner lifts FORCE for this one statement
# (as 0042_apaar_id does); FORCE is restored in the same transaction.
UNFORCE = "ALTER TABLE sis.dq_findings NO FORCE ROW LEVEL SECURITY"  # nosemgrep: sos-migration-rls-bypass -- owner maps a status value back on downgrade; FORCE is restored in the same transaction
REFORCE = "ALTER TABLE sis.dq_findings FORCE ROW LEVEL SECURITY"
_A01_REOPEN = (
    "UPDATE sis.dq_findings SET status = 'open', version = version + 1, updated_at = now() "
    "WHERE status = 'needs_confirmation'"
)

# --- Change-request evidence pin, Tally links, exam names (audits 2026-10-05/06) -----------------

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

# --- Critical announcements are two-person (control plane) ---------------------------------------

ANNOUNCEMENTS_UP = """
ALTER TABLE platform.announcements
  ADD COLUMN submitted_by uuid REFERENCES platform.operators(id),
  ADD COLUMN submitted_at timestamptz,
  ADD COLUMN approved_by  uuid REFERENCES platform.operators(id),
  ADD COLUMN approved_at  timestamptz;
ALTER TABLE platform.announcements DROP CONSTRAINT announcements_status_check;
ALTER TABLE platform.announcements ADD CONSTRAINT announcements_status_check
  CHECK (status IN ('draft','pending_approval','scheduled','cancelled'));
ALTER TABLE platform.announcements ADD CONSTRAINT announcements_submitted
  CHECK ((submitted_by IS NULL) = (submitted_at IS NULL));
ALTER TABLE platform.announcements ADD CONSTRAINT announcements_approved
  CHECK ((approved_by IS NULL) = (approved_at IS NULL));
ALTER TABLE platform.announcements ADD CONSTRAINT announcements_pending_submitted
  CHECK (status <> 'pending_approval' OR (submitted_by IS NOT NULL AND approved_by IS NULL));
-- Two different operators put a critical banner in front of schools (SEC-029 pattern).
ALTER TABLE platform.announcements ADD CONSTRAINT announcements_critical_two_person
  CHECK (severity <> 'critical' OR status <> 'scheduled'
         OR (approved_by IS NOT NULL AND submitted_by IS NOT NULL
             AND approved_by <> submitted_by)) NOT VALID;
"""

ANNOUNCEMENTS_DOWN = """
UPDATE platform.announcements SET status = 'draft' WHERE status = 'pending_approval';
ALTER TABLE platform.announcements DROP CONSTRAINT announcements_critical_two_person;
ALTER TABLE platform.announcements DROP CONSTRAINT announcements_pending_submitted;
ALTER TABLE platform.announcements DROP CONSTRAINT announcements_approved;
ALTER TABLE platform.announcements DROP CONSTRAINT announcements_submitted;
ALTER TABLE platform.announcements DROP CONSTRAINT announcements_status_check;
ALTER TABLE platform.announcements ADD CONSTRAINT announcements_status_check
  CHECK (status IN ('draft','scheduled','cancelled'));
ALTER TABLE platform.announcements
  DROP COLUMN approved_at,
  DROP COLUMN approved_by,
  DROP COLUMN submitted_at,
  DROP COLUMN submitted_by;
"""


def upgrade() -> None:
    op.execute(_A01_UP)
    for statement in (*EVIDENCE_PIN_UP, *TALLY_LINKS_UP, *EXAM_NAMES_UP):
        op.execute(statement)
    op.execute(ANNOUNCEMENTS_UP)


def downgrade() -> None:
    op.execute(ANNOUNCEMENTS_DOWN)
    for statement in (*EXAM_NAMES_DOWN, *TALLY_LINKS_DOWN, *EVIDENCE_PIN_DOWN):
        op.execute(statement)
    op.execute(UNFORCE)
    op.execute(_A01_REOPEN)
    op.execute(REFORCE)
    op.execute(_A01_DOWN)
