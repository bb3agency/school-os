"""Who may change a document they can see (audit 2026-10-04 api-auth AA-10; FR-DOC-005,
FR-DOC-006, FR-DOC-010, SEC-015; invariants 2 and 3). Synthetic data only.

A new version (upload intent, ``POST /documents/{id}/versions``, a saved sheet) or a metadata
PATCH needs ``document.upload`` AND either having uploaded the document or holding
``document.manage_acl``. Seeing the document through its ACL is not enough: a class teacher
must not replace a file the principal shared with their sections. A visible document answers
403 ``document_owner_only``; an invisible one stays 404.
"""

from __future__ import annotations

import io
import sys
import uuid
from typing import Any

import pytest
from openpyxl import Workbook
from sqlalchemy import Engine

pytestmark = pytest.mark.db
S = sys.modules["sos_test_documents_support"]
DOCS = "/api/v1/documents"
PDF_CT = "application/pdf"
XLSX_CT = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
CODE = "document_owner_only"


@pytest.fixture(autouse=True)
def _store(store: Any) -> Any:
    return store


def _shared_doc(world: Any, admin: Engine, **kw: Any) -> uuid.UUID:
    """A document the owner uploaded and shared with section 9A (the class teacher's)."""
    doc: uuid.UUID = S.make_document(
        admin,
        world.a.tenant_id,
        world.person("owner").user_id,
        acl=[("section", str(world.a.ids["section_9a"]))],
        **kw,
    )
    return doc


def _upload_intent(api: Any, who: Any, data: bytes, document_id: Any = None) -> Any:
    body: dict[str, Any] = {
        "filename": "circular.pdf",
        "content_type": PDF_CT,
        "size_bytes": len(data),
        "purpose": "circular",
    }
    if document_id is not None:
        body["document_id"] = str(document_id)
    return api.call(who, "POST", f"{DOCS}/uploads", json=body)


def _etag(api: Any, who: Any, doc_id: Any) -> str:
    res = api.call(who, "GET", f"{DOCS}/{doc_id}")
    assert res.status_code == 200, res.text
    value: str = res.headers["ETag"]
    return value


def _patch(api: Any, who: Any, doc_id: Any, etag: str) -> Any:
    return api.call(
        who,
        "PATCH",
        f"{DOCS}/{doc_id}",
        json={"title": "Synthetic new title"},
        headers={"If-Match": etag},
    )


def _own_document(api: Any, who: Any, world: Any) -> str:
    data = S.pdf(f"own-{uuid.uuid4()}")
    up = _upload_intent(api, who, data)
    assert up.status_code == 201, up.text
    assert S.memory_store().browser_post(up.json()["fields"], data, PDF_CT) == 204
    res = api.call(
        who,
        "POST",
        DOCS,
        json={
            "upload_id": up.json()["upload_id"],
            "title": "Synthetic own circular",
            "acl": [{"principal_type": "section", "principal_ref": str(world.a.ids["section_9a"])}],
        },
    )
    assert res.status_code == 202, res.text
    doc_id: str = res.json()["id"]
    return doc_id


def test_AA_10_class_teacher_cannot_start_a_new_version_of_a_shared_document(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    doc = _shared_doc(world, admin_engine)
    ct = world.person("class_teacher")
    assert api.call(ct, "GET", f"{DOCS}/{doc}").status_code == 200  # visible
    res = _upload_intent(api, ct, S.pdf("replacement"), document_id=doc)
    assert res.status_code == 403, res.text
    assert res.json()["code"] == CODE


def test_AA_10_class_teacher_cannot_register_a_version_of_a_shared_document(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    doc = _shared_doc(world, admin_engine)
    ct = world.person("class_teacher")
    own = _own_document(api, ct, world)
    data = S.pdf("own-v2")
    up = _upload_intent(api, ct, data, document_id=own)
    assert up.status_code == 201, up.text
    assert S.memory_store().browser_post(up.json()["fields"], data, PDF_CT) == 204
    res = api.call(ct, "POST", f"{DOCS}/{doc}/versions", json={"upload_id": up.json()["upload_id"]})
    assert res.status_code == 403, res.text
    assert res.json()["code"] == CODE
    detail = api.call(world.person("owner"), "GET", f"{DOCS}/{doc}").json()
    assert [v["version_no"] for v in detail["versions"]] == [1]
    # Their own document still takes the version.
    mine = api.call(
        ct, "POST", f"{DOCS}/{own}/versions", json={"upload_id": up.json()["upload_id"]}
    )
    assert mine.status_code == 202, mine.text


def test_AA_10_class_teacher_cannot_patch_a_shared_document_but_can_patch_their_own(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    doc = _shared_doc(world, admin_engine)
    ct = world.person("class_teacher")
    res = _patch(api, ct, doc, _etag(api, ct, doc))
    assert res.status_code == 403, res.text
    assert res.json()["code"] == CODE
    own = _own_document(api, ct, world)
    ok = _patch(api, ct, own, _etag(api, ct, own))
    assert ok.status_code == 200, ok.text


def test_AA_10_school_wide_uploader_without_manage_acl_cannot_change_others_documents(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    doc = _shared_doc(world, admin_engine)
    staff = world.person("office_staff")
    res = _patch(api, staff, doc, _etag(api, staff, doc))
    assert res.status_code == 403
    assert res.json()["code"] == CODE
    assert _upload_intent(api, staff, S.pdf("x"), document_id=doc).status_code == 403


@pytest.mark.parametrize("role", ["office_admin", "principal"])
def test_AA_10_document_managers_still_change_any_visible_document(
    world: Any, api: Any, admin_engine: Engine, role: str
) -> None:
    doc = _shared_doc(world, admin_engine)
    who = world.person(role)
    assert _patch(api, who, doc, _etag(api, who, doc)).status_code == 200
    assert _upload_intent(api, who, S.pdf(f"m-{role}"), document_id=doc).status_code == 201


def test_AA_10_invisible_documents_stay_404(world: Any, api: Any, admin_engine: Engine) -> None:
    hidden: uuid.UUID = S.make_document(
        admin_engine,
        world.a.tenant_id,
        world.person("owner").user_id,
        acl=[("section", str(world.a.ids["section_10a"]))],
    )
    ct = world.person("class_teacher")
    assert _patch(api, ct, hidden, 'W/"1"').status_code == 404
    assert _upload_intent(api, ct, S.pdf("h"), document_id=hidden).status_code == 404


def _xlsx() -> bytes:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.append(["Name", "Class"])
    ws.append(["Synthetica One", "9A"])
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def test_AA_10_sheet_save_needs_the_uploader_or_a_document_manager(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    doc = _shared_doc(world, admin_engine, data=_xlsx(), mime_type=XLSX_CT, ext="xlsx")
    ct = world.person("class_teacher")
    sheet = api.call(ct, "GET", f"{DOCS}/{doc}/sheet")
    assert sheet.status_code == 200, sheet.text
    assert (sheet.json()["editable"], sheet.json()["read_only_reason"]) == (
        False,
        "no_permission",
    )
    res = api.call(
        ct,
        "POST",
        f"{DOCS}/{doc}/sheet/versions",
        json={
            "base_version_no": 1,
            "edits": [{"row_no": 2, "column": 0, "value": "Synthetica Two"}],
        },
        headers={"If-Match": _etag(api, ct, doc)},
    )
    assert res.status_code == 403, res.text
    assert res.json()["code"] == CODE
