"""CBSE Appendix IX disclosure routes (R3-1C; ADR-0051; US-2901..US-2904; FR-DISC-001..012;
SEC-050, PRV-037). Real database (RLS), synthetic data only."""

from __future__ import annotations

import sys
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from app.core import ratelimit as rl
from app.core.db import tenant_session
from app.disclosure import service
from app.documents import service as documents

pytestmark = pytest.mark.db
S = sys.modules["sos_test_disclosure_support"]
BASE = S.BASE


def _reset_budgets() -> None:
    rl.get_rate_limiter().store.reset()  # type: ignore[attr-defined]


def _public(client: TestClient, key: str, suffix: str = "") -> Any:
    _reset_budgets()
    return S.P.public_call(client, "GET", f"/api/v1/public/disclosure/{key}{suffix}")


def _published(api: Any, school: Any, **draft: Any) -> dict[str, Any]:
    owner = school.people["owner"]
    saved = S.save_draft(api, owner, **draft)
    assert saved.status_code == 200, saved.text
    res = S.publish(api, owner)
    assert res.status_code == 201, res.text
    body: dict[str, Any] = res.json()
    return body


# --- authorization -------------------------------------------------------------------------------

EDITORS = {"owner", "principal", "office_admin"}
PUBLISHERS = {"owner", "principal"}


def test_FR_DISC_001_authz_matrix_for_edit_and_publish(api: Any, school: Any) -> None:
    for role in S.ROLES:
        who = school.people[role]
        res = api.call(who, "GET", f"{BASE}/draft")
        assert res.status_code == (200 if role in EDITORS else 403), (role, res.text)
        res = api.call(who, "POST", f"{BASE}/publish", json={"confirm_no_student_data": True})
        expected = 422 if role in PUBLISHERS else 403  # draft still incomplete
        assert res.status_code == expected, (role, res.text)


def test_FR_DISC_001_publish_needs_step_up(api: Any, school: Any) -> None:
    owner = school.people["owner"]
    S.save_draft(api, owner)
    res = api.call(
        owner,
        "POST",
        f"{BASE}/publish",
        json={"confirm_no_student_data": True},
        auth_age_s=3600,
    )
    assert res.status_code == 428, res.text
    assert res.json()["code"] == "step_up_required"


def test_FR_DISC_001_schools_without_cbse_see_module_hidden(
    api: Any, school: Any, admin_engine: Engine
) -> None:
    S.set_boards(admin_engine, school.tenant_id, ["BSEAP"])
    res = api.call(school.people["owner"], "GET", f"{BASE}/format")
    assert res.status_code == 404
    assert res.json()["code"] == "module_hidden"


def test_FR_DISC_001_format_is_listed_with_verified_false(api: Any, school: Any) -> None:
    res = api.call(school.people["principal"], "GET", f"{BASE}/format")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["key"] == "cbse-appendix-ix"
    assert body["verified"] is False
    items = {i["key"]: i for s in body["sections"] for i in s["items"]}
    assert items["fire_safety"]["takes_documents"] is True
    assert items["fee_structure"]["fill"] == "derived"
    assert "source" not in items["fee_structure"]


# --- draft ---------------------------------------------------------------------------------------


def test_US_2901_AC1_derived_values_come_from_schoolos(api: Any, school: Any) -> None:
    res = api.call(school.people["office_admin"], "GET", f"{BASE}/draft")
    assert res.status_code == 200, res.text
    assert res.headers["ETag"] == 'W/"0"'
    body = res.json()
    derived = body["from_schoolos"]
    assert derived["school_name"]["value"] == "Synthetic Model School"
    assert derived["sections_count"]["value"] == "3"
    assert derived["teachers_total"]["value"] == "2"  # one teacher, one class teacher
    assert derived["teacher_section_ratio"]["value"] == "0.67"
    assert derived["principal_name"]["value"] == school.people["principal"].display_name
    assert derived["academic_session"]["value"].startswith("2026-27 (01 Jun 2026")
    roles = dict(map(tuple, derived["staff_by_role"]["table"]["rows"]))
    assert roles["Principal"] == "1"
    assert derived["fee_structure"] == {"value": None, "table": None}  # fees are off
    assert body["missing"] == [
        "affiliation_no",
        "school_code",
        "principal_qualification",
        "school_email",
        "contact_phone",
    ]


