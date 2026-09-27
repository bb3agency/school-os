"""Documents API and service (FR-DOC-001..008, SEC-016, SEC-015, SEC-001; docs/09 Documents).

Uses the shared synthetic world (tests/api/world.py) and an in-memory object store that
enforces the presigned POST policy like S3 does. The real S3 round trip is in
``test_storage_s3.py``.
"""

from __future__ import annotations

import sys
import urllib.parse
import uuid
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import Engine, text

from app.core.config import Environment, Settings
from app.core.db import tenant_session
from app.core.errors import Conflict
from app.documents import service
from app.documents.scanning import DevNoopScanner, ScannerUnavailable, ScanResult

pytestmark = pytest.mark.db
S = sys.modules["sos_test_documents_support"]
W = sys.modules["sos_test_api_world"]

PDF_CT = "application/pdf"
DOCX_CT = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
DEV_SCANNER = DevNoopScanner(
    Settings(env=Environment.CI, local_dev_master_key=SecretStr("synthetic-ci-master-key-0123"))
)


# --- helpers --------------------------------------------------------------------------------


def upload(
    api: Any,
    who: Any,
    data: bytes,
    *,
    purpose: str = "circular",
    filename: str = "circular.pdf",
    content_type: str = PDF_CT,
    document_id: uuid.UUID | str | None = None,
    send: bool = True,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "filename": filename,
        "content_type": content_type,
        "size_bytes": len(data),
        "purpose": purpose,
    }
    if document_id is not None:
        body["document_id"] = str(document_id)
    res = api.call(who, "POST", "/api/v1/documents/uploads", json=body)
    assert res.status_code == 201, res.text
    out: dict[str, Any] = res.json()
    if send:
        assert S.memory_store().browser_post(out["fields"], data, content_type) == 204
    return out


def register(api: Any, who: Any, upload_id: str, **meta: Any) -> Any:
    body = {"upload_id": upload_id, "title": "Synthetic circular", **meta}
    return api.call(who, "POST", "/api/v1/documents", json=body)


def new_document(api: Any, who: Any, data: bytes | None = None, **meta: Any) -> dict[str, Any]:
    up = upload(api, who, data or S.pdf())
    res = register(api, who, up["upload_id"], **meta)
    assert res.status_code == 202, res.text
    doc: dict[str, Any] = res.json()
    return doc


def scan(tenant_id: uuid.UUID, doc: dict[str, Any]) -> str:
    return service.scan_version(
        tenant_id,
        uuid.UUID(doc["id"]),
        uuid.UUID(doc["current_version"]["id"]),
        scanner=DEV_SCANNER,
    )


def visible_ids(api: Any, who: Any, **params: Any) -> set[str]:
    res = api.call(who, "GET", "/api/v1/documents", params={"limit": 200, **params})
    assert res.status_code == 200, res.text
    return {d["id"] for d in res.json()["data"]}


@pytest.fixture(autouse=True)
def _store(store: Any) -> Any:
    return store


# --- uploads --------------------------------------------------------------------------------


def test_FR_DOC_001_upload_returns_a_constrained_presigned_post(world: Any, api: Any) -> None:
    who = world.person("office_admin")
    data = S.pdf()
    out = upload(api, who, data, send=False)
    policy = S.memory_store().posts[-1]
    tenant = world.a.tenant_id
    # Staging key per upload; verified bytes are later copied to the final key.
    assert policy["key"] == f"t/{tenant}/uploads/{out['upload_id']}/original.pdf"
    assert policy["content_type"] == PDF_CT
    assert policy["max"] == len(data)
    assert policy["ttl"] <= 600
    assert out["max_bytes"] == len(data)
    assert out["fields"]["key"] == policy["key"]
    assert out["document_id"] is None


@pytest.mark.parametrize(
    ("filename", "content_type", "purpose", "size", "status"),
    [
        ("page.html", "text/html", "circular", 100, 415),
        ("setup.exe", "application/x-msdownload", "other", 100, 415),
        ("scan.png", PDF_CT, "circular", 100, 415),  # extension disagrees with type
        ("letter.docx", DOCX_CT, "evidence", 100, 415),  # evidence: scans only
        ("list.csv", "text/csv", "circular", 100, 415),  # CSV only for imports
        ("big.pdf", PDF_CT, "circular", 25 * 1024 * 1024 + 1, 413),
        (
            "big.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "import_file",
            10 * 1024 * 1024 + 1,
            413,
        ),
    ],
)
def test_FR_DOC_001_uploads_outside_the_allowlist_are_refused(
    world: Any,
    api: Any,
    *,
    filename: str,
    content_type: str,
    purpose: str,
    size: int,
    status: int,
) -> None:
    res = api.call(
        world.person("office_admin"),
        "POST",
        "/api/v1/documents/uploads",
        json={
            "filename": filename,
            "content_type": content_type,
            "size_bytes": size,
            "purpose": purpose,
        },
    )
    assert res.status_code == status, res.text
    assert res.headers["content-type"].startswith("application/problem+json")


def test_FR_IMP_import_files_use_the_imports_layout(world: Any, api: Any) -> None:
    who = world.person("office_staff")
    data = S.csv_text()
    out = upload(
        api, who, data, purpose="import_file", filename="students.csv", content_type="text/csv"
    )
    assert out["batch_id"]
    res = register(api, who, out["upload_id"], title="Admissions list")
    assert res.status_code == 202, res.text
    objects = S.memory_store().objects
    assert f"t/{world.a.tenant_id}/imports/{out['batch_id']}/raw.csv" in objects
    assert out["fields"]["key"] not in objects, "staging copy removed"
    body = res.json()
    assert (body["purpose"], body["doc_type"], body["sensitivity"]) == (
        "import_file",
        "import_file",
        "C2",
    )
    # Import files never get versions.
    res = api.call(
        who,
        "POST",
        "/api/v1/documents/uploads",
        json={
            "filename": "students.csv",
            "content_type": "text/csv",
            "size_bytes": 10,
            "purpose": "import_file",
            "document_id": body["id"],
        },
    )
    assert res.status_code == 409
    assert res.json()["code"] == "not_versionable"


def test_FR_IMP_binary_declared_as_csv_is_rejected(world: Any, api: Any) -> None:
    who = world.person("office_staff")
    up = upload(
        api,
        who,
        b"a,b\n\x00\x00\x00",
        purpose="import_file",
        filename="x.csv",
        content_type="text/csv",
    )
    res = register(api, who, up["upload_id"])
    assert res.status_code == 415
    assert res.json()["code"] == "not_text"


