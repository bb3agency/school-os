"""Board and portal readiness: permission catalog (ADR-0040; FR-DQ-040..FR-DQ-046, SEC-003).

Data migration on the global catalog ``core.permissions`` (no RLS; ``0004_authz_seed``'s
docstring: later catalog changes get their own revision that re-runs the upsert):

- ``dq.readiness.read``: see board and portal readiness and print parent verification slips;
- ``dq.readiness.manage``: run readiness checks (they store DQ-031 findings).

No table changes: readiness is computed from the per-source values and stored as findings in
``sis.dq_findings`` (rule DQ-031, profile key), which already has RLS and the composite keys.

The rows are written out here rather than read from ``permissions.yaml`` so this revision means
the same thing whatever the YAML says later. **Role grants of existing schools are NOT changed
here** (system roles are tenant rows under FORCE RLS; see 0019): the release needs the
post-migration system-role sync (``python -m app.identity.sync_system_roles --apply``,
ADR-0022, docs/10 §8).

Downgrade deletes the two keys, each in its own savepoint, keeping a key that a role still holds
(the foreign-key check sees rows RLS hides from the migrator), like ``0019_export_access``.

Revision ID: 0053_readiness
Revises: 0052_boards_import_presets
Create Date: 2026-10-10
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0053_readiness"
down_revision = "0052_boards_import_presets"
branch_labels = None
depends_on = None

NEW_PERMISSIONS: tuple[dict[str, object], ...] = (
    {
        "key": "dq.readiness.read",
        "description": "See board and portal readiness and print parent verification slips",
        "sensitivity": "normal",
        "step_up": False,
        "is_platform": False,
    },
    {
        "key": "dq.readiness.manage",
        "description": "Run board and portal readiness checks",
        "sensitivity": "normal",
        "step_up": False,
        "is_platform": False,
    },
)

UPSERT = sa.text(
    """
    INSERT INTO core.permissions (key, description, sensitivity, step_up, is_platform)
    VALUES (:key, :description, :sensitivity, :step_up, :is_platform)
    ON CONFLICT (key) DO UPDATE SET
      description = EXCLUDED.description,
      sensitivity = EXCLUDED.sensitivity,
      step_up = EXCLUDED.step_up,
      is_platform = EXCLUDED.is_platform
    """
)


def upgrade() -> None:
    bind = op.get_bind()
    for row in NEW_PERMISSIONS:
        bind.execute(UPSERT, row)


def downgrade() -> None:
    bind = op.get_bind()
    for row in NEW_PERMISSIONS:
        savepoint = bind.begin_nested()
        try:
            bind.execute(sa.text("DELETE FROM core.permissions WHERE key = :k"), {"k": row["key"]})
            savepoint.commit()
        except sa.exc.IntegrityError:
            savepoint.rollback()
