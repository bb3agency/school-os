"""Verification queue over the API (US-402 AC1..AC4, FR-IMP-020, FR-IMP-021, FR-IMP-023).

Batch creation checks, progress per page, low-confidence highlighting, the page image beside
each row, confirm (creates or links a student; source admission_register; page as evidence)
and reject. Authz matrix, BOLA and cross-tenant cases live in tests/security.
"""

from __future__ import annotations

import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.extraction import service

pytestmark = pytest.mark.db
X = sys.modules["sos_test_extraction_support"]


def _values(admin: Engine, student_id: str | uuid.UUID) -> dict[str, dict[str, Any]]:
    with admin.connect() as c:
        rows = c.execute(
            text(
                "SELECT id, attribute_key, source, value_text, verification_status, verified_by, "
                "evidence_document_id FROM sis.attribute_values "
                "WHERE student_id = :s AND superseded_by IS NULL"
            ),
            {"s": student_id},
        )
        return {r.attribute_key: dict(r._mapping) for r in rows}


def _item(admin: Engine, school: Any, **row: Any) -> tuple[uuid.UUID, dict[str, Any]]:
    item_id = X.pending_item(admin, school, **row)
    return item_id, X.row_of(admin, "sis.extraction_items", item_id)


# --- batches --------------------------------------------------------------------------------


