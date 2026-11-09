"""Opt-in coverage, reach follow-up and the urgent closure message (FP-01, FP-02;
FR-MSG-039..055; ADR-0043 amendment of 2026-11-09, lead decision D85).

Synthetic data only: the separate synthetic sending school (``send_school``), guardians with
synthetic 90xxxxxxxx numbers, the fake provider (no network). Times are pinned with
``S.at(...)`` (IST).
"""

from __future__ import annotations

import datetime as dt
import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.messaging.config import load_config
from app.messaging.providers.fake import STATE
from app.messaging.sends import rules
from app.messaging.sends import service as sends

M = sys.modules["sos_test_messaging_support"]
S = sys.modules["sos_test_messaging_sends_support"]
W = sys.modules["sos_test_api_world"]
IST = rules.IST


# --- pure rules (no database) --------------------------------------------------------------------


def _ist(day: int, hour: int, minute: int = 0) -> dt.datetime:
    return dt.datetime(2026, 11, day, hour, minute, tzinfo=IST)


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (_ist(10, 5, 59), _ist(10, 6, 0)),  # before the window: today's opening
        (_ist(10, 6, 0), None),  # opening, inside the school's quiet hours (21:00-07:00)
        (_ist(10, 6, 30), None),
        (_ist(10, 21, 0), None),  # notices are quiet from 21:00, closures until 21:30
        (_ist(10, 21, 29), None),
        (_ist(10, 21, 30), _ist(11, 6, 0)),  # never outside the window
        (_ist(10, 23, 45), _ist(11, 6, 0)),
        (_ist(10, 0, 30), _ist(10, 6, 0)),
    ],
)
def test_FR_MSG_050_closure_window(now: dt.datetime, expected: dt.datetime | None) -> None:
    cfg = load_config().closures
    assert (cfg.window_start, cfg.window_end) == (dt.time(6, 0), dt.time(21, 30))  # D85
    got = rules.closure_next_open(now, window_start=cfg.window_start, window_end=cfg.window_end)
    assert got == expected


def test_FR_MSG_045_not_opened_after_the_configured_hours() -> None:
    sent = _ist(10, 9)
    hours = load_config().reach.not_opened_hours
    assert hours == 48
    assert not rules.not_opened(sent, sent + dt.timedelta(hours=47), hours=hours, opened=False)
    assert rules.not_opened(sent, sent + dt.timedelta(hours=48), hours=hours, opened=False)
    assert not rules.not_opened(sent, sent + dt.timedelta(hours=99), hours=hours, opened=True)
    assert not rules.not_opened(None, sent, hours=hours, opened=False)


def test_FR_MSG_047_closure_template_is_utility_without_buttons() -> None:
    from app.messaging import catalogue as cat

    item = cat.template("school_closure")
    assert item.category == "utility"
    assert item.optin_category == "notices"
    assert item.buttons == ()
    assert [v.name for v in item.variables] == ["school_name", "dates", "reason"]
    assert set(load_config().closures.reasons) == {
        "weather",
        "government_order",
        "local_situation",
        "other",
    }


# --- FP-01 coverage and not-reached lists (database) ---------------------------------------------


@pytest.mark.db
def test_FR_MSG_039_coverage_per_section_and_scope(send_school: Any, api: Any) -> None:
    school = send_school
    _, _, mobile = S.opted_guardian(api, school, "notices")
    M.guardian(school)  # a student whose guardian never opted in
    res = api.call(school.people["office_admin"], "GET", "/api/v1/messaging/coverage")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["category"] == "notices"
    nine_a = next(s for s in body["sections"] if s["label"] == "IX-A")
    assert nine_a["students_reached"] >= 1
    assert nine_a["students"] > nine_a["students_reached"]
    assert nine_a["guardians_opted_in"] >= 1
    assert body["students"] >= nine_a["students"]
    assert mobile not in res.text
    fees = api.call(
        school.people["office_admin"], "GET", "/api/v1/messaging/coverage?category=fees"
    )
    assert fees.status_code == 200
    assert fees.json()["category"] == "fees"
    # A class teacher (9A) sees their section only.
    own = api.call(school.people["class_teacher"], "GET", "/api/v1/messaging/coverage")
    assert own.status_code == 200, own.text
    assert [s["label"] for s in own.json()["sections"]] == ["IX-A"]
    # No message.read: 403.
    assert (
        api.call(school.people["teacher"], "GET", "/api/v1/messaging/coverage").status_code == 403
    )


