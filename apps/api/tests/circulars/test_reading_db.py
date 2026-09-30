"""Circular reading end to end on the database (US-1601; FR-CIR-001..007; invariants 5, 7, 8, 9).

The real ingestion pipeline indexes a synthetic circular; its indexing hook queues the reading
(outbox); the worker job reads the passages, calls the gateway (offline fake provider) and stores
only what the checks keep. Nothing becomes a task.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

import pytest
from sqlalchemy import Engine, text
from structlog.testing import capture_logs

from app.circulars import service

from .conftest import C

pytestmark = pytest.mark.db


def _outbox(admin: Engine, tenant_id: Any, event: str) -> list[dict[str, Any]]:
    with admin.connect() as c:
        rows = c.execute(
            text(
                "SELECT payload FROM ops.outbox WHERE tenant_id = :t AND event_type = :e "
                "ORDER BY created_at"
            ),
            {"t": tenant_id, "e": event},
        )
        return [dict(r._mapping)["payload"] for r in rows]


def test_FR_CIR_001_indexing_a_circular_queues_one_reading(
    ai_on: Any, admin_engine: Engine
) -> None:
    school = ai_on.a
    document_id = C.circular(admin_engine, school)
    row = C.reading_row(admin_engine, document_id)
    assert row["status"] == "queued"
    assert row["version_no"] == 1
    events = _outbox(admin_engine, school.tenant_id, service.READ_EVENT)
    assert {"reading_id": str(row["id"])} in events
    # Indexing the same version again does not read it twice (idempotent per version).
    before = len(events)
    assert C.KB.pipeline().ingest(school.tenant_id, document_id, row["version_id"]) == "indexed"
    assert len(_outbox(admin_engine, school.tenant_id, service.READ_EVENT)) == before
    assert (
        C.db_value(
            admin_engine,
            "SELECT count(*) FROM kb.circular_readings WHERE document_id = :d",
            d=document_id,
        )
        == 1
    )


def test_FR_CIR_001_other_document_types_are_not_read(ai_on: Any, admin_engine: Engine) -> None:
    doc, _ = C.KB.text_document(
        admin_engine, ai_on.a, C.EN_CIRCULAR, doc_type="policy", acl=C.KB.ALL_ROLES_ACL
    )
    assert (
        C.db_value(
            admin_engine, "SELECT count(*) FROM kb.circular_readings WHERE document_id = :d", d=doc
        )
        == 0
    )


def test_FR_CIR_002_reading_stores_grounded_suggestions_only(
    ai_on: Any, admin_engine: Engine, installed: Any
) -> None:
    school = ai_on.a
    _store, _transport, _pdf = installed
    # ADR-0036: the bilingual output is the Telugu-on path, so it runs with the switch on
    # explicitly; English first (the default) is pinned in tests/knowledge.
    _runtime, transport = C.KB.install_runtime(telugu=True)
    document_id = C.circular(admin_engine, school)
    with capture_logs() as logs:
        assert C.read_now(admin_engine, school, document_id) == "ready"
    row = C.reading_row(admin_engine, document_id)
    assert row["status"] == "ready"
    assert row["reference_no"] == "Rc.No.456/C/2026"
    assert row["issued_on"] == dt.date(2026, 10, 1)
    assert row["prompt"] == "circular_reading.v1"
    assert row["summary_te"]
    found = C.suggestions(admin_engine, document_id)
    assert [s["due_on"] for s in found] == [dt.date(2026, 10, 15), dt.date(2026, 10, 22)]
    for s in found:
        assert s["status"] == "suggested"
        assert s["citation"]["source"].startswith(f"sos://doc/{document_id}/v1#p")
        assert s["citation"]["quote"] in C.EN_CIRCULAR
    # Invariant 9: nothing became a task.
    assert (
        C.db_value(
            admin_engine, "SELECT count(*) FROM ops.tasks WHERE document_id = :d", d=document_id
        )
        == 0
    )
    # The model saw only this circular's passages (no other document, no student record).
    structured = [b for b in transport.sent if (b.get("output_config") or {}).get("format")]
    sent = json.dumps(structured[-1], ensure_ascii=False)
    assert "UDISE+ data sheets" in sent
    assert "Parent-teacher meeting" not in sent
    # FR-CIR-007: metered and audited with ids and counts; logs carry no circular text.
    assert (
        C.db_value(
            admin_engine,
            "SELECT count(*) FROM kb.llm_calls WHERE tenant_id = :t AND feature = 'circulars' "
            "AND role = 'circular'",
            t=school.tenant_id,
        )
        >= 1
    )
    (event,) = _events(admin_engine, school.tenant_id, "circular.read_completed", document_id)
    assert event["summary"]["suggestions"] == 2
    text_ = json.dumps(logs, default=str, ensure_ascii=False)
    for secret in ("Headmasters", "UDISE", "Guntur", "Rc.No"):
        assert secret not in text_


def _events(admin: Engine, tenant_id: Any, action: str, resource_id: Any) -> list[dict[str, Any]]:
    return [
        e for e in C.W.audit_events(admin, tenant_id, action) if e["resource_id"] == resource_id
    ]


def test_FR_CIR_002_telugu_circular(ai_on: Any, admin_engine: Engine) -> None:
    document_id = C.read_circular(admin_engine, ai_on.a, C.TE_CIRCULAR)
    assert [s["due_on"] for s in C.suggestions(admin_engine, document_id)] == [
        dt.date(2026, 10, 20)
    ]


def test_FR_CIR_005_ai_switched_off_needs_manual_review(ai_on: Any, admin_engine: Engine) -> None:
    school = ai_on.b
    C.KB.enable_ai(admin_engine, school.tenant_id, enabled=False)
    try:
        document_id = C.circular(admin_engine, school)
        assert C.read_now(admin_engine, school, document_id) == "ai_disabled"
    finally:
        C.KB.enable_ai(admin_engine, school.tenant_id)
    row = C.reading_row(admin_engine, document_id)
    assert (row["status"], row["error_code"]) == ("needs_review", "ai_disabled")
    assert C.suggestions(admin_engine, document_id) == []
    assert _events(admin_engine, school.tenant_id, "circular.read_failed", document_id)
    # Rerunning the job changes nothing (the status gates the work).
    assert C.read_now(admin_engine, school, document_id) == "skipped"


def test_FR_CIR_005_a_scan_without_text_needs_manual_review(
    ai_on: Any, admin_engine: Engine
) -> None:
    school = ai_on.a
    document_id = C.circular(admin_engine, school)
    with admin_engine.begin() as c:
        c.execute(text("DELETE FROM kb.document_chunks WHERE document_id = :d"), {"d": document_id})
    assert C.read_now(admin_engine, school, document_id) == "no_text"


def test_FR_CIR_002_no_dates_means_no_suggestions(ai_on: Any, admin_engine: Engine) -> None:
    document_id = C.read_circular(admin_engine, ai_on.a, C.NO_DATES)
    assert C.suggestions(admin_engine, document_id) == []
    assert C.reading_row(admin_engine, document_id)["status"] == "ready"


def test_FR_CIR_001_deleting_the_circular_removes_its_reading(
    ai_on: Any, admin_engine: Engine
) -> None:
    document_id = C.read_circular(admin_engine, ai_on.a)
    with admin_engine.begin() as c:
        c.execute(text("DELETE FROM kb.documents WHERE id = :d"), {"d": document_id})
    assert (
        C.db_value(
            admin_engine,
            "SELECT count(*) FROM kb.circular_readings WHERE document_id = :d",
            d=document_id,
        )
        == 0
    )
