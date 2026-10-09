"""The school's AI answer allowance from its AI answer bundle (FR-KB-011, NFR-CST-001, ADR-0038).

Owner decision 2026-10-03: a school's monthly AI budget is derived from its AI answer bundle.
The control plane hands the bundle's included answers to the school through the lifecycle-style
call ``tenancy.service.set_ai_answer_allowance`` (ADR-0020 amendment B3): it opens the school's
own ``tenant_session`` (sos_app, RLS applies), stores one number in ``core.tenants.settings``
and audits it in the same transaction. The school cannot set it through its settings form.
Synthetic schools only.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.core.db import tenant_session
from app.core.errors import NotFound
from app.tenancy import service
from app.tenancy.schemas import TenantSettingsPatch

pytestmark = pytest.mark.db

MakeTenant = Callable[..., uuid.UUID]


def _settings(admin: Engine, tenant_id: uuid.UUID) -> dict[str, Any]:
    with admin.connect() as c:
        raw: Any = c.execute(
            text("SELECT settings FROM core.tenants WHERE id = :t"), {"t": tenant_id}
        ).scalar_one()
    return dict(raw or {})


def _events(admin: Engine, tenant_id: uuid.UUID) -> list[tuple[str, str, dict[str, Any]]]:
    with admin.connect() as c:
        rows = c.execute(
            text(
                "SELECT action, actor_type, summary FROM audit.events WHERE tenant_id = :t "
                "AND action = 'tenant.ai_allowance_set' ORDER BY occurred_at, id"
            ),
            {"t": tenant_id},
        ).all()
    return [(r[0], r[1], dict(r[2])) for r in rows]


def test_FR_KB_011_allowance_is_set_once_and_audited(
    make_tenant: MakeTenant, admin_engine: Engine
) -> None:
    school = make_tenant()
    with tenant_session(school) as s:
        assert service.ai_answer_allowance(s) is None  # no bundle yet
    assert service.set_ai_answer_allowance(school, 1000) is True
    assert service.set_ai_answer_allowance(school, 1000) is False  # idempotent: no new event
    with tenant_session(school) as s:
        assert service.ai_answer_allowance(s) == 1000
    assert _settings(admin_engine, school)["ai_answers_per_month"] == 1000
    assert _events(admin_engine, school) == [
        ("tenant.ai_allowance_set", "system", {"included_answers": 1000})
    ]
    assert service.set_ai_answer_allowance(school, None) is True  # the bundle was removed
    with tenant_session(school) as s:
        assert service.ai_answer_allowance(s) is None
    assert "ai_answers_per_month" not in _settings(admin_engine, school)
    assert _events(admin_engine, school)[-1] == (
        "tenant.ai_allowance_set",
        "system",
        {"included_answers": 0, "bundle": "none"},
    )


def test_FR_KB_011_allowance_keeps_school_settings_and_survives_their_edit(
    make_tenant: MakeTenant, admin_engine: Engine
) -> None:
    school = make_tenant()
    with admin_engine.begin() as c:
        c.execute(
            text("UPDATE core.tenants SET settings = CAST(:s AS jsonb) WHERE id = :t"),
            {
                "s": json.dumps({"ai_monthly_budget_inr": 1200, "idle_timeout_minutes": 20}),
                "t": school,
            },
        )
    service.set_ai_answer_allowance(school, 300)
    stored = _settings(admin_engine, school)
    assert (stored["ai_monthly_budget_inr"], stored["idle_timeout_minutes"]) == (1200, 20)
    # A school settings edit afterwards keeps the allowance (it is not a school setting).
    with tenant_session(school) as s:
        version = service.get_tenant(s).version
        service.update_tenant_settings(
            s, TenantSettingsPatch(idle_timeout_minutes=25), expected_version=version
        )
    assert _settings(admin_engine, school)["ai_answers_per_month"] == 300
    # The school cannot set it itself: it is not part of the settings form.
    with pytest.raises(ValueError, match="ai_answers_per_month"):
        TenantSettingsPatch.model_validate({"ai_answers_per_month": 99_999})


def test_FR_KB_011_allowance_rejects_nonsense_and_unknown_schools(make_tenant: MakeTenant) -> None:
    school = make_tenant()
    for bad in (0, -1):
        with pytest.raises(ValueError, match="positive"):
            service.set_ai_answer_allowance(school, bad)
    with pytest.raises(NotFound):
        service.set_ai_answer_allowance(uuid.uuid4(), 300)


def test_invariant_1_allowance_is_written_only_in_the_schools_own_session(
    make_tenant: MakeTenant, admin_engine: Engine
) -> None:
    school, other = make_tenant(), make_tenant()
    service.set_ai_answer_allowance(school, 3000)
    assert "ai_answers_per_month" not in _settings(admin_engine, other)
    with tenant_session(other) as s:
        assert service.ai_answer_allowance(s) is None
