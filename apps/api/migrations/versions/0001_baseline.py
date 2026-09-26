"""Baseline: schemas, roles and extensions come from infra/db/bootstrap.sql.

This revision asserts the bootstrap ran, so a migration never silently creates objects
in a database without the role separation that RLS depends on.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-09-26
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None

REQUIRED_SCHEMAS = ("core", "sis", "kb", "audit", "ops", "platform")
REQUIRED_ROLES = (
    "sos_owner",
    "sos_migrator",
    "sos_app",
    "sos_platform",
    "sos_readonly",
    "sos_definer",
)


def upgrade() -> None:
    bind = op.get_bind()
    schemas = {r[0] for r in bind.execute(text("SELECT nspname FROM pg_namespace"))}
    roles = {r[0] for r in bind.execute(text("SELECT rolname FROM pg_roles"))}
    missing = [s for s in REQUIRED_SCHEMAS if s not in schemas] + [
        r for r in REQUIRED_ROLES if r not in roles
    ]
    if missing:
        raise RuntimeError(f"run infra/db/bootstrap.sql first; missing: {', '.join(missing)}")


def downgrade() -> None:
    """Nothing to undo: bootstrap objects are managed outside Alembic."""
