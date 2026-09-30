"""Contextual chunk headers wired through the composition root, on the real database (docs/06
§4.11; FR-KB-001, FR-KB-009, FR-KB-011; PO approval 2026-09-30).

- Off by default: the runtime has no contextualizer and retrieval no reranker; the settings
  ``SOS_KB_CONTEXTUAL_CHUNKS`` / ``SOS_KB_RERANK`` switch them per environment (fake mode runs
  the offline fakes).
- The real pipeline (documents service, SQL chunk store, gateway with the fake provider,
  metering ledger) stores contexts, meters each call with the document id and keeps the chunk
  text as the searchable content.
- The backfill re-indexes documents indexed while contextual chunks were off, is idempotent, and
  stops for a school whose AI budget is used up (chunks stay ``deferred``, indexed plainly).
Synthetic data only.
"""

from __future__ import annotations

import importlib.util
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.core.config import get_settings
from app.knowledge import composition, contextual_backfill
from app.knowledge.config.contextual import load_contextual_config
from app.knowledge.rerank import FakeReranker

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
W = K.W
world = W.world

BODY = (
    "Sub: Science exhibition 2026 for classes VI to X\n"
    "Students register with their class teacher by 10/10/2026.\n"
    "Each student pays Rs. 150 towards the said event at the office."
)
LIMITS = load_contextual_config().backfill


def install(*, contextual: bool, rerank: str | None = None) -> Any:
    settings = get_settings().model_copy(
        update={
            "kb_enabled": True,
            "kb_contextual_chunks": "on" if contextual else "off",
            "kb_rerank": rerank,
        }
    )
    rt = composition.build_runtime(settings, transport=K.FakeTransport(record=True))
    composition.set_runtime(rt)
    K.SW.configure_keyring()
    return rt


@pytest.fixture
def off(world: Any) -> Iterator[Any]:
    yield install(contextual=False)
    composition.set_runtime(None)


def statuses(admin: Engine, document_id: uuid.UUID) -> list[tuple[str, str]]:
    with admin.connect() as c:
        return [
            (r.context_status, r.chunk_context)
            for r in c.execute(
                text(
                    "SELECT context_status, chunk_context FROM kb.document_chunks "
                    "WHERE document_id = :d AND is_latest ORDER BY chunk_no"
                ),
                {"d": document_id},
            )
        ]


def test_FR_KB_001_off_by_default_and_switched_per_environment(off: Any) -> None:
    assert off.contextualizer is None
    assert off.search._retriever.reranker is None
    assert not off.search._retriever.config.contextual
    on = install(contextual=True, rerank="voyage")
    assert on.contextualizer is not None
    assert isinstance(on.search._retriever.reranker, FakeReranker)
    assert on.search._retriever.config.contextual


def test_FR_KB_001_backfill_adds_contexts_meters_per_document_and_is_idempotent(
    world: Any, off: Any, admin_engine: Engine
) -> None:
    school = world.a
    K.enable_ai(admin_engine, school.tenant_id)
    doc, _ = K.text_document(admin_engine, school, BODY, title="Circular No. 14/2026-27")
    assert {s for s, _ in statuses(admin_engine, doc)} == {"none"}

    install(contextual=True)
    K.enable_ai(admin_engine, school.tenant_id)
    result = contextual_backfill.run(K.pipeline(), LIMITS, tenant_ids=[school.tenant_id])
    assert (result.documents, result.stopped, result.failed) >= (1, 0, 0)
    after = statuses(admin_engine, doc)
    assert after
    assert all(s == "ok" and "Science exhibition 2026" in c for s, c in after)
    with admin_engine.connect() as c:
        calls: int = c.execute(
            text(
                "SELECT count(*) FROM kb.llm_calls WHERE document_id = :d "
                "AND feature = 'contextualize' AND role = 'contextualize'"
            ),
            {"d": doc},
        ).scalar_one()
        content: list[str] = list(
            c.execute(
                text("SELECT content FROM kb.document_chunks WHERE document_id = :d AND is_latest"),
                {"d": doc},
            )
            .scalars()
            .all()
        )
    assert calls >= 1
    assert content
    assert all("Circular No. 14/2026-27:" not in x for x in content)  # context is never content

    again = contextual_backfill.run(K.pipeline(), LIMITS, tenant_ids=[school.tenant_id])
    assert again.documents == 0  # nothing pending: no re-index, no model call


def test_FR_KB_011_backfill_stops_for_a_school_whose_budget_is_used_up(
    world: Any, off: Any, admin_engine: Engine
) -> None:
    school = world.b
    K.enable_ai(admin_engine, school.tenant_id)
    doc, _ = K.text_document(admin_engine, school, BODY, title="Circular No. 15/2026-27")
    rt = install(contextual=True)
    with admin_engine.begin() as c:
        c.execute(
            text(
                "UPDATE core.tenants SET settings = settings || '{\"ai_monthly_budget_inr\": 0}' "
                "WHERE id = :t"
            ),
            {"t": school.tenant_id},
        )
    try:
        K.enable_ai(admin_engine, school.tenant_id)
        result = contextual_backfill.run(K.pipeline(), LIMITS, tenant_ids=[school.tenant_id])
    finally:
        with admin_engine.begin() as c:
            c.execute(
                text(
                    "UPDATE core.tenants SET settings = settings - 'ai_monthly_budget_inr' "
                    "WHERE id = :t"
                ),
                {"t": school.tenant_id},
            )
        if rt.policy is not None:
            rt.policy.forget()
    assert (result.documents, result.stopped) == (1, 1)
    assert {s for s, _ in statuses(admin_engine, doc)} == {"deferred"}
