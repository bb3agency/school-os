"""WhatsApp messaging over the API and the worker functions (R2-1C; ADR-0043, ADR-0044,
ADR-0046; US-2201..US-2203, US-2208..US-2210; FR-MSG-002..010, FR-MSG-016..024, FR-PUB-007).

Synthetic data only: schools of the shared world, guardians with synthetic 90xxxxxxxx numbers,
the fake provider, synthetic Meta-shaped webhook fixtures signed with the synthetic app secret.
"""

from __future__ import annotations

import datetime as dt
import re
import sys
import time
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from app.core.config import get_settings
from app.core.db import tenant_session
from app.messaging import service
from app.messaging.providers.fake import STATE
from app.public import service as public

pytestmark = pytest.mark.db
M = sys.modules["sos_test_messaging_support"]
W = sys.modules["sos_test_api_world"]
D = sys.modules["sos_test_documents_support"]
TODAY = time.strftime("%Y-%m-%d")


def _optins(admin: Engine, guardian_id: uuid.UUID) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = M.rows(
        admin,
        "SELECT category, status, method, recorded_by FROM msg.optins WHERE guardian_id = :g "
        "ORDER BY category, seq",
        g=guardian_id,
    )
    return rows


def _states(api: Any, who: Any, guardian_id: uuid.UUID) -> dict[str, dict[str, Any]]:
    res = api.call(who, "GET", f"/api/v1/guardians/{guardian_id}/optins")
    assert res.status_code == 200, res.text
    return {c["category"]: c for c in res.json()["categories"]}


# --- availability ------------------------------------------------------------------------------


