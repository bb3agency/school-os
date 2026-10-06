"""Owner decisions on the 2026-10-06 API route audit (R-17).

Data migration on the global catalog ``core.permissions`` (no RLS; ``0004_authz_seed``'s
docstring: later catalog changes get their own revision that re-runs the upsert):

- R-17 adds ``support.manage`` (read and reply on every support ticket of the school). Without
  it a member reads and answers only the tickets they opened themselves; ticket subjects and
  messages may name students (OWASP API3, DPDP purpose limit). ``support.ticket.create`` gets
  the matching description (open tickets; read and reply on the ones you opened).

The rows are written out here rather than read from ``permissions.yaml`` so this revision means
the same thing whatever the YAML says later.

**Role grants of existing schools are NOT changed here** (system roles are tenant rows under
FORCE RLS; see ``0019_export_access``). New schools get ``support.manage`` for owner, principal
and office admin from ``roles.yaml`` at provisioning; existing schools get it from
``python -m app.identity.sync_system_roles --apply`` (docs/10 runbook). Until then their owner,
principal and office admin see only the tickets they opened themselves: the change fails safe.

Downgrade deletes the key in a savepoint and keeps it while a role still holds it (the
foreign-key check sees rows RLS hides from the migrator), exactly like ``0019_export_access``.

Revision ID: 0047_security_decisions
Revises: 0046_audit_append_guard
Create Date: 2026-10-06
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0047_security_decisions"
down_revision = "0046_audit_append_guard"
branch_labels = None
depends_on = None

NEW_PERMISSIONS: tuple[dict[str, object], ...] = (
    {
        "key": "support.manage",
        "description": "Read and reply on every support ticket of the school",
        "sensitivity": "sensitive",
        "step_up": False,
        "is_platform": False,
    },
)

TICKET_CREATE_DESCRIPTION = {
    "new": "Open support tickets and read and reply on the ones you opened",
    "old": "Open and read the school's own support tickets",
}
SET_DESCRIPTION = sa.text("UPDATE core.permissions SET description = :d WHERE key = :k")

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
    bind.execute(
        SET_DESCRIPTION, {"k": "support.ticket.create", "d": TICKET_CREATE_DESCRIPTION["new"]}
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        SET_DESCRIPTION, {"k": "support.ticket.create", "d": TICKET_CREATE_DESCRIPTION["old"]}
    )
    for row in NEW_PERMISSIONS:
        savepoint = bind.begin_nested()
        try:
            bind.execute(sa.text("DELETE FROM core.permissions WHERE key = :k"), {"k": row["key"]})
            savepoint.commit()
        except sa.exc.IntegrityError:
            savepoint.rollback()