def test_FR_DISC_004_draft_needs_if_match_and_refuses_stale_versions(api: Any, school: Any) -> None:
    who = school.people["office_admin"]
    res = api.call(who, "PUT", f"{BASE}/draft", json={"inputs": {}, "documents": {}})
    assert res.status_code == 400
    assert res.json()["code"] == "if_match_required"
    first = S.save_draft(api, who)
    assert first.status_code == 200, first.text
    assert first.headers["ETag"] == 'W/"1"'
    stale = api.call(
        who,
        "PUT",
        f"{BASE}/draft",
        json={"inputs": dict(S.REQUIRED), "documents": {}},
        headers={"If-Match": 'W/"0"'},
    )
    assert stale.status_code == 412


def test_FR_DISC_004_inputs_refuse_aadhaar_and_phone_numbers(api: Any, school: Any) -> None:
    res = S.save_draft(
        api,
        school.people["office_admin"],
        inputs={
            "affiliation_no": "2345 6789 0123",
            "special_educator": "Call 9876543210",
            "contact_phone": "9876543210",
        },
    )
    assert res.status_code == 422, res.text
    codes = {e["field"]: e["code"] for e in res.json()["errors"]}
    assert codes == {
        "inputs.affiliation_no": "aadhaar_not_allowed",
        "inputs.special_educator": "phone_not_allowed",
    }
    assert "2345 6789 0123" not in res.text


def test_FR_DISC_004_documents_must_be_visible_and_publishable(
    api: Any, school: Any, admin_engine: Engine
) -> None:
    ok = S.document(admin_engine, school)
    c3 = S.document(admin_engine, school, sensitivity="C3")
    unscanned = S.document(admin_engine, school, status="queued")
    other = S.W.School(S.W.provision_school())
    other_owner = S.W.add_member(admin_engine, other.tenant_id, ["owner"])
    other_school = S.D.make_document(admin_engine, other.tenant_id, other_owner.user_id)
    res = S.save_draft(
        api,
        school.people["office_admin"],
        documents={
            "fire_safety": [str(ok)],
            "building_safety": [str(c3)],
            "noc": [str(unscanned)],
            "pta_list": [str(other_school)],
        },
    )
    assert res.status_code == 422, res.text
    codes = {e["field"]: e["code"] for e in res.json()["errors"]}
    assert codes == {
        "documents.building_safety": "document_not_publishable",
        "documents.noc": "document_not_publishable",
        "documents.pta_list": "document_not_found",
    }


# --- publish -------------------------------------------------------------------------------------


def test_FR_DISC_005_publish_needs_the_confirmation_and_required_items(
    api: Any, school: Any
) -> None:
    owner = school.people["owner"]
    res = api.call(owner, "POST", f"{BASE}/publish", json={"confirm_no_student_data": False})
    assert res.status_code == 422
    assert res.json()["code"] == "confirmation_required"
    res = S.publish(api, owner)
    assert res.status_code == 422
    assert res.json()["code"] == "incomplete"
    S.save_draft(api, owner, inputs={"affiliation_no": "1130001"})
    res = S.publish(api, owner)
    assert res.status_code == 422
    fields = [e["field"] for e in res.json()["errors"]]
    assert "inputs.school_code" in fields
    assert "inputs.affiliation_no" not in fields


