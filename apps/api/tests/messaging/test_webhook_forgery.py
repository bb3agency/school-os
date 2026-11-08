"""Meta webhook forgery and replay (R2-3I hardening; ADR-0043 decision 6, ADR-0047; FR-MSG-016,
FR-MSG-020; SEC-034, SEC-037; owner decision D31). Synthetic Meta-shaped fixtures only.

A delivery status for a message really sent through the fake provider is forged (wrong secret,
no header, another algorithm, a signature of another body, an upper-case or whitespace-padded
copy of the body), replayed (inside the guard's replay window and after it, when only the
school's inbox dedupe is left) and sent out of order. Every forgery is refused with a bare 401
problem, no state change and one ``security.webhook.rejected`` log line without the signature,
the secret, the WABA id, the message id or the phone number. The verify-token GET refuses every
wrong combination without echoing the token. A suspended school's WABA stores nothing (D31).
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import time
import uuid
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import Engine, text

from app.core.config import Environment, Settings
from app.core.db import tenant_session
from app.messaging import service
from app.messaging.config import load_config
from app.messaging.providers.fake import STATE
from app.messaging.webhook import signature
from app.public import guards
from app.public import service as public

pytestmark = pytest.mark.db
M = sys.modules["sos_test_messaging_support"]
TODAY = time.strftime("%Y-%m-%d")


def _sent(world: Any, api: Any) -> tuple[uuid.UUID, str, str, str]:
    """An absence alert sent through the fake provider: (message id, WABA, wamid, mobile)."""
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
            variables={
                "school_name": "Synthetic Model School",
                "child_first_name": "Synthetica",
                "date": TODAY,
            },
        )
    assert queued is not None
    assert service.deliver(world.a.tenant_id, queued.message_id) == "sent"
    sent = next(s for s in reversed(STATE.sent) if s["to"] == "91" + mobile)
    return queued.message_id, waba, sent["id"], mobile


def _set_status(admin: Engine, tenant_id: uuid.UUID, status: str) -> None:
    with admin.begin() as c:
        c.execute(
            text("UPDATE core.tenants SET status = :s WHERE id = :t"), {"s": status, "t": tenant_id}
        )


def _state(admin: Engine, message_id: uuid.UUID) -> dict[str, Any]:
    row: dict[str, Any] = M.rows(
        admin,
        "SELECT status, delivered_at, read_at FROM msg.messages WHERE id = :i",
        i=message_id,
    )[0]
    return row


def _events(admin: Engine, wamid: str) -> int:
    rows = M.rows(
        admin,
        "SELECT count(*) AS n FROM msg.provider_events WHERE event_key LIKE :k",
        k=f"status:{wamid}:%",
    )
    return int(rows[0]["n"])


def test_SEC_034_forged_status_deliveries_change_nothing_and_log_no_secret(
    world: Any,
    api: Any,
    admin_engine: Engine,
    fake_state: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    message_id, waba, wamid, mobile = _sent(world, api)
    before = _state(admin_engine, message_id)
    payload = M.status(waba, wamid, "read")
    body = json.dumps(payload).encode()
    good = signature(M.secret(), body)
    forgeries = {
        "wrong_secret": (body, signature(b"synthetic-not-the-app-secret", body)),
        "missing": (body, ""),
        "sha1": (body, "sha1=" + hashlib.sha1(body, usedforsecurity=False).hexdigest()),
        "bare_hex": (body, good.removeprefix("sha256=")),
        "other_body": (body.replace(b'"read"', b'"failed"'), good),
        "reserialised": (json.dumps(payload, indent=1).encode(), good),
        "truncated_sig": (body, good[:-2]),
        "stale_body": (json.dumps(M.status(waba, wamid, "delivered")).encode(), good),
    }
    capsys.readouterr()
    for name, (sent_body, sig) in forgeries.items():
        res = M.post(api.client, sent_body, header=sig)
        assert res.status_code == 401, (name, res.text)
        problem = res.json()
        assert set(problem) <= {"type", "title", "status", "code", "instance", "request_id"}
        assert problem["instance"] == "/api/v1/webhooks/meta"
        assert "no-store" in res.headers["cache-control"]
        for value in (wamid, waba, sig, mobile):
            if value:
                assert value not in res.text, name
    out, err = capsys.readouterr()
    logged = out + err
    assert logged.count("security.webhook.rejected") >= len(forgeries)
    for value in (wamid, waba, good, good.removeprefix("sha256="), mobile):
        assert value not in logged
    assert M.secret().decode() not in logged
    service.process_inbox(world.a.tenant_id)
    assert _state(admin_engine, message_id) == before
    assert _events(admin_engine, wamid) == 0


def test_FR_MSG_020_replays_inside_and_after_the_replay_window_apply_once(
    world: Any, api: Any, admin_engine: Engine, fake_state: Any
) -> None:
    message_id, waba, wamid, _ = _sent(world, api)
    payload = M.status(waba, wamid, "delivered")
    body = json.dumps(payload).encode()
    assert M.post(api.client, body).status_code == 200
    assert M.post(api.client, body).status_code == 200  # inside the window: a duplicate
    # After the window the guard has forgotten the body; the school's inbox still has the event.
    guards.forget_event("meta", "body:" + hashlib.sha256(body).hexdigest())
    assert M.post(api.client, body).status_code == 200
    service.process_inbox(world.a.tenant_id)
    assert _events(admin_engine, wamid) == 1
    delivered = _state(admin_engine, message_id)
    assert delivered["status"] == "delivered"
    # An old "sent" status replayed later, re-signed (a valid delivery of stale news): the
    # message never moves backwards (statuses only move forward, FR-MSG-016).
    old = M.status(waba, wamid, "sent", ts=int(time.time()) - 3600)
    M.deliver_webhook(api, world.a, old)
    assert _state(admin_engine, message_id) == delivered


def test_SEC_034_verify_token_get_refuses_every_wrong_combination(
    api: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    token = "synthetic-verify-token-" + uuid.uuid4().hex
    good = {"hub.mode": "subscribe", "hub.verify_token": token, "hub.challenge": "8812345"}
    unset = Settings(env=Environment.CI)
    monkeypatch.setattr(guards, "get_settings", lambda: unset)
    assert api.client.get(M.WEBHOOK, params=good).status_code == 403  # unset: always refused
    configured = Settings(env=Environment.CI, meta_webhook_verify_token=SecretStr(token))
    monkeypatch.setattr(guards, "get_settings", lambda: configured)
    capsys.readouterr()
    wrong = [
        {},
        {**good, "hub.mode": "unsubscribe"},
        {**good, "hub.verify_token": token + "x"},
        {**good, "hub.verify_token": token.upper()},
        {**good, "hub.verify_token": ""},
        {k: v for k, v in good.items() if k != "hub.verify_token"},
        {**good, "hub.challenge": "<script>alert(1)</script>"},
        {**good, "hub.challenge": "x" * 129},
        {**good, "hub.challenge": ""},
    ]
    for params in wrong:
        res = api.client.get(M.WEBHOOK, params=params)
        assert res.status_code == 403, params
        assert token not in res.text
        assert "<script>" not in res.text
        assert "no-store" in res.headers["cache-control"]
    out, err = capsys.readouterr()
    assert token not in out + err
    assert "verify_token_mismatch" in out + err
    ok = api.client.get(M.WEBHOOK, params=good)
    assert ok.status_code == 200
    assert ok.text == "8812345"


def test_D31_a_suspended_schools_waba_stores_nothing(
    world: Any, api: Any, admin_engine: Engine, fake_state: Any
) -> None:
    message_id, waba, wamid, _ = _sent(world, api)
    before = _state(admin_engine, message_id)
    _set_status(admin_engine, world.a.tenant_id, "suspended")
    try:
        res = M.post(api.client, M.status(waba, wamid, "read"))
        assert res.status_code == 200  # acknowledged: Meta sends one callback for every school
    finally:
        _set_status(admin_engine, world.a.tenant_id, "active")
    service.process_inbox(world.a.tenant_id)
    assert _events(admin_engine, wamid) == 0
    assert _state(admin_engine, message_id) == before


def test_D35_a_suspended_school_records_stop_silently_and_drops_everything_else(
    world: Any, api: Any, admin_engine: Engine, fake_state: Any
) -> None:
    """Owner decision D35 (ADR-0043 / ADR-0047 follow-up; FR-MSG-010): while suspended, a
    parent's STOP is recorded at once (opt-outs, links revoked) but nothing is sent, not even
    the confirmation, and not after reactivation either; statuses, JOIN and other messages are
    dropped as before (D31)."""
    message_id, waba, wamid, _ = _sent(world, api)
    before = _state(admin_engine, message_id)
    _, guardian, mobile = M.guardian(world.a)
    assert mobile is not None
    assert M.opt_in(api, world.person("office_staff"), guardian, "notices").status_code == 201
    with tenant_session(world.a.tenant_id) as db:
        link = public.issue_handle(
            db,
            kind="parent_notice",
            target_type="notice",
            target_id=uuid.uuid4(),
            guardian_id=guardian,
        )
    sent_before = len(STATE.sent)
    stop = M.inbound(waba, "91" + mobile, text_body="STOP")
    _set_status(admin_engine, world.a.tenant_id, "suspended")
    try:
        for payload in (
            stop,
            M.inbound(waba, "91" + mobile, text_body="When is the exam?"),
            M.inbound(waba, "91" + mobile, text_body="JOIN 4F7K2Q"),
            M.status(waba, wamid, "read"),
        ):
            assert M.post(api.client, payload).status_code == 200
        # Meta retries the same STOP (a new delivery, same message): recorded once.
        retry = json.loads(json.dumps(stop))
        retry["entry"][0]["changes"][0]["value"]["metadata"]["display_phone_number"] = "91900"
        assert M.post(api.client, retry).status_code == 200
        service.process_inbox(world.a.tenant_id)
    finally:
        _set_status(admin_engine, world.a.tenant_id, "active")
    stops = M.rows(
        admin_engine,
        "SELECT category, status FROM msg.optins WHERE guardian_id = :g "
        "AND method = 'whatsapp_stop'",
        g=guardian,
    )
    assert sorted(r["category"] for r in stops) == ["attendance", "fees", "notices"]
    assert all(r["status"] == "opted_out" for r in stops)
    handle = M.rows(
        admin_engine,
        "SELECT revoked_reason FROM core.public_handles WHERE id = :i",
        i=link.handle_id,
    )
    assert handle == [{"revoked_reason": "opted_out"}]
    inbound = M.rows(
        admin_engine,
        "SELECT kind, replied, outcome FROM msg.inbound_events WHERE tenant_id = :t "
        "AND outcome = 'stopped_while_suspended'",
        t=world.a.tenant_id,
    )
    assert inbound == [{"kind": "stop", "replied": False, "outcome": "stopped_while_suspended"}]
    # Nothing else was stored: the status update is not applied, now or after reactivation.
    assert _events(admin_engine, wamid) == 0
    service.process_inbox(world.a.tenant_id)
    assert _state(admin_engine, message_id) == before
    # Nothing was sent or queued: no confirmation, no auto-reply.
    assert len(STATE.sent) == sent_before
    queued = M.rows(
        admin_engine,
        "SELECT count(*) AS n FROM msg.messages WHERE guardian_id = :g "
        "AND purpose = 'optin_stopped'",
        g=guardian,
    )
    assert queued == [{"n": 0}]


# --- audit 2026-11-09 (YM): replay window, late deliveries, STOP ordering ----------------------


def _stops(admin: Engine, guardian: uuid.UUID) -> list[str]:
    rows = M.rows(
        admin,
        "SELECT category FROM msg.optins WHERE guardian_id = :g AND method = 'whatsapp_stop'",
        g=guardian,
    )
    return sorted(r["category"] for r in rows)


def _current_notices(admin: Engine, guardian: uuid.UUID) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = M.rows(
        admin,
        "SELECT status, method FROM msg.optins WHERE guardian_id = :g AND category = 'notices' "
        "ORDER BY seq DESC LIMIT 1",
        g=guardian,
    )
    return rows


def _with_timestamp(payload: dict[str, Any], ts: int) -> dict[str, Any]:
    out: dict[str, Any] = json.loads(json.dumps(payload))
    value = out["entry"][0]["changes"][0]["value"]
    for item in value.get("messages", []) + value.get("statuses", []):
        item["timestamp"] = str(ts)
    return out


def test_YM_01_FR_MSG_020_a_delivery_not_stored_is_not_a_replay_when_meta_retries(
    world: Any, api: Any, admin_engine: Engine, fake_state: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A delivery the route could not store (a database error answers 5xx) must not stay
    claimed by the replay check: Meta retries the same body, and that retry must be stored, or
    a parent's STOP is lost (FR-MSG-010, FR-MSG-020)."""
    _, waba = M.connect(world.a)
    _, guardian, mobile = M.guardian(world.a)
    assert mobile is not None
    assert M.opt_in(api, world.person("office_staff"), guardian, "notices").status_code == 201
    stop = M.inbound(waba, "91" + mobile, text_body="STOP")
    real = service.store_events

    def broken(*args: Any, **kwargs: Any) -> int:
        raise RuntimeError("synthetic database outage")

    monkeypatch.setattr(service, "store_events", broken)
    with pytest.raises(RuntimeError):
        M.post(api.client, stop)
    monkeypatch.setattr(service, "store_events", real)
    M.deliver_webhook(api, world.a, stop)  # Meta's retry: the same body
    assert _stops(admin_engine, guardian) == ["attendance", "fees", "notices"]