def test_FR_IMP_020_create_batch_and_follow_progress_per_page(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    a = world.a
    who = a.people["office_staff"]
    owner = a.people["owner"].user_id
    docs = [
        X.register_scan(
            admin_engine, a.tenant_id, owner, X.page_png([X.register_row(f"Synthetica P{i}")])
        )
        for i in range(2)
    ]
    res = api.call(
        who,
        "POST",
        "/api/v1/extraction-batches",
        json={"document_ids": [str(d) for d in docs]},
        headers={"Idempotency-Key": f"batch-{uuid.uuid4().hex}"},
    )
    assert res.status_code == 202, res.text
    body = res.json()
    assert res.headers["Location"] == f"/api/v1/extraction-batches/{body['id']}"
    assert (body["status"], body["page_count"], body["source"]) == (
        "queued",
        2,
        "admission_register",
    )
    service.process_batch(a.tenant_id, uuid.UUID(body["id"]))
    detail = api.call(who, "GET", f"/api/v1/extraction-batches/{body['id']}").json()
    assert detail["status"] == "review"
    assert [p["status"] for p in detail["pages"]] == ["done", "done"]
    assert [p["document_id"] for p in detail["pages"]] == [str(d) for d in docs]
    assert all(p["row_count"] == 1 for p in detail["pages"])
    listing = api.call(who, "GET", "/api/v1/extraction-batches", params={"limit": 200}).json()
    assert body["id"] in {b["id"] for b in listing["data"]}


def test_FR_IMP_020_batch_input_is_checked(world: Any, api: Any, admin_engine: Engine) -> None:
    a = world.a
    who = a.people["office_admin"]
    owner = a.people["owner"].user_id
    pdf = X.register_scan(admin_engine, a.tenant_id, owner, X.D.pdf(), ext="pdf")
    circular = X.register_scan(admin_engine, a.tenant_id, owner, X.page_png([]), purpose="circular")
    scanning = X.register_scan(admin_engine, a.tenant_id, owner, X.page_png([]), status="scanning")
    other_school = X.register_scan(
        admin_engine, world.b.tenant_id, world.b.people["owner"].user_id, X.page_png([])
    )
    cases = [
        (pdf, "pdf_not_supported"),
        (circular, "not_register_scan"),
        (scanning, "document_not_ready"),
        (other_school, "not_found"),
        (uuid.uuid4(), "not_found"),
    ]
    for doc, code in cases:
        res = api.call(who, "POST", "/api/v1/extraction-batches", json={"document_ids": [str(doc)]})
        assert res.status_code == 422, (code, res.text)
        assert res.json()["errors"][0]["code"] == code
        assert res.json()["errors"][0]["field"] == "document_ids.0"
    pdf_res = api.call(who, "POST", "/api/v1/extraction-batches", json={"document_ids": [str(pdf)]})
    assert "JPG or PNG" in pdf_res.json()["detail"]
    ok = X.register_scan(admin_engine, a.tenant_id, owner, X.page_png([]))
    dup = api.call(who, "POST", "/api/v1/extraction-batches", json={"document_ids": [str(ok)] * 2})
    assert dup.status_code == 422
    first = api.call(who, "POST", "/api/v1/extraction-batches", json={"document_ids": [str(ok)]})
    assert first.status_code == 202
    again = api.call(who, "POST", "/api/v1/extraction-batches", json={"document_ids": [str(ok)]})
    assert again.status_code == 422
    assert again.json()["errors"][0]["code"] == "already_extracted"


def test_class_teacher_cannot_reach_documents_outside_scope(world: Any, api: Any) -> None:
    """Scoped roles never hold import.run (docs/07 §6.2): the route answers 403."""
    res = api.call(
        world.a.people["class_teacher"],
        "POST",
        "/api/v1/extraction-batches",
        json={"document_ids": [str(uuid.uuid4())]},
    )
    assert res.status_code == 403


# --- queue ----------------------------------------------------------------------------------


def test_US_402_AC4_low_confidence_fields_are_highlighted(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    a = world.a
    row = X.register_row("Synthetica Unclear")
    row["dob"] = X.cell("2012-06-15", 0.31)
    batch_id, items = X.processed_batch(
        admin_engine, a, [X.page_png([row, X.register_row("Synthetica Clear")])]
    )
    who = a.people["office_staff"]
    res = api.call(
        who,
        "GET",
        "/api/v1/extraction-items",
        params={"batch_id": str(batch_id), "status": "pending_review", "limit": 1},
    )
    assert res.status_code == 200, res.text
    page1 = res.json()
    assert len(page1["data"]) == 1
    first = page1["data"][0]
    assert first["low_confidence"] is True
    assert first["low_confidence_fields"] == ["dob"]
    assert first["fields"]["dob"]["confidence"] == 0.31
    assert first["fields"]["full_name"]["low_confidence"] is False
    assert page1["next_cursor"]
    page2 = api.call(
        who,
        "GET",
        "/api/v1/extraction-items",
        params={"batch_id": str(batch_id), "limit": 1, "cursor": page1["next_cursor"]},
    ).json()
    assert [i["id"] for i in page2["data"]] == [str(items[1])]
    assert page2["data"][0]["low_confidence"] is False
    assert page2["next_cursor"] is None
    bad = api.call(who, "GET", "/api/v1/extraction-items", params={"cursor": "!!"})
    assert bad.status_code == 422


def test_US_402_AC1_item_shows_the_page_image_and_possible_matches(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    a = world.a
    existing = X.SW.create(a, name="Synthetica Existing", section_key=None, admission_no="EXM-771")
    item_id, _ = _item(admin_engine, a, name="Synthetica Existing", admission_no="EXM-771")
    res = api.call(a.people["office_admin"], "GET", f"/api/v1/extraction-items/{item_id}")
    assert res.status_code == 200, res.text
    assert res.headers["Cache-Control"] == "no-store"
    body = res.json()
    assert body["image"]["mime_type"] == "image/png"
    assert body["image"]["url"].startswith("https://s3.synthetic.test/")
    assert body["image_unavailable"] is None
    assert [m["id"] for m in body["possible_matches"]] == [str(existing)]


def test_US_402_AC1_image_needs_document_read_on_the_page(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    """A page shared only with a membership the reviewer is not: the row is shown, the image
    is not (the document ACL still applies)."""
    a = world.a
    owner = a.people["owner"].user_id
    doc = X.register_scan(
        admin_engine,
        a.tenant_id,
        owner,
        X.page_png([X.register_row("Synthetica Hidden Page")]),
        acl=[("membership", str(a.people["target"].membership_id))],
    )
    batch_id = X.start_batch(a, [doc])
    service.process_batch(a.tenant_id, batch_id)
    item_id = X.item_ids(admin_engine, batch_id)[0]
    staff = api.call(a.people["office_staff"], "GET", f"/api/v1/extraction-items/{item_id}")
    assert staff.status_code == 200
    assert staff.json()["image"] is None
    assert staff.json()["image_unavailable"] == "not_visible"
    # document.manage_acl holders (office admin) see every document.
    admin = api.call(a.people["office_admin"], "GET", f"/api/v1/extraction-items/{item_id}")
    assert admin.json()["image"] is not None


# --- confirm / reject -----------------------------------------------------------------------


def test_US_402_AC2_FR_IMP_023_confirm_creates_the_student_with_evidence(
    world: Any, api: Any, admin_engine: Engine, extraction_templates: None
) -> None:
    a = world.a
    who = a.people["office_admin"]
    item_id, item = _item(
        admin_engine, a, name="SYNTHETICA REGISTER KUMARI", admission_no="RG-1001"
    )
    body = {
        "fields": {
            "admission_no": "RG-1001",
            "full_name": "Synthetica Register Kumari",  # the reviewer fixes the spelling
            "dob": "2012-06-15",
            "gender": "female",
            "father_name": "Synthetica Father Rao",
            "mother_name": None,
            "mother_tongue": "Telugu",
        },
        "section_id": str(a.ids["section_9a"]),
    }
    res = api.call(who, "POST", f"/api/v1/extraction-items/{item_id}/confirm", json=body)
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["status"] == "confirmed"
    assert out["created_student"] is True
    assert out["reviewed_by"] == str(who.user_id)
    assert out["corrected_fields"] == ["full_name"]
    student_id = out["student_id"]
    values = _values(admin_engine, student_id)
    assert set(values) == {
        "admission_no",
        "full_name",
        "dob",
        "gender",
        "father_name",
        "mother_tongue",
    }
    assert {str(v["id"]) for v in values.values()} == set(out["value_ids"])
    for key, v in values.items():
        assert v["source"] == "admission_register"
        assert v["evidence_document_id"] == item["document_id"], key
    # Identity values: first register value, unverified until a change request (BR-01);
    # other values: verified by the person who read them on the page.
    assert values["full_name"]["verification_status"] == "unverified"
    assert values["full_name"]["value_text"] == "Synthetica Register Kumari"
    assert values["mother_tongue"]["verification_status"] == "verified"
    assert values["mother_tongue"]["verified_by"] == who.user_id

    profile = api.call(who, "GET", f"/api/v1/students/{student_id}").json()
    assert profile["enrollment"]["section_id"] == str(a.ids["section_9a"])
    assert profile["canonical"]["full_name"]["provisional"] is True

    events = X.D.outbox_events(admin_engine, a.tenant_id, service.CONFIRMED_EVENT)
    assert {
        "batch_id": str(item["batch_id"]),
        "item_id": str(item_id),
        "student_id": student_id,
    } in events
    audit = X.W.audit_events(admin_engine, a.tenant_id, "extraction.item.confirmed")
    mine = [e for e in audit if e["resource_id"] == item_id]
    assert mine
    assert mine[0]["summary"]["corrected_fields"] == ["full_name"]
    assert mine[0]["summary"]["created_student"] is True
    batch = X.row_of(admin_engine, "sis.extraction_batches", item["batch_id"])
    assert (batch["items_pending"], batch["items_confirmed"], batch["status"]) == (
        0,
        1,
        "completed",
    )

    again = api.call(who, "POST", f"/api/v1/extraction-items/{item_id}/confirm", json=body)
    assert again.status_code == 409
    assert again.json()["code"] == "item_already_reviewed"


def test_US_402_AC2_confirm_adds_to_an_existing_student(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    a = world.a
    who = a.people["principal"]
    student_id = X.SW.create(a, name="Synthetica Linked Student", section_key="section_9c")
    item_id, item = _item(admin_engine, a, name="Synthetica Linked Student")
    body = {
        "student_id": str(student_id),
        "fields": {"admission_no": "LNK-2002", "nationality": "Indian"},
    }
    res = api.call(who, "POST", f"/api/v1/extraction-items/{item_id}/confirm", json=body)
    assert res.status_code == 200, res.text
    assert res.json()["student_id"] == str(student_id)
    assert res.json()["created_student"] is False
    values = _values(admin_engine, student_id)
    assert values["admission_no"]["evidence_document_id"] == item["document_id"]
    assert values["admission_no"]["verification_status"] == "unverified"
    assert values["nationality"]["verification_status"] == "verified"

    # A different register identity value is a change request, never an overwrite (BR-01).
    other_id, _ = _item(admin_engine, a, name="Synthetica Linked Student")
    clash = api.call(
        who,
        "POST",
        f"/api/v1/extraction-items/{other_id}/confirm",
        json={"student_id": str(student_id), "fields": {"full_name": "Synthetica Other Name"}},
    )
    assert clash.status_code == 403
    assert clash.json()["code"] == "identity_change_required"
    assert X.row_of(admin_engine, "sis.extraction_items", other_id)["status"] == "pending_review"


def test_confirm_input_rules(world: Any, api: Any, admin_engine: Engine) -> None:
    a = world.a
    who = a.people["exam_coordinator"]
    item_id, _ = _item(admin_engine, a)
    url = f"/api/v1/extraction-items/{item_id}/confirm"
    cases = [
        ({"fields": {"caste": "x"}}, "fields.caste", "unknown_field"),
        ({"fields": {"full_name": "XXXX XXXX 4821"}}, "fields.full_name", "masked_value"),
        ({"fields": {"full_name": None}}, "fields", "no_values"),
        ({"fields": {"dob": "2012-01-01"}}, "fields.full_name", "required"),
        ({"fields": {"full_name": "Synthetica Ok", "dob": "15/06/2012"}}, "fields.dob", None),
    ]
    for body, field, code in cases:
        res = api.call(who, "POST", url, json=body)
        assert res.status_code == 422, (body, res.text)
        err = res.json()["errors"][0]
        assert err["field"] == field, res.text
        if code:
            assert err["code"] == code
    number = X.valid_aadhaar_like(77)
    res = api.call(who, "POST", url, json={"fields": {"full_name": f"Synthetica {number}"}})
    assert res.status_code == 422
    assert number not in res.text
    student = api.call(
        who,
        "POST",
        url,
        json={"student_id": str(uuid.uuid4()), "fields": {"nationality": "Indian"}},
    )
    assert student.status_code == 404
    assert X.row_of(admin_engine, "sis.extraction_items", item_id)["status"] == "pending_review"


def test_reject_records_nothing(world: Any, api: Any, admin_engine: Engine) -> None:
    a = world.a
    item_id, item = _item(admin_engine, a)
    who = a.people["office_admin"]
    res = api.call(
        who, "POST", f"/api/v1/extraction-items/{item_id}/reject", json={"reason": "duplicate"}
    )
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "rejected"
    assert res.json()["reject_reason"] == "duplicate"
    assert res.json()["student_id"] is None
    batch = X.row_of(admin_engine, "sis.extraction_batches", item["batch_id"])
    assert (batch["items_rejected"], batch["status"]) == (1, "completed")
    again = api.call(
        who,
        "POST",
        f"/api/v1/extraction-items/{item_id}/confirm",
        json={"fields": {"full_name": "S"}},
    )
    assert again.status_code == 409
    assert X.D.outbox_events(admin_engine, a.tenant_id, service.CONFIRMED_EVENT) == [] or all(
        e["item_id"] != str(item_id)
        for e in X.D.outbox_events(admin_engine, a.tenant_id, service.CONFIRMED_EVENT)
    )
    assert any(
        e["resource_id"] == item_id
        for e in X.W.audit_events(admin_engine, a.tenant_id, "extraction.item.rejected")
    )


def test_evidence_document_cannot_be_deleted_while_rows_point_at_it(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    a = world.a
    _, item = _item(admin_engine, a)
    res = api.call(a.people["office_admin"], "DELETE", f"/api/v1/documents/{item['document_id']}")
    assert res.status_code == 409
    assert res.json()["code"] == "document_in_use"
