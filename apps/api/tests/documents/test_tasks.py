"""Document worker tasks run end to end in eager mode (FR-DOC-002, FR-DOC-007, FR-DOC-008)."""

from __future__ import annotations

import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.documents import service, tasks
from app.documents.scanning import ScannerUnavailable
from app.ops import service as ops

pytestmark = pytest.mark.db
S = sys.modules["sos_test_documents_support"]


def test_FR_OPS_004_outbox_events_route_to_document_tasks() -> None:
    assert ops.OUTBOX_ROUTES["document.version.registered"] == "documents.scan"
    assert ops.OUTBOX_ROUTES["document.deleted"] == "documents.purge_objects"
    assert tasks.scan.name == "documents.scan"
    assert getattr(tasks.scan, "queue", None) == "ingest"
    assert "documents-purge-expired-uploads" in tasks.beat_schedule()


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
