"""0024_kb_metering: the ``kb.llm_calls`` ledger, its isolation and a populated round trip.

FR-KB-009, FR-KB-011, NFR-CST-001; CLAUDE.md invariants 1 and 12. ``kb.llm_calls`` is
tenant-owned (RLS ENABLE + FORCE, standard policy), append-only for ``sos_app`` and invisible to
``sos_platform``; walking 0024 down drops the ledger (documented as lossy) and walking it up
again recreates it empty and usable.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from decimal import Decimal
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from app.core.db import tenant_session
from app.knowledge.gateway.metering import MeteringEvent
from app.knowledge.policy import LedgerMeteringSink

pytestmark = pytest.mark.db
REVISION = "0024_kb_metering"
PREVIOUS = "0023_api_gaps"
DB = "schoolos_kb_metering_migration"


def _tenant(admin: Engine) -> uuid.UUID:
    tid = uuid.uuid4()
    with admin.begin() as c:
        c.execute(
            text(
                "INSERT INTO core.tenants (id, code, name, status) VALUES (:i, :c, 'S', 'active')"
            ),
            {"i": tid, "c": f"kbl-{tid.hex[:10]}"},
        )
    return tid


def _event(tenant_id: uuid.UUID, query_id: uuid.UUID | None = None) -> MeteringEvent:
    return MeteringEvent(
        tenant_id=tenant_id,
        feature="ask",
        role="answer",
        query_id=query_id,
        provider="fake",
        model="claude-sonnet-5",
        outcome="ok",
        attempts=1,
        latency_ms=12,
        input_tokens=100,
        output_tokens=20,
        cache_write_tokens=0,
        cache_read_tokens=0,
        cost_usd=Decimal("0.000400"),
        month_spend_usd=Decimal("0.000400"),
    )


def test_FR_KB_009_ledger_rows_are_isolated_and_append_only(
    admin_engine: Engine, app_engine: Engine, platform_engine: Engine
) -> None:
    a, b = _tenant(admin_engine), _tenant(admin_engine)
    sink = LedgerMeteringSink()
    query = uuid.uuid4()
    sink.record(_event(a, query))
    sink.record(_event(b))
    with tenant_session(a) as s:
        rows = s.execute(text("SELECT tenant_id, query_id, cost_usd FROM kb.llm_calls")).all()
        assert [(r.tenant_id, r.query_id, r.cost_usd) for r in rows] == [
            (a, query, Decimal("0.000400"))
        ]
    with pytest.raises(DBAPIError, match="permission denied"), tenant_session(a) as s:
        s.execute(text("UPDATE kb.llm_calls SET cost_usd = 0"))
    with pytest.raises(DBAPIError, match="permission denied"), platform_engine.connect() as c:
        c.execute(text("SELECT 1 FROM kb.llm_calls")).all()
    with pytest.raises(DBAPIError), tenant_session(a) as s:
        # WITH CHECK: a school cannot write another school's spend.
        s.execute(
            text(
                "INSERT INTO kb.llm_calls (id, tenant_id, feature, role, provider, model, "
                "outcome, attempts, latency_ms, input_tokens, output_tokens, cost_usd) VALUES "
                "(:i, :t, 'ask', 'answer', 'fake', 'claude-sonnet-5', 'ok', 1, 1, 1, 1, 0)"
            ),
            {"i": uuid.uuid4(), "t": b},
        )


def test_FR_KB_009_unrecorded_metering_never_breaks_the_answer() -> None:
    sink = LedgerMeteringSink()
    sink.record(_event(uuid.uuid4()))  # no such school: FK fails, logged, not raised


@pytest.fixture
def fresh(
    test_database: Any, make_alembic_config: Callable[[str], Config]
) -> Iterator[tuple[Config, Engine]]:
    url = test_database.create_fresh(DB)
    cfg = make_alembic_config(url)
    command.upgrade(cfg, "head")
    admin = create_engine(
        make_url(test_database.admin_url).set(database=DB).render_as_string(hide_password=False)
    )
    yield cfg, admin
    admin.dispose()


def _tables(admin: Engine) -> set[str]:
    with admin.connect() as c:
        return set(
            c.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'kb'")).scalars()
        )


def test_invariant_12_0024_round_trips_on_a_populated_database(
    fresh: tuple[Config, Engine],
) -> None:
    cfg, admin = fresh
    tid = _tenant(admin)
    with admin.begin() as c:
        c.execute(
            text(
                "INSERT INTO kb.llm_calls (id, tenant_id, feature, role, provider, model, outcome, "
                "attempts, latency_ms, input_tokens, output_tokens, cost_usd) VALUES "
                "(:i, :t, 'ask', 'answer', 'fake', 'claude-sonnet-5', 'ok', 1, 5, 10, 2, 0.0001)"
            ),
            {"i": uuid.uuid4(), "t": tid},
        )
    command.downgrade(cfg, PREVIOUS)
    assert "llm_calls" not in _tables(admin)
    assert {"document_chunks", "queries", "verified_answers"} <= _tables(admin)
    command.upgrade(cfg, REVISION)
    assert "llm_calls" in _tables(admin)
    with admin.connect() as c:
        assert c.execute(text("SELECT count(*) FROM kb.llm_calls")).scalar_one() == 0
        forced: bool = c.execute(
            text(
                "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class "
                "WHERE oid = 'kb.llm_calls'::regclass"
            )
        ).scalar_one()
    assert forced is True
