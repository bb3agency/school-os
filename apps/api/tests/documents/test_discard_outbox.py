"""W3-06 (a): the api never tags or deletes stored objects; only the worker does.

The api role has no ``s3:DeleteObject``, ``s3:PutObjectTagging`` or ``s3:PutObjectVersionTagging``
on ``files/t/*`` (infra/terraform). Every object the upload path no longer needs (a rejected or
duplicate upload, the staging copy after promotion, a half-made version after a failed insert)
is queued as the outbox event ``document.object.discard_requested``; the worker task
``documents.discard_unused_object`` rebuilds the key from IDs, checks that no version uses it and
discards it (tag ``sos-lifecycle=discarded``, delete; docs/08 §7).
"""

from __future__ import annotations

import datetime as dt
import sys
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.documents import repository as repo
from app.documents import service, storage, tasks
from app.ops import service as ops

pytestmark = pytest.mark.db
S = sys.modules["sos_test_documents_support"]

PDF_CT = "application/pdf"
EVENT = "document.object.discard_requested"


class StoreTouched(AssertionError):
    pass


class ApiRoleStore:
    """The in-memory store as the api role sees it: every tag or delete is refused (IAM)."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def __getattr__(self, name: str) -> Any:
        if name in {"discard", "delete", "delete_prefix", "purge_prefix"}:
            raise StoreTouched(f"the api called ObjectStore.{name}")
        return getattr(self._inner, name)


@pytest.fixture
def api_store(store: Any) -> Iterator[Any]:
    storage.set_object_store(ApiRoleStore(store))
    try:
        yield store
    finally:
        storage.set_object_store(store)


def _upload(api: Any, who: Any, data: bytes, *, content_type: str = PDF_CT) -> dict[str, Any]:
    res = api.call(
        who,
        "POST",
        "/api/v1/documents/uploads",
        json={
            "filename": "circular.pdf",
            "content_type": content_type,
            "size_bytes": len(data),
            "purpose": "circular",
        },
    )
    assert res.status_code == 201, res.text
    out: dict[str, Any] = res.json()
    assert S.memory_store().browser_post(out["fields"], data, content_type) == 204
    return out


def _register(api: Any, who: Any, upload_id: str) -> Any:
    return api.call(
        who, "POST", "/api/v1/documents", json={"upload_id": upload_id, "title": "Synthetic"}
    )


def _events(admin: Engine, tenant_id: uuid.UUID) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = S.outbox_events(admin, tenant_id, EVENT)
    return out


def _run(tenant_id: uuid.UUID, payload: dict[str, Any]) -> bool:
    result: bool = tasks.discard_unused_object.apply(
        kwargs={"tenant_id": str(tenant_id), "event_id": str(uuid.uuid4()), "payload": payload}
    ).get()
    return result


def test_W3_06_the_discard_event_routes_to_the_maintenance_worker() -> None:
    from sos_worker.celery_app import celery_app

    assert ops.OUTBOX_ROUTES[EVENT] == service.OBJECT_DISCARD_TASK
    assert tasks.discard_unused_object.name == "documents.discard_unused_object"
    celery_app.loader.import_default_modules()
    assert service.OBJECT_DISCARD_TASK in celery_app.tasks
    route = celery_app.amqp.router.route({}, service.OBJECT_DISCARD_TASK)
    assert route["queue"].name == "maintenance"


def test_W3_06_a_rejected_upload_is_queued_not_deleted_by_the_api(
    world: Any, api: Any, admin_engine: Engine, api_store: Any
) -> None:
    who = world.person("office_admin")
    tenant = world.a.tenant_id
    # A PNG declared as a PDF: the content check refuses it.
    data = S.png().ljust(200, b"\x00")
    up = _upload(api, who, data[:200], content_type=PDF_CT)
    res = _register(api, who, up["upload_id"])
    assert res.status_code == 415, res.text
    key = up["fields"]["key"]
    assert key in api_store.objects, "the api left the staging object alone"
    events = [e for e in _events(admin_engine, tenant) if e.get("upload_id") == up["upload_id"]]
    assert events == [{"upload_id": up["upload_id"]}], "queued although the request failed"
    # The worker discards it (tag, then delete), once.
    storage.set_object_store(api_store)
    assert _run(tenant, events[0]) is True
    assert key not in api_store.objects
    assert key in api_store.discarded
    assert _run(tenant, events[0]) is True  # idempotent: discarding again is harmless
    assert key not in api_store.objects


def test_W3_06_registration_queues_the_staging_cleanup_in_its_transaction(
    world: Any, api: Any, admin_engine: Engine, api_store: Any
) -> None:
    who = world.person("office_admin")
    tenant = world.a.tenant_id
    up = _upload(api, who, S.pdf())
    res = _register(api, who, up["upload_id"])
    assert res.status_code == 202, res.text
    doc = res.json()
    staging = up["fields"]["key"]
    final = f"t/{tenant}/docs/{doc['id']}/v1/original.pdf"
    assert staging in api_store.objects
    assert final in api_store.objects
    (event,) = [e for e in _events(admin_engine, tenant) if e.get("upload_id") == up["upload_id"]]
    storage.set_object_store(api_store)
    assert _run(tenant, event) is True
    assert staging not in api_store.objects
    assert final in api_store.objects, "the registered file is never touched"


def test_W3_06_a_duplicate_upload_is_queued_not_deleted(
    world: Any, api: Any, admin_engine: Engine, api_store: Any
) -> None:
    who = world.person("office_admin")
    data = S.pdf()
    first = _upload(api, who, data)
    assert _register(api, who, first["upload_id"]).status_code == 202
    again = _upload(api, who, data)
    res = _register(api, who, again["upload_id"])
    assert res.status_code == 409
    assert res.json()["code"] == "duplicate_document"
    assert again["fields"]["key"] in api_store.objects
    assert {"upload_id": again["upload_id"]} in _events(admin_engine, world.a.tenant_id)


def test_W3_06_a_failed_insert_queues_the_half_made_version(
    world: Any,
    api: Any,
    admin_engine: Engine,
    api_store: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    who = world.person("office_admin")
    tenant = world.a.tenant_id
    up = _upload(api, who, S.pdf())

    def boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("synthetic insert failure")

    monkeypatch.setattr(repo, "insert_version", boom)
    with pytest.raises(RuntimeError):
        _register(api, who, up["upload_id"])
    monkeypatch.undo()
    with admin_engine.connect() as c:
        doc_id: Any = c.execute(
            text("SELECT document_id FROM kb.upload_intents WHERE id = :i"),
            {"i": up["upload_id"]},
        ).scalar_one()
    final = f"t/{tenant}/docs/{doc_id}/v1/original.pdf"
    assert final in api_store.objects, "the api did not delete the copied object"
    (event,) = [e for e in _events(admin_engine, tenant) if e.get("document_id") == str(doc_id)]
    assert event["version_no"] == 1
    assert event["ext"] == "pdf"
    storage.set_object_store(api_store)
    assert _run(tenant, event) is True
    assert final not in api_store.objects
    assert final in api_store.discarded


def test_W3_06_the_worker_never_discards_a_key_a_version_uses(
    world: Any, admin_engine: Engine, store: Any
) -> None:
    """A forged event (the api's database role can write the outbox) cannot make the worker
    discard a registered file, another school's file, or a key outside the known layouts."""
    tenant = world.a.tenant_id
    owner = world.person("owner").user_id
    doc_id = S.make_document(admin_engine, tenant, owner)
    key = f"t/{tenant}/docs/{doc_id}/v1/original.pdf"
    now = dt.datetime.now(dt.UTC).isoformat()
    forged = {"document_id": str(doc_id), "version_no": 1, "ext": "pdf", "requested_at": now}
    assert _run(tenant, forged) is False
    assert _run(world.b.tenant_id, forged) is False
    assert _run(tenant, {**forged, "ext": "../../x"}) is False
    assert _run(tenant, {**forged, "version_no": "1/../../v1"}) is False
    assert _run(tenant, {"upload_id": str(uuid.uuid4())}) is False
    assert _run(tenant, {"object_key": key}) is False
    assert _run(tenant, {}) is False
    assert key in store.objects
    assert key not in store.discarded


def test_W3_06_the_worker_skips_a_key_written_again_after_the_request(
    world: Any, admin_engine: Engine, store: Any
) -> None:
    """A retry may copy the same final key again before its insert commits: an object newer
    than the request is left alone."""
    tenant = world.a.tenant_id
    doc_id = uuid.uuid4()
    key = f"t/{tenant}/docs/{doc_id}/v1/original.pdf"
    before = (dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1)).isoformat()
    store.put(key, S.pdf(), PDF_CT)
    payload = {"document_id": str(doc_id), "version_no": 1, "ext": "pdf", "requested_at": before}
    assert _run(tenant, payload) is False
    assert key in store.objects
    later = (dt.datetime.now(dt.UTC) + dt.timedelta(minutes=1)).isoformat()
    assert _run(tenant, {**payload, "requested_at": later}) is True
    assert key not in store.objects


def test_W3_06_another_schools_upload_id_is_not_found(
    world: Any, api: Any, admin_engine: Engine, store: Any
) -> None:
    who = world.person("office_admin")
    up = _upload(api, who, S.pdf())
    assert _run(world.b.tenant_id, {"upload_id": up["upload_id"]}) is False
    assert up["fields"]["key"] in store.objects
