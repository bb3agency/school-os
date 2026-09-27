"""Import routes over HTTP (docs/09 Imports; US-401). Authorization for every role lives in
tests/security/test_authz_matrix.py; other-school ids in tests/security/test_bola.py."""

from __future__ import annotations

import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text

pytestmark = pytest.mark.db
S = sys.modules["sos_test_imports_support"]


def _doc(
    admin: Engine, school: Any, data: bytes, role: str = "office_admin", **kw: Any
) -> uuid.UUID:
    doc: uuid.UUID = S.import_document(
        admin, school.tenant_id, school.people[role].user_id, data, **kw
    )
    return doc


def test_US_401_full_flow_over_http(world: Any, api: Any, admin_engine: Engine) -> None:
    admin = world.person("office_admin")
    rows, numbers = S.class_list(2)
    rows.append(["", "Synthetica No Number", "", "14/03/2012", "M", "IX", "A"])
    doc = _doc(admin_engine, world.a, S.xlsx_bytes(rows))
    res = api.call(
        admin,
        "POST",
        "/api/v1/imports",
        json={"document_id": str(doc), "source": "admission_register", "kind": "spreadsheet"},
        headers={"Idempotency-Key": f"imp-{uuid.uuid4().hex}"},
    )
    assert res.status_code == 202, res.text
    body = res.json()
    assert res.headers["Location"] == f"/api/v1/imports/{body['id']}"
    assert body["status"] == "uploaded"
    assert body["job_id"]
    batch_id = uuid.UUID(body["id"])
    S.run_parse(world.a, batch_id)

    got = api.call(admin, "GET", f"/api/v1/imports/{batch_id}")
    assert got.status_code == 200
    detail = got.json()
    assert detail["status"] == "validated"
    assert detail["error_count"] == 1
    assert detail["can_commit"] is True
    assert [c["target"] for c in detail["columns"]][:2] == ["admission_no", "full_name"]
    etag = got.headers["ETag"]

    errors = api.call(admin, "GET", f"/api/v1/imports/{batch_id}/rows", params={"status": "error"})
    assert errors.status_code == 200
    page = errors.json()
    assert [r["row_no"] for r in page["data"]] == [4]
    assert page["data"][0]["errors"][0]["code"] == "missing"
    first = api.call(admin, "GET", f"/api/v1/imports/{batch_id}/rows", params={"limit": 2})
    assert [r["row_no"] for r in first.json()["data"]] == [2, 3]
    nxt = api.call(
        admin,
        "GET",
        f"/api/v1/imports/{batch_id}/rows",
        params={"limit": 2, "cursor": first.json()["next_cursor"]},
    )
    assert [r["row_no"] for r in nxt.json()["data"]] == [4]
    assert nxt.json()["next_cursor"] is None

    refused = api.call(admin, "POST", f"/api/v1/imports/{batch_id}/commit", json={})
    assert refused.status_code == 409
    assert refused.json()["code"] == "import_has_errors"
    ok = api.call(
        admin, "POST", f"/api/v1/imports/{batch_id}/commit", json={"skip_error_rows": True}
    )
    assert ok.status_code == 202, ok.text
    assert ok.json()["status"] == "committing"
    assert S.run_commit(world.a, batch_id, skip_error_rows=True) == "committed"
    done = api.call(admin, "GET", f"/api/v1/imports/{batch_id}").json()
    assert done["status"] == "committed"
    assert done["can_revert"] is True
    assert done["stats"]["created"] == 2
    listing = api.call(admin, "GET", "/api/v1/imports", params={"limit": 200}).json()
    assert str(batch_id) in {b["id"] for b in listing["data"]}

    reverted = api.call(admin, "POST", f"/api/v1/imports/{batch_id}/revert")
    assert reverted.status_code == 200, reverted.text
    assert reverted.json()["status"] == "reverted"
    assert S.student_by_adm(admin_engine, world.a.tenant_id, numbers[0]) is None
    stale = api.call(
        admin,
        "PUT",
        f"/api/v1/imports/{batch_id}/mapping",
        json={"columns": []},
        headers={"If-Match": etag},
    )
    assert stale.status_code == 412


