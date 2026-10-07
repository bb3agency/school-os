"""Open security audit items, wave 6 (owner decisions of 2026-10-07).

- A-01 (audit-2026-10-05-app-logic): a DQ **blocker** that a run no longer finds because a value
  was written without evidence is no longer ``resolved``/``auto_cleared``; it becomes
  ``needs_confirmation``. That status counts as unresolved everywhere (certificates, exports,
  pre-check lists), so the record cannot be printed until a ``dq.findings.waive`` holder with a
  fresh MFA sign-in confirms the correction through ``POST /dq/findings/{id}/resolve`` (audited
  ``dq.finding.resolved``). Expand only: one more value in the status CHECK.

Downgrade: rows waiting for confirmation go back to ``open`` (they keep blocking, which fails
safe: the previous code then asks for a resolution or a waiver as before), then the CHECK is
restored.

Revision ID: 0049_open_items
Revises: 0047_security_decisions
Create Date: 2026-10-07
"""

from __future__ import annotations

from alembic import op

revision = "0049_open_items"
down_revision = "0047_security_decisions"
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


def upgrade() -> None:
    op.execute(_A01_UP)


def downgrade() -> None:
    op.execute(UNFORCE)
    op.execute(_A01_REOPEN)
    op.execute(REFORCE)
    op.execute(_A01_DOWN)