@pytest.mark.db
def test_FR_MSG_041_not_reached_list_names_reasons_no_phones(
    world: Any, send_school: Any, api: Any
) -> None:
    school = send_school
    opted_student, _, opted_mobile = S.opted_guardian(api, school, "notices")
    missing_student, missing_guardian, missing_mobile = M.guardian(school)
    path = f"/api/v1/sections/{school.ids['section_9a']}/optins/not-reached"
    res = api.call(school.people["office_admin"], "GET", path)
    assert res.status_code == 200, res.text
    body = res.json()
    listed = {i["student_id"]: i for i in body["items"]}
    assert str(missing_student) in listed
    assert str(opted_student) not in listed
    item = listed[str(missing_student)]
    assert item["name"]  # the office reads student names
    assert item["guardians"][0]["guardian_id"] == str(missing_guardian)
    assert item["guardians"][0]["reason"] == "no_optin"
    for number in (opted_mobile, missing_mobile):
        assert number is not None
        assert number not in res.text
    # The class teacher of 9A reads it; 9B is outside their scope (404).
    assert api.call(school.people["class_teacher"], "GET", path).status_code == 200
    other_section = f"/api/v1/sections/{school.ids['section_9c']}/optins/not-reached"
    assert api.call(school.people["class_teacher"], "GET", other_section).status_code == 404
    assert api.call(school.people["teacher"], "GET", path).status_code == 403
    # Another school's member: the section does not exist for them.
    assert api.call(world.person("office_admin"), "GET", path).status_code == 404
    # FR-MSG-043: join slips for exactly the missing students (existing route, reused).
    slips = api.call(
        school.people["office_staff"],
        "POST",
        "/api/v1/messaging/join-slips",
        json={"student_ids": [str(missing_student)], "categories": ["notices"]},
    )
    assert slips.status_code == 200, slips.text

    # ... or the section's print link with missing_only: the reached family gets no slip.
    def section_slips(missing_only: bool) -> str:
        res = api.call(
            school.people["office_staff"],
            "GET",
            f"/api/v1/sections/{school.ids['section_9a']}/join-slips",
            params={"categories": ["notices"], "missing_only": str(missing_only).lower()},
        )
        assert res.status_code == 200, res.text
        return str(res.text)

    missing_page, full_page = section_slips(True), section_slips(False)
    assert item["name"] in missing_page
    marker = '<section class="slip"'
    assert missing_page.count(marker) < full_page.count(marker)


