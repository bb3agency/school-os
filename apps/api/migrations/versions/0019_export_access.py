"""Export access and step-up: permission catalog changes (ADR-0021; FR-EXP-004, SEC-003, SEC-005).

Data migration on the global catalog ``core.permissions`` (no RLS; ``0004_authz_seed``'s
docstring: later catalog changes get their own revision that re-runs the upsert):

- adds ``export.read_all`` (see every export of the school, not its files) and
  ``export.download_any`` (download other staff's exports, step-up);
- marks ``export.board`` and ``export.portal`` as step-up permissions (creating any export needs
  a recent MFA sign-in, ADR-0021 decision 1).

The rows are written out here rather than read from ``permissions.yaml`` so this revision means
the same thing whatever the YAML says later.

**Role grants of existing schools are NOT changed here.** System roles are tenant rows under
FORCE RLS: the migrator (acting as the table owner ``sos_owner``) cannot see or write them, and
no allowlisted definer function grants permissions (docs/05 §3.3-3.4, §14). New schools get the
grants from ``roles.yaml`` at provisioning (``identity.service.clone_system_roles``, which is
idempotent and adds missing grants to existing system roles, audited). Delivering the new grants
to schools provisioned before this revision is an open item (ADR-0021 "Follow-up work").

Downgrade restores the previous step-up flags and deletes the two new keys, each in its own
savepoint, keeping a key that a role still holds (the foreign-key check sees rows RLS hides from
the migrator), exactly like ``0004_authz_seed``'s downgrade. Keeping it loses nothing: the code
of 0018 never uses it, and re-upgrading upserts the same rows.

Revision ID: 0019_export_access
Revises: 0018_extraction_redaction
Create Date: 2026-09-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0019_export_access"
down_revision = "0018_extraction_redaction"
branch_labels = None
depends_on = None

NEW_PERMISSIONS: tuple[dict[str, object], ...] = (
    {
        "key": "export.read_all",
        "description": (
            "See every export of the school (who made it, what, when and its status; not the files)"
        ),
        "sensitivity": "sensitive",
        "step_up": False,
        "is_platform": False,
    },
    {
        "key": "export.download_any",
        "description": "Download exports made by other staff (recent MFA sign-in every time)",
        "sensitivity": "critical",
        "step_up": True,
        "is_platform": False,
    },
)
STEP_UP_KEYS: tuple[str, ...] = ("export.board", "export.portal")

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
SET_STEP_UP = sa.text("UPDATE core.permissions SET step_up = :v WHERE key = :k")


def upgrade() -> None:
    bind = op.get_bind()
    for row in NEW_PERMISSIONS:
        bind.execute(UPSERT, row)
    for key in STEP_UP_KEYS:
        bind.execute(SET_STEP_UP, {"k": key, "v": True})


def downgrade() -> None:
    bind = op.get_bind()
    for key in STEP_UP_KEYS:
        bind.execute(SET_STEP_UP, {"k": key, "v": False})
    for row in NEW_PERMISSIONS:
        savepoint = bind.begin_nested()
        try:
            bind.execute(sa.text("DELETE FROM core.permissions WHERE key = :k"), {"k": row["key"]})
            savepoint.commit()
        except sa.exc.IntegrityError:
            savepoint.rollback()
