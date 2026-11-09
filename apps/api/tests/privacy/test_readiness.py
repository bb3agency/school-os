"""DPDP readiness checklist, notice given at admission and the privacy notice sent on WhatsApp
over HTTP (FC-03 items 1 and 3, FP-13; FR-DPDP-053..FR-DPDP-066). Synthetic data only."""

from __future__ import annotations

import datetime as dt
import importlib.util
import sys
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import Engine, text

pytestmark = pytest.mark.db
P = sys.modules["sos_test_privacy_support"]
EX = sys.modules["sos_test_exports_objects"]
W = sys.modules["sos_test_api_world"]
IST = ZoneInfo("Asia/Kolkata")
TESTS = Path(__file__).resolve().parents[1]


def _load(name: str, path: Path) -> ModuleType:
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def _today() -> dt.date:
    return dt.datetime.now(IST).date()


@pytest.fixture(scope="module")
def ready_school(world: Any, admin_engine: Engine) -> Any:
    """A school of its own: the checklist and the counts start empty."""
    return EX.build_school(admin_engine)


def _items(out: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {i["key"]: i for i in out["items"]}


def _issue_parents_notice(api: Any, school: Any, issued_on: dt.date) -> dict[str, Any]:
    templates = api.call(school.people["owner"], "GET", "/api/v1/privacy/templates").json()
    version = next(t for t in templates if t["key"] == "notice_parents")["template_version"]
    res = api.call(
        school.people["office_admin"],
        "POST",
        "/api/v1/privacy/notices",
        json={
            "template_key": "notice_parents",
            "template_version": version,
            "audience": "parents",
            "issued_on": issued_on.isoformat(),
            "channel": "paper",
        },
    )
    assert res.status_code == 201, res.text
    out: dict[str, Any] = res.json()
    return out


# --- checklist (FR-DPDP-053..057) ---------------------------------------------------------------


def test_FR_DPDP_053_checklist_starts_todo_and_says_not_legal_advice(
    ready_school: Any, api: Any
) -> None:
    res = api.call(ready_school.people["principal"], "GET", "/api/v1/privacy/readiness")
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["not_legal_advice"] is True
    items = _items(out)
    assert list(items) == [
        "privacy_contact",
        "notices",
        "dpa_signed",
        "dpia_done",
        "test_request",
        "breach_contact",
        "retention_reviewed",
        "consent_forms",
    ]
    assert items["dpa_signed"]["status"] == "todo"
    assert items["notices"]["status"] == "todo"
    assert items["test_request"]["status"] == "todo"
    # No active optional purpose: the consent forms item does not apply.
    assert items["consent_forms"]["status"] == "not_applicable"
    assert out["applicable"] == 7
    assert out["admission"]["since"] is None


def test_FR_DPDP_054_recording_a_mark_completes_the_item_and_is_audited(
    ready_school: Any, api: Any, admin_engine: Engine
) -> None:
    admin = ready_school.people["office_admin"]
    res = api.call(
        admin,
        "POST",
        "/api/v1/privacy/readiness/marks",
        json={"item": "dpa_signed", "done_on": _today().isoformat()},
    )
    assert res.status_code == 201, res.text
    assert res.json()["item"] == "dpa_signed"
    items = _items(api.call(admin, "GET", "/api/v1/privacy/readiness").json())
    assert items["dpa_signed"]["status"] == "done"
    assert items["dpa_signed"]["mark"]["done_on"] == _today().isoformat()
    (event,) = W.audit_events(admin_engine, ready_school.tenant_id, "privacy.readiness.recorded")[
        -1:
    ]
    assert event["summary"] == {"item": "dpa_signed", "has_document": False}


def test_FR_DPDP_056_retention_review_counts_only_in_the_current_academic_year(
    ready_school: Any, api: Any
) -> None:
    owner = ready_school.people["owner"]
    old = _today() - dt.timedelta(days=800)
    res = api.call(
        owner,
        "POST",
        "/api/v1/privacy/readiness/marks",
        json={"item": "retention_reviewed", "done_on": old.isoformat()},
    )
    assert res.status_code == 201, res.text
    items = _items(api.call(owner, "GET", "/api/v1/privacy/readiness").json())
    assert items["retention_reviewed"]["status"] == "todo"


@pytest.mark.parametrize(
    ("data", "code"),
    [
        ({"item": "dpia_done", "done_on": "2099-01-01"}, "date_in_future"),
        (
            {"item": "dpia_done", "done_on": "2026-01-01", "document_id": str(uuid.uuid4())},
            "not_found",
        ),
    ],
)
def test_FR_DPDP_054_bad_marks_are_refused_with_a_code(
    ready_school: Any, api: Any, data: dict[str, Any], code: str
) -> None:
    res = api.call(
        ready_school.people["office_admin"], "POST", "/api/v1/privacy/readiness/marks", json=data
    )
    assert res.status_code == 422, res.text
    assert code in [e["code"] for e in res.json()["errors"]]


def test_FR_DPDP_054_free_text_and_unknown_items_are_refused(ready_school: Any, api: Any) -> None:
    res = api.call(
        ready_school.people["office_admin"],
        "POST",
        "/api/v1/privacy/readiness/marks",
        json={"item": "privacy_contact", "done_on": "2026-01-01", "note": "x"},
    )
    assert res.status_code == 422


def test_FR_DPDP_053_authz_reader_writer_and_outsider(world: Any, api: Any) -> None:
    a = world.a
    assert (
        api.call(a.people["auditor_readonly"], "GET", "/api/v1/privacy/readiness").status_code
        == 200
    )
    assert api.call(a.people["teacher"], "GET", "/api/v1/privacy/readiness").status_code == 403
    denied = api.call(
        a.people["auditor_readonly"],
        "POST",
        "/api/v1/privacy/readiness/marks",
        json={"item": "dpia_done", "done_on": "2026-01-01"},
    )
    assert denied.status_code == 403
    assert (
        api.call(
            a.people["office_staff"], "GET", "/api/v1/privacy/admission-notices/pending"
        ).status_code
        == 403
    )


# --- notice given at admission (FR-DPDP-058..060) ----------------------------------------------


def test_FR_DPDP_058_students_added_after_the_notice_need_their_own_record(
    ready_school: Any, api: Any, admin_engine: Engine
) -> None:
    admin = ready_school.people["office_admin"]
    notice = _issue_parents_notice(api, ready_school, _today() - dt.timedelta(days=3))
    sid = P.student(ready_school)

    pending = api.call(admin, "GET", "/api/v1/privacy/admission-notices/pending")
    assert pending.status_code == 200, pending.text
    body = pending.json()
    assert body["since"] == notice["issued_on"]
    assert body["not_legal_advice"] is True
    assert str(sid) in [r["student_id"] for r in body["items"]]
    before = body["without_notice"]
    counts = api.call(admin, "GET", "/api/v1/privacy/readiness").json()["admission"]
    assert counts["without_notice"] == before

    assert api.call(admin, "GET", f"/api/v1/privacy/admission-notices/{sid}").status_code == 404
    res = api.call(
        admin,
        "POST",
        "/api/v1/privacy/admission-notices",
        json={"student_ids": [str(sid)], "given_on": _today().isoformat(), "channel": "paper"},
    )
    assert res.status_code == 201, res.text
    assert res.json() == {"recorded": 1, "already_recorded": 0}
    again = api.call(
        admin,
        "POST",
        "/api/v1/privacy/admission-notices",
        json={"student_ids": [str(sid)], "given_on": _today().isoformat(), "channel": "email"},
    )
    assert again.json() == {"recorded": 0, "already_recorded": 1}

    one = api.call(admin, "GET", f"/api/v1/privacy/admission-notices/{sid}")
    assert one.status_code == 200, one.text
    assert one.json()["channel"] == "paper"
    assert one.json()["notice_id"] == notice["id"]
    after = api.call(admin, "GET", "/api/v1/privacy/admission-notices/pending").json()
    assert after["without_notice"] == before - 1
    assert str(sid) not in [r["student_id"] for r in after["items"]]

    events = W.audit_events(
        admin_engine, ready_school.tenant_id, "privacy.admission_notice.recorded"
    )
    assert events[-2]["summary"] == {"channel": "paper", "recorded": 1, "already_recorded": 0}
    assert "Synthetica" not in str(events[-2]["summary"])

    # Append-only in the database too.
    with pytest.raises(Exception, match="append-only"), admin_engine.begin() as c:
        c.execute(text("UPDATE ops.privacy_admission_notices SET channel = 'other'"))


def test_FR_DPDP_058_other_schools_student_is_404(world: Any, api: Any) -> None:
    other = P.student(world.b)
    a = world.a.people["office_admin"]
    assert api.call(a, "GET", f"/api/v1/privacy/admission-notices/{other}").status_code == 404
    res = api.call(
        a,
        "POST",
        "/api/v1/privacy/admission-notices",
        json={"student_ids": [str(other)], "given_on": "2026-01-01", "channel": "paper"},
    )
    assert res.status_code == 404, res.text


def test_FR_DPDP_060_admission_record_refuses_future_dates(ready_school: Any, api: Any) -> None:
    sid = P.student(ready_school)
    res = api.call(
        ready_school.people["office_admin"],
        "POST",
        "/api/v1/privacy/admission-notices",
        json={"student_ids": [str(sid)], "given_on": "2099-01-01", "channel": "paper"},
    )
    assert res.status_code == 422
    assert "date_in_future" in [e["code"] for e in res.json()["errors"]]


# --- the privacy notice sent on WhatsApp (FR-DPDP-061..066) ------------------------------------


@pytest.fixture(scope="module")
def wa_school(world: Any, admin_engine: Engine) -> Any:
    sends_support = _load(
        "sos_test_messaging_sends_support", TESTS / "messaging" / "sends_support.py"
    )
    return sends_support.make_school(admin_engine)


def test_FR_DPDP_061_sending_needs_notice_approval_and_message_send(
    wa_school: Any, api: Any
) -> None:
    # office_admin manages privacy requests but cannot approve parent notices.
    res = api.call(
        wa_school.people["office_admin"],
        "POST",
        "/api/v1/privacy/notices/whatsapp/preview",
        json={},
    )
    assert res.status_code == 403, res.text
    res = api.call(
        wa_school.people["auditor_readonly"],
        "POST",
        "/api/v1/privacy/notices/whatsapp/preview",
        json={},
    )
    assert res.status_code == 403


def test_FR_DPDP_063_send_on_whatsapp_records_the_broadcast_and_delivery(
    wa_school: Any, api: Any, admin_engine: Engine
) -> None:
    from app.messaging.providers.fake import STATE
    from app.messaging.sends import service as sends

    S = sys.modules["sos_test_messaging_sends_support"]  # noqa: N806 - the suite alias
    STATE.sent.clear()
    principal = wa_school.people["principal"]
    reached_student, _, mobile = S.opted_guardian(api, wa_school, "notices")
    missed_student, _, _ = S.opted_guardian(api, wa_school, "fees")  # no notices opt-in
    section = str(wa_school.ids["section_9a"])

    res = api.call(
        principal,
        "POST",
        "/api/v1/privacy/notices/whatsapp/preview",
        json={"section_ids": [section]},
    )
    assert res.status_code == 200, res.text
    preview = res.json()
    assert preview["not_legal_advice"] is True
    assert preview["preview"]["recipients"] >= 1
    assert res.headers["Cache-Control"] == "no-store"
    assert "privacy notice" in preview["body"].lower()

    # A second preview reuses the parent notice instead of making a new one.
    again = api.call(
        principal,
        "POST",
        "/api/v1/privacy/notices/whatsapp/preview",
        json={"section_ids": [section], "parent_notice_id": preview["parent_notice_id"]},
    )
    assert again.status_code == 200, again.text
    assert again.json()["parent_notice_id"] == preview["parent_notice_id"]
    preview = again.json()

    sent = api.call(
        principal,
        "POST",
        "/api/v1/privacy/notices/whatsapp",
        json={
            "parent_notice_id": preview["parent_notice_id"],
            "preview_token": preview["preview"]["preview_token"],
            "section_ids": [section],
        },
    )
    assert sent.status_code == 201, sent.text
    record = sent.json()
    assert record["channel"] == "sos_whatsapp"
    assert record["audience"] == "parents"
    assert record["parent_notice_id"] == preview["parent_notice_id"]
    assert record["broadcast_id"]
    (event,) = [
        e
        for e in W.audit_events(admin_engine, wa_school.tenant_id, "privacy.notice.issued")
        if str(e["resource_id"]) == record["id"]
    ]
    assert event["summary"]["channel"] == "sos_whatsapp"
    assert event["summary"]["broadcast_id"] == record["broadcast_id"]

    sends.send_due(wa_school.tenant_id, now=S.at(12))
    with admin_engine.begin() as c:
        c.execute(
            text(
                "UPDATE msg.messages SET status = 'delivered' "
                "WHERE broadcast_id = :b AND status = 'sent'"
            ),
            {"b": record["broadcast_id"]},
        )

    delivery = api.call(principal, "GET", f"/api/v1/privacy/notices/{record['id']}/delivery")
    assert delivery.status_code == 200, delivery.text
    out = delivery.json()
    assert out["not_legal_advice"] is True
    assert out["guardians"].get("delivered", 0) >= 1
    assert out["guardians"]["left_out"] >= 1
    not_reached = [r["student_id"] for r in out["not_reached"]]
    assert str(missed_student) in not_reached
    assert str(reached_student) not in not_reached
    assert out["students_not_reached"] == len(not_reached) + out["hidden"]
    assert mobile not in delivery.text

    listed = api.call(principal, "GET", "/api/v1/privacy/notices").json()
    assert any(n["id"] == record["id"] and n["broadcast_id"] for n in listed)

    # Other permissions: the teacher may not read it; another school gets 404.
    assert (
        api.call(
            wa_school.people["teacher"], "GET", f"/api/v1/privacy/notices/{record['id']}/delivery"
        ).status_code
        == 403
    )


def test_FR_DPDP_065_delivery_of_a_paper_notice_or_other_school_is_404(
    world: Any, api: Any, ready_school: Any
) -> None:
    notice = _issue_parents_notice(api, ready_school, _today())
    res = api.call(
        ready_school.people["principal"], "GET", f"/api/v1/privacy/notices/{notice['id']}/delivery"
    )
    assert res.status_code == 404
    res = api.call(
        world.a.people["principal"], "GET", f"/api/v1/privacy/notices/{notice['id']}/delivery"
    )
    assert res.status_code == 404


def test_FR_DPDP_063_the_whatsapp_channel_cannot_be_recorded_by_hand(
    ready_school: Any, api: Any
) -> None:
    res = api.call(
        ready_school.people["office_admin"],
        "POST",
        "/api/v1/privacy/notices",
        json={
            "template_key": "notice_parents",
            "template_version": "0.1",
            "audience": "parents",
            "issued_on": "2026-01-01",
            "channel": "sos_whatsapp",
        },
    )
    assert res.status_code == 422
