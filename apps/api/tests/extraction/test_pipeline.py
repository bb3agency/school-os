"""Register-photo pipeline end to end with the fake provider (US-402, FR-IMP-020..024,
PRV-015/016, SEC-013, invariant 4).

Documents -> batch -> outbox -> Celery task (eager) -> masked items. Nothing becomes a student
record before a person confirms; a valid-checksum Aadhaar number on a page never reaches the
database, the logs or the API.
"""

from __future__ import annotations

import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.core.config import Environment, ExtractionProviderKind, Settings
from app.core.redaction import contains_full_aadhaar
from app.extraction import service, tasks
from app.extraction.providers import (
    ExtractionUnavailable,
    NotConfiguredProvider,
    ProviderNotConfigured,
)
from app.ops import service as ops

pytestmark = pytest.mark.db
X = sys.modules["sos_test_extraction_support"]


def _count(admin: Engine, sql: str, **params: Any) -> int:
    with admin.connect() as c:
        return int(c.execute(text(sql), params).scalar_one())


def _outbox_payload(admin: Engine, tenant_id: uuid.UUID, event: str, batch_id: uuid.UUID) -> Any:
    return next(
        p for p in X.D.outbox_events(admin, tenant_id, event) if p["batch_id"] == str(batch_id)
    )


def test_FR_OPS_004_outbox_routes_batches_to_the_ocr_queue() -> None:
    assert (
        ops.OUTBOX_ROUTES[service.BATCH_EVENT] == service.PROCESS_TASK == "extraction.process_batch"
    )
    assert tasks.process_batch.name == service.PROCESS_TASK
    assert getattr(tasks.process_batch, "queue", None) == "ocr"


def test_worker_routes_extraction_tasks_to_ocr() -> None:
    from sos_worker.celery_app import TASK_MODULES, celery_app

    assert "app.extraction.tasks" in TASK_MODULES
    celery_app.loader.import_default_modules()
    assert service.PROCESS_TASK in celery_app.tasks
    # send_task (used by the outbox dispatcher) honours task_routes, not the task's own queue.
    route = celery_app.amqp.router.route({}, service.PROCESS_TASK)
    assert route["queue"].name == "ocr"


def test_US_402_AC1_end_to_end_through_the_outbox_task(
    world: Any, admin_engine: Engine, extraction_templates: None
) -> None:
    a = world.a
    owner = a.people["owner"].user_id
    pages = [
        X.page_png([X.register_row("Synthetica Pipeline One"), X.register_row("Synthetica Two")]),
        X.page_png([X.register_row("Synthetica Pipeline Three", confidence=0.4)]),
    ]
    docs = [X.register_scan(admin_engine, a.tenant_id, owner, p) for p in pages]
    students_before = _count(
        admin_engine, "SELECT count(*) FROM sis.students WHERE tenant_id = :t", t=a.tenant_id
    )
    batch_id = X.start_batch(a, docs)
    assert X.row_of(admin_engine, "sis.extraction_batches", batch_id)["status"] == "queued"

    payload = _outbox_payload(admin_engine, a.tenant_id, service.BATCH_EVENT, batch_id)
    result = tasks.process_batch.apply(
        kwargs={"tenant_id": str(a.tenant_id), "event_id": str(uuid.uuid4()), "payload": payload}
    )
    assert result.get() == "review"

    batch = X.row_of(admin_engine, "sis.extraction_batches", batch_id)
    assert batch["pages_done"] == 2
    assert batch["items_total"] == batch["items_pending"] == 3
    assert batch["items_low_confidence"] == 1
    assert batch["provider"] == "fake"
    items = X.item_ids(admin_engine, batch_id)
    assert len(items) == 3
    first = X.row_of(admin_engine, "sis.extraction_items", items[0])
    assert first["status"] == "pending_review"
    assert first["fields"]["full_name"]["value"] == "Synthetica Pipeline One"
    assert first["fields"]["full_name"]["confidence"] == 0.95
    assert first["fields"]["full_name"]["bbox"] == [0.1, 0.1, 0.2, 0.05]
    third = X.row_of(admin_engine, "sis.extraction_items", items[2])
    assert third["low_confidence"] is True

    # FR-IMP-020 / US-402 AC2: nothing is a record yet.
    students_after = _count(
        admin_engine, "SELECT count(*) FROM sis.students WHERE tenant_id = :t", t=a.tenant_id
    )
    assert students_after == students_before
    assert (
        _count(
            admin_engine,
            "SELECT count(*) FROM sis.attribute_values WHERE evidence_document_id = ANY(:d)",
            d=docs,
        )
        == 0
    )

    # Idempotent: a redelivered event changes nothing.
    again = tasks.process_batch.apply(
        kwargs={"tenant_id": str(a.tenant_id), "event_id": str(uuid.uuid4()), "payload": payload}
    )
    assert again.get() == "review"
    assert len(X.item_ids(admin_engine, batch_id)) == 3

    # Audit + the batch owner is told (FR-NOT-001).
    actions = [e["action"] for e in X.W.audit_events(admin_engine, a.tenant_id)]
    assert "extraction.batch.created" in actions
    assert "extraction.batch.processed" in actions
    assert (
        _count(
            admin_engine,
            "SELECT count(*) FROM ops.notifications WHERE resource_id = :b "
            "AND template_key = 'extraction.batch.ready' AND recipient_membership_id = :m",
            b=batch_id,
            m=a.people["office_admin"].membership_id,
        )
        == 1
    )


