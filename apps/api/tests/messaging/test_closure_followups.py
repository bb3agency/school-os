"""FP-02 follow-ups: the closure message's read-only parent page and "the school reopens"
message (FR-MSG-056..065, FR-PUB-013..016; ADR-0043 amendment of 2026-11-12, ADR-0046
amendment; lead decision D86, PO to confirm).

Synthetic data only: the separate synthetic sending school (``send_school``), guardians with
synthetic 90xxxxxxxx numbers, the fake provider (no network). Public routes are called the way
the BFF calls them (service token, the link token in ``X-SOS-Public-Token``, no user token).
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import sys
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy import Engine

from app.core.db import tenant_session
from app.messaging import catalogue as cat
from app.messaging.config import load_config
from app.messaging.providers.fake import STATE
from app.messaging.sends import rules
from app.messaging.sends import service as sends
from app.public import service as public
from app.public.config import rules as public_rules

S = sys.modules["sos_test_messaging_sends_support"]
M = sys.modules["sos_test_messaging_support"]
W = sys.modules["sos_test_api_world"]
IST = rules.IST


def _load(name: str, path: Path) -> ModuleType:
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


P = _load("sos_test_public_support", Path(__file__).resolve().parents[1] / "public" / "support.py")

PREVIEW = "/api/v1/messaging/closures/preview"
SEND = "/api/v1/messaging/closures"
ORDER = "As per the District Collector's order, all schools stay closed. Stay safe indoors."
# Keys a parent response must never carry (ADR-0046 decision 2; invariants 4 and 5).
FORBIDDEN_KEYS = {
    "phone", "mobile", "guardian_id", "guardian_name", "student_id", "tenant_id", "handle_id",
    "section_ids", "sent_by", "recipients", "skipped", "email", "name",
}  # fmt: skip


def _reopen_paths(closure_id: str) -> tuple[str, str]:
    base = f"/api/v1/messaging/closures/{closure_id}/reopening"
    return base + "/preview", base


def _day(days: int) -> str:
    return (S.today() + dt.timedelta(days=days)).isoformat()


def _idem() -> dict[str, str]:
    return {"Idempotency-Key": str(uuid.uuid4())}


def _closure(api: Any, school: Any, *, days: int = 1, **extra: Any) -> dict[str, Any]:
    body = {"whole_school": True, "closed_from": _day(days), "reason": "weather", **extra}
    principal = school.people["principal"]
    preview = api.call(principal, "POST", PREVIEW, json=body)
    assert preview.status_code == 200, preview.text
    sent = api.call(
        principal,
        "POST",
        SEND,
        json={**body, "preview_token": preview.json()["preview_token"]},
        headers=_idem(),
    )
    assert sent.status_code == 202, sent.text
    out: dict[str, Any] = sent.json()
    return out


def _keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in _keys(v)}
    if isinstance(value, list):
        return {k for v in value for k in _keys(v)}
    return set()


# --- pure ---------------------------------------------------------------------------------------


def test_FR_MSG_059_reopening_template_is_utility_with_date_only() -> None:
    item = cat.template("school_reopening")
    assert item.category == "utility"
    assert item.optin_category == "notices"
    assert item.buttons == ()
    assert [v.name for v in item.variables] == ["school_name", "date"]
    assert "reopen on {{2}}" in item.en.body
    assert load_config().closures.reopen_max_days_after == 7  # D86


def test_FR_PUB_014_closure_link_expires_seven_days_after_the_last_day() -> None:
    assert public_rules().lifetimes_days["parent_closure"] == 7  # D86
    last_day = dt.date(2026, 11, 20)
    expires = public.dated_link_expiry("parent_closure", last_day)
    assert expires == dt.datetime(2026, 11, 28, 0, 0, tzinfo=IST)
    with pytest.raises(ValueError, match="lifetime"):
        public.dated_link_expiry("parent_notice", last_day)


# --- the closure's parent page (database) -------------------------------------------------------


@pytest.mark.db
def test_FR_MSG_056_order_text_on_the_page_only_and_aadhaar_refused(
    send_school: Any, api: Any, admin_engine: Engine, fake_state: Any
) -> None:
    school = send_school
    principal = school.people["principal"]
    aadhaar = "Order no 2345 6789 0124 of today"  # Verhoeff-valid synthetic 12 digits
    bad = api.call(
        principal,
        "POST",
        PREVIEW,
        json={
            "whole_school": True,
            "closed_from": _day(1),
            "reason": "weather",
            "notice_text": aadhaar,
        },
    )
    assert bad.status_code == 422, bad.text
    assert bad.json()["errors"][0]["code"] == "aadhaar_full_number_rejected"
    S.opted_guardian(api, school, "notices")
    closure = _closure(api, school, notice_text=f"  {ORDER}  ")
    assert closure["notice_text"] == ORDER
    preview = api.call(
        principal,
        "POST",
        PREVIEW,
        json={"whole_school": True, "closed_from": _day(1), "reason": "weather"},
    ).json()
    assert ORDER not in preview["message_en"]
    events = W.audit_events(admin_engine, school.tenant_id, "messaging.closure.sent")
    event = next(e for e in events if e["resource_id"] == uuid.UUID(closure["id"]))
    assert event["summary"]["has_notice_text"] is True
    assert ORDER not in str(event["summary"])
    # The text never goes into the WhatsApp message variables.
    sends.send_due(school.tenant_id, now=S.at(12))
    assert all(ORDER not in " ".join(m["values"]) for m in STATE.sent)


@pytest.mark.db
def test_FR_PUB_013_closure_link_opens_a_read_only_page(
    world: Any, send_school: Any, api: Any, admin_engine: Engine, fake_state: Any
) -> None:
    school = send_school
    S.settings(school)
    _, guardian, mobile = S.opted_guardian(api, school, "notices")
    closure = _closure(api, school, days=1, notice_text=ORDER)
    sends.send_due(school.tenant_id, now=S.at(12))
    sent = [m for m in STATE.sent if m["to"] == "91" + mobile]
    assert sent[-1]["name"].startswith("sos_school_closure_v2_")
    key = sent[-1]["url_suffix"]
    assert key
    assert key not in " ".join(sent[-1]["values"])
    row = S.message_rows(admin_engine, guardian_id=guardian, purpose="school_closure")[-1]
    handle = S.handle(admin_engine, row["public_handle_id"])
    assert (handle["kind"], handle["target_type"], handle["target_id"]) == (
        "parent_closure",
        "school_closure",
        uuid.UUID(closure["id"]),
    )
    assert handle["guardian_id"] == guardian
    closed_to = dt.date.fromisoformat(closure["closed_to"])
    assert handle["expires_at"] == public.dated_link_expiry("parent_closure", closed_to)

    exchange = P.exchange(api.client, key)
    assert exchange.status_code == 200, exchange.text
    assert exchange.json()["kind"] == "parent_closure"
    page = P.current(api.client, key)
    assert page.status_code == 200, page.text
    body = page.json()
    assert body["kind"] == "parent_closure"
    assert body["school_name"]
    assert body["closure"] == {
        "closed_from": closure["closed_from"],
        "closed_to": closure["closed_to"],
        "reason": "heavy rain or a storm warning",
        "notice_text": ORDER,
        "reopens_on": None,
    }
    assert (body["notice"], body["receipt"], body["dues"], body["optin"]) == (None,) * 4
    assert not _keys(body) & FORBIDDEN_KEYS
    assert mobile not in page.text
    # Read-only: the link opens no document, payment or opt-in route (uniform 404).
    for method, path in (
        ("GET", "/api/v1/public/links/current/document"),
        ("GET", "/api/v1/public/links/current/payment"),
        ("POST", "/api/v1/public/links/current/optin"),
        ("POST", "/api/v1/public/links/current/pay"),
    ):
        res = P.public_call(api.client, method, path, token=key, body={})
        assert res.status_code in (404, 422), (path, res.status_code)
        assert res.status_code != 200
    # A user token is refused on the public route (FR-PUB-001).
    assert P.current(api.client, key, bearer="x").status_code == 401
    # The first view is audited, without the key.
    views = W.audit_events(admin_engine, school.tenant_id, "public.link.viewed")
    assert any(e["resource_id"] == row["public_handle_id"] for e in views)
    assert key not in " ".join(str(e["summary"]) for e in views)
    # A revoked link is the uniform 404.
    with tenant_session(school.tenant_id) as db:
        assert public.revoke(db, row["public_handle_id"], reason="test", by_user=False)
    assert P.current(api.client, key).status_code == 404


@pytest.mark.db
def test_FR_PUB_015_closure_link_of_a_wrong_target_is_404(world: Any, api: Any) -> None:
    # A parent_closure link pointing at no closure of this school: the same 404.
    _, guardian = P.student_with_guardian(world.a)
    issued = P.issue(world.a, "parent_closure", guardian_id=guardian, target_id=uuid.uuid4())
    assert issued.key is not None
    assert P.current(api.client, issued.key).status_code == 404


# --- "the school reopens" (database) ------------------------------------------------------------


@pytest.mark.db
def test_FR_MSG_060_reopening_authz_step_up_and_other_school(
    world: Any, send_school: Any, api: Any
) -> None:
    school = send_school
    closure = _closure(api, school, days=0)
    preview_path, send_path = _reopen_paths(closure["id"])
    body = {"reopens_on": _day(1)}
    for role in ("owner", "office_admin", "office_staff", "accountant", "teacher", "class_teacher"):
        res = api.call(school.people[role], "POST", preview_path, json=body)
        assert res.status_code == 403, role
    preview = api.call(school.people["principal"], "POST", preview_path, json=body)
    assert preview.status_code == 200, preview.text
    token = preview.json()["preview_token"]
    stale = api.call(
        school.people["principal"],
        "POST",
        send_path,
        json={**body, "preview_token": token},
        headers=_idem(),
        auth_age_s=900,
    )
    assert stale.status_code == 428
    # Another school's principal: the closure does not exist for them.
    other = api.call(world.person("principal"), "POST", preview_path, json=body)
    assert other.status_code == 404
    missing, _ = _reopen_paths(str(uuid.uuid4()))
    assert api.call(school.people["principal"], "POST", missing, json=body).status_code == 404


@pytest.mark.db
def test_FR_MSG_061_reopening_preview_send_page_and_audit(
    send_school: Any, api: Any, admin_engine: Engine, fake_state: Any
) -> None:
    school = send_school
    S.settings(school)
    _, guardian, mobile = S.opted_guardian(api, school, "notices")
    _, fees_guardian, _ = S.opted_guardian(api, school, "fees")
    closure = _closure(api, school, days=0)
    sends.send_due(school.tenant_id, now=S.at(12))
    key = [m for m in STATE.sent if m["to"] == "91" + mobile][-1]["url_suffix"]
    preview_path, send_path = _reopen_paths(closure["id"])
    body = {"reopens_on": _day(1)}
    preview = api.call(school.people["principal"], "POST", preview_path, json=body)
    assert preview.status_code == 200, preview.text
    p = preview.json()
    assert p["recipients"] >= 1
    assert p["blocked_reason"] is None
    assert "will reopen on" in p["message_en"]
    assert preview.headers["Cache-Control"] == "no-store"
    sent = api.call(
        school.people["principal"],
        "POST",
        send_path,
        json={**body, "preview_token": p["preview_token"]},
        headers=_idem(),
    )
    assert sent.status_code == 202, sent.text
    reopening = sent.json()
    assert reopening["queued"] == p["recipients"]
    assert reopening["closure_id"] == closure["id"]
    rows = S.message_rows(admin_engine, guardian_id=guardian, purpose="school_reopening")
    assert len(rows) == 1
    assert rows[0]["source_id"] == uuid.UUID(reopening["id"])
    assert rows[0]["source_type"] == "school_reopening"
    assert S.message_rows(admin_engine, purpose="school_reopening", guardian_id=fees_guardian) == []
    events = W.audit_events(admin_engine, school.tenant_id, "messaging.closure.reopening_sent")
    event = next(e for e in events if e["resource_id"] == uuid.UUID(closure["id"]))
    assert event["summary"]["reopens_on"] == body["reopens_on"]
    assert mobile not in str(event["summary"])
    # Same window as closures: 22:00 waits for 06:00, then goes out inside quiet hours.
    sends.send_due(school.tenant_id, now=S.at(22, 0))
    row = S.message_rows(admin_engine, guardian_id=guardian, purpose="school_reopening")[0]
    assert row["status"] == "queued"
    assert row["next_attempt_at"].astimezone(IST) == S.at(6, 0, days=1)
    sends.send_due(school.tenant_id, now=S.at(6, 30, days=1))
    row = S.message_rows(admin_engine, guardian_id=guardian, purpose="school_reopening")[0]
    assert row["status"] == "sent"
    msg = [m for m in STATE.sent if m["to"] == "91" + mobile]
    assert msg[-1]["name"].startswith("sos_school_reopening_v1_")
    assert not msg[-1].get("url_suffix")
    # The closure page now says when the school reopens.
    page = P.current(api.client, key)
    assert page.status_code == 200, page.text
    assert page.json()["closure"]["reopens_on"] == body["reopens_on"]
    # One reopening message per closure.
    again = api.call(school.people["principal"], "POST", preview_path, json=body)
    assert again.status_code == 409
    assert again.json()["code"] == "reopening_already_sent"
    listing = api.call(school.people["principal"], "GET", SEND).json()
    item = next(i for i in listing["items"] if i["id"] == closure["id"])
    assert item["reopening"]["id"] == reopening["id"]
    assert item["reopening"]["counts"].get("sent", 0) >= 1


@pytest.mark.db
def test_FR_MSG_060_reopening_dates_and_stale_preview(send_school: Any, api: Any) -> None:
    school = send_school
    principal = school.people["principal"]
    closure = _closure(api, school, days=1)  # closed tomorrow only
    preview_path, send_path = _reopen_paths(closure["id"])

    def code(reopens_on: str) -> str:
        res = api.call(principal, "POST", preview_path, json={"reopens_on": reopens_on})
        assert res.status_code == 422, res.text
        return str(res.json()["errors"][0]["code"])

    assert code(_day(-1)) == "in_past"
    assert code(_day(1)) == "not_after_closure_start"
    assert code(_day(30)) == "too_far_ahead"
    cfg = load_config().closures
    if cfg.reopen_max_days_after + 2 <= cfg.max_days_ahead:
        assert code(_day(cfg.reopen_max_days_after + 3)) == "too_long_after_closure"
    preview = api.call(principal, "POST", preview_path, json={"reopens_on": _day(2)})
    assert preview.status_code == 200, preview.text
    S.opted_guardian(api, school, "notices")  # the audience changed since the preview
    stale = api.call(
        principal,
        "POST",
        send_path,
        json={"reopens_on": _day(2), "preview_token": preview.json()["preview_token"]},
        headers=_idem(),
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "preview_stale"


@pytest.mark.db
def test_FR_MSG_063_reopening_after_its_day_is_too_late(
    send_school: Any, api: Any, admin_engine: Engine, fake_state: Any
) -> None:
    school = send_school
    S.settings(school)
    sends.send_due(school.tenant_id, now=S.at(12, days=1))
    _, guardian, _ = S.opted_guardian(api, school, "notices")
    closure = _closure(api, school, days=0)
    preview_path, send_path = _reopen_paths(closure["id"])
    body = {"reopens_on": _day(1)}
    p = api.call(school.people["principal"], "POST", preview_path, json=body).json()
    res = api.call(
        school.people["principal"],
        "POST",
        send_path,
        json={**body, "preview_token": p["preview_token"]},
        headers=_idem(),
    )
    assert res.status_code == 202, res.text
    # Nothing went out (say the account was paused) until the day after the reopening.
    sends.send_due(school.tenant_id, now=S.at(7, 0, days=2))
    row = S.message_rows(admin_engine, guardian_id=guardian, purpose="school_reopening")[-1]
    assert (row["status"], row["skip_reason"]) == ("skipped", "too_late")


@pytest.mark.db
def test_SEC_008_reopening_logs_no_phone_or_token(
    send_school: Any, api: Any, capsys: pytest.CaptureFixture[str], fake_state: Any
) -> None:
    school = send_school
    S.settings(school)
    _, _, mobile = S.opted_guardian(api, school, "notices")
    capsys.readouterr()
    _closure(api, school, days=0, notice_text=ORDER)
    sends.send_due(school.tenant_id, now=S.at(12))
    key = [m for m in STATE.sent if m["to"] == "91" + mobile][-1]["url_suffix"]
    P.current(api.client, key)
    logs = "".join(capsys.readouterr())
    for secret in (mobile, "91" + mobile, key, ORDER):
        assert secret not in logs
