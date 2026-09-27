"""Document metadata edits, archive/unarchive and uploader names (FR-DOC-005, FR-DOC-006,
SEC-015, SEC-001, invariants 2, 3, 5 and 7; docs/09 Documents). Synthetic data only."""

from __future__ import annotations

import sys
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import Engine

from app.documents import service

pytestmark = pytest.mark.db
S = sys.modules["sos_test_documents_support"]
W = sys.modules["sos_test_api_world"]
DOCS = "/api/v1/documents"
TITLE = "Circular for Pothuraju Synthetica family"


def _doc(world: Any, admin: Engine, *, acl: list[tuple[str, str]] | None = None, **kw: Any) -> Any:
    return S.make_document(admin, world.a.tenant_id, world.person("owner").user_id, acl=acl, **kw)


def _etag(api: Any, who: Any, doc_id: uuid.UUID) -> str:
    res = api.call(who, "GET", f"{DOCS}/{doc_id}")
    assert res.status_code == 200, res.text
    value: str = res.headers["ETag"]
    return value


def _patch(api: Any, who: Any, doc_id: uuid.UUID, body: dict[str, Any], etag: str) -> Any:
    return api.call(who, "PATCH", f"{DOCS}/{doc_id}", json=body, headers={"If-Match": etag})


def _post(api: Any, who: Any, doc_id: uuid.UUID, action: str, etag: str | None) -> Any:
    headers = {"If-Match": etag} if etag is not None else {}
    return api.call(who, "POST", f"{DOCS}/{doc_id}/{action}", headers=headers)


def _events(admin: Engine, world: Any, doc_id: uuid.UUID, prefix: str) -> list[dict[str, Any]]:
    return [
        e
        for e in W.audit_events(admin, world.a.tenant_id)
        if e["resource_id"] == doc_id and e["action"].startswith(prefix)
    ]


