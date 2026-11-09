"""Write-off of fee dues as a maker-checker adjustment (FM-01; FR-FEE-041; ADR-0042 follow-up).

Earlier-year dues (arrears) need no new table: fee accounts are already per academic year and
stay open until they are settled. What the database lacks is a reason code for management
writing off what a family will not pay: an adjustment with ``reason_code = 'write_off'``,
requested with ``fee.manage`` and approved by another holder of ``fee.concession.approve``
(principal or accountant, step-up), exactly like every other adjustment (FR-FEE-007).

Expand only:
- ``fin.adjustments.reason_code`` also accepts ``write_off`` (the CHECK is replaced by a wider
  one; existing rows all satisfy it).
- ``adjustments_write_off_shape``: a write-off names a head and lowers what is due
  (``amount < 0``); the service checks the same with field-level codes and never lets it take a
  line below zero (409 ``adjustment_exceeds_due``).

Downgrade: restores the narrower CHECK and drops the shape rule. It fails while any write-off
exists (the narrower CHECK is validated against every row, whatever RLS would show): an approved
write-off is a ledger fact the old reason list cannot describe, so it is never rewritten.

Revision ID: 0083_fee_arrears
Revises: 0082_audit_ym_fixes
Create Date: 2026-11-09
"""

from __future__ import annotations

from alembic import op

revision = "0083_fee_arrears"
down_revision = "0082_audit_ym_fixes"
branch_labels = None
depends_on = None

_OLD = "('late_fee_waiver','plan_change','concession_reversal','correction','other')"
_NEW = "('late_fee_waiver','plan_change','concession_reversal','correction','write_off','other')"


def upgrade() -> None:
    op.execute("ALTER TABLE fin.adjustments DROP CONSTRAINT adjustments_reason_code_check")
    op.execute(
        "ALTER TABLE fin.adjustments ADD CONSTRAINT adjustments_reason_code_check "
        f"CHECK (reason_code IN {_NEW})"
    )
    op.execute(
        "ALTER TABLE fin.adjustments ADD CONSTRAINT adjustments_write_off_shape "
        "CHECK (reason_code <> 'write_off' OR (head_id IS NOT NULL AND amount < 0))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE fin.adjustments DROP CONSTRAINT IF EXISTS adjustments_write_off_shape")
    op.execute("ALTER TABLE fin.adjustments DROP CONSTRAINT adjustments_reason_code_check")
    op.execute(
        "ALTER TABLE fin.adjustments ADD CONSTRAINT adjustments_reason_code_check "
        f"CHECK (reason_code IN {_OLD})"
    )
