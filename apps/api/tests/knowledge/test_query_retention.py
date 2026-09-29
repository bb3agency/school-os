"""Query-log retention (docs/05 §13, docs/08 §7; FR-KB-009; shown by FR-ADM-002).

Ask-the-school questions and answers (``kb.queries``, encrypted) are deleted 180 days after they
were asked, per school in its own ``tenant_session``: older rows go, newer rows stay, and another
school's rows are never touched by the first school's purge. Synthetic data only; the ciphertext
bytes are placeholders (the purge never decrypts).
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import sys
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.admin.config import load_retention
from app.core.db import tenant_session
from app.knowledge import repository as repo
from app.knowledge import service, tasks
from app.knowledge.config.llm import load_llm_config

pytestmark = pytest.mark.db

TESTS = Path(__file__).resolve().parents[1]


def _load(name: str, path: Path) -> ModuleType:
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


W = _load("sos_test_api_world", TESTS / "api" / "world.py")
world = W.world

NOW = dt.datetime(2026, 9, 29, 6, 0, tzinfo=dt.UTC)


def _ask(tenant: uuid.UUID, asked_at: dt.datetime) -> uuid.UUID:
    query_id = uuid.uuid4()
    with tenant_session(tenant) as s:
        repo.insert_query(
            s,
            {
                "id": query_id,
                "session_id": uuid.uuid4(),
                "user_id": uuid.uuid4(),
                "question_ciphertext": b"\x01\x00\x01synthetic-question",
                "question_hmac": bytes(32),
                "answer_ciphertext": b"\x01\x00\x01synthetic-answer",
                "key_version": 1,
                "mode": "full",
                "status": "answered",
                "created_at": asked_at,
            },
        )
    return query_id


def _ids(admin: Engine, tenant: uuid.UUID) -> set[uuid.UUID]:
    with admin.connect() as c:
        rows = c.execute(text("SELECT id FROM kb.queries WHERE tenant_id = :t"), {"t": tenant})
        return {r.id for r in rows}


def test_FR_ADM_002_query_log_retention_matches_the_retention_settings() -> None:
    kept = load_llm_config().query_log.retention_days
    category = load_retention().categories[service.QUERY_RETENTION_CATEGORY]
    assert kept == category.default_days == 180  # docs/05 §13
    assert not category.configurable
    assert category.enforced_by == tasks.PURGE_QUERIES_TASK


def test_FR_KB_009_questions_older_than_180_days_are_deleted_per_school(
    world: Any, admin_engine: Engine
) -> None:
    school, other = W.provision_school(), W.provision_school()
    old = _ask(school, NOW - dt.timedelta(days=181))
    edge = _ask(school, NOW - dt.timedelta(days=179))
    recent = _ask(school, NOW - dt.timedelta(days=2))
    others_old = _ask(other, NOW - dt.timedelta(days=400))

    with tenant_session(school) as s:
        assert service.purge_old_queries(s, now=NOW) == 1
    assert _ids(admin_engine, school) == {edge, recent}
    assert old not in _ids(admin_engine, school)
    # The other school's rows are out of reach of this school's purge (RLS + tenant filter).
    assert _ids(admin_engine, other) == {others_old}

    with tenant_session(school) as s:  # idempotent
        assert service.purge_old_queries(s, now=NOW) == 0


def test_FR_KB_009_daily_job_purges_every_school(world: Any, admin_engine: Engine) -> None:
    school, other = W.provision_school(), W.provision_school()
    long_ago = dt.datetime.now(dt.UTC) - dt.timedelta(days=200)
    _ask(school, long_ago)
    _ask(other, long_ago)
    kept = _ask(other, dt.datetime.now(dt.UTC))

    result = tasks.purge_queries_all()
    assert result["purged"] >= 2
    assert result["tenants"] >= 2
    assert _ids(admin_engine, school) == set()
    assert _ids(admin_engine, other) == {kept}