def test_FR_IMP_002_mapping_and_templates_over_http(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    staff = world.person("office_staff")
    rows = [
        ["Ref", "Pupil", "Born", "Std", "Div", f"Layout {uuid.uuid4().hex[:8]}"],
        [S.adm(), "Synthetica Http", "14/03/2012", "9", "A"],
    ]
    doc = _doc(admin_engine, world.a, S.xlsx_bytes(rows), role="office_staff")
    res = api.call(
        staff,
        "POST",
        "/api/v1/imports",
        json={"document_id": str(doc), "source": "admission_register"},
    )
    batch_id = uuid.UUID(res.json()["id"])
    S.run_parse(world.a, batch_id, role="office_staff")
    got = api.call(staff, "GET", f"/api/v1/imports/{batch_id}")
    assert got.json()["status"] == "parsed"
    no_etag = api.call(staff, "PUT", f"/api/v1/imports/{batch_id}/mapping", json={"columns": []})
    assert no_etag.status_code == 400
    bad = api.call(
        staff,
        "PUT",
        f"/api/v1/imports/{batch_id}/mapping",
        json={"columns": [{"index": 0, "target": "not_a_field"}]},
        headers={"If-Match": got.headers["ETag"]},
    )
    assert bad.status_code == 422
    assert bad.json()["errors"][0] == {
        "field": "columns.0",
        "code": "unknown_target",
        "message_key": "errors.unknown_target",
    }
    columns = [
        {"index": 0, "target": "admission_no"},
        {"index": 1, "target": "full_name"},
        {"index": 2, "target": "dob"},
        {"index": 3, "target": "class"},
        {"index": 4, "target": "section"},
    ]
    put = api.call(
        staff,
        "PUT",
        f"/api/v1/imports/{batch_id}/mapping",
        json={"columns": columns},
        headers={"If-Match": got.headers["ETag"]},
    )
    assert put.status_code == 200, put.text
    assert put.headers["ETag"] != got.headers["ETag"]
    val = api.call(staff, "POST", f"/api/v1/imports/{batch_id}/validate")
    assert val.status_code == 202
    assert val.json()["status"] == "validating"
    assert S.run_validate(world.a, batch_id, role="office_staff") == "validated"
    name = f"Synthetic HTTP layout {uuid.uuid4().hex[:6]}"
    tpl = api.call(
        staff, "POST", "/api/v1/import-templates", json={"name": name, "import_id": str(batch_id)}
    )
    assert tpl.status_code == 201, tpl.text
    assert tpl.json()["mapping"]["ref"] == "admission_no"
    dup = api.call(
        staff, "POST", "/api/v1/import-templates", json={"name": name, "import_id": str(batch_id)}
    )
    assert dup.status_code == 409
    listed = api.call(staff, "GET", "/api/v1/import-templates").json()
    assert name in {t["name"] for t in listed}
    # Office staff may import but not commit (docs/07 §6.2).
    denied = api.call(staff, "POST", f"/api/v1/imports/{batch_id}/commit", json={})
    assert denied.status_code == 403


def test_FR_IMP_001_create_import_checks_the_document(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    admin = world.person("office_admin")
    data = S.xlsx_bytes(S.class_list(1)[0])

    def post(doc: uuid.UUID) -> Any:
        return api.call(
            admin, "POST", "/api/v1/imports", json={"document_id": str(doc), "source": "udise_plus"}
        )

    assert post(uuid.uuid4()).status_code == 404
    other_school = S.import_document(
        admin_engine, world.b.tenant_id, world.b.people["owner"].user_id, data
    )
    assert post(other_school).status_code == 404
    queued = _doc(admin_engine, world.a, data, status="queued")
    res = post(queued)
    assert res.status_code == 409
    assert res.json()["code"] == "document_not_ready"
    circular = S.D.make_document(admin_engine, world.a.tenant_id, world.a.people["owner"].user_id)
    res = post(circular)
    assert res.status_code == 422
    assert res.json()["errors"][0]["code"] == "not_an_import_file"
    big = _doc(admin_engine, world.a, data)
    with admin_engine.begin() as c:
        c.execute(
            text("UPDATE kb.document_versions SET size_bytes = :n WHERE document_id = :d"),
            {"n": 10 * 1024 * 1024 + 1, "d": big},
        )
    assert post(big).status_code == 413
    ok = _doc(admin_engine, world.a, data)
    assert post(ok).status_code == 202
    again = post(ok)
    assert again.status_code == 409
    assert again.json()["code"] == "import_exists"
    bad_source = api.call(
        admin, "POST", "/api/v1/imports", json={"document_id": str(ok), "source": "gossip"}
    )
    assert bad_source.status_code == 422


def test_FR_IMP_001_idempotent_create(world: Any, api: Any, admin_engine: Engine) -> None:
    admin = world.person("office_admin")
    doc = _doc(admin_engine, world.a, S.xlsx_bytes(S.class_list(1)[0]))
    key = {"Idempotency-Key": f"imp-{uuid.uuid4().hex}"}
    body = {"document_id": str(doc), "source": "admission_register"}
    first = api.call(admin, "POST", "/api/v1/imports", json=body, headers=key)
    second = api.call(admin, "POST", "/api/v1/imports", json=body, headers=key)
    assert first.status_code == second.status_code == 202
    assert first.json()["id"] == second.json()["id"]
    assert second.headers.get("Idempotent-Replayed") == "true"


def test_SEC_001_import_lists_never_show_other_school(world: Any, api: Any) -> None:
    b_batch = S.start(None, world.b, S.xlsx_bytes(S.class_list(1)[0]), role="owner")
    principal = world.person("principal")
    listing = api.call(principal, "GET", "/api/v1/imports", params={"limit": 200})
    assert listing.status_code == 200
    assert str(b_batch) not in {b["id"] for b in listing.json()["data"]}
    assert api.call(principal, "GET", "/api/v1/import-templates").status_code == 200
    body = {"name": f"Cross school {uuid.uuid4().hex[:6]}", "import_id": str(b_batch)}
    assert api.call(principal, "POST", "/api/v1/import-templates", json=body).status_code == 404