def test_FR_MSG_024_routes_answer_module_unavailable_when_messaging_is_off(
    world: Any, api: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SOS_MESSAGING_ENABLED", "false")
    get_settings.cache_clear()
    try:
        res = api.call(world.person("owner"), "GET", "/api/v1/messaging/account")
        assert res.status_code == 404
        assert res.json()["code"] == "module_unavailable"
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()


# --- connecting ----------------------------------------------------------------------------------


def test_FR_MSG_003_manual_token_connect_disconnect_and_audit(
    world: Any, api: Any, admin_engine: Engine, fake_state: Any
) -> None:
    owner = world.person("owner")
    M.disconnect(world.a)
    token = "synthetic-pilot-token-" + M.digits(20)
    waba, number = "3" + M.digits(14), "4" + M.digits(14)
    body = {"token": token, "waba_id": waba, "phone_number_id": number}
    assert api.call(world.person("office_admin"), "POST", "/api/v1/messaging/account/manual", json=body).status_code == 403
    stale = api.call(owner, "POST", "/api/v1/messaging/account/manual", json=body, auth_age_s=900)
    assert stale.status_code == 428
    res = api.call(owner, "POST", "/api/v1/messaging/account/manual", json=body)
    assert res.status_code == 201, res.text
    out = res.json()
    assert out["connected"] and out["status"] == "active" and out["mode"] == "manual_token"
    assert out["currency_state"] == "confirmed_inr"
    assert out["default_daily_cap"] == 225  # TIER_250 minus 10% (D25)
    assert len(out["templates"]) == 10  # five templates in English and Telugu
    assert token not in res.text
    again = api.call(owner, "POST", "/api/v1/messaging/account/manual", json=body)
    assert again.status_code == 409 and again.json()["code"] == "messaging_already_connected"
    stored = M.rows(admin_engine, "SELECT token_ciphertext FROM msg.accounts WHERE waba_id = :w", w=waba)
    assert token.encode() not in bytes(stored[0]["token_ciphertext"])
    events = W.audit_events(admin_engine, world.a.tenant_id, "messaging.connected")
    assert events and token not in str(events[-1]["summary"])
    handle = M.rows(
        admin_engine,
        "SELECT kind, revoked_at FROM core.public_handles WHERE target_id = :a",
        a=uuid.UUID(out["id"]),
    )
    assert handle == [{"kind": "meta_waba", "revoked_at": None}]

    gone = api.call(owner, "DELETE", "/api/v1/messaging/account")
    assert gone.status_code == 204
    row = M.rows(admin_engine, "SELECT status, token_ciphertext FROM msg.accounts WHERE waba_id = :w", w=waba)[0]
    assert row == {"status": "disconnected", "token_ciphertext": None}
    assert M.rows(
        admin_engine,
        "SELECT revoked_reason FROM core.public_handles WHERE target_id = :a",
        a=uuid.UUID(out["id"]),
    ) == [{"revoked_reason": "whatsapp_disconnected"}]
    assert W.audit_events(admin_engine, world.a.tenant_id, "messaging.disconnected")
    # Webhooks for a disconnected account are acknowledged and dropped.
    res = M.post(api.client, M.inbound(waba, "919000000001", text_body="STOP"))
    assert res.status_code == 200
    assert M.rows(admin_engine, "SELECT id FROM msg.provider_events WHERE account_id = :a", a=uuid.UUID(out["id"])) == []


def test_FR_MSG_002_embedded_signup(world: Any, api: Any, fake_state: Any) -> None:
    owner = world.person("principal")
    M.disconnect(world.a)
    waba, number = "5" + M.digits(14), "6" + M.digits(14)
    bad = api.call(
        owner,
        "POST",
        "/api/v1/messaging/account/embedded-signup",
        json={"code": "bad-signup-code-123", "waba_id": waba, "phone_number_id": number},
    )
    assert bad.status_code == 502 and bad.json()["code"] == "messaging_auth_failed"
    res = api.call(
        owner,
        "POST",
        "/api/v1/messaging/account/embedded-signup",
        json={"code": "synthetic-signup-code-" + M.digits(6), "waba_id": waba, "phone_number_id": number},
    )
    assert res.status_code == 201, res.text
    assert res.json()["mode"] == "embedded_signup"
    assert number in STATE.registered and waba in STATE.subscribed
    M.disconnect(world.a)


def test_FR_MSG_003_manual_token_switch(
    world: Any, api: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SOS_MESSAGING_MANUAL_TOKEN_ALLOWED", "false")
    get_settings.cache_clear()
    try:
        res = api.call(
            world.person("owner"),
            "POST",
            "/api/v1/messaging/account/manual",
            json={"token": "synthetic-pilot-token-0000000", "waba_id": "1", "phone_number_id": "2"},
        )
        assert res.status_code in (403, 409)
        if res.status_code == 403:
            assert res.json()["code"] == "manual_token_not_allowed"
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()


# --- templates -----------------------------------------------------------------------------------


def test_FR_MSG_006_templates_created_and_synced(world: Any, api: Any) -> None:
    M.connect(world.a)
    res = api.call(world.person("accountant"), "GET", "/api/v1/messaging/templates")
    assert res.status_code == 200
    items = {(i["template_key"], i["language"]): i for i in res.json()["items"]}
    assert items[("optin_confirm", "en")]["status"] == "approved"
    assert items[("optin_confirm", "en")]["usable"] is True
    assert items[("absence_alert", "te")]["reviewed"] is False
    # URL-button templates need SOS_PUBLIC_BASE_URL (unset in these tests): not created yet.
    assert items[("school_notice", "en")]["status"] == "not_created"
    assert items[("school_notice", "en")]["provider_name"] == "sos_school_notice_v1_en"
    assert api.call(world.person("teacher"), "GET", "/api/v1/messaging/templates").status_code == 403


def test_FR_MSG_007_template_moved_to_marketing_is_paused_and_owners_told(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    _, waba = M.connect(world.a)
    value = {
        "message_template_id": M.digits(12),
        "message_template_name": "sos_absence_alert_v1_te",
        "message_template_language": "te",
        "previous_category": "UTILITY",
        "new_category": "MARKETING",
    }
    M.deliver_webhook(api, world.a, M.envelope(waba, "template_category_update", value))
    items = {
        (i["template_key"], i["language"]): i
        for i in api.call(world.person("owner"), "GET", "/api/v1/messaging/templates").json()["items"]
    }
    paused = items[("absence_alert", "te")]
    assert paused["paused_by_sos"] is True and paused["usable"] is False
    assert W.audit_events(admin_engine, world.a.tenant_id, "messaging.template.paused")
    notes = M.rows(
        admin_engine,
        "SELECT count(*) AS n FROM ops.notifications WHERE tenant_id = :t "
        "AND template_key = 'messaging.template_paused'",
        t=world.a.tenant_id,
    )
    assert notes[0]["n"] >= 2  # owner and principal hold messaging.configure


# --- opt-ins -------------------------------------------------------------------------------------


def test_FR_MSG_008_record_optin_with_evidence_rules(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    M.connect(world.a)
    staff = world.person("office_staff")
    _, guardian, _ = M.guardian(world.a)
    res = M.opt_in(api, staff, guardian, "notices")
    assert res.status_code == 201, res.text
    body = res.json()
    notices = next(c for c in body["categories"] if c["category"] == "notices")
    assert notices["status"] == "opted_in" and notices["for_current_number"] is True
    assert body["history"][0]["recorded_by_name"] and body["phone_last4"]
    assert next(c for c in body["categories"] if c["category"] == "fees")["status"] == "not_asked"
    no_form = M.opt_in(api, staff, guardian, "fees", method="signed_form")
    assert no_form.status_code == 422
    assert no_form.json()["errors"][0]["code"] == "evidence_required"
    doc = D.make_document(
        admin_engine, world.a.tenant_id, staff.user_id, purpose="evidence", doc_type="form", sensitivity="C3"
    )
    signed = M.opt_in(api, staff, guardian, "fees", method="signed_form", evidence_document_id=str(doc))
    assert signed.status_code == 201, signed.text
    mismatch = M.opt_in(api, staff, guardian, "attendance", method="staff_recorded_stop")
    assert mismatch.status_code == 422
    future = M.opt_in(api, staff, guardian, "attendance", decided_on=(dt.date.today() + dt.timedelta(days=3)).isoformat())
    assert future.status_code == 422
    _, no_phone, _ = M.guardian(world.a, phone=None)
    missing = M.opt_in(api, staff, no_phone, "notices")
    assert missing.status_code == 422 and missing.json()["errors"][0]["code"] == "no_mobile_number"
    assert [r["method"] for r in _optins(admin_engine, guardian)] == ["signed_form", "in_person"]
    events = W.audit_events(admin_engine, world.a.tenant_id, "messaging.optin.recorded")
    assert any(e["resource_id"] == guardian for e in events)


def test_FR_MSG_008_authz_scope_and_other_school(world: Any, api: Any) -> None:
    M.connect(world.a)
    _, in_9a, _ = M.guardian(world.a, section_key="section_9a")
    _, in_9c, _ = M.guardian(world.a, section_key="section_9c")
    teacher = world.person("class_teacher")
    assert M.opt_in(api, teacher, in_9a).status_code == 201
    assert M.opt_in(api, teacher, in_9c).status_code == 404
    assert api.call(teacher, "GET", f"/api/v1/guardians/{in_9c}/optins").status_code == 404
    assert M.opt_in(api, world.person("accountant"), in_9a).status_code == 403
    assert api.call(world.person("accountant"), "GET", f"/api/v1/guardians/{in_9a}/optins").status_code == 200
    assert api.call(world.person("teacher"), "GET", f"/api/v1/guardians/{in_9a}/optins").status_code == 403
    _, other, _ = M.guardian(world.b)
    assert api.call(world.person("owner"), "GET", f"/api/v1/guardians/{other}/optins").status_code == 404
    assert api.call(world.person("owner"), "GET", f"/api/v1/guardians/{uuid.uuid4()}/optins").status_code == 404


def test_FR_LANG_003_guardian_language_shows_on_the_optin_panel(world: Any, api: Any) -> None:
    M.connect(world.a)
    student, guardian, _ = M.guardian(world.a)
    admin = world.person("office_admin")
    current = api.call(admin, "GET", f"/api/v1/students/{student}/guardians").json()
    version = next(g["version"] for g in current if g["id"] == str(guardian))
    res = api.call(
        admin,
        "PATCH",
        f"/api/v1/students/{student}/guardians/{guardian}",
        json={"preferred_language": "te"},
        headers={"If-Match": f'W/"{version}"'},
    )
    assert res.status_code == 200, res.text
    panel = api.call(admin, "GET", f"/api/v1/guardians/{guardian}/optins").json()
    assert panel["preferred_language"] == "te" and panel["effective_language"] == "te"


# --- STOP, JOIN and replies ----------------------------------------------------------------------


def test_FR_MSG_010_stop_opts_out_everything_revokes_links_and_confirms(
    world: Any, api: Any, admin_engine: Engine, fake_state: Any
) -> None:
    _, waba = M.connect(world.a)
    staff = world.person("office_staff")
    _, guardian, mobile = M.guardian(world.a)
    assert M.opt_in(api, staff, guardian, "notices").status_code == 201
    with tenant_session(world.a.tenant_id) as db:
        link = public.issue_handle(
            db, kind="parent_notice", target_type="notice", target_id=uuid.uuid4(), guardian_id=guardian
        )
    started = time.monotonic()
    M.deliver_webhook(api, world.a, M.inbound(waba, "91" + mobile, text_body=" Stop "))
    assert time.monotonic() - started < 60
    rows = _optins(admin_engine, guardian)
    stops = [r for r in rows if r["method"] == "whatsapp_stop"]
    assert sorted(r["category"] for r in stops) == ["attendance", "fees", "notices"]
    assert all(r["status"] == "opted_out" and r["recorded_by"] is None for r in stops)
    handle = M.rows(admin_engine, "SELECT revoked_reason FROM core.public_handles WHERE id = :i", i=link.handle_id)
    assert handle == [{"revoked_reason": "opted_out"}]
    confirmations = [s for s in STATE.sent if s["to"] == "91" + mobile]
    assert len(confirmations) == 1 and confirmations[0]["kind"] == "text"
    states = _states(api, staff, guardian)
    assert states["notices"]["stopped_by_parent"] is True
    # Staff cannot opt the parent back in in person; a signed form (a new parent action) can.
    again = M.opt_in(api, staff, guardian, "notices")
    assert again.status_code == 409 and again.json()["code"] == "opted_out_by_parent"
    doc = D.make_document(
        admin_engine, world.a.tenant_id, staff.user_id, purpose="evidence", doc_type="form", sensitivity="C3"
    )
    assert M.opt_in(api, staff, guardian, "notices", method="signed_form", evidence_document_id=str(doc)).status_code == 201
    # A second STOP is confirmed again but records nothing new for already-stopped categories.
    M.deliver_webhook(api, world.a, M.inbound(waba, "91" + mobile, button="SOS_STOP"))
    assert len([r for r in _optins(admin_engine, guardian) if r["method"] == "whatsapp_stop"]) == 4
    kinds = M.rows(
        admin_engine,
        "SELECT kind FROM msg.inbound_events WHERE tenant_id = :t ORDER BY received_at DESC LIMIT 2",
        t=world.a.tenant_id,
    )
    assert [k["kind"] for k in kinds] == ["stop", "stop"]


def test_FR_MSG_009_join_slip_code_from_the_guardians_own_number(
    world: Any, api: Any, admin_engine: Engine, fake_state: Any
) -> None:
    _, waba = M.connect(world.a)
    student, guardian, mobile = M.guardian(world.a)
    recorder = world.person("office_admin")
    slips = api.call(
        recorder,
        "POST",
        "/api/v1/messaging/join-slips",
        json={"student_ids": [str(student)], "categories": ["notices", "fees"]},
    )
    assert slips.status_code == 200, slips.text
    assert slips.headers["content-type"].startswith("text/html")
    assert mobile not in slips.text
    code = re.findall(r"JOIN ([A-Z0-9]{8})", slips.text)[0]
    stored = M.rows(admin_engine, "SELECT code_hash FROM msg.join_codes WHERE guardian_id = :g", g=guardian)
    assert len(stored) == 1 and code.encode() not in bytes(stored[0]["code_hash"])
    # From another number: refused silently, nothing recorded.
    M.deliver_webhook(api, world.a, M.inbound(waba, "91" + M.synthetic_mobile(), text_body=f"JOIN {code}"))
    assert _optins(admin_engine, guardian) == []
    M.deliver_webhook(api, world.a, M.inbound(waba, "91" + mobile, text_body=f"join {code.lower()}"))
    rows = _optins(admin_engine, guardian)
    assert {(r["category"], r["status"], r["method"]) for r in rows} == {
        ("fees", "opted_in", "whatsapp_join"),
        ("notices", "opted_in", "whatsapp_join"),
    }
    confirm = [s for s in STATE.sent if s["to"] == "91" + mobile]
    assert confirm and confirm[0]["name"] == "sos_optin_confirm_v1_en"
    # Single use: the same code again records nothing more.
    M.deliver_webhook(api, world.a, M.inbound(waba, "91" + mobile, text_body=f"JOIN {code}"))
    assert len(_optins(admin_engine, guardian)) == 2
    assert W.audit_events(admin_engine, world.a.tenant_id, "messaging.join_slips.printed")


def test_FR_MSG_018_one_automatic_reply_a_day_and_no_text_stored(
    world: Any, api: Any, admin_engine: Engine, fake_state: Any
) -> None:
    _, waba = M.connect(world.a)
    _, _, mobile = M.guardian(world.a)
    question = "Synthetic question about the exam timetable"
    for _ in range(2):
        M.deliver_webhook(api, world.a, M.inbound(waba, "91" + mobile, text_body=question))
    replies = [s for s in STATE.sent if s["to"] == "91" + mobile]
    assert len(replies) == 1 and replies[0]["kind"] == "text"
    with admin_engine.connect() as c:
        dump = c.execute(
            text(
                "SELECT string_agg(t::text, ' ') FROM (SELECT * FROM msg.inbound_events "
                "WHERE tenant_id = :t) t"
            ),
            {"t": world.a.tenant_id},
        ).scalar_one()
        payloads = c.execute(
            text("SELECT payload_ciphertext FROM msg.provider_events WHERE tenant_id = :t"),
            {"t": world.a.tenant_id},
        ).all()
    assert "exam" not in (dump or "") and mobile not in (dump or "")
    assert all(question.encode() not in bytes(p[0]) for p in payloads)


# --- statuses ------------------------------------------------------------------------------------


def _sent_alert(world: Any, api: Any) -> tuple[uuid.UUID, str, str]:
    """An absence alert to an opted-in guardian, sent through the fake provider."""
    _, waba = M.connect(world.a)
    student, guardian, mobile = M.guardian(world.a)
    assert M.opt_in(api, world.person("office_staff"), guardian, "attendance").status_code == 201
    with tenant_session(world.a.tenant_id) as db:
        queued = service.queue_message(
            db,
            purpose="absence_alert",
            guardian_id=guardian,
            student_id=student,
            source_type="attendance_day",
            source_id=uuid.uuid4(),
            dedupe_key=TODAY,
            variables={"school_name": "Synthetic Model School", "child_first_name": "Synthetica", "date": TODAY},
        )
    assert queued is not None
    assert service.deliver(world.a.tenant_id, queued.message_id) == "sent"
    sent = next(s for s in reversed(STATE.sent) if s["to"] == "91" + mobile)
    return queued.message_id, waba, sent["id"]


def test_FR_MSG_016_statuses_only_move_forward(
    world: Any, api: Any, admin_engine: Engine, fake_state: Any
) -> None:
    message_id, waba, wamid = _sent_alert(world, api)
    now = int(time.time())
    M.deliver_webhook(api, world.a, M.status(waba, wamid, "read", ts=now))
    M.deliver_webhook(api, world.a, M.status(waba, wamid, "delivered", ts=now - 5))
    M.deliver_webhook(api, world.a, M.status(waba, wamid, "failed", ts=now - 1, code=131026))
    row = M.rows(admin_engine, "SELECT status, delivered_at, read_at, billable FROM msg.messages WHERE id = :i", i=message_id)[0]
    assert row["status"] == "read" and row["read_at"] is not None and row["delivered_at"] is not None
    assert row["billable"] is True
    with pytest.raises(DBAPIError, match="final"), admin_engine.begin() as c:
        c.execute(text("UPDATE msg.messages SET status = 'sent' WHERE id = :i"), {"i": message_id})
    applied = W.audit_events(admin_engine, world.a.tenant_id, "messaging.statuses.applied")
    assert any("read" in e["summary"] for e in applied)


def test_FR_MSG_020_replayed_and_duplicate_events_apply_once(
    world: Any, api: Any, admin_engine: Engine, fake_state: Any
) -> None:
    message_id, waba, wamid = _sent_alert(world, api)
    payload = M.status(waba, wamid, "delivered")
    first = M.post(api.client, payload)
    replay = M.post(api.client, payload)
    assert first.status_code == replay.status_code == 200
    # The same event inside a differently shaped delivery is stored once (event key).
    payload["entry"][0]["changes"].append({"field": "unknown_field", "value": {"x": 1}})
    assert M.post(api.client, payload).status_code == 200
    stored = M.rows(
        admin_engine,
        "SELECT count(*) AS n FROM msg.provider_events WHERE event_key LIKE :k",
        k=f"status:{wamid}:delivered:%",
    )
    assert stored[0]["n"] == 1
    service.process_inbox(world.a.tenant_id)
    assert M.rows(admin_engine, "SELECT status FROM msg.messages WHERE id = :i", i=message_id)[0]["status"] == "delivered"


def test_FR_MSG_016_permanent_failure_is_not_retried(world: Any, api: Any, admin_engine: Engine, fake_state: Any) -> None:
    M.connect(world.a)
    student, guardian, mobile = M.guardian(world.a)
    assert M.opt_in(api, world.person("office_staff"), guardian, "attendance").status_code == 201
    STATE.failing["91" + mobile] = 131026
    with tenant_session(world.a.tenant_id) as db:
        queued = service.queue_message(
            db,
            purpose="absence_alert",
            guardian_id=guardian,
            student_id=student,
            source_type="attendance_day",
            source_id=uuid.uuid4(),
            dedupe_key=TODAY,
            variables={"school_name": "Synthetic Model School", "child_first_name": "Synthetica", "date": TODAY},
        )
    assert queued is not None
    assert service.deliver(world.a.tenant_id, queued.message_id) == "failed"
    res = api.call(world.person("office_admin"), "POST", "/api/v1/messages/search", json={"guardian_id": str(guardian)})
    item = res.json()["items"][0]
    assert item["status"] == "failed" and item["error_reason"] == "not_on_whatsapp"
    assert item["phone_last4"] == mobile[-4:] and mobile not in res.text


def test_FR_MSG_011_skip_without_optin(world: Any, api: Any, admin_engine: Engine) -> None:
    M.connect(world.a)
    student, guardian, _ = M.guardian(world.a)
    with tenant_session(world.a.tenant_id) as db:
        queued = service.queue_message(
            db,
            purpose="absence_alert",
            guardian_id=guardian,
            student_id=student,
            source_type="attendance_day",
            source_id=uuid.uuid4(),
            dedupe_key=TODAY,
            variables={"school_name": "Synthetic Model School", "child_first_name": "Synthetica", "date": TODAY},
        )
        duplicate = service.queue_message(
            db,
            purpose="absence_alert",
            guardian_id=guardian,
            student_id=student,
            source_type="attendance_day",
            source_id=uuid.uuid4(),
            dedupe_key=TODAY,
            variables={"school_name": "x", "child_first_name": "y", "date": TODAY},
        )
    assert queued is not None and duplicate is not None  # different sources: two messages
    assert service.deliver(world.a.tenant_id, queued.message_id) == "skipped"
    row = M.rows(admin_engine, "SELECT skip_reason FROM msg.messages WHERE id = :i", i=queued.message_id)
    assert row == [{"skip_reason": "no_optin"}]


# --- pause and resume ----------------------------------------------------------------------------


def test_FR_MSG_021_flagged_quality_pauses_until_a_person_resumes(
    world: Any, api: Any, admin_engine: Engine, fake_state: Any
) -> None:
    account_id, waba = M.connect(world.a)
    value = {"display_phone_number": "919000000000", "event": "FLAGGED", "current_limit": "TIER_250"}
    M.deliver_webhook(api, world.a, M.envelope(waba, "phone_number_quality_update", value))
    owner = world.person("owner")
    account = api.call(owner, "GET", "/api/v1/messaging/account").json()
    assert account["status"] == "paused" and account["pause_reason"] == "quality_flagged"
    assert M.rows(
        admin_engine,
        "SELECT count(*) AS n FROM ops.notifications WHERE tenant_id = :t AND template_key = 'messaging.paused'",
        t=world.a.tenant_id,
    )[0]["n"] >= 1
    number = M.live_account(world.a).phone_number_id
    STATE.quality[number] = "RED"
    still = api.call(owner, "POST", "/api/v1/messaging/account/resume")
    assert still.status_code == 409 and still.json()["code"] == "messaging_still_restricted"
    STATE.quality[number] = "GREEN"
    assert api.call(world.person("office_admin"), "POST", "/api/v1/messaging/account/resume").status_code == 403
    resumed = api.call(owner, "POST", "/api/v1/messaging/account/resume")
    assert resumed.status_code == 200 and resumed.json()["status"] == "active"
    assert W.audit_events(admin_engine, world.a.tenant_id, "messaging.paused")
    assert W.audit_events(admin_engine, world.a.tenant_id, "messaging.resumed")
    del account_id


# --- settings, summary, search -------------------------------------------------------------------


def test_FR_MSG_004_settings_defaults_and_update(world: Any, api: Any, admin_engine: Engine) -> None:
    M.connect(world.a)
    owner = world.person("owner")
    current = api.call(world.person("accountant"), "GET", "/api/v1/messaging/settings")
    assert current.status_code == 200
    body = current.json()
    assert body["absence_alerts_enabled"] is False  # owner decision D25
    assert body["quiet_start"] == "21:00:00" and body["quiet_end"] == "07:00:00"
    change = {
        "notices_enabled": True,
        "absence_alerts_enabled": False,
        "fee_reminders_enabled": False,
        "fee_receipts_enabled": True,
        "absence_alert_time": "11:00:00",
        "quiet_start": "21:00:00",
        "quiet_end": "07:00:00",
        "daily_cap": 200,
        "office_phone": "+91 90000 11111",
    }
    etag = current.headers["ETag"]
    assert api.call(world.person("accountant"), "PUT", "/api/v1/messaging/settings", json=change, headers={"If-Match": etag}).status_code == 403
    res = api.call(owner, "PUT", "/api/v1/messaging/settings", json=change, headers={"If-Match": etag})
    assert res.status_code == 200, res.text
    assert res.json()["office_phone"] == "9000011111" and res.json()["daily_cap"] == 200
    stale = api.call(owner, "PUT", "/api/v1/messaging/settings", json=change, headers={"If-Match": etag})
    assert stale.status_code == 412
    assert W.audit_events(admin_engine, world.a.tenant_id, "messaging.settings.changed")


def test_US_2202_section_summary_counts_and_scope(world: Any, api: Any) -> None:
    M.connect(world.a)
    _, guardian, _ = M.guardian(world.a, section_key="section_9a")
    M.opt_in(api, world.person("office_staff"), guardian, "notices")
    res = api.call(world.person("class_teacher"), "GET", f"/api/v1/sections/{world.a.ids['section_9a']}/optins/summary")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["categories"]["notices"]["opted_in"] >= 1 and body["guardians"] >= 1
    assert api.call(world.person("class_teacher"), "GET", f"/api/v1/sections/{world.a.ids['section_9c']}/optins/summary").status_code == 404
    assert api.call(world.person("owner"), "GET", f"/api/v1/sections/{world.b.ids['section_9a']}/optins/summary").status_code == 404


def test_FR_MSG_017_search_scope(world: Any, api: Any) -> None:
    M.connect(world.a)
    _, in_9c, _ = M.guardian(world.a, section_key="section_9c")
    teacher = world.person("class_teacher")
    assert api.call(teacher, "POST", "/api/v1/messages/search", json={}).status_code == 422
    assert api.call(teacher, "POST", "/api/v1/messages/search", json={"guardian_id": str(in_9c)}).status_code == 404
    everything = api.call(world.person("auditor_readonly"), "POST", "/api/v1/messages/search", json={"limit": 5})
    assert everything.status_code == 200
    assert api.call(world.person("exam_coordinator"), "POST", "/api/v1/messages/search", json={}).status_code == 403


def test_SEC_008_messaging_flows_log_no_phone_token_or_text(
    world: Any, api: Any, admin_engine: Engine, capsys: pytest.CaptureFixture[str], fake_state: Any
) -> None:
    _, waba = M.connect(world.a)
    _, guardian, mobile = M.guardian(world.a)
    capsys.readouterr()
    M.opt_in(api, world.person("office_staff"), guardian, "fees")
    M.deliver_webhook(api, world.a, M.inbound(waba, "91" + mobile, text_body="Synthetic private question"))
    M.deliver_webhook(api, world.a, M.inbound(waba, "91" + mobile, text_body="STOP"))
    api.call(world.person("office_admin"), "POST", "/api/v1/messages/search", json={"guardian_id": str(guardian)})
    out = capsys.readouterr()
    logs = out.out + out.err
    for secret in (mobile, "91" + mobile, "Synthetic private question", M.secret().decode()):
        assert secret not in logs
    summaries = " ".join(str(e["summary"]) for e in W.audit_events(admin_engine, world.a.tenant_id))
    assert mobile not in summaries and "private question" not in summaries