def test_YM_02_FR_MSG_010_a_join_sent_before_the_parents_stop_does_not_opt_back_in(
    world: Any, api: Any, admin_engine: Engine, fake_state: Any
) -> None:
    """Meta does not deliver in order: a JOIN the parent sent before STOP, delivered (a Meta
    retry) after the STOP was applied, must not opt the parent back in. The parent's latest
    word (STOP) wins; a JOIN sent after the STOP still works with the same unused code."""
    _, waba = M.connect(world.a)
    student, guardian, mobile = M.guardian(world.a)
    assert mobile is not None
    slips = api.call(
        world.person("office_admin"),
        "POST",
        "/api/v1/messaging/join-slips",
        json={"student_ids": [str(student)], "categories": ["notices"]},
    )
    assert slips.status_code == 200, slips.text
    code = re.findall(r"JOIN ([A-Z0-9]{8})", slips.text)[0]
    now = int(time.time())
    M.deliver_webhook(api, world.a, M.inbound(waba, "91" + mobile, text_body="STOP"))
    late = _with_timestamp(M.inbound(waba, "91" + mobile, text_body=f"JOIN {code}"), now - 600)
    M.deliver_webhook(api, world.a, late)
    assert _current_notices(admin_engine, guardian) == [
        {"status": "opted_out", "method": "whatsapp_stop"}
    ]
    later = _with_timestamp(M.inbound(waba, "91" + mobile, text_body=f"JOIN {code}"), now + 5)
    M.deliver_webhook(api, world.a, later)
    assert _current_notices(admin_engine, guardian) == [
        {"status": "opted_in", "method": "whatsapp_join"}
    ]


