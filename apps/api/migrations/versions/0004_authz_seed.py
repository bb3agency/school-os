"""Seed the permission catalog from app/authz/permissions.yaml (FR-IAM-011, SEC-003).

Data migration: upserts every catalog entry (tenant, implicit and ``platform.*``) into
``core.permissions``. The YAML is read at migration time, so the catalog in the database is
always the one shipped with the code at that revision; later catalog changes get their own
revision that re-runs the same upsert. System roles are NOT created here: they are cloned into
each tenant at provisioning (``tenancy.POST_PROVISION_HOOKS``, identity service), because roles
are tenant rows under RLS.

Downgrade deletes the seeded keys. It fails (FK violation) while any tenant role still holds
one of them, which is intended: downgrading below the catalog with live tenants is not safe.

Revision ID: 0004_authz_seed
Revises: 0003_core_schema
Create Date: 2026-09-26
"""

from __future__ import annotations

from importlib import resources
from typing import Any

import sqlalchemy as sa
import yaml
from alembic import op

revision = "0004_authz_seed"
down_revision = "0003_core_schema"
branch_labels = None
depends_on = None


def _catalog() -> list[dict[str, Any]]:
    raw = yaml.safe_load(
        resources.files("app.authz").joinpath("permissions.yaml").read_text("utf-8")
    )
    rows: list[dict[str, Any]] = []
    for key, spec in raw["permissions"].items():
        rows.append(
            {
                "key": key,
                "description": spec["description"],
                "sensitivity": spec.get("sensitivity", "normal"),
                "step_up": bool(spec.get("step_up", False)),
                "is_platform": bool(spec.get("is_platform", False)),
            }
        )
    return rows


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
    for row in _catalog():
        bind.execute(UPSERT, row)


def downgrade() -> None:
    keys = [row["key"] for row in _catalog()]
    op.get_bind().execute(
        sa.text("DELETE FROM core.permissions WHERE key = ANY(:keys)"), {"keys": keys}
    )