# --- registration ---------------------------------------------------------------------------


def test_FR_DOC_005_register_creates_document_version_acl_audit_and_scan_job(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    who = world.person("office_admin")
    up = upload(api, who, S.pdf())
    res = register(
        api,
        who,
        up["upload_id"],
        title="Sankranti holidays circular",
        issuer="Synthetic Model School",
        issued_on="2027-01-05",
        academic_year_id=str(world.a.ids["year"]),
        language="te",
        acl=[{"principal_type": "role", "principal_ref": "teacher"}],
    )
    assert res.status_code == 202, res.text
    doc = res.json()
    assert res.headers["Location"] == f"/api/v1/documents/{doc['id']}"
    assert res.headers["ETag"] == 'W/"1"'
    assert doc["current_version"]["status"] == "queued"
    assert doc["current_version"]["version_no"] == 1
    assert doc["sensitivity"] == "C1"
    assert doc["acl"] == [{"principal_type": "role", "principal_ref": "teacher"}]
    assert "object_key" not in res.text
    assert "sha256" not in res.text
    events = W.audit_events(admin_engine, world.a.tenant_id, "document.registered")
    mine = [e for e in events if str(e["resource_id"]) == doc["id"]]
    assert len(mine) == 1
    assert mine[0]["summary"]["acl_entries"] == 1
    assert mine[0]["summary"]["mime_type"] == PDF_CT
    jobs = S.outbox_events(admin_engine, world.a.tenant_id, "document.version.registered")
    assert {"document_id": doc["id"], "version_id": doc["current_version"]["id"]} in jobs
    again = register(api, who, up["upload_id"])
    assert again.status_code == 409
    assert again.json()["code"] == "upload_already_used"


def test_FR_DOC_001_png_renamed_to_pdf_is_415_and_object_deleted(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    who = world.person("office_admin")
    up = upload(api, who, S.png())  # declared PDF, bytes are PNG
    before = W.audit_events(admin_engine, world.a.tenant_id, "document.registered")
    res = register(api, who, up["upload_id"])
    assert res.status_code == 415
    assert res.json()["code"] == "unsupported_file_type"
    assert up["fields"]["key"] not in S.memory_store().objects
    assert W.audit_events(admin_engine, world.a.tenant_id, "document.registered") == before


@pytest.mark.parametrize(
    ("data", "code"),
    [
        (
            b"<!doctype html><html><body><script>alert(1)</script></body></html>",
            "unsupported_file_type",
        ),
        (
            b"\xff\xd8\xff\xe0\x00\x10JFIF\x00<script>alert(1)</script>\xff\xd9",
            "polyglot_suspected",
        ),
    ],
    ids=["html-as-jpeg", "jpeg-html-polyglot"],
)
def test_SEC_016_html_and_polyglots_declared_as_images_are_rejected(
    world: Any, api: Any, data: bytes, code: str
) -> None:
    who = world.person("office_admin")
    up = upload(
        api, who, data, filename="photo.jpg", content_type="image/jpeg", purpose="register_scan"
    )
    res = register(api, who, up["upload_id"], title="Register page 4")
    assert res.status_code == 415, res.text
    assert res.json()["code"] == code
    assert up["fields"]["key"] not in S.memory_store().objects


def test_SEC_016_stored_object_bigger_than_declared_is_rejected(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    who = world.person("office_admin")
    data = S.pdf()
    intent = S.make_intent(admin_engine, world.a.tenant_id, who.user_id, data)
    with admin_engine.begin() as c:
        key: Any = c.execute(
            text("SELECT object_key FROM kb.upload_intents WHERE id = :i"), {"i": intent}
        ).scalar_one()
    S.memory_store().put(key, data + b"% padding\n%%EOF\n", PDF_CT)
    res = register(api, who, str(intent))
    assert res.status_code == 422
    assert res.json()["errors"][0]["code"] == "size_mismatch"
    assert key not in S.memory_store().objects

    intent = S.make_intent(admin_engine, world.a.tenant_id, who.user_id, data)
    with admin_engine.begin() as c:
        c.execute(
            text("UPDATE kb.upload_intents SET max_bytes = 16, declared_size = 16 WHERE id = :i"),
            {"i": intent},
        )
    res = register(api, who, str(intent))
    assert res.status_code == 413
    assert res.json()["code"] == "file_too_large"


def test_SEC_016_upload_not_sent_yet_is_409(world: Any, api: Any) -> None:
    who = world.person("office_admin")
    up = upload(api, who, S.pdf(), send=False)
    res = register(api, who, up["upload_id"])
    assert res.status_code == 409
    assert res.json()["code"] == "upload_missing"


def test_SEC_016_expired_intent_is_refused(world: Any, api: Any, admin_engine: Engine) -> None:
    who = world.person("office_admin")
    intent = S.make_intent(admin_engine, world.a.tenant_id, who.user_id, S.pdf())
    with admin_engine.begin() as c:
        c.execute(
            text(
                "UPDATE kb.upload_intents SET created_at = now() - interval '50 minutes', "
                "expires_at = now() - interval '1 minute' WHERE id = :i"
            ),
            {"i": intent},
        )
    res = register(api, who, str(intent))
    assert res.status_code == 409
    assert res.json()["code"] == "upload_expired"


def test_SEC_016_intent_of_another_user_or_school_is_404(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    mine = S.make_intent(
        admin_engine, world.a.tenant_id, world.person("office_admin").user_id, S.pdf()
    )
    res = register(api, world.person("principal"), str(mine))
    assert res.status_code == 404
    b_owner = world.b.people["owner"]
    theirs = S.make_intent(admin_engine, world.b.tenant_id, b_owner.user_id, S.pdf())
    res = register(api, world.person("owner"), str(theirs))
    assert res.status_code == 404
    random = register(api, world.person("owner"), str(uuid.uuid4()))
    assert {k: res.json()[k] for k in ("status", "code")} == {
        k: random.json()[k] for k in ("status", "code")
    }


def test_FR_DOC_001_duplicate_file_is_reported_only_when_visible(world: Any, api: Any) -> None:
    admin = world.person("office_admin")
    data = S.pdf()
    first = new_document(api, admin, data)
    up = upload(api, admin, data)
    res = register(api, admin, up["upload_id"])
    assert res.status_code == 409
    assert res.json()["code"] == "duplicate_document"
    assert first["id"] in res.json()["detail"]
    assert up["fields"]["key"] not in S.memory_store().objects

    # A duplicate the caller cannot see is not revealed: the upload proceeds.
    secret = S.pdf()
    new_document(api, admin, secret, acl=[{"principal_type": "role", "principal_ref": "owner"}])
    staff = world.person("office_staff")
    up = upload(api, staff, secret)
    res = register(api, staff, up["upload_id"])
    assert res.status_code == 202, res.text


def test_FR_DOC_005_metadata_rules(world: Any, api: Any) -> None:
    who = world.person("office_admin")
    up = upload(api, who, S.pdf(), purpose="evidence", filename="birth-certificate.pdf")
    low = register(api, who, up["upload_id"], sensitivity="C1")
    assert low.status_code == 422
    assert low.json()["errors"][0]["code"] == "sensitivity_below_minimum"
    wrong_type = register(api, who, up["upload_id"], doc_type="circular")
    assert wrong_type.status_code == 422
    other_year = register(api, who, up["upload_id"], academic_year_id=str(world.b.ids["year"]))
    assert other_year.status_code == 422
    ok = register(api, who, up["upload_id"], doc_type="certificate")
    assert ok.status_code == 202, ok.text
    assert ok.json()["sensitivity"] == "C3"


@pytest.mark.parametrize(
    "entry",
    [
        {"principal_type": "role", "principal_ref": "headmaster_of_galaxy"},
        {"principal_type": "section", "principal_ref": "not-a-uuid"},
        {"principal_type": "membership", "principal_ref": str(uuid.uuid4())},
    ],
)
def test_SEC_001_acl_references_must_exist_in_this_school(
    world: Any, api: Any, entry: dict[str, str]
) -> None:
    who = world.person("office_admin")
    up = upload(api, who, S.pdf())
    res = register(api, who, up["upload_id"], acl=[entry])
    assert res.status_code == 422


def test_SEC_001_acl_cannot_point_at_another_schools_section(world: Any, api: Any) -> None:
    who = world.person("office_admin")
    up = upload(api, who, S.pdf())
    res = register(
        api,
        who,
        up["upload_id"],
        acl=[{"principal_type": "section", "principal_ref": str(world.b.ids["section_9a"])}],
    )
    assert res.status_code == 422


# --- visibility (ACL, scopes) ---------------------------------------------------------------


def test_SEC_015_empty_acl_is_fail_closed(world: Any, api: Any, admin_engine: Engine) -> None:
    doc = str(S.make_document(admin_engine, world.a.tenant_id, world.person("owner").user_id))
    for role in (
        "owner",
        "principal",
        "office_admin",
        "office_staff",
        "accountant",
        "exam_coordinator",
        "auditor_readonly",
    ):
        assert doc in visible_ids(api, world.person(role)), role
    for role in ("class_teacher", "teacher"):
        assert doc not in visible_ids(api, world.person(role)), role
        res = api.call(world.person(role), "GET", f"/api/v1/documents/{doc}")
        assert res.status_code == 404


def test_SEC_015_role_acl_limits_school_wide_readers(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    doc = str(
        S.make_document(
            admin_engine,
            world.a.tenant_id,
            world.person("owner").user_id,
            acl=[("role", "accountant")],
        )
    )
    assert doc in visible_ids(api, world.person("accountant"))
    assert doc in visible_ids(api, world.person("principal")), "document.manage_acl sees all"
    for role in ("office_staff", "exam_coordinator", "auditor_readonly", "teacher"):
        assert doc not in visible_ids(api, world.person(role)), role


def test_SEC_015_membership_acl(world: Any, api: Any, admin_engine: Engine) -> None:
    teacher = world.person("teacher")
    doc = str(
        S.make_document(
            admin_engine,
            world.a.tenant_id,
            world.person("owner").user_id,
            acl=[("membership", str(teacher.membership_id))],
        )
    )
    assert api.call(teacher, "GET", f"/api/v1/documents/{doc}").status_code == 200
    other = world.person("class_teacher")
    assert api.call(other, "GET", f"/api/v1/documents/{doc}").status_code == 404


@pytest.mark.parametrize(
    ("acl", "class_teacher_sees", "teacher_sees"),
    [
        ([("section", "section_9a")], True, False),
        ([("section", "section_9c")], False, False),
        ([("class", "class_ix")], True, False),  # 9A is in class IX
        ([("section", "section_10a")], False, True),  # class X scope covers 10A
        ([("class", "class_x")], False, True),
    ],
)
def test_SEC_015_class_teacher_sees_only_documents_for_their_sections(
    world: Any,
    api: Any,
    admin_engine: Engine,
    *,
    acl: list[tuple[str, str]],
    class_teacher_sees: bool,
    teacher_sees: bool,
) -> None:
    refs = [(t, str(world.a.ids[k])) for t, k in acl]
    doc = str(
        S.make_document(admin_engine, world.a.tenant_id, world.person("owner").user_id, acl=refs)
    )
    for role, expected in (("class_teacher", class_teacher_sees), ("teacher", teacher_sees)):
        res = api.call(world.person(role), "GET", f"/api/v1/documents/{doc}")
        assert res.status_code == (200 if expected else 404), (role, acl)
        assert (doc in visible_ids(api, world.person(role))) is expected
    assert doc in visible_ids(api, world.person("office_staff")), "school-wide covers sections"


def test_SEC_015_scoped_uploader_must_restrict_acl_to_own_sections(world: Any, api: Any) -> None:
    ct = world.person("class_teacher")
    for acl, code in (
        ([], "acl_required_for_scoped_upload"),
        ([{"principal_type": "role", "principal_ref": "teacher"}], "acl_outside_scope"),
        (
            [{"principal_type": "section", "principal_ref": str(world.a.ids["section_9c"])}],
            "acl_outside_scope",
        ),
    ):
        up = upload(api, ct, S.pdf())
        res = register(api, ct, up["upload_id"], acl=acl)
        assert res.status_code == 422, res.text
        assert res.json()["errors"][0]["code"] == code
    up = upload(api, ct, S.pdf())
    ok = register(
        api,
        ct,
        up["upload_id"],
        acl=[{"principal_type": "section", "principal_ref": str(world.a.ids["section_9a"])}],
    )
    assert ok.status_code == 202, ok.text
    assert ok.json()["id"] in visible_ids(api, ct)


def test_SEC_001_lists_never_show_other_schools_documents(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    b_doc = str(S.make_document(admin_engine, world.b.tenant_id, world.b.people["owner"].user_id))
    assert b_doc not in visible_ids(api, world.person("owner"))
    res = api.call(world.person("owner"), "GET", f"/api/v1/documents/{b_doc}")
    assert res.status_code == 404


def test_list_pagination_and_filters(world: Any, api: Any, admin_engine: Engine) -> None:
    owner = world.person("owner")
    ids = {
        str(
            S.make_document(
                admin_engine, world.a.tenant_id, owner.user_id, purpose="policy", doc_type="policy"
            )
        )
        for _ in range(3)
    }
    seen: list[str] = []
    cursor = None
    while True:
        params: dict[str, Any] = {"limit": 2, "purpose": "policy"}
        if cursor:
            params["cursor"] = cursor
        page = api.call(owner, "GET", "/api/v1/documents", params=params).json()
        seen += [d["id"] for d in page["data"]]
        assert all(d["purpose"] == "policy" for d in page["data"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert ids <= set(seen)
    assert len(seen) == len(set(seen)), "no duplicates across pages"
    assert seen == sorted(seen, reverse=True), "newest first"
    bad = api.call(owner, "GET", "/api/v1/documents", params={"cursor": "!!"})
    assert bad.status_code == 422


# --- versions -------------------------------------------------------------------------------


def test_FR_DOC_006_new_versions_keep_history(world: Any, api: Any) -> None:
    who = world.person("office_admin")
    doc = new_document(api, who)
    up = upload(api, who, S.pdf(), document_id=doc["id"])
    assert up["document_id"] == doc["id"]
    res = api.call(
        who, "POST", f"/api/v1/documents/{doc['id']}/versions", json={"upload_id": up["upload_id"]}
    )
    assert res.status_code == 202, res.text
    assert res.json()["current_version"]["version_no"] == 2
    detail = api.call(who, "GET", f"/api/v1/documents/{doc['id']}").json()
    assert [v["version_no"] for v in detail["versions"]] == [2, 1]
    # Same bytes as the current version -> 409; an upload for a new doc cannot be a version.
    same = S.pdf("same")
    up = upload(api, who, same, document_id=doc["id"])
    assert (
        api.call(
            who,
            "POST",
            f"/api/v1/documents/{doc['id']}/versions",
            json={"upload_id": up["upload_id"]},
        ).status_code
        == 202
    )
    up = upload(api, who, same, document_id=doc["id"])
    res = api.call(
        who, "POST", f"/api/v1/documents/{doc['id']}/versions", json={"upload_id": up["upload_id"]}
    )
    assert res.status_code == 409
    assert res.json()["code"] == "version_unchanged"
    fresh = upload(api, who, S.pdf())
    res = api.call(
        who,
        "POST",
        f"/api/v1/documents/{doc['id']}/versions",
        json={"upload_id": fresh["upload_id"]},
    )
    assert res.status_code == 422


def test_FR_DOC_006_concurrent_version_uploads_first_wins(world: Any, api: Any) -> None:
    who = world.person("office_admin")
    doc = new_document(api, who)
    upload(api, who, S.pdf(), document_id=doc["id"], send=False)  # abandoned attempt
    mine = upload(api, who, S.pdf(), document_id=doc["id"])
    theirs = upload(api, world.person("principal"), S.pdf(), document_id=doc["id"])
    res = api.call(
        who,
        "POST",
        f"/api/v1/documents/{doc['id']}/versions",
        json={"upload_id": mine["upload_id"]},
    )
    assert res.status_code == 202, res.text
    key = f"t/{world.a.tenant_id}/docs/{doc['id']}/v2/original.pdf"
    assert key in S.memory_store().objects
    res = api.call(
        world.person("principal"),
        "POST",
        f"/api/v1/documents/{doc['id']}/versions",
        json={"upload_id": theirs["upload_id"]},
    )
    assert res.status_code == 409
    assert res.json()["code"] == "version_conflict"


def test_FR_DOC_006_archived_document_gets_no_new_version(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    """An archived document is read-only: no version upload or registration (409
    ``document_archived``), until it is unarchived."""
    who = world.person("office_admin")
    doc = new_document(api, who)
    path = f"/api/v1/documents/{doc['id']}"
    pending = upload(api, who, S.pdf(), document_id=doc["id"])  # issued before the archive
    etag = api.call(who, "GET", path).headers["ETag"]
    archived = api.call(who, "POST", f"{path}/archive", headers={"If-Match": etag})
    assert archived.status_code == 200, archived.text
    res = api.call(who, "POST", f"{path}/versions", json={"upload_id": pending["upload_id"]})
    assert res.status_code == 409, res.text
    assert res.json()["code"] == "document_archived"
    body = {
        "filename": "circular.pdf",
        "content_type": PDF_CT,
        "size_bytes": 100,
        "purpose": "circular",
        "document_id": doc["id"],
    }
    refused = api.call(who, "POST", "/api/v1/documents/uploads", json=body)
    assert refused.status_code == 409, refused.text
    assert refused.json()["code"] == "document_archived"
    detail = api.call(who, "GET", path).json()
    assert [v["version_no"] for v in detail["versions"]] == [1]
    assert not [
        e
        for e in W.audit_events(admin_engine, world.a.tenant_id, "document.version_added")
        if e["resource_id"] == uuid.UUID(doc["id"])
    ]
    back = api.call(
        who, "POST", f"{path}/unarchive", headers={"If-Match": archived.headers["ETag"]}
    )
    assert back.status_code == 200, back.text
    res = api.call(who, "POST", f"{path}/versions", json={"upload_id": pending["upload_id"]})
    assert res.status_code == 202, res.text
    assert res.json()["current_version"]["version_no"] == 2


def test_SEC_016_reposting_after_registration_cannot_replace_the_checked_file(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    who = world.person("office_admin")
    store = S.memory_store()
    data = S.pdf()
    up = upload(api, who, data)
    doc = register(api, who, up["upload_id"]).json()
    final = f"t/{world.a.tenant_id}/docs/{doc['id']}/v1/original.pdf"
    assert store.objects[final].data == data
    # The presigned POST is still valid for a few minutes: re-post different bytes.
    evil = S.pdf(S.EICAR.decode())[: len(data)].ljust(len(data), b" ")
    assert store.browser_post(up["fields"], evil, PDF_CT) == 204
    assert store.objects[final].data == data, "the final key is not writable by the upload"
    assert scan(world.a.tenant_id, doc) == "ready"
    with tenant_session(world.a.tenant_id) as s:
        obj = service.document_object(s, uuid.UUID(doc["id"]))
        assert obj.object_key == final
    # Daily purge removes the re-created staging object once the intent is old enough.
    with admin_engine.begin() as c:
        c.execute(
            text(
                "UPDATE kb.upload_intents SET consumed_at = now() - interval '2 days' WHERE id = :i"
            ),
            {"i": up["upload_id"]},
        )
    service.purge_expired_uploads(world.a.tenant_id, store=store)
    assert up["fields"]["key"] not in store.objects


def test_SEC_016_file_swapped_during_checks_is_refused(world: Any, api: Any) -> None:
    who = world.person("office_admin")
    store = S.memory_store()
    data = S.pdf()
    up = upload(api, who, data)

    def swap(src: str) -> None:
        store.objects[src] = S.StoredObj(S.pdf("swapped")[: len(data)], PDF_CT)

    store.before_copy = swap
    try:
        res = register(api, who, up["upload_id"])
    finally:
        store.before_copy = None
    assert res.status_code == 409
    assert res.json()["code"] == "upload_changed"
    assert up["fields"]["key"] not in store.objects


# --- scanning -------------------------------------------------------------------------------


def test_FR_DOC_002_clean_scan_makes_the_version_ready(world: Any, api: Any) -> None:
    who = world.person("office_admin")
    doc = new_document(api, who)
    assert scan(world.a.tenant_id, doc) == "ready"
    assert scan(world.a.tenant_id, doc) == "ready", "idempotent"
    got = api.call(who, "GET", f"/api/v1/documents/{doc['id']}").json()
    assert got["current_version"]["status"] == "ready"


def test_FR_DOC_002_infected_file_is_quarantined_audited_and_never_served(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    who = world.person("office_admin")
    calls: list[tuple[uuid.UUID, uuid.UUID]] = []
    service.QUARANTINE_HOOKS.append(lambda s, d, v: calls.append((d, v)))
    try:
        doc = new_document(api, who, S.pdf(S.EICAR.decode()))
        assert scan(world.a.tenant_id, doc) == "quarantined"
    finally:
        service.QUARANTINE_HOOKS.pop()
    assert calls == [(uuid.UUID(doc["id"]), uuid.UUID(doc["current_version"]["id"]))]
    got = api.call(who, "GET", f"/api/v1/documents/{doc['id']}").json()
    assert got["current_version"]["status"] == "quarantined"
    assert got["current_version"]["error"] == "malware_detected"
    events = [
        e
        for e in W.audit_events(admin_engine, world.a.tenant_id, "document.quarantined")
        if str(e["resource_id"]) == doc["id"]
    ]
    assert len(events) == 1
    assert events[0]["actor_id"] is None
    assert events[0]["summary"]["engine"] == "dev-noop"
    res = api.call(who, "GET", f"/api/v1/documents/{doc['id']}/download-url")
    assert res.status_code == 409
    assert res.json()["code"] == "document_not_ready"
    with tenant_session(world.a.tenant_id) as s:
        assert service.evidence_exists(s, uuid.UUID(doc["id"])) is False


def test_FR_DOC_008_scanner_outage_retries_then_fails(world: Any, api: Any) -> None:
    who = world.person("office_admin")
    doc = new_document(api, who)

    class Down:
        engine = "down"

        def scan(self, chunks: Any) -> ScanResult:
            raise ScannerUnavailable("clamd_unreachable")

    ids = (world.a.tenant_id, uuid.UUID(doc["id"]), uuid.UUID(doc["current_version"]["id"]))
    with pytest.raises(ScannerUnavailable):
        service.scan_version(*ids, scanner=Down())
    got = api.call(who, "GET", f"/api/v1/documents/{doc['id']}").json()
    assert got["current_version"]["status"] == "scanning"
    service.mark_scan_failed(*ids, "scan_unavailable")
    got = api.call(who, "GET", f"/api/v1/documents/{doc['id']}").json()
    assert got["current_version"]["status"] == "failed"
    assert service.scan_version(*ids, scanner=DEV_SCANNER) == "failed", "never flips to ready"


# --- downloads ------------------------------------------------------------------------------


def test_FR_DOC_004_download_url_is_short_lived_attachment(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    who = world.person("office_staff")
    doc = new_document(api, world.person("office_admin"))
    not_ready = api.call(who, "GET", f"/api/v1/documents/{doc['id']}/download-url")
    assert not_ready.status_code == 409
    scan(world.a.tenant_id, doc)
    res = api.call(who, "GET", f"/api/v1/documents/{doc['id']}/download-url")
    assert res.status_code == 200, res.text
    assert res.headers["Cache-Control"] == "no-store"
    body = res.json()
    query = urllib.parse.parse_qs(urllib.parse.urlparse(body["url"]).query)
    assert int(query["X-Amz-Expires"][0]) <= 300
    assert query["response-content-disposition"][0].startswith("attachment;")
    assert query["response-content-type"][0] == PDF_CT
    assert body["filename"].endswith("-v1.pdf")
    assert "Synthetic" not in body["url"], "titles never reach URLs"
    got = S.memory_store().gets[-1]
    assert got["ttl"] <= 300
    assert got["key"].startswith(f"t/{world.a.tenant_id}/")
    missing = api.call(
        who, "GET", f"/api/v1/documents/{doc['id']}/download-url", params={"version": 7}
    )
    assert missing.status_code == 404
    events = W.audit_events(admin_engine, world.a.tenant_id, "document.download_url_issued")
    assert any(str(e["resource_id"]) == doc["id"] for e in events)


def test_FR_DOC_004_restricted_c3_files_need_sensitive_read(world: Any, api: Any) -> None:
    maker = world.person("office_staff")
    up = upload(api, maker, S.pdf(), purpose="evidence", filename="tc.pdf")
    doc = register(api, maker, up["upload_id"], title="Transfer certificate").json()
    scan(world.a.tenant_id, doc)
    path = f"/api/v1/documents/{doc['id']}/download-url"
    assert api.call(maker, "GET", path).status_code == 200, "the uploader may open it"
    res = api.call(world.person("accountant"), "GET", path)
    assert res.status_code == 403
    assert res.json()["code"] == "sensitive_document"
    assert api.call(world.person("principal"), "GET", path).status_code == 200


# --- ACL changes and delete -----------------------------------------------------------------


def test_FR_DOC_005_acl_change_needs_if_match_and_is_audited(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    owner = world.person("owner")
    doc = str(
        S.make_document(
            admin_engine, world.a.tenant_id, owner.user_id, acl=[("role", "accountant")]
        )
    )
    path = f"/api/v1/documents/{doc}/acl"
    body = {"acl": [{"principal_type": "section", "principal_ref": str(world.a.ids["section_9a"])}]}
    assert api.call(owner, "PUT", path, json=body).status_code == 400
    stale = api.call(owner, "PUT", path, json=body, headers={"If-Match": 'W/"9"'})
    assert stale.status_code == 412
    ok = api.call(
        owner,
        "PUT",
        path,
        json=body,
        headers={"If-Match": f'W/"{S.document_version(admin_engine, uuid.UUID(doc))}"'},
    )
    assert ok.status_code == 200, ok.text
    assert ok.headers["ETag"] == 'W/"2"'
    assert ok.json()["acl"] == [
        {"principal_type": "section", "principal_ref": str(world.a.ids["section_9a"])}
    ]
    assert (
        api.call(world.person("class_teacher"), "GET", f"/api/v1/documents/{doc}").status_code
        == 200
    )
    events = [
        e
        for e in W.audit_events(admin_engine, world.a.tenant_id, "document.acl_changed")
        if str(e["resource_id"]) == doc
    ]
    assert events[-1]["summary"]["added"] == 1
    assert events[-1]["summary"]["removed"] == 1


def test_FR_DOC_007_delete_removes_rows_audits_and_purges_objects(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    owner = world.person("owner")
    doc = new_document(api, owner)
    store = S.memory_store()
    key_prefix = f"t/{world.a.tenant_id}/docs/{doc['id']}/"
    assert any(k.startswith(key_prefix) for k in store.objects)
    res = api.call(owner, "DELETE", f"/api/v1/documents/{doc['id']}")
    assert res.status_code == 204
    assert api.call(owner, "GET", f"/api/v1/documents/{doc['id']}").status_code == 404
    with admin_engine.connect() as c:
        n: Any = c.execute(
            text("SELECT count(*) FROM kb.document_versions WHERE document_id = :d"),
            {"d": doc["id"]},
        ).scalar_one()
    assert n == 0
    events = W.audit_events(admin_engine, world.a.tenant_id, "document.deleted")
    assert any(str(e["resource_id"]) == doc["id"] for e in events)
    jobs = S.outbox_events(admin_engine, world.a.tenant_id, "document.deleted")
    payload = next(p for p in jobs if p["document_id"] == doc["id"])
    removed = service.purge_document_objects(
        world.a.tenant_id, uuid.UUID(payload["document_id"]), payload["batch_ids"], store=store
    )
    assert removed >= 1
    assert not any(k.startswith(key_prefix) for k in store.objects)


def test_FR_DOC_007_retention_guard_blocks_delete(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    owner = world.person("owner")
    doc = str(S.make_document(admin_engine, world.a.tenant_id, owner.user_id))
    service.DELETE_GUARDS.append(lambda s, d: "evidence_in_use" if str(d) == doc else None)
    try:
        res = api.call(owner, "DELETE", f"/api/v1/documents/{doc}")
    finally:
        service.DELETE_GUARDS.pop()
    assert res.status_code == 409
    assert res.json()["code"] == "evidence_in_use"
    assert api.call(owner, "GET", f"/api/v1/documents/{doc}").status_code == 200


def _audit_rows(admin: Engine, tenant_id: uuid.UUID, action: str, doc: uuid.UUID) -> list[Any]:
    with admin.connect() as c:
        return list(
            c.execute(
                text(
                    "SELECT actor_type, actor_id, summary FROM audit.events WHERE tenant_id = :t "
                    "AND action = :a AND resource_id = :d ORDER BY seq"
                ),
                {"t": tenant_id, "a": action, "d": doc},
            )
        )


def test_FR_DOC_007_system_retention_delete_removes_rows_audits_and_purges_objects(
    world: Any, admin_engine: Engine
) -> None:
    """A retention job deletes a document without a user (docs/05 §13): rows now, objects after
    commit through the outbox; audited with the system as actor and the reason."""
    owner = world.person("owner")
    doc = S.make_document(admin_engine, world.a.tenant_id, owner.user_id, acl=[("role", "teacher")])
    store = S.memory_store()
    prefix = f"t/{world.a.tenant_id}/docs/{doc}/"
    assert any(k.startswith(prefix) for k in store.objects)
    with tenant_session(world.a.tenant_id) as s:
        assert service.delete_for_retention(s, doc, reason="import_raw_file") is True
    with tenant_session(world.a.tenant_id) as s:
        assert service.delete_for_retention(s, doc, reason="import_raw_file") is False  # gone
    with admin_engine.connect() as c:
        left: Any = c.execute(
            text("SELECT count(*) FROM kb.document_versions WHERE document_id = :d"), {"d": doc}
        ).scalar_one()
    assert left == 0
    events = _audit_rows(admin_engine, world.a.tenant_id, "document.deleted", doc)
    assert [(e.actor_type, e.actor_id) for e in events] == [("system", None)]
    assert events[0].summary == {"purpose": "circular", "versions": 1, "reason": "import_raw_file"}
    payload = next(
        p
        for p in S.outbox_events(admin_engine, world.a.tenant_id, "document.deleted")
        if p["document_id"] == str(doc)
    )
    assert payload["batch_ids"] == []
    assert service.purge_document_objects(world.a.tenant_id, doc, [], store=store) >= 1
    assert not any(k.startswith(prefix) for k in store.objects)


def test_FR_DOC_007_system_retention_delete_keeps_guards_reasons_and_tenant(
    world: Any, admin_engine: Engine
) -> None:
    owner = world.person("owner")
    doc = S.make_document(admin_engine, world.a.tenant_id, owner.user_id)
    with (
        pytest.raises(ValueError, match="unknown retention reason"),
        tenant_session(world.a.tenant_id) as s,
    ):
        service.delete_for_retention(s, doc, reason="because I said so")
    service.DELETE_GUARDS.append(lambda s, d: "import_file_retained" if d == doc else None)
    try:
        with pytest.raises(Conflict) as err, tenant_session(world.a.tenant_id) as s:
            service.delete_for_retention(s, doc, reason="import_raw_file")
    finally:
        service.DELETE_GUARDS.pop()
    assert err.value.code == "import_file_retained"
    # Another school's job never reaches this school's document (RLS): nothing happens.
    with tenant_session(world.b.tenant_id) as s:
        assert service.delete_for_retention(s, doc, reason="import_raw_file") is False
    with admin_engine.connect() as c:
        kept: Any = c.execute(
            text("SELECT count(*) FROM kb.documents WHERE id = :d"), {"d": doc}
        ).scalar_one()
    assert kept == 1
    assert _audit_rows(admin_engine, world.a.tenant_id, "document.deleted", doc) == []


# --- service API for other modules and workers ----------------------------------------------


def test_service_api_for_changes_and_workers(world: Any, api: Any, admin_engine: Engine) -> None:
    who = world.person("office_admin")
    doc = new_document(api, who, S.pdf("service-api"))
    doc_id = uuid.UUID(doc["id"])
    b_doc = S.make_document(admin_engine, world.b.tenant_id, world.b.people["owner"].user_id)
    with tenant_session(world.a.tenant_id) as s:
        assert service.evidence_exists(s, doc_id) is True, "queued evidence may be attached"
        assert service.evidence_exists(s, b_doc) is False
        assert service.evidence_exists(s, uuid.uuid4()) is False
        with pytest.raises(Exception, match="malware scan"):
            service.document_object(s, doc_id)
    scan(world.a.tenant_id, doc)
    with tenant_session(world.a.tenant_id) as s:
        obj = service.document_object(s, doc_id)
        assert obj.version_no == 1
        assert obj.mime_type == PDF_CT
        assert service.read_document_object(s, obj) == S.pdf("service-api")
        key = service.store_page_image(s, doc_id, 1, 1, S.png())
        assert key.endswith(f"/docs/{doc_id}/v1/derived/pages/1.png")
        with pytest.raises(service.UnsupportedFileType):
            service.store_page_image(s, doc_id, 1, 2, S.pdf())
    with tenant_session(world.b.tenant_id) as s, pytest.raises(Exception, match="not found"):
        service.document_object(s, doc_id)


def test_purge_expired_uploads(world: Any, admin_engine: Engine) -> None:
    who = world.person("office_admin")
    intent = S.make_intent(admin_engine, world.a.tenant_id, who.user_id, S.pdf())
    with admin_engine.begin() as c:
        key: Any = c.execute(
            text(
                "UPDATE kb.upload_intents SET created_at = now() - interval '50 minutes', "
                "expires_at = now() - interval '1 minute' WHERE id = :i RETURNING object_key"
            ),
            {"i": intent},
        ).scalar_one()
    assert service.purge_expired_uploads(world.a.tenant_id, store=S.memory_store()) >= 1
    assert key not in S.memory_store().objects
    with admin_engine.connect() as c:
        n: Any = c.execute(
            text("SELECT count(*) FROM kb.upload_intents WHERE id = :i"), {"i": intent}
        ).scalar_one()
    assert n == 0


# --- idempotency and logging ----------------------------------------------------------------


def test_idempotency_key_replays_upload(world: Any, api: Any) -> None:
    who = world.person("office_admin")
    body = {"filename": "a.pdf", "content_type": PDF_CT, "size_bytes": 100, "purpose": "circular"}
    headers = {"Idempotency-Key": f"docs-{uuid.uuid4().hex}"}
    first = api.call(who, "POST", "/api/v1/documents/uploads", json=body, headers=headers)
    again = api.call(who, "POST", "/api/v1/documents/uploads", json=body, headers=headers)
    assert first.status_code == again.status_code == 201
    assert first.json()["upload_id"] == again.json()["upload_id"]
    assert again.headers.get("Idempotent-Replayed") == "true"


NAME = "Kommineni Venkata Lakshmi Synthetica"


def test_SEC_008_titles_and_file_names_never_reach_logs(
    world: Any, api: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    who = world.person("office_admin")
    capsys.readouterr()
    up = upload(api, who, S.png(), filename=f"{NAME}.pdf")  # PNG bytes: rejected later
    register(api, who, up["upload_id"], title=f"TC of {NAME}", issuer=NAME)
    doc = new_document(api, who, title=f"Birth certificate of {NAME}", issuer=NAME)
    api.call(who, "GET", f"/api/v1/documents/{doc['id']}")
    api.call(who, "GET", "/api/v1/documents")
    scan(world.a.tenant_id, doc)
    api.call(who, "GET", f"/api/v1/documents/{doc['id']}/download-url")
    out = capsys.readouterr()
    logs = out.out + out.err
    assert "http.request" in logs, "access log lines were captured"
    assert "documents.registered" in logs
    for secret in (NAME, "Venkata", "Synthetica"):
        assert secret not in logs, secret


# --- PRV-016: no primitive keeps a file that showed a full Aadhaar number --------------------


def test_PRV_016_documents_offer_no_withhold_that_keeps_the_file() -> None:
    """``withhold_version`` kept the stored object of a version that could show a full Aadhaar
    number; PRV-016 redacts or discards instead (tests below), so it is gone for good."""
    assert not hasattr(service, "withhold_version")
    assert not hasattr(service, "WITHHOLD_REASONS")
    assert "withhold_version" not in service.__all__


# --- PRV-016: redacted copies and discarded originals ---------------------------------------

PNG_CT = "image/png"


def _page_document(api: Any, who: Any, tenant_id: uuid.UUID) -> dict[str, Any]:
    """A scanned register page (PNG) uploaded through the API."""
    up = upload(
        api, who, S.png(), purpose="register_scan", filename="page.png", content_type=PNG_CT
    )
    res = register(api, who, up["upload_id"], title="Register page")
    assert res.status_code == 202, res.text
    doc: dict[str, Any] = res.json()
    assert scan(tenant_id, doc) == "ready"
    return doc


def _versions(admin: Engine, document_id: uuid.UUID) -> list[Any]:
    with admin.connect() as c:
        return list(
            c.execute(
                text(
                    "SELECT id, version_no, status, error, object_key, mime_type, size_bytes "
                    "FROM kb.document_versions WHERE document_id = :d ORDER BY version_no"
                ),
                {"d": document_id},
            )
        )


def _discard_payload(admin: Engine, tenant_id: uuid.UUID, document_id: uuid.UUID) -> Any:
    return next(
        p
        for p in S.outbox_events(admin, tenant_id, service.DISCARDED_EVENT)
        if p["document_id"] == str(document_id)
    )


def _run_discard(tenant_id: uuid.UUID, payload: Any) -> Any:
    from app.documents import tasks

    return tasks.discard_object.apply(
        kwargs={"tenant_id": str(tenant_id), "event_id": str(uuid.uuid4()), "payload": payload}
    ).get()


def test_PRV_016_redacted_copy_replaces_the_version_and_the_original_is_discarded(
    world: Any, api: Any, admin_engine: Engine, store: Any
) -> None:
    who = world.person("office_admin")
    tenant = world.a.tenant_id
    doc = _page_document(api, who, tenant)
    doc_id = uuid.UUID(doc["id"])
    original_key = f"t/{tenant}/docs/{doc_id}/v1/original.png"
    assert original_key in store.objects
    redacted = S.png("redacted-copy")
    with tenant_session(tenant) as s:
        new_no = service.replace_with_redacted(s, doc_id, 1, redacted, PNG_CT, regions=2)
        # The copy is usable evidence at once (queued counts, like any new version).
        assert service.evidence_exists(s, doc_id) is True
    assert new_no == 2

    v1, v2 = _versions(admin_engine, doc_id)
    assert (v1.status, v1.error) == ("quarantined", "aadhaar_redacted")
    assert (v2.status, v2.error, v2.mime_type) == ("queued", None, PNG_CT)
    assert v2.object_key == f"t/{tenant}/docs/{doc_id}/v2/original.png"
    assert store.objects[v2.object_key].data == redacted
    assert v2.size_bytes == len(redacted)
    detail = api.call(who, "GET", f"/api/v1/documents/{doc_id}").json()
    assert detail["current_version"]["version_no"] == 2

    # The copy is scanned like every other version (outbox), then it can be downloaded.
    scans = [
        p
        for p in S.outbox_events(admin_engine, tenant, service.SCAN_EVENT)
        if p["document_id"] == str(doc_id)
    ]
    assert {p["version_id"] for p in scans} == {str(v1.id), str(v2.id)}
    assert service.scan_version(tenant, doc_id, v2.id, scanner=DEV_SCANNER) == "ready"
    path = f"/api/v1/documents/{doc_id}/download-url"
    assert api.call(who, "GET", path, params={"version": 2}).status_code == 200
    assert api.call(who, "GET", path, params={"version": 1}).status_code == 409
    assert api.call(who, "GET", path).json()["version_no"] == 2

    # The original's bytes are deleted after commit by the outbox task (idempotent).
    payload = _discard_payload(admin_engine, tenant, doc_id)
    assert set(payload) == {"document_id", "version_id", "object_key"}
    assert original_key in store.objects, "never before the transaction committed"
    assert _run_discard(tenant, payload) is True
    assert original_key not in store.objects
    assert original_key in store.discarded, "tagged for the short lifecycle rule, not deleted"
    assert _run_discard(tenant, payload) is True
    assert v2.object_key in store.objects

    events = W.audit_events(admin_engine, tenant)
    mine = [e for e in events if str(e["resource_id"]) == str(doc_id)]
    by_action = {e["action"]: e["summary"] for e in mine}
    assert by_action["document.version_redacted"] == {
        "version_no": 1,
        "redacted_version_no": 2,
        "regions": 2,
        "mime_type": PNG_CT,
        "size_bytes": len(redacted),
    }
    assert by_action["document.version_discarded"] == {
        "version_no": 1,
        "reason": "aadhaar_redacted",
    }


def test_PRV_016_discarded_version_without_a_copy_is_gone_for_good(
    world: Any, api: Any, admin_engine: Engine, store: Any
) -> None:
    who = world.person("office_admin")
    tenant = world.a.tenant_id
    doc = _page_document(api, who, tenant)
    doc_id = uuid.UUID(doc["id"])
    key = f"t/{tenant}/docs/{doc_id}/v1/original.png"
    with tenant_session(tenant) as s:
        assert service.discard_version(s, doc_id, 1, "aadhaar_unredactable") is True
    with tenant_session(tenant) as s:
        assert service.discard_version(s, doc_id, 1, "aadhaar_unredactable") is False
        assert service.evidence_exists(s, doc_id) is False
    (v1,) = _versions(admin_engine, doc_id)
    assert (v1.status, v1.error) == ("quarantined", "aadhaar_unredactable")
    path = f"/api/v1/documents/{doc_id}/download-url"
    assert api.call(who, "GET", path, params={"version": 1}).status_code == 409
    assert _run_discard(tenant, _discard_payload(admin_engine, tenant, doc_id)) is True
    assert key not in store.objects
    events = W.audit_events(admin_engine, tenant, "document.version_discarded")
    mine = [e for e in events if str(e["resource_id"]) == str(doc_id)]
    assert [e["summary"] for e in mine] == [{"version_no": 1, "reason": "aadhaar_unredactable"}]


def test_PRV_016_daily_sweep_discards_what_the_task_missed(
    world: Any, api: Any, admin_engine: Engine, store: Any
) -> None:
    who = world.person("office_admin")
    tenant = world.a.tenant_id
    doc_id = uuid.UUID(_page_document(api, who, tenant)["id"])
    key = f"t/{tenant}/docs/{doc_id}/v1/original.png"
    with tenant_session(tenant) as s:
        service.discard_version(s, doc_id, 1, "aadhaar_unredactable")
    assert key in store.objects  # the outbox task never ran (e.g. retries exhausted)
    assert service.sweep_discarded_objects(tenant, store=store) >= 1
    assert key not in store.objects


def test_PRV_016_discard_task_never_deletes_a_usable_version(
    world: Any, api: Any, admin_engine: Engine, store: Any
) -> None:
    who = world.person("office_admin")
    tenant = world.a.tenant_id
    doc = _page_document(api, who, tenant)
    doc_id = uuid.UUID(doc["id"])
    key = f"t/{tenant}/docs/{doc_id}/v1/original.png"
    forged = {"document_id": str(doc_id), "version_id": doc["current_version"]["id"]}
    assert _run_discard(tenant, {**forged, "object_key": key}) is False
    other = f"t/{world.b.tenant_id}/docs/{doc_id}/v1/original.png"
    assert _run_discard(tenant, {**forged, "object_key": other}) is False
    assert key in store.objects


def test_PRV_016_replace_with_redacted_checks_its_input(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    who = world.person("office_admin")
    tenant = world.a.tenant_id
    doc_id = uuid.UUID(_page_document(api, who, tenant)["id"])
    with tenant_session(tenant) as s, pytest.raises(service.UnsupportedFileType):
        service.replace_with_redacted(s, doc_id, 1, S.pdf(), PNG_CT, regions=1)
    with tenant_session(tenant) as s, pytest.raises(service.UnsupportedFileType):
        service.replace_with_redacted(s, doc_id, 1, S.png(), PDF_CT, regions=1)
    with tenant_session(tenant) as s, pytest.raises(ValueError, match="reason"):
        service.discard_version(s, doc_id, 1, "because I said so")
    with tenant_session(world.b.tenant_id) as s, pytest.raises(Exception, match="not found"):
        service.replace_with_redacted(s, doc_id, 1, S.png(), PNG_CT, regions=1)
    with tenant_session(tenant) as s:
        service.discard_version(s, doc_id, 1, "aadhaar_unredactable")
    with tenant_session(tenant) as s, pytest.raises(Exception, match="no longer available"):
        service.replace_with_redacted(s, doc_id, 1, S.png(), PNG_CT, regions=1)
    assert [v.version_no for v in _versions(admin_engine, doc_id)] == [1]