def test_US_2902_AC1_publishing_freezes_the_page(
    api: Any, school: Any, admin_engine: Engine, public_client: TestClient
) -> None:
    doc = S.document(admin_engine, school)
    snap = _published(api, school, documents={"fire_safety": [str(doc)]})
    assert snap["live"] is True
    assert snap["documents"] == 1
    assert snap["pdf_status"] == "queued"
    key = S.link_key(api, school.people["principal"])
    assert len(key) == 22
    page = _public(public_client, key)
    assert page.status_code == 200, page.text
    items = {i["key"]: i for s in page.json()["sections"] for i in s["items"]}
    assert items["teachers_total"]["value"] == "2"
    # Records change; the published page does not until someone publishes again.
    S.W.add_member(admin_engine, school.tenant_id, ["teacher"])
    again = _public(public_client, key)
    items = {i["key"]: i for s in again.json()["sections"] for i in s["items"]}
    assert items["teachers_total"]["value"] == "2"
    draft = api.call(school.people["owner"], "GET", f"{BASE}/draft").json()
    assert draft["from_schoolos"]["teachers_total"]["value"] == "3"
    # Frozen content and the published versions are recorded.
    row = S.rows(
        admin_engine,
        "SELECT content_sha256, published_by FROM ops.disclosure_snapshots WHERE id = :i",
        i=snap["id"],
    )[0]
    assert len(row["content_sha256"]) == 32
    assert row["published_by"] == school.people["owner"].user_id
    versions = S.rows(
        admin_engine,
        "SELECT n, item_key, document_id FROM ops.disclosure_snapshot_documents "
        "WHERE snapshot_id = :i",
        i=snap["id"],
    )
    assert versions == [{"n": 1, "item_key": "fire_safety", "document_id": doc}]
    events = S.W.audit_events(admin_engine, school.tenant_id, "disclosure.published")
    assert len(events) == 1
    assert events[0]["summary"]["documents"] == 1
    outbox = S.scalar(
        admin_engine,
        "SELECT count(*) FROM ops.outbox WHERE tenant_id = :t AND event_type = :e",
        t=school.tenant_id,
        e=service.RENDER_EVENT,
    )
    assert outbox == 1


def _as_app(app_engine: Engine, tenant_id: Any, sql: str, snapshot_id: Any) -> None:
    with app_engine.begin() as c:
        c.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant_id)})
        c.execute(text(sql), {"i": snapshot_id})


def test_SEC_050_snapshots_cannot_be_rewritten_by_the_app(
    api: Any, school: Any, app_engine: Engine
) -> None:
    snap = _published(api, school)
    update = "UPDATE ops.disclosure_snapshots SET content = '{}'::jsonb WHERE id = :i"
    with pytest.raises(Exception, match="permission denied"):
        _as_app(app_engine, school.tenant_id, update, snap["id"])
    delete = "DELETE FROM ops.disclosure_snapshots WHERE id = :i"
    with pytest.raises(Exception, match="permission denied"):
        _as_app(app_engine, school.tenant_id, delete, snap["id"])


def test_FR_DISC_006_board_results_below_ten_are_suppressed_on_the_page(
    api: Any, school: Any, public_client: TestClient
) -> None:
    inputs = {
        **S.REQUIRED,
        "board_results": [
            {"year": "2025-26", "class_level": "X", "appeared": 8, "passed": 8},
            {"year": "2025-26", "class_level": "XII", "appeared": 45, "passed": 44},
        ],
    }
    _published(api, school, inputs=inputs)
    key = S.link_key(api, school.people["owner"])
    page = _public(public_client, key)
    items = {i["key"]: i for s in page.json()["sections"] for i in s["items"]}
    assert items["board_results"]["table"]["rows"] == [
        ["2025-26", "Class X", "fewer than 10", "fewer than 10", ""],
        ["2025-26", "Class XII", "45", "44", "97.8%"],
    ]


# --- public page ---------------------------------------------------------------------------------


