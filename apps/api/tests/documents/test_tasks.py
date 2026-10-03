"""Document worker tasks run end to end in eager mode (FR-DOC-002, FR-DOC-007, FR-DOC-008)."""

from __future__ import annotations

import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.documents import service, tasks
from app.documents.scanning import ScannerUnavailable
from app.documents.storage import ObjectStoreError
from app.ops import service as ops

pytestmark = pytest.mark.db
S = sys.modules["sos_test_documents_support"]


def test_FR_OPS_004_outbox_events_route_to_document_tasks() -> None:
    assert ops.OUTBOX_ROUTES["document.version.registered"] == "documents.scan"
    assert ops.OUTBOX_ROUTES["document.deleted"] == "documents.purge_objects"
    assert tasks.scan.name == "documents.scan"
    assert getattr(tasks.scan, "queue", None) == "ingest"
    assert "documents-purge-expired-uploads" in tasks.beat_schedule()


def test_PRV_016_discarded_versions_route_to_the_maintenance_queue() -> None:
    from sos_worker.celery_app import celery_app

    assert ops.OUTBOX_ROUTES[service.DISCARDED_EVENT] == service.DISCARD_TASK
    assert tasks.discard_object.name == service.DISCARD_TASK == "documents.discard_object"
    assert "documents-sweep-discarded-objects" in tasks.beat_schedule()
    celery_app.loader.import_default_modules()
    for name in (service.DISCARD_TASK, "documents.sweep_discarded_objects"):
        assert name in celery_app.tasks
        # send_task (used by the outbox dispatcher) honours task_routes, not the task's queue.
        assert celery_app.amqp.router.route({}, name)["queue"].name == "maintenance"


def test_PRV_016_sweep_task_walks_every_school(world: Any) -> None:
    result = tasks.sweep_discarded_objects.apply().get()
    assert result["discarded"] >= 0


def _dispatch_payload(admin: Engine, tenant_id: uuid.UUID, event: str, key: str, value: str) -> Any:
    return next(p for p in S.outbox_events(admin, tenant_id, event) if p[key] == value)


def test_FR_DOC_002_scan_task_consumes_the_outbox_payload(
    world: Any, api: Any, admin_engine: Engine, store: Any
) -> None:
    who = world.person("office_admin")
    data = S.pdf()
    up = api.call(
        who,
        "POST",
        "/api/v1/documents/uploads",
        json={
            "filename": "c.pdf",
            "content_type": "application/pdf",
            "size_bytes": len(data),
            "purpose": "circular",
        },
    ).json()
    assert store.browser_post(up["fields"], data, "application/pdf") == 204
    doc = api.call(
        who, "POST", "/api/v1/documents", json={"upload_id": up["upload_id"], "title": "Task test"}
    ).json()
    payload = _dispatch_payload(
        admin_engine, world.a.tenant_id, "document.version.registered", "document_id", doc["id"]
    )
    result = tasks.scan.apply(
        kwargs={
            "tenant_id": str(world.a.tenant_id),
            "event_id": str(uuid.uuid4()),
            "payload": payload,
        }
    ).get()
    assert result == "ready"
    got = api.call(who, "GET", f"/api/v1/documents/{doc['id']}").json()
    assert got["current_version"]["status"] == "ready"