def test_YM_03_FR_MSG_020_a_signed_delivery_older_than_metas_retry_period_is_dropped(
    world: Any, api: Any, admin_engine: Engine, fake_state: Any
) -> None:
    """Meta retries a delivery for at most 7 days. A signed message or status older than
    ``inbox.max_event_age_hours`` can only be a replay of a captured body after the guard's
    replay window and the inbox's dedupe have both forgotten it: acknowledged, never stored
    (a replayed STOP would undo a later signed-form opt-in; FR-MSG-010, SEC-034)."""
    _, waba = M.connect(world.a)
    _, guardian, mobile = M.guardian(world.a)
    assert mobile is not None
    assert M.opt_in(api, world.person("office_staff"), guardian, "notices").status_code == 201
    max_age = load_config().inbox.max_event_age_hours
    stale = int(time.time()) - max_age * 3600 - 60
    old = _with_timestamp(M.inbound(waba, "91" + mobile, text_body="STOP"), stale)
    assert M.post(api.client, old).status_code == 200
    service.process_inbox(world.a.tenant_id)
    assert _stops(admin_engine, guardian) == []
    # A Meta retry inside the period is applied as usual.
    recent = int(time.time()) - 6 * 24 * 3600
    M.deliver_webhook(
        api, world.a, _with_timestamp(M.inbound(waba, "91" + mobile, text_body="STOP"), recent)
    )
    assert _stops(admin_engine, guardian) == ["attendance", "fees", "notices"]