def test_FR_DISC_009_public_page_is_an_explicit_allowlist(
    api: Any, school: Any, public_client: TestClient, admin_engine: Engine
) -> None:
    doc = S.document(admin_engine, school)
    _published(
        api,
        school,
        inputs={**S.REQUIRED, "infrastructure_video": "https://video.example/tour"},
        documents={"fire_safety": [str(doc)]},
    )
    key = S.link_key(api, school.people["owner"])
    res = _public(public_client, key)
    assert res.status_code == 200, res.text
    assert "no-store" in res.headers["cache-control"]
    assert res.headers["x-robots-tag"].startswith("noindex")
    assert "set-cookie" not in res.headers
    body = res.json()
    assert set(body) == {"school_name", "title", "published_at", "format_version", "sections"}
    for section in body["sections"]:
        assert set(section) == {"key", "title", "items"}
        for item in section["items"]:
            assert set(item) == {"key", "label", "value", "table", "documents", "link"}
    assert str(doc) not in res.text
    assert str(school.tenant_id) not in res.text
    assert school.people["owner"].display_name not in res.text
    views = S.scalar(
        admin_engine,
        "SELECT views_by_day FROM ops.disclosure_snapshots WHERE tenant_id = :t",
        t=school.tenant_id,
    )
    assert sum(views.values()) == 1
    # The published document: a presigned link to exactly the published version.
    store = S.D.memory_store()
    store.gets.clear()
    res = _public(public_client, key, "/documents/1")
    assert res.status_code == 200, res.text
    assert store.gets[-1]["ttl"] <= 300
    assert _public(public_client, key, "/documents/2").status_code == 404