def test_FR_DOC_008_scan_task_marks_failed_after_last_retry(
    world: Any, admin_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = S.make_document(
        admin_engine, world.a.tenant_id, world.person("owner").user_id, status="queued"
    )

    def down(*_: Any, **__: Any) -> str:
        raise ScannerUnavailable("clamd_unreachable")

    monkeypatch.setattr(service, "scan_version", down)
    monkeypatch.setattr(tasks, "SCAN_MAX_RETRIES", 0)
    with admin_engine.connect() as c:
        version_id: Any = c.execute(
            text("SELECT current_version_id FROM kb.documents WHERE id = :d"), {"d": doc}
        ).scalar_one()
    result = tasks.scan.apply(
        kwargs={
            "tenant_id": str(world.a.tenant_id),
            "event_id": str(uuid.uuid4()),
            "payload": {"document_id": str(doc), "version_id": str(version_id)},
        }
    ).get()
    assert result == "failed"
    with admin_engine.connect() as c:
        status = c.execute(
            text("SELECT status, error FROM kb.document_versions WHERE id = :v"),
            {"v": version_id},
        ).one()
    assert tuple(status) == ("failed", "scan_unavailable")


def test_FR_DOC_007_purge_task_removes_import_file_objects(
    world: Any, api: Any, admin_engine: Engine, store: Any
) -> None:
    who = world.person("owner")
    data = S.xlsx()
    ct = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    up = api.call(
        who,
        "POST",
        "/api/v1/documents/uploads",
        json={
            "filename": "admissions.xlsx",
            "content_type": ct,
            "size_bytes": len(data),
            "purpose": "import_file",
        },
    ).json()
    assert store.browser_post(up["fields"], data, ct) == 204
    doc = api.call(
        who, "POST", "/api/v1/documents", json={"upload_id": up["upload_id"], "title": "Admissions"}
    ).json()
    key = f"t/{world.a.tenant_id}/imports/{up['batch_id']}/raw.xlsx"
    assert key in store.objects
    assert api.call(who, "DELETE", f"/api/v1/documents/{doc['id']}").status_code == 204
    payload = _dispatch_payload(
        admin_engine, world.a.tenant_id, "document.deleted", "document_id", doc["id"]
    )
    assert payload["batch_ids"] == [up["batch_id"]]
    removed = tasks.purge_objects.apply(
        kwargs={
            "tenant_id": str(world.a.tenant_id),
            "event_id": str(uuid.uuid4()),
            "payload": payload,
        }
    ).get()
    assert removed == 1
    assert key not in store.objects


def test_purge_expired_uploads_task_walks_every_school(world: Any) -> None:
    result = tasks.purge_expired_uploads.apply().get()
    assert result["purged"] >= 0


def test_FR_DOC_007_purge_task_discards_for_retention_and_deletes_for_a_person(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """docs/08 §7: the ``discard`` flag of the outbox payload decides how objects go: automatic
    retention deletions discard (1-day rule), a person's delete keeps the 90-day window. Events
    queued before the flag existed carry none and are treated as a person's delete."""
    calls: list[tuple[uuid.UUID, list[uuid.UUID], bool]] = []

    def purge(
        tenant_id: uuid.UUID,
        document_id: uuid.UUID,
        batch_ids: list[uuid.UUID],
        *,
        discard: bool = False,
    ) -> int:
        calls.append((document_id, batch_ids, discard))
        return 1

    monkeypatch.setattr(service, "purge_document_objects", purge)
    doc, batch = uuid.uuid4(), uuid.uuid4()
    base = {"tenant_id": str(uuid.uuid4()), "event_id": str(uuid.uuid4())}
    for payload, expected in (
        ({"document_id": str(doc), "batch_ids": [str(batch)], "discard": True}, True),
        ({"document_id": str(doc), "batch_ids": [], "discard": False}, False),
        ({"document_id": str(doc), "batch_ids": []}, False),
    ):
        assert tasks.purge_objects.apply(kwargs={**base, "payload": payload}).get() == 1
        assert calls[-1][2] is expected
    assert calls[0][1] == [batch]


def test_FR_DOC_007_purge_task_retries_while_the_store_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A store outage retries with backoff; the purge is idempotent, so a retry finishes what is
    left. After the last attempt the failure is logged (IDs only) and the task gives up."""
    attempts: list[bool] = []

    def flaky(*_: Any, **kwargs: Any) -> int:
        attempts.append(bool(kwargs.get("discard")))
        if len(attempts) == 1:
            raise ObjectStoreError("delete_failed")
        return 3

    monkeypatch.setattr(service, "purge_document_objects", flaky)
    payload = {"document_id": str(uuid.uuid4()), "batch_ids": [], "discard": True}
    kwargs = {"tenant_id": str(uuid.uuid4()), "event_id": str(uuid.uuid4()), "payload": payload}
    assert tasks.purge_objects.apply(kwargs=kwargs).get() == 3
    assert attempts == [True, True]

    def down(*_: Any, **__: Any) -> int:
        raise ObjectStoreError("delete_failed")

    monkeypatch.setattr(service, "purge_document_objects", down)
    monkeypatch.setattr(tasks, "PURGE_MAX_RETRIES", 0)
    assert tasks.purge_objects.apply(kwargs=kwargs).get() == 0
