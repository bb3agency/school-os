"""Register-photo pipeline end to end with the fake provider (US-402, FR-IMP-020..024,
PRV-015/016, SEC-013, invariant 4).

Documents -> batch -> outbox -> Celery task (eager) -> masked items. Nothing becomes a student
record before a person confirms; a valid-checksum Aadhaar number on a page never reaches the
database, the logs or the API.
"""

from __future__ import annotations

import io
import sys
import uuid
from collections.abc import Sequence
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.core.config import Environment, ExtractionProviderKind, Settings
from app.core.redaction import contains_full_aadhaar
from app.extraction import service, tasks
from app.extraction.providers import (
    ExtractionUnavailable,
    FakeExtractionProvider,
    NotConfiguredProvider,
    PageExtraction,
    ProviderNotConfigured,
)
from app.ops import service as ops

pytestmark = pytest.mark.db
X = sys.modules["sos_test_extraction_support"]


def _count(admin: Engine, sql: str, **params: Any) -> int:
    with admin.connect() as c:
        return int(c.execute(text(sql), params).scalar_one())


def _run_discard_tasks(admin: Engine, tenant_id: uuid.UUID) -> None:
    """Deliver ``document.version.discarded`` events (as the outbox dispatcher would)."""
    from app.documents import service as documents
    from app.documents import tasks as document_tasks

    for payload in X.D.outbox_events(admin, tenant_id, documents.DISCARDED_EVENT):
        document_tasks.discard_object.apply(
            kwargs={"tenant_id": str(tenant_id), "event_id": str(uuid.uuid4()), "payload": payload}
        ).get()


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


