"""Migration 0004_authz_seed: core.permissions equals the YAML catalog (FR-IAM-011, SEC-003)."""

from __future__ import annotations

import pytest
from sqlalchemy import Engine, text

from app.authz import catalog

pytestmark = pytest.mark.db


def test_FR_IAM_011_core_permissions_equal_catalog(app_engine: Engine) -> None:
    with app_engine.connect() as conn:
        rows = conn.execute(
            text("SELECT key, description, sensitivity, step_up, is_platform FROM core.permissions")
        ).all()
    db = {r.key: (r.description, r.sensitivity, r.step_up, r.is_platform) for r in rows}
    expected = {
        k: (p.description, p.sensitivity, p.step_up, p.is_platform)
        for k, p in catalog.permission_catalog().items()
    }
    assert db == expected


def test_SEC_003_app_role_cannot_change_catalog(app_engine: Engine) -> None:
    with app_engine.connect() as conn, pytest.raises(Exception, match="permission denied"):
        conn.execute(
            text("INSERT INTO core.permissions (key, description) VALUES ('rogue.grant', 'x')")
        )
