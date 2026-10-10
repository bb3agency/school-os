"""A school declares its own boards (FR-TEN-020; ADR-0041).

``core.tenants.boards`` (text[], 0003) was written only at provisioning. A school now chooses
its boards in its settings (AP State Board, CBSE, CISCE), so ``sos_app`` gets a column-level
UPDATE on ``boards`` next to its existing ``name, settings, version``. RLS on ``core.tenants``
(policy ``own_tenant``, ENABLE + FORCE) is unchanged, so the app can change only its own row.

Nothing else changes in the schema: class boards and the operating mode live in the validated
``settings`` object, and import presets and templates are packaged configuration
(``app/imports/presets/``), not tables.

Downgrade revokes the column grant (the previous code never writes ``boards``); stored values
are kept.

Revision ID: 0051_boards_import_presets
Revises: 0050_verified_answer_drafter
Create Date: 2026-10-10
"""

from __future__ import annotations

from alembic import op

revision = "0051_boards_import_presets"
down_revision = "0050_verified_answer_drafter"
branch_labels = None
depends_on = None

UP = "GRANT UPDATE (boards) ON core.tenants TO sos_app"
DOWN = "REVOKE UPDATE (boards) ON core.tenants FROM sos_app"


def upgrade() -> None:
    op.execute(UP)


def downgrade() -> None:
    op.execute(DOWN)