def test_FR_IMP_022_PRV_016_valid_aadhaar_never_reaches_db_logs_or_api(  # noqa: PLR0915 - e2e
    world: Any, api: Any, admin_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    a = world.a
    number = X.valid_aadhaar_like(42)
    spaced = f"{number[:4]} {number[4:8]} {number[8:]}"
    row = X.register_row("Synthetica Masked Row", admission_no=f"A{W_unique()}")
    row["father_name"] = X.cell(f"Synthetica Father {spaced}")
    row["uid"] = X.cell(number)  # an unknown column: dropped, but still scanned
    # The engine gave no geometry for the number, so it cannot be placed on the image.
    row["father_name"]["bbox"] = row["uid"]["bbox"] = None
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
    # No geometry for the number on this page: it cannot be redacted, so it stays withheld.
    assert page_row["image_withheld"] is True
    assert page_row["image_redacted"] is False
    assert page_row["document_version_no"] == 1
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
    assert batch.json()["pages"][0]["image_redacted"] is False
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

    # PRV-016: the original is discarded (never served, and its object deleted after commit).
    with admin_engine.connect() as c:
        versions = c.execute(
            text(
                "SELECT version_no, status, error, object_key FROM kb.document_versions "
                "WHERE document_id = :d ORDER BY version_no"
            ),
            {"d": page_row["document_id"]},
        ).all()
    assert [(v.version_no, v.status, v.error) for v in versions] == [
        (1, "quarantined", "aadhaar_unredactable")
    ], "no redacted copy was made"
    assert "document.version_discarded" in actions
    assert "document.version_redacted" not in actions
    key = versions[0].object_key
    assert key in X.D.memory_store().objects
    _run_discard_tasks(admin_engine, a.tenant_id)
    assert key not in X.D.memory_store().objects
    assert key in X.D.memory_store().discarded
    download = api.call(
        a.people["owner"],
        "GET",
        f"/api/v1/documents/{page_row['document_id']}/download-url",
        params={"version": page_row["document_version_no"]},
    )
    assert download.status_code == 409, download.text
    # Its rows wait for a redacted copy: the withheld original is not usable evidence.
    confirm = api.call(
        who,
        "POST",
        f"/api/v1/extraction-items/{items[0]}/confirm",
        json={"fields": {"full_name": "Synthetica Masked Row"}},
    )
    assert (confirm.status_code, confirm.json()["code"]) == (409, "evidence_unavailable")


def W_unique() -> str:
    return str(X.W.unique())[:8]


def _versions(admin: Engine, document_id: Any) -> list[Any]:
    with admin.connect() as c:
        return list(
            c.execute(
                text(
                    "SELECT id, version_no, status, error, object_key, mime_type "
                    "FROM kb.document_versions WHERE document_id = :d ORDER BY version_no"
                ),
                {"d": document_id},
            ).all()
        )


def _number_page(number: str, name: str) -> bytes:
    """A real page image: a register row, plus the number as three word spans (and in a
    father_name cell whose box covers them), so the pipeline can place it."""
    row = X.register_row(name, admission_no=f"A{W_unique()}")
    row["father_name"] = X.cell(
        f"Synthetica Father {number[:4]} {number[4:8]} {number[8:]}",
        bbox=[0.49, 0.65, 0.24, 0.1],
    )
    spans = [
        {"text": "Aadhaar", "box": [200, 200, 290, 220]},
        *X.number_spans(number, top=200),
        {"text": name, "box": [20, 20, 200, 40]},
    ]
    page: bytes = X.rendered_page([row], spans)
    return page


def test_PRV_016_page_image_is_redacted_and_its_rows_become_confirmable(  # noqa: PLR0915 - e2e
    world: Any, api: Any, admin_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    from PIL import Image

    a = world.a
    number = X.valid_aadhaar_like(314)
    spaced = f"{number[:4]} {number[4:8]} {number[8:]}"
    name = f"Synthetica Redacted {W_unique()}"
    capsys.readouterr()
    batch_id, items = X.processed_batch(admin_engine, a, [_number_page(number, name)])
    item = X.row_of(admin_engine, "sis.extraction_items", items[0])
    assert item["fields"]["father_name"]["masked"] is True
    page_row = X.row_of(admin_engine, "sis.extraction_pages", item["page_id"])
    assert (page_row["aadhaar_detected"], page_row["image_withheld"]) == (True, False)
    assert page_row["image_redacted"] is True
    assert page_row["document_version_no"] == 2, "the page now shows the redacted copy"
    batch = X.row_of(admin_engine, "sis.extraction_batches", batch_id)
    assert batch["pages_withheld"] == 0

    # Documents: v2 is the redacted copy (current, queued for the malware scan); v1 discarded.
    doc_id = page_row["document_id"]
    v1, v2 = _versions(admin_engine, doc_id)
    assert (v1.status, v1.error) == ("quarantined", "aadhaar_redacted")
    assert (v2.status, v2.mime_type) == ("queued", "image/png")
    with admin_engine.connect() as c:
        current: object = c.execute(
            text("SELECT current_version_id FROM kb.documents WHERE id = :d"), {"d": doc_id}
        ).scalar_one()
    assert current == v2.id
    store = X.D.memory_store()
    redacted = store.objects[v2.object_key].data
    assert number.encode() not in redacted
    assert b"sos-fake-extraction" not in redacted, "no metadata from the original"
    with Image.open(io.BytesIO(redacted)) as img:
        rgb = img.convert("RGB")
        for x, y in ((300, 200), (365, 210), (429, 219)):
            assert rgb.getpixel((x, y)) == (0, 0, 0), (x, y)
        assert rgb.getpixel((550, 280)) == (255, 255, 255)

    # The original's object is deleted once the outbox event is delivered.
    assert v1.object_key in store.objects
    _run_discard_tasks(admin_engine, a.tenant_id)
    assert v1.object_key not in store.objects
    assert v2.object_key in store.objects

    actions = [e["action"] for e in X.W.audit_events(admin_engine, a.tenant_id)]
    for action in (
        "document.version_redacted",
        "document.version_discarded",
        "extraction.page.image_redacted",
    ):
        assert action in actions

    # Reviewers see the redacted copy once it has been scanned, never the original.
    who = a.people["office_admin"]
    detail = api.call(who, "GET", f"/api/v1/extraction-items/{items[0]}")
    assert (detail.json()["image"], detail.json()["image_unavailable"]) == (None, "not_ready")
    with admin_engine.begin() as c:
        c.execute(
            text("UPDATE kb.document_versions SET status = 'ready' WHERE id = :v"), {"v": v2.id}
        )
    detail = api.call(who, "GET", f"/api/v1/extraction-items/{items[0]}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["image_unavailable"] is None
    assert "/v2/" in detail.json()["image"]["url"]
    batch_out = api.call(who, "GET", f"/api/v1/extraction-batches/{batch_id}")
    assert batch_out.json()["pages"][0]["image_redacted"] is True
    original = api.call(
        a.people["owner"],
        "GET",
        f"/api/v1/documents/{doc_id}/download-url",
        params={"version": 1},
    )
    assert original.status_code == 409, original.text

    # The redacted copy is usable evidence: the row can be confirmed.
    confirm = api.call(
        who,
        "POST",
        f"/api/v1/extraction-items/{items[0]}/confirm",
        json={"fields": {"full_name": name}},
    )
    assert confirm.status_code == 200, confirm.text
    assert (
        _count(
            admin_engine,
            "SELECT count(*) FROM sis.attribute_values WHERE evidence_document_id = :d",
            d=doc_id,
        )
        > 0
    )

    for res in (detail, batch_out, confirm):
        assert number not in res.text
        assert not contains_full_aadhaar(res.text)
    stored = X.dump_tenant_text(admin_engine, a.tenant_id)
    assert number not in stored
    assert spaced not in stored
    out, err = capsys.readouterr()
    for stream in (out, err):
        assert number not in stream
        assert spaced not in stream


class _ReadsAgain:
    """Fake provider for the first read of each page; ``again`` for the redacted copy."""

    name = "fake"

    def __init__(self, again: PageExtraction | Exception) -> None:
        self.first = FakeExtractionProvider(Settings(env=Environment.CI))
        self.again = again
        self.calls = 0

    def extract(self, page_image: bytes, *, language_hints: Sequence[str]) -> PageExtraction:
        self.calls += 1
        if self.calls % 2 == 1:
            return self.first.extract(page_image, language_hints=language_hints)
        if isinstance(self.again, Exception):
            raise self.again
        return self.again


def test_PRV_016_a_copy_that_still_shows_the_number_is_discarded_with_the_original(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    a = world.a
    number = X.valid_aadhaar_like(271)
    doc = X.register_scan(
        admin_engine,
        a.tenant_id,
        a.people["owner"].user_id,
        _number_page(number, f"Synthetica Legible {W_unique()}"),
    )
    batch_id = X.start_batch(a, [doc])
    provider = _ReadsAgain(PageExtraction(raw_text=f"x {number} y"))
    assert service.process_batch(a.tenant_id, batch_id, provider=provider) == "review"
    assert provider.calls == 2
    items = X.item_ids(admin_engine, batch_id)
    page_row = X.row_of(
        admin_engine,
        "sis.extraction_pages",
        X.row_of(admin_engine, "sis.extraction_items", items[0])["page_id"],
    )
    assert (page_row["image_withheld"], page_row["image_redacted"]) == (True, False)
    assert [(v.status, v.error) for v in _versions(admin_engine, doc)] == [
        ("quarantined", "aadhaar_unredactable")
    ]
    assert X.row_of(admin_engine, "sis.extraction_batches", batch_id)["pages_withheld"] == 1
    events = [
        e
        for e in X.W.audit_events(admin_engine, a.tenant_id, "extraction.page.image_withheld")
        if str(e["resource_id"]) == str(page_row["id"])
    ]
    assert len(events) == 1
    assert events[0]["summary"]["cause"] == "still_legible"
    confirm = api.call(
        a.people["office_admin"],
        "POST",
        f"/api/v1/extraction-items/{items[0]}/confirm",
        json={"fields": {"gender": "female"}},
    )
    assert (confirm.status_code, confirm.json()["code"]) == (409, "evidence_unavailable")


def test_PRV_016_transient_error_reading_the_copy_retries_the_page_untouched(
    world: Any, admin_engine: Engine
) -> None:
    a = world.a
    number = X.valid_aadhaar_like(159)
    doc = X.register_scan(
        admin_engine,
        a.tenant_id,
        a.people["owner"].user_id,
        _number_page(number, f"Synthetica Retry {W_unique()}"),
    )
    batch_id = X.start_batch(a, [doc])
    keys = set(X.D.memory_store().objects)
    with pytest.raises(ExtractionUnavailable):
        service.process_batch(
            a.tenant_id, batch_id, provider=_ReadsAgain(ExtractionUnavailable("busy"))
        )
    assert [(v.status, v.error) for v in _versions(admin_engine, doc)] == [("ready", None)]
    assert set(X.D.memory_store().objects) == keys, "no redacted copy was stored"
    assert X.item_ids(admin_engine, batch_id) == []
    # The retry (provider healthy again) redacts the page.
    assert service.process_batch(a.tenant_id, batch_id) == "review"
    statuses = [(v.status, v.error) for v in _versions(admin_engine, doc)]
    assert statuses == [("quarantined", "aadhaar_redacted"), ("queued", None)]


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
