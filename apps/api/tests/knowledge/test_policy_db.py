"""The school's AI switch and budget as the gateway sees them (FR-KB-011, NFR-CST-001).

``SchoolAiPolicy``: AI is on only when the school's ``ai_features_enabled`` setting AND its
``kb.ask.enabled`` flag are on; the budget is ``ai_monthly_budget_inr``. Read through
``tenancy.service`` and ``core.feature_flags`` (knowledge never imports the control plane).
"""

from __future__ import annotations

import importlib.util
import sys
import uuid
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.knowledge.config.llm import load_llm_config
from app.knowledge.gateway.budget import bundle_budget_inr
from app.knowledge.policy import SchoolAiPolicy
from app.tenancy import service as tenancy

pytestmark = pytest.mark.db


def _load(name: str, path: Path) -> ModuleType:
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


K = _load("sos_test_ask_support", Path(__file__).with_name("ask_support.py"))
world = K.W.world


def _flag(admin: Engine, tenant_id: uuid.UUID, enabled: bool | None) -> None:
    with admin.begin() as c:
        c.execute(
            text("DELETE FROM platform.feature_flags WHERE key = :k AND tenant_id = :t"),
            {"k": K.ASK_FLAG, "t": tenant_id},
        )
        if enabled is not None:
            c.execute(
                text(
                    "INSERT INTO platform.feature_flags (id, key, tenant_id, enabled) "
                    "VALUES (:i, :k, :t, :e)"
                ),
                {"i": uuid.uuid4(), "k": K.ASK_FLAG, "t": tenant_id, "e": enabled},
            )


def _settings(admin: Engine, tenant_id: uuid.UUID, patch: str | None) -> None:
    with admin.begin() as c:
        if patch is None:
            c.execute(
                text(
                    "UPDATE core.tenants SET settings = settings - 'ai_features_enabled' "
                    "- 'ai_monthly_budget_inr' WHERE id = :t"
                ),
                {"t": tenant_id},
            )
        else:
            c.execute(
                text(
                    "UPDATE core.tenants SET settings = settings || CAST(:p AS jsonb) WHERE id = :t"
                ),
                {"p": patch, "t": tenant_id},
            )


def test_FR_KB_011_ai_needs_the_setting_and_the_flag(world: Any, admin_engine: Engine) -> None:
    school = world.b.tenant_id
    try:
        _flag(admin_engine, school, None)
        _settings(admin_engine, school, '{"ai_monthly_budget_inr": 1200}')
        off = SchoolAiPolicy(ttl_s=0.001).settings_for(school)
        assert off.ai_enabled is False  # unknown flag = off (fail closed)
        assert off.monthly_budget_inr == Decimal(1200)

        _flag(admin_engine, school, True)
        assert SchoolAiPolicy(ttl_s=0.001).settings_for(school).ai_enabled is True

        _settings(admin_engine, school, '{"ai_features_enabled": false}')
        assert SchoolAiPolicy(ttl_s=0.001).settings_for(school).ai_enabled is False
    finally:
        _settings(admin_engine, school, None)
        _flag(admin_engine, school, True)


def test_policy_answers_are_cached_briefly(world: Any, admin_engine: Engine) -> None:
    now = [0.0]
    policy = SchoolAiPolicy(ttl_s=15, clock=lambda: now[0])
    school = world.b.tenant_id
    _flag(admin_engine, school, True)
    assert policy.settings_for(school).ai_enabled is True
    _flag(admin_engine, school, False)
    try:
        assert policy.settings_for(school).ai_enabled is True  # within the TTL
        now[0] = 16.0
        assert policy.settings_for(school).ai_enabled is False
    finally:
        _flag(admin_engine, school, True)


def test_FR_KB_011_budget_comes_from_the_ai_answer_bundle(world: Any, admin_engine: Engine) -> None:
    """Owner decision 2026-10-03: with a bundle, the budget is derived from its included answers
    (``models.yaml`` ``budget.bundle``); without one, the school's own setting applies."""
    school = world.b.tenant_id
    try:
        _flag(admin_engine, school, True)
        _settings(admin_engine, school, '{"ai_monthly_budget_inr": 1200}')
        assert tenancy.set_ai_answer_allowance(school, 1000)
        got = SchoolAiPolicy(ttl_s=0.001).settings_for(school)
        assert got.monthly_budget_inr == bundle_budget_inr(load_llm_config(), 1000)
        assert got.monthly_budget_inr != Decimal(1200)
        assert got.ai_enabled is True
        # The school's AI switch still applies on top of the bundle.
        _settings(admin_engine, school, '{"ai_features_enabled": false}')
        assert SchoolAiPolicy(ttl_s=0.001).settings_for(school).ai_enabled is False
        _settings(admin_engine, school, '{"ai_features_enabled": true}')
        # Bundle removed: back to the school's own setting.
        assert tenancy.set_ai_answer_allowance(school, None)
        assert SchoolAiPolicy(ttl_s=0.001).settings_for(school).monthly_budget_inr == Decimal(1200)
    finally:
        tenancy.set_ai_answer_allowance(school, None)
        _settings(admin_engine, school, None)
        _flag(admin_engine, school, True)