def _all_documents(api: Any, who: Any, **params: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        query = {"limit": 200, **params, **({"cursor": cursor} if cursor else {})}
        page = api.call(who, "GET", DOCS, params=query).json()
        out += page["data"]
        cursor = page["next_cursor"]
        if not cursor:
            return out


# --- PATCH /documents/{id} ---------------------------------------------------------------------


def test_FR_DOC_005_patch_metadata_audits_field_names_only(
    world: Any, api: Any, admin_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    admin = world.person("office_admin")
    doc = _doc(world, admin_engine)
    etag = _etag(api, admin, doc)
    capsys.readouterr()
    res = _patch(
        api,
        admin,
        doc,
        {
            "title": TITLE,
            "doc_type": "letter",
            "language": "te",
            "issuer": "Synthetic Education Office",
            "issued_on": "2026-07-01",
        },
        etag,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert (body["title"], body["doc_type"], body["language"]) == (TITLE, "letter", "te")
    assert (body["issuer"], body["issued_on"]) == ("Synthetic Education Office", "2026-07-01")
    assert res.headers["ETag"] != etag
    logs = "".join(capsys.readouterr())
    assert "http.request" in logs
    assert "Pothuraju" not in logs
    events = _events(admin_engine, world, doc, "document.metadata_updated")
    assert [e["summary"] for e in events] == [
        {"fields": ["doc_type", "issued_on", "issuer", "language", "title"]}
    ]

    # Clearing optional fields; unchanged values are ignored.
    cleared = _patch(
        api, admin, doc, {"issuer": None, "issued_on": None, "title": TITLE}, res.headers["ETag"]
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["issuer"] is None
    assert _events(admin_engine, world, doc, "document.metadata_updated")[-1]["summary"] == {
        "fields": ["issued_on", "issuer"]
    }
    same = _patch(api, admin, doc, {"title": TITLE}, cleared.headers["ETag"])
    assert same.status_code == 200
    assert same.headers["ETag"] == cleared.headers["ETag"]


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"title": None},
        {"doc_type": None},
        {"title": ""},
        {"doc_type": "unknown"},
        {"language": "hi"},
        {"sensitivity": "C1"},
        {"purpose": "evidence"},
        {"acl": []},
    ],
)
def test_FR_DOC_005_patch_rejects_bad_bodies(
    world: Any, api: Any, admin_engine: Engine, body: dict[str, Any]
) -> None:
    owner = world.person("owner")
    doc = _doc(world, admin_engine)
    res = _patch(api, owner, doc, body, _etag(api, owner, doc))
    assert res.status_code == 422, res.text
    assert not _events(admin_engine, world, doc, "document.metadata_updated")


def test_FR_DOC_005_doc_type_must_suit_purpose(world: Any, api: Any, admin_engine: Engine) -> None:
    owner = world.person("owner")
    doc = _doc(world, admin_engine, purpose="register_scan", doc_type="register_scan")
    res = _patch(api, owner, doc, {"doc_type": "circular"}, _etag(api, owner, doc))
    assert res.status_code == 422
    assert res.json()["errors"][0]["code"] == "doc_type_not_allowed_for_purpose"


def test_FR_DOC_005_patch_needs_if_match_and_rejects_stale(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    owner = world.person("owner")
    doc = _doc(world, admin_engine)
    missing = api.call(owner, "PATCH", f"{DOCS}/{doc}", json={"title": TITLE})
    assert missing.status_code == 400
    assert missing.json()["code"] == "if_match_required"
    assert _patch(api, owner, doc, {"title": TITLE}, 'W/"99"').status_code == 412


def test_SEC_015_patch_follows_document_acl_and_upload_permission(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    in_scope = _doc(world, admin_engine, acl=[("section", str(world.a.ids["section_9a"]))])
    out_of_scope = _doc(world, admin_engine, acl=[("section", str(world.a.ids["section_10a"]))])
    ct = world.person("class_teacher")
    ok = _patch(api, ct, in_scope, {"title": TITLE}, _etag(api, ct, in_scope))
    assert ok.status_code == 200, ok.text
    hidden = _patch(api, ct, out_of_scope, {"title": TITLE}, 'W/"1"')
    assert hidden.status_code == 404
    random = _patch(api, ct, uuid.uuid4(), {"title": TITLE}, 'W/"1"')
    assert hidden.json()["detail"] == random.json()["detail"]
    for role in ("teacher", "auditor_readonly"):
        who = world.person(role)
        res = _patch(api, who, in_scope, {"title": "x"}, 'W/"2"')
        assert res.status_code == 403, role
    b_doc = S.make_document(
        admin_engine, world.b.tenant_id, world.b.people["owner"].user_id, acl=None
    )
    assert _patch(api, world.person("owner"), b_doc, {"title": TITLE}, 'W/"1"').status_code == 404


# --- archive / unarchive --------------------------------------------------------------------------


@pytest.fixture
def hook_calls() -> Iterator[list[tuple[uuid.UUID, str]]]:
    calls: list[tuple[uuid.UUID, str]] = []

    def hook(session: Any, document_id: uuid.UUID, status: str) -> None:
        calls.append((document_id, status))

    service.STATUS_CHANGED_HOOKS.append(hook)
    try:
        yield calls
    finally:
        service.STATUS_CHANGED_HOOKS.remove(hook)


def test_FR_DOC_006_archive_and_unarchive(
    world: Any, api: Any, admin_engine: Engine, hook_calls: list[tuple[uuid.UUID, str]]
) -> None:
    admin = world.person("office_admin")
    doc = _doc(world, admin_engine)
    res = _post(api, admin, doc, "archive", _etag(api, admin, doc))
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "archived"
    archived = _all_documents(api, admin, status="archived")
    assert str(doc) in {d["id"] for d in archived}
    active = _all_documents(api, admin, status="active")
    assert str(doc) not in {d["id"] for d in active}
    assert api.call(admin, "GET", f"{DOCS}/{doc}").status_code == 200, "kept, readable"

    # No edits while archived; archiving again changes nothing.
    edit = _patch(api, admin, doc, {"title": TITLE}, res.headers["ETag"])
    assert edit.status_code == 409
    assert edit.json()["code"] == "document_archived"
    again = _post(api, admin, doc, "archive", res.headers["ETag"])
    assert again.status_code == 200
    assert again.headers["ETag"] == res.headers["ETag"]

    back = _post(api, admin, doc, "unarchive", res.headers["ETag"])
    assert back.status_code == 200, back.text
    assert back.json()["status"] == "active"
    events = _events(admin_engine, world, doc, "document.")
    assert [e["action"] for e in events] == ["document.archived", "document.unarchived"]
    assert all(e["summary"] == {"purpose": "circular"} for e in events)
    assert hook_calls == [(doc, "archived"), (doc, "active")]


def test_FR_DOC_006_archive_needs_manage_acl_and_if_match(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    doc = _doc(world, admin_engine, acl=[("section", str(world.a.ids["section_9a"]))])
    etag = _etag(api, world.person("owner"), doc)
    for role in ("office_staff", "class_teacher", "teacher", "auditor_readonly"):
        for action in ("archive", "unarchive"):
            assert _post(api, world.person(role), doc, action, etag).status_code == 403, role
    owner = world.person("owner")
    missing = _post(api, owner, doc, "archive", None)
    assert missing.status_code == 400
    assert _post(api, owner, doc, "archive", 'W/"99"').status_code == 412
    b_doc = S.make_document(admin_engine, world.b.tenant_id, world.b.people["owner"].user_id)
    assert _post(api, owner, b_doc, "archive", 'W/"1"').status_code == 404
    assert not _events(admin_engine, world, doc, "document.archived")


# --- uploader -------------------------------------------------------------------------------------


def test_FR_DOC_005_uploader_name_without_contact_details(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    uploader = W.add_member(
        admin_engine, world.a.tenant_id, ["office_staff"], email="uploader@example.test"
    )
    doc = S.make_document(admin_engine, world.a.tenant_id, uploader.user_id)
    mine = api.call(uploader, "GET", f"{DOCS}/{doc}").json()
    expected = {"membership_id": str(uploader.membership_id), "display_name": uploader.display_name}
    assert mine["uploaded_by"] == expected
    assert mine["uploaded_by_me"] is True
    assert mine["versions"][0]["uploaded_by"] == expected
    assert mine["versions"][0]["uploaded_by_me"] is True
    assert mine["current_version"]["uploaded_by_me"] is True

    theirs = api.call(world.person("owner"), "GET", f"{DOCS}/{doc}")
    assert theirs.json()["uploaded_by"] == expected
    assert theirs.json()["uploaded_by_me"] is False
    assert "uploader@example.test" not in theirs.text
    row = next(d for d in _all_documents(api, world.person("owner")) if d["id"] == str(doc))
    assert row["uploaded_by"] == expected
    assert row["uploaded_by_me"] is False