def test_FR_DISC_007_withdrawn_page_is_the_uniform_404(
    api: Any, school: Any, public_client: TestClient, admin_engine: Engine
) -> None:
    snap = _published(api, school)
    key = S.link_key(api, school.people["owner"])
    principal = school.people["principal"]
    short = api.call(
        principal, "POST", f"{BASE}/snapshots/{snap['id']}/withdraw", json={"reason": "short"}
    )
    assert short.status_code == 422
    res = api.call(
        principal,
        "POST",
        f"{BASE}/snapshots/{snap['id']}/withdraw",
        json={"reason": "Fire certificate expired; renewing it"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["live"] is False
    page = _public(public_client, key)
    assert page.status_code == 404
    assert page.json()["code"] == "not_found"
    again = api.call(
        principal,
        "POST",
        f"{BASE}/snapshots/{snap['id']}/withdraw",
        json={"reason": "Fire certificate expired; renewing it"},
    )
    assert again.status_code == 409
    assert again.json()["code"] == "snapshot_not_live"
    events = S.W.audit_events(admin_engine, school.tenant_id, "disclosure.withdrawn")
    assert events[0]["summary"] == {"reason_length": 37}
    # Publishing again makes a new live page at the same link.
    assert S.publish(api, school.people["owner"]).status_code == 201
    assert _public(public_client, key).status_code == 200


def test_FR_DISC_008_changing_the_link_kills_the_old_one(
    api: Any, school: Any, public_client: TestClient
) -> None:
    owner = school.people["owner"]
    res = api.call(owner, "POST", f"{BASE}/link/rotate")
    assert res.status_code == 409
    assert res.json()["code"] == "not_published"
    _published(api, school)
    old = S.link_key(api, owner)
    res = api.call(owner, "POST", f"{BASE}/link/rotate")
    assert res.status_code == 200, res.text
    new = res.json()["url"].rsplit("/", 1)[1]
    assert new != old
    assert _public(public_client, old).status_code == 404
    assert _public(public_client, new).status_code == 200
    assert api.call(school.people["office_admin"], "POST", f"{BASE}/link/rotate").status_code == 403


def test_FR_DISC_010_a_suspended_school_page_is_the_uniform_404(
    api: Any, school: Any, public_client: TestClient, admin_engine: Engine
) -> None:
    _published(api, school)
    key = S.link_key(api, school.people["owner"])
    S.P.set_tenant_status(admin_engine, school.tenant_id, "suspended")
    try:
        assert _public(public_client, key).status_code == 404
    finally:
        S.P.set_tenant_status(admin_engine, school.tenant_id, "active")
    assert _public(public_client, key).status_code == 200


def test_FR_DISC_010_a_school_that_drops_cbse_stops_showing_the_page(
    api: Any, school: Any, public_client: TestClient, admin_engine: Engine
) -> None:
    _published(api, school)
    key = S.link_key(api, school.people["owner"])
    S.set_boards(admin_engine, school.tenant_id, ["BSEAP"])
    assert _public(public_client, key).status_code == 404


# --- snapshots, files and BOLA -------------------------------------------------------------------


def test_FR_DISC_011_files_are_made_by_the_worker_and_downloaded(
    api: Any, school: Any, renderer: Any, admin_engine: Engine
) -> None:
    snap = _published(api, school)
    editor = school.people["office_admin"]
    url = f"{BASE}/snapshots/{snap['id']}/download-url"
    res = api.call(editor, "GET", url, params={"format": "pdf"})
    assert res.status_code == 409
    assert res.json()["code"] == "files_not_ready"
    assert service.render_files(school.tenant_id, uuid.UUID(snap["id"])) == "ready"
    assert "<script" not in renderer.pages[-1].lower()
    for fmt, mime in (("pdf", "application/pdf"), ("html", "text/html; charset=utf-8")):
        res = api.call(editor, "GET", url, params={"format": fmt})
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["mime_type"] == mime
        assert body["filename"].endswith(f".{fmt}")
    events = S.W.audit_events(admin_engine, school.tenant_id, "disclosure.downloaded")
    assert [e["summary"]["format"] for e in events] == ["pdf", "html"]
    detail = api.call(editor, "GET", f"{BASE}/snapshots/{snap['id']}").json()
    assert detail["pdf_status"] == "ready"
    assert detail["content"]["school_name"] == "Synthetic Model School"


def test_FR_DISC_012_snapshots_of_another_school_are_404(
    api: Any, school: Any, admin_engine: Engine
) -> None:
    snap = _published(api, school)
    other = S.cbse_school(admin_engine)
    who = other.people["owner"]
    assert api.call(who, "GET", f"{BASE}/snapshots/{snap['id']}").status_code == 404
    res = api.call(
        who,
        "POST",
        f"{BASE}/snapshots/{snap['id']}/withdraw",
        json={"reason": "Not my school at all here"},
    )
    assert res.status_code == 404
    res = api.call(who, "GET", f"{BASE}/snapshots/{snap['id']}/download-url?format=pdf")
    assert res.status_code == 404
    assert api.call(who, "GET", f"{BASE}/snapshots").json() == []


def test_FR_DISC_012_a_published_document_cannot_be_deleted(
    api: Any, school: Any, admin_engine: Engine
) -> None:
    doc = S.document(admin_engine, school)
    _published(api, school, documents={"fire_safety": [str(doc)]})
    with (
        pytest.raises(Exception, match="kept"),
        tenant_session(school.tenant_id, school.people["owner"].user_id) as db,
    ):
        documents.delete_for_retention(db, doc, reason=next(iter(documents.RETENTION_REASONS)))


def test_FR_DISC_012_export_and_purge_cover_the_tables(api: Any, school: Any) -> None:
    _published(api, school)
    with tenant_session(school.tenant_id) as db:
        tables = {t.name: t for t in service.export_records(db)}
        assert set(tables) == {
            "disclosure_drafts",
            "disclosure_snapshots",
            "disclosure_snapshot_documents",
        }
        assert len(tables["disclosure_snapshots"].rows) == 1
        assert "pdf_key" not in tables["disclosure_snapshots"].columns
        counts = service.tenant_data_counts(db)
    assert counts["ops.disclosure_snapshots"] == 1
    assert counts["ops.disclosure_drafts"] == 1


def test_FR_DISC_003_fee_structure_comes_from_active_plans_when_fees_are_on(
    api: Any, school: Any, admin_engine: Engine
) -> None:
    fees_support = S._load("sos_test_fees_support", S.TESTS / "fees" / "support.py")
    fees_support.enable(admin_engine, school.tenant_id)
    fees_support.ensure_setup(school)
    res = api.call(school.people["principal"], "GET", f"{BASE}/draft")
    assert res.status_code == 200, res.text
    table = res.json()["from_schoolos"]["fee_structure"]["table"]
    assert table["columns"] == ["Class", "Fee heads (per year)", "Total per year"]
    assert table["rows"] == [
        [
            "Class IX",
            "Tuition fee ₹6,000.00; Exam fee ₹1,000.00; Transport fee ₹2,000.00",
            "₹9,000.00",
        ]
    ]
