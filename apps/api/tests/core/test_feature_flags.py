"""Tenant-side feature-flag reads (FR-PLT-022; CLAUDE.md invariant 1: ``sos_app`` may only read
``platform.feature_flags``).

``app.core.feature_flags`` must evaluate exactly like the control plane's ``app.platform.flags``
(school override wins, then the global row with its stable % rollout, unknown = off), so the
two copies cannot drift; and it must work in a school's ``tenant_session``.
"""

from __future__ import annotations

import itertools
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.core import feature_flags
from app.core.db import tenant_session
from app.platform import flags as platform_flags

TENANTS = [uuid.UUID(int=i * 7919) for i in range(1, 60)]


def _rows(
    tenant: uuid.UUID, override: bool | None, global_on: bool | None, percent: int | None
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if override is not None:
        rows.append({"tenant_id": tenant, "enabled": override, "rollout_percent": None})
    if global_on is not None:
        rows.append({"tenant_id": None, "enabled": global_on, "rollout_percent": percent})
    return rows


@pytest.mark.parametrize(
    ("override", "global_on", "percent"),
    list(itertools.product([None, True, False], [None, True, False], [None, 0, 37, 100])),
)
def test_FR_PLT_022_core_reader_matches_the_control_plane(
    override: bool | None, global_on: bool | None, percent: int | None
) -> None:
    for tenant in TENANTS:
        rows = _rows(tenant, override, global_on, percent)
        assert feature_flags.evaluate(rows, "kb.ask.enabled", tenant) == platform_flags.evaluate(
            rows, "kb.ask.enabled", tenant
        )
        assert feature_flags.bucket("kb.ask.enabled", tenant) == platform_flags.bucket(
            "kb.ask.enabled", tenant
        )


@pytest.mark.db
def test_invariant_1_sos_app_reads_flags_in_a_tenant_session(
    admin_engine: Engine, app_engine: Engine
) -> None:
    key = f"test.flag_{uuid.uuid4().hex[:8]}"
    school, other = uuid.uuid4(), uuid.uuid4()
    with admin_engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO platform.feature_flags (id, key, tenant_id, enabled) VALUES "
                "(:a, :k, NULL, false), (:b, :k, :t, true)"
            ),
            {"a": uuid.uuid4(), "b": uuid.uuid4(), "k": key, "t": school},
        )
    with tenant_session(school) as s:
        assert feature_flags.is_enabled(key, school, session=s) is True
    assert feature_flags.is_enabled(key, other) is False
    assert feature_flags.is_enabled("test.unknown_flag", school) is False