def _broadcast(api: Any, school: Any) -> tuple[str, uuid.UUID]:
    notice_id = S.notice(school)
    preview = api.call(
        school.people["office_admin"],
        "POST",
        f"/api/v1/notices/{notice_id}/broadcasts/preview",
        json={"section_ids": [str(school.ids["section_9a"])]},
    ).json()
    out = api.call(
        school.people["office_admin"],
        "POST",
        f"/api/v1/notices/{notice_id}/broadcasts",
        json={
            "preview_token": preview["preview_token"],
            "section_ids": [str(school.ids["section_9a"])],
        },
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert out.status_code == 202, out.text
    return str(out.json()["id"]), notice_id


@pytest.mark.db
def test_FR_MSG_044_broadcast_follow_up_lists_who_was_not_reached(
    world: Any, send_school: Any, api: Any, admin_engine: Engine, fake_state: Any
) -> None:
    school = send_school
    S.settings(school)
    sends.send_due(school.tenant_id, now=S.at(12, days=1))
    unread_student, unread_guardian, mobile = S.opted_guardian(api, school, "notices")
    read_student, read_guardian, _ = S.opted_guardian(api, school, "notices")
    never_student, _, _ = M.guardian(school)
    broadcast_id, _ = _broadcast(api, school)
    sends.send_due(school.tenant_id, now=S.at(12))
    with admin_engine.begin() as c:
        # Two days later: one parent read it, the other never opened it.
        c.execute(
            text(
                "UPDATE msg.messages SET sent_at = now() - interval '49 hours' "
                "WHERE broadcast_id = :b AND status = 'sent'"
            ),
            {"b": broadcast_id},
        )
        c.execute(
            text(
                "UPDATE msg.messages SET status = 'read', read_at = now() "
                "WHERE broadcast_id = :b AND guardian_id = :g"
            ),
            {"b": broadcast_id, "g": read_guardian},
        )
    path = f"/api/v1/messaging/broadcasts/{broadcast_id}/follow-up"
    res = api.call(school.people["principal"], "GET", path)
    assert res.status_code == 200, res.text
    body = res.json()
    outcome = {i["student_id"]: i["outcome"] for i in body["items"]}
    assert outcome[str(unread_student)] == "not_opened"
    assert outcome[str(never_student)] == "not_sent"
    assert str(read_student) not in outcome
    assert body["outcomes"]["opened"] >= 1
    assert body["not_opened_hours"] == 48
    unread = next(i for i in body["items"] if i["student_id"] == str(unread_student))
    assert unread["guardians"][0]["guardian_id"] == str(unread_guardian)
    assert unread["guardians"][0]["reason"] == "sent"
    assert mobile not in res.text
    # Nothing was resent by reading the list.
    assert len([m for m in STATE.sent if m["to"] == "91" + mobile]) == 1
    assert api.call(school.people["class_teacher"], "GET", path).status_code == 200
    assert api.call(school.people["teacher"], "GET", path).status_code == 403
    assert api.call(world.person("office_admin"), "GET", path).status_code == 404


# --- FP-02 urgent closure message ----------------------------------------------------------------

PREVIEW = "/api/v1/messaging/closures/preview"
SEND = "/api/v1/messaging/closures"


def _closure_body(days: int = 1, **extra: Any) -> dict[str, Any]:
    day = (S.today() + dt.timedelta(days=days)).isoformat()
    return {"whole_school": True, "closed_from": day, "reason": "weather", **extra}


def _send(api: Any, who: Any, body: dict[str, Any], **kw: Any) -> Any:
    return api.call(
        who, "POST", SEND, json=body, headers={"Idempotency-Key": str(uuid.uuid4())}, **kw
    )


@pytest.mark.db
def test_FR_MSG_048_closure_authz_step_up_and_other_school(
    world: Any, send_school: Any, api: Any
) -> None:
    school = send_school
    body = _closure_body()
    for role in ("owner", "office_admin", "office_staff", "accountant", "teacher", "class_teacher"):
        assert api.call(school.people[role], "POST", PREVIEW, json=body).status_code == 403, role
    preview = api.call(school.people["principal"], "POST", PREVIEW, json=body)
    assert preview.status_code == 200, preview.text
    token = preview.json()["preview_token"]
    # Sending needs step-up MFA (428 with an old sign-in) and the principal's permission.
    stale = _send(api, school.people["principal"], {**body, "preview_token": token}, auth_age_s=900)
    assert stale.status_code == 428
    assert (
        _send(api, school.people["office_admin"], {**body, "preview_token": token}).status_code
        == 403
    )
    # Another school's principal: their own school has no such preview (stale token).
    other = _send(api, world.person("principal"), {**body, "preview_token": token})
    assert other.status_code in (404, 409, 422)
    assert api.call(school.people["teacher"], "GET", SEND).status_code == 403


@pytest.mark.db
def test_FR_MSG_049_closure_preview_send_window_and_audit(
    send_school: Any, api: Any, admin_engine: Engine, fake_state: Any
) -> None:
    school = send_school
    S.settings(school)
    sends.send_due(school.tenant_id, now=S.at(12, days=1))
    _, guardian, mobile = S.opted_guardian(api, school, "notices")
    _, fees_guardian, _ = S.opted_guardian(api, school, "fees")
    body = _closure_body()
    preview = api.call(school.people["principal"], "POST", PREVIEW, json=body)
    assert preview.status_code == 200, preview.text
    p = preview.json()
    assert p["recipients"] >= 1
    assert p["skipped"].get("no_optin", 0) >= 1
    assert p["blocked_reason"] is None
    assert "will be closed on" in p["message_en"]
    assert "heavy rain or a storm warning" in p["message_en"]
    assert (p["window_start"], p["window_end"]) == ("06:00:00", "21:30:00")
    assert preview.headers["Cache-Control"] == "no-store"

    sent = _send(api, school.people["principal"], {**body, "preview_token": p["preview_token"]})
    assert sent.status_code == 202, sent.text
    closure = sent.json()
    assert closure["queued"] == p["recipients"]
    assert closure["reason"] == "weather"
    rows = S.message_rows(admin_engine, guardian_id=guardian, purpose="school_closure")
    assert len(rows) == 1
    assert rows[0]["source_id"] == uuid.UUID(closure["id"])
    assert rows[0]["source_type"] == "school_closure"
    assert S.message_rows(admin_engine, purpose="school_closure", guardian_id=fees_guardian) == []
    # Audited in the same transaction, without a phone number.
    events = W.audit_events(admin_engine, school.tenant_id, "messaging.closure.sent")
    event = next(e for e in events if e["resource_id"] == uuid.UUID(closure["id"]))
    assert event["summary"]["reason"] == "weather"
    assert mobile not in str(event["summary"])

    # 22:00: after the window (notices' quiet hours too) -> waits for 06:00.
    out = sends.send_due(school.tenant_id, now=S.at(22, 0))
    assert out.get("deferred", 0) >= 1
    row = S.message_rows(admin_engine, guardian_id=guardian, purpose="school_closure")[0]
    assert row["status"] == "queued"
    assert row["next_attempt_at"].astimezone(IST) == S.at(6, 0, days=1)
    # 06:30 the next morning: inside the school's quiet hours (until 07:00), still sent.
    sends.send_due(school.tenant_id, now=S.at(6, 30, days=1))
    row = S.message_rows(admin_engine, guardian_id=guardian, purpose="school_closure")[0]
    assert row["status"] == "sent"
    msg = [m for m in STATE.sent if m["to"] == "91" + mobile]
    assert msg[-1]["name"].startswith("sos_school_closure_v1_")
    assert not msg[-1].get("url_suffix")

    listing = api.call(school.people["principal"], "GET", SEND)
    assert listing.status_code == 200, listing.text
    page = listing.json()
    assert page["uses"] >= 1
    assert page["items"][0]["id"] == closure["id"]
    assert page["items"][0]["counts"].get("sent", 0) >= 1
    assert mobile not in listing.text
    # A section-scoped reader does not get the school-wide list.
    assert api.call(school.people["class_teacher"], "GET", SEND).status_code == 403


@pytest.mark.db
def test_FR_MSG_052_closure_dates_validated_and_stale_preview(send_school: Any, api: Any) -> None:
    school = send_school
    principal = school.people["principal"]
    past = api.call(principal, "POST", PREVIEW, json=_closure_body(days=-1))
    assert past.status_code == 422
    assert past.json()["errors"][0]["code"] == "in_past"
    far = api.call(principal, "POST", PREVIEW, json=_closure_body(days=30))
    assert far.status_code == 422
    long = _closure_body(closed_to=(S.today() + dt.timedelta(days=20)).isoformat())
    assert api.call(principal, "POST", PREVIEW, json=long).status_code == 422
    both = _closure_body(section_ids=[str(school.ids["section_9a"])])
    assert api.call(principal, "POST", PREVIEW, json=both).status_code == 422
    sections = _closure_body(whole_school=False, section_ids=[str(school.ids["section_9a"])])
    preview = api.call(principal, "POST", PREVIEW, json=sections)
    assert preview.status_code == 200, preview.text
    S.opted_guardian(api, school, "notices")  # the audience changed since the preview
    stale = _send(api, principal, {**sections, "preview_token": preview.json()["preview_token"]})
    assert stale.status_code == 409
    assert stale.json()["code"] == "preview_stale"


@pytest.mark.db
def test_FR_MSG_050_closure_after_its_last_day_is_too_late(
    send_school: Any, api: Any, admin_engine: Engine, fake_state: Any
) -> None:
    school = send_school
    S.settings(school)
    sends.send_due(school.tenant_id, now=S.at(12, days=1))
    _, guardian, _ = S.opted_guardian(api, school, "notices")
    body = _closure_body(days=0)
    p = api.call(school.people["principal"], "POST", PREVIEW, json=body).json()
    assert (
        _send(
            api, school.people["principal"], {**body, "preview_token": p["preview_token"]}
        ).status_code
        == 202
    )
    # Nothing went out today (say the account was paused); tomorrow the closure is over.
    sends.send_due(school.tenant_id, now=S.at(7, 0, days=1))
    row = S.message_rows(admin_engine, guardian_id=guardian, purpose="school_closure")[-1]
    assert (row["status"], row["skip_reason"]) == ("skipped", "too_late")


@pytest.mark.db
def test_FR_MSG_051_notices_off_blocks_closures(send_school: Any, api: Any) -> None:
    school = send_school
    S.settings(school, notices_enabled=False)
    try:
        body = _closure_body()
        p = api.call(school.people["principal"], "POST", PREVIEW, json=body).json()
        assert p["blocked_reason"] == "notices_off"
        res = _send(api, school.people["principal"], {**body, "preview_token": p["preview_token"]})
        assert res.status_code == 409
        assert res.json()["code"] == "notices_off"
    finally:
        S.settings(school)