def test_FR_IMP_022_PRV_016_valid_aadhaar_never_reaches_db_logs_or_api(
    world: Any, api: Any, admin_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    a = world.a
    number = X.valid_aadhaar_like(42)
    spaced = f"{number[:4]} {number[4:8]} {number[8:]}"
    row = X.register_row("Synthetica Masked Row", admission_no=f"A{W_unique()}")
    row["father_name"] = X.cell(f"Synthetica Father {spaced}")
    row["uid"] = X.cell(number)  # an unknown column: dropped, but still scanned
    page = X.page_png([row], raw_text=f"Aadhaar {spaced} Synthetica Masked Row")
    capsys.readouterr()
    batch_id, items = X.processed_batch(admin_engine, a, [page])
    item = X.row_of(admin_engine, "sis.extraction_items", items[0])
    assert item["masked"] is True
    assert item["fields"]["father_name"]["masked"] is True
    assert item["fields"]["father_name"]["value"].endswith(number[-4:])
    assert "uid" not in item["fields"]
    page_row = X.row_of(admin_engine, "sis.extraction_pages", item["page_id"])
    assert page_row["aadhaar_detected"] is True
    assert page_row["image_withheld"] is True
    assert page_row["dropped_field_count"] == 1
    assert X.row_of(admin_engine, "sis.extraction_batches", batch_id)["pages_withheld"] == 1

    # The API never shows the image of that page nor the number.
    who = a.people["office_admin"]
    detail = api.call(who, "GET", f"/api/v1/extraction-items/{items[0]}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["image"] is None
    assert detail.json()["image_unavailable"] == "withheld_sensitive_number"
    listing = api.call(who, "GET", "/api/v1/extraction-items", params={"batch_id": str(batch_id)})
    batch = api.call(who, "GET", f"/api/v1/extraction-batches/{batch_id}")
    assert batch.json()["pages"][0]["image_withheld"] is True
    for res in (detail, listing, batch):
        assert not contains_full_aadhaar(res.text)
        assert number not in res.text

    stored = X.dump_tenant_text(admin_engine, a.tenant_id)
    assert number not in stored
    assert spaced not in stored
    out, err = capsys.readouterr()
    for stream in (out, err):
        assert number not in stream
        assert spaced not in stream
        assert "Synthetica Masked Row" not in stream
    actions = [e["action"] for e in X.W.audit_events(admin_engine, a.tenant_id)]
    assert "extraction.page.image_withheld" in actions


def W_unique() -> str:
    return str(X.W.unique())[:8]


def test_FR_IMP_024_not_configured_provider_fails_the_batch_for_operators(
    world: Any, admin_engine: Engine
) -> None:
    a = world.a
    owner = a.people["owner"].user_id
    doc = X.register_scan(
        admin_engine, a.tenant_id, owner, X.page_png([X.register_row("Synthetica NC")])
    )
    batch_id = X.start_batch(a, [doc])
    with pytest.raises(ProviderNotConfigured):
        service.process_batch(a.tenant_id, batch_id, provider=NotConfiguredProvider())
    batch = X.row_of(admin_engine, "sis.extraction_batches", batch_id)
    assert batch["status"] == "failed"
    assert batch["error_code"] == "provider_not_configured"
    assert batch["pages_failed"] == 1
    assert X.item_ids(admin_engine, batch_id) == []
    assert "extraction.batch.failed" in [
        e["action"] for e in X.W.audit_events(admin_engine, a.tenant_id)
    ]
    # A failed page may be extracted again in a new batch.
    X.start_batch(a, [doc])


def test_FR_IMP_024_prod_settings_never_run_the_fake(world: Any, admin_engine: Engine) -> None:
    from pydantic import SecretStr

    from app.core.config import KeyWrapperKind
    from app.extraction.providers import ProviderRefused

    prod = Settings(
        env=Environment.PROD,
        key_wrapper=KeyWrapperKind.KMS,
        database_url=SecretStr("postgresql+psycopg://sos_app:x@db:5432/schoolos"),
        platform_database_url=SecretStr("postgresql+psycopg://sos_platform:y@db:5432/s"),
        service_token_key=SecretStr("k" * 48),
        extraction_provider=ExtractionProviderKind.FAKE,
    )
    a = world.a
    doc = X.register_scan(
        admin_engine, a.tenant_id, a.people["owner"].user_id, X.page_png([X.register_row("S P")])
    )
    batch_id = X.start_batch(a, [doc])
    with pytest.raises(ProviderRefused):
        service.process_batch(a.tenant_id, batch_id, settings=prod)
    batch = X.row_of(admin_engine, "sis.extraction_batches", batch_id)
    assert (batch["status"], batch["error_code"]) == ("failed", "provider_refused")


def test_unreadable_page_fails_alone_and_transient_errors_resume(
    world: Any, admin_engine: Engine
) -> None:
    a = world.a
    owner = a.people["owner"].user_id
    good = X.register_scan(
        admin_engine, a.tenant_id, owner, X.page_png([X.register_row("Synthetica Good")])
    )
    bad = X.register_scan(admin_engine, a.tenant_id, owner, X.page_png([], fail="unreadable"))
    flaky = X.register_scan(admin_engine, a.tenant_id, owner, X.page_png([], fail="unavailable"))
    batch_id = X.start_batch(a, [good, bad, flaky])
    with pytest.raises(ExtractionUnavailable):
        service.process_batch(a.tenant_id, batch_id)
    batch = X.row_of(admin_engine, "sis.extraction_batches", batch_id)
    assert (batch["status"], batch["pages_done"], batch["pages_failed"]) == ("processing", 1, 1)
    # Retries exhausted: the task marks the batch failed; finished pages keep their items.
    service.fail_batch(a.tenant_id, batch_id, "provider_unavailable")
    batch = X.row_of(admin_engine, "sis.extraction_batches", batch_id)
    assert (batch["status"], batch["pages_failed"]) == ("failed", 2)
    assert len(X.item_ids(admin_engine, batch_id)) == 1


def test_task_retries_transient_errors(
    world: Any, admin_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    a = world.a
    doc = X.register_scan(
        admin_engine, a.tenant_id, a.people["owner"].user_id, X.page_png([], fail="unavailable")
    )
    batch_id = X.start_batch(a, [doc])
    payload = {"batch_id": str(batch_id)}
    result = tasks.process_batch.apply(
        kwargs={"tenant_id": str(a.tenant_id), "event_id": str(uuid.uuid4()), "payload": payload},
        retries=tasks.MAX_RETRIES,
    )
    assert result.get() == "failed"
    batch = X.row_of(admin_engine, "sis.extraction_batches", batch_id)
    assert (batch["status"], batch["error_code"]) == ("failed", "provider_unavailable")


def test_no_rows_completes_the_batch_at_once(world: Any, admin_engine: Engine) -> None:
    batch_id, items = X.processed_batch(admin_engine, world.a, [X.page_png([])])
    assert items == []
    batch = X.row_of(admin_engine, "sis.extraction_batches", batch_id)
    assert batch["status"] == "completed"
    assert batch["completed_at"] is not None


def test_extracted_values_are_immutable_in_the_database(world: Any, admin_engine: Engine) -> None:
    from sqlalchemy.exc import DBAPIError

    from app.core.db import tenant_session

    item = X.pending_item(admin_engine, world.a)
    with pytest.raises(DBAPIError), tenant_session(world.a.tenant_id) as s:
        s.execute(
            text("UPDATE sis.extraction_items SET fields = '{}'::jsonb WHERE id = :i"), {"i": item}
        )
    with pytest.raises(DBAPIError), tenant_session(world.a.tenant_id) as s:
        s.execute(text("DELETE FROM sis.extraction_items WHERE id = :i"), {"i": item})
