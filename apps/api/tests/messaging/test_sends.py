"""WhatsApp sending (R2-3G; ADR-0043 decisions 3, 10, 11; ADR-0046; owner decisions D25, D27,
D31; US-2204..US-2208; FR-MSG-011..017, FR-MSG-022, FR-PUB-005, FR-PUB-007).

Synthetic data only: a separate synthetic school (``send_school``), guardians with synthetic
90xxxxxxxx numbers, the fake provider (no network). Times are pinned with ``S.at(...)`` (IST).
"""

from __future__ import annotations

import datetime as dt
import sys
import uuid
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.academics import service as academics
from app.academics.schemas import AttendanceEntryIn, AttendanceWrite
from app.core.db import tenant_session
from app.fees import service as fees
from app.fees.schemas import FeeSettingsIn
from app.messaging import service as messaging
from app.messaging.providers.fake import STATE
from app.messaging.sends import service as sends
from app.public import service as public

pytestmark = pytest.mark.db
M = sys.modules["sos_test_messaging_support"]
S = sys.modules["sos_test_messaging_sends_support"]
F = sys.modules["sos_test_fees_support"]
W = sys.modules["sos_test_api_world"]


def _sent_to(mobile: str) -> list[dict[str, Any]]:
    return [m for m in STATE.sent if m["to"] == "91" + mobile]


def _preview(api: Any, school: Any, notice_id: uuid.UUID, *, resend: bool = False) -> Any:
    return api.call(
        school.people["office_admin"],
        "POST",
        f"/api/v1/notices/{notice_id}/broadcasts/preview",
        json={"section_ids": [str(school.ids["section_9a"])], "resend": resend},
    )


def _confirm(api: Any, school: Any, notice_id: uuid.UUID, token: str, **extra: Any) -> Any:
    return api.call(
        school.people["office_admin"],
        "POST",
        f"/api/v1/notices/{notice_id}/broadcasts",
        json={"preview_token": token, "section_ids": [str(school.ids["section_9a"])], **extra},
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )


def _reset(school: Any) -> None:
    """Send everything left queued by earlier tests at a safe time, so each test starts clean."""
    S.settings(school)
    sends.send_due(school.tenant_id, now=S.at(12, days=1))


# --- notice broadcasts ---------------------------------------------------------------------------


def test_US_2204_preview_confirm_send_and_report(  # noqa: PLR0915 - one end-to-end journey
    send_school: Any, api: Any, admin_engine: Engine, fake_state: Any
) -> None:
    school = send_school
    _reset(school)
    _, opted, mobile = S.opted_guardian(api, school, "notices")
    _, _, other_mobile = S.opted_guardian(api, school, "fees")  # not opted in for notices
    notice_id = S.notice(school)

    res = _preview(api, school, notice_id)
    assert res.status_code == 200, res.text
    preview = res.json()
    assert preview["recipients"] >= 1
    assert preview["skipped"].get("no_optin", 0) >= 1
    assert preview["blocked_reason"] is None
    assert preview["languages"].get("en", 0) == preview["recipients"]
    # FR-MSG-012 / D27: rate x count x (1 + GST), from config.yaml.
    expected = (
        Decimal(preview["recipients"])
        * Decimal(preview["utility_rate_inr"])
        * (1 + Decimal(preview["gst_percent"]) / 100)
    ).quantize(Decimal("0.01"))
    assert Decimal(preview["estimated_cost_inr"]) == expected
    assert res.headers["Cache-Control"] == "no-store"

    confirmed = _confirm(api, school, notice_id, preview["preview_token"])
    assert confirmed.status_code == 202, confirmed.text
    broadcast = confirmed.json()
    assert broadcast["status"] == "sending"
    assert broadcast["queued"] == preview["recipients"]
    # Same preview again: refused (one confirmation per preview).
    again = _confirm(api, school, notice_id, preview["preview_token"])
    assert again.status_code == 409
    assert again.json()["code"] == "preview_invalid"

    out = sends.send_due(school.tenant_id, now=S.at(12))
    assert out.get("sent", 0) >= 1
    sent = _sent_to(mobile)
    assert len(sent) == 1
    assert sent[0]["name"].startswith("sos_school_notice_v2_")
    assert sent[0]["url_suffix"]  # the parent token, in the URL button only
    assert sent[0]["url_suffix"] not in " ".join(sent[0]["values"])
    assert _sent_to(other_mobile) == []

    row = S.message_rows(admin_engine, guardian_id=opted, purpose="school_notice")[0]
    assert row["broadcast_id"] == uuid.UUID(broadcast["id"])
    assert row["status"] == "sent"
    h = S.handle(admin_engine, row["public_handle_id"])
    # The R2-3H contract: parent_notice handles target the notice (target_type parent_notice).
    assert (h["kind"], h["target_type"], h["target_id"]) == (
        "parent_notice",
        "parent_notice",
        notice_id,
    )
    assert h["guardian_id"] == opted
    resolved = public.resolve("parent_notice", sent[0]["url_suffix"])
    assert resolved is not None
    assert resolved.handle_id == row["public_handle_id"]

    report = api.call(
        school.people["office_admin"], "GET", f"/api/v1/messaging/broadcasts/{broadcast['id']}"
    )
    assert report.status_code == 200, report.text
    body = report.json()
    assert body["broadcast"]["status"] == "done"
    assert body["broadcast"]["counts"]["sent"] == preview["recipients"]
    assert any(s["label"] == "IX-A" for s in body["sections"])
    assert mobile not in report.text
    listing = api.call(school.people["principal"], "GET", "/api/v1/messaging/broadcasts")
    assert listing.status_code == 200
    assert listing.json()["items"][0]["id"] == broadcast["id"]
    assert listing.json()["items"][0]["notice_title"] == "Sports day"
    per_notice = api.call(
        school.people["principal"], "GET", f"/api/v1/notices/{notice_id}/broadcasts"
    )
    assert [b["id"] for b in per_notice.json()["items"]] == [broadcast["id"]]
    events = W.audit_events(admin_engine, school.tenant_id, "messaging.broadcast.sent")
    assert any(e["resource_id"] == notice_id for e in events)
    assert mobile not in " ".join(str(e["summary"]) for e in events)

    # US-2204 AC3: once per guardian unless "Send again" with a reason.
    second = _preview(api, school, notice_id).json()
    assert second["skipped"].get("already_sent", 0) >= 1
    assert second["already_sent"] >= 1
    resend = _preview(api, school, notice_id, resend=True).json()
    assert resend["already_sent"] == 0
    no_reason = _confirm(api, school, notice_id, resend["preview_token"])
    assert no_reason.status_code == 422
    with_reason = _confirm(
        api,
        school,
        notice_id,
        resend["preview_token"],
        resend_reason="The first message went out with the wrong date",
    )
    assert with_reason.status_code == 202, with_reason.text
    sends.send_due(school.tenant_id, now=S.at(12))
    assert len(_sent_to(mobile)) == 2


def test_FR_MSG_012_stale_preview_and_refusals(
    send_school: Any, api: Any, admin_engine: Engine
) -> None:
    school = send_school
    _reset(school)
    S.opted_guardian(api, school, "notices")
    notice_id = S.notice(school)
    preview = _preview(api, school, notice_id).json()
    S.opted_guardian(api, school, "notices")  # the audience changed after the preview
    stale = _confirm(api, school, notice_id, preview["preview_token"])
    assert stale.status_code == 409
    assert stale.json()["code"] == "preview_stale"

    # A draft notice cannot be sent.
    draft_id, _ = S.C.complete_notice(school, approved=False)
    assert _preview(api, school, draft_id).json()["code"] == "notice_not_approved"
    # Sections of another year or school: 422; unknown notice: 404.
    bad = api.call(
        school.people["office_admin"],
        "POST",
        f"/api/v1/notices/{notice_id}/broadcasts/preview",
        json={"section_ids": [str(uuid.uuid4())]},
    )
    assert bad.status_code == 422
    assert _preview(api, school, uuid.uuid4()).status_code == 404

    # The switch: notices off blocks the confirmation (and the preview says why).
    S.settings(school, notices_enabled=False)
    blocked = _preview(api, school, notice_id).json()
    assert blocked["blocked_reason"] == "notices_off"
    refused = _confirm(api, school, notice_id, blocked["preview_token"])
    assert refused.status_code == 409
    assert refused.json()["code"] == "notices_off"
    S.settings(school)


def test_FR_MSG_012_authz_and_other_school(world: Any, send_school: Any, api: Any) -> None:
    school = send_school
    notice_id = S.notice(school)
    body = {"section_ids": [str(school.ids["section_9a"])]}
    path = f"/api/v1/notices/{notice_id}/broadcasts/preview"
    for role in ("office_staff", "accountant", "teacher", "class_teacher", "auditor_readonly"):
        assert api.call(school.people[role], "POST", path, json=body).status_code == 403, role
    assert api.call(school.people["principal"], "POST", path, json=body).status_code == 200
    # Another school's member: the notice does not exist for them.
    other = world.person("office_admin")
    assert api.call(other, "POST", path, json=body).status_code in (404, 422)
    reports = api.call(school.people["class_teacher"], "GET", "/api/v1/messaging/broadcasts")
    assert reports.status_code == 403


# --- the sender: quiet hours, cap, retries, suspension ------------------------------------------


def _queue_notice(api: Any, school: Any) -> tuple[uuid.UUID, str]:
    _, guardian, mobile = S.opted_guardian(api, school, "notices")
    notice_id = S.notice(school)
    preview = _preview(api, school, notice_id).json()
    assert _confirm(api, school, notice_id, preview["preview_token"]).status_code == 202
    return guardian, mobile


def test_FR_MSG_011_quiet_hours_wait_then_send(
    send_school: Any, api: Any, admin_engine: Engine, fake_state: Any
) -> None:
    school = send_school
    _reset(school)
    guardian, mobile = _queue_notice(api, school)
    out = sends.send_due(school.tenant_id, now=S.at(22, 30))
    assert out.get("deferred", 0) >= 1
    row = S.message_rows(admin_engine, guardian_id=guardian)[0]
    assert row["status"] == "queued"
    assert row["next_attempt_at"] == S.at(7, days=1)
    assert _sent_to(mobile) == []
    sends.send_due(school.tenant_id, now=S.at(7, 5, days=1))
    assert len(_sent_to(mobile)) == 1


def test_FR_MSG_011_daily_cap_defers_to_the_next_morning(
    send_school: Any, api: Any, admin_engine: Engine, fake_state: Any
) -> None:
    school = send_school
    _reset(school)
    with tenant_session(school.tenant_id) as db:
        from app.messaging.sends import repository as srepo

        already = srepo.sent_since(db, S.at(0))
    S.settings(school, daily_cap=max(already, 1))
    guardian, _ = _queue_notice(api, school)
    sends.send_due(school.tenant_id, now=S.at(13))
    row = S.message_rows(admin_engine, guardian_id=guardian)[0]
    assert row["status"] == "queued"
    assert row["next_attempt_at"] == S.at(7, days=1)
    S.settings(school)


def test_FR_MSG_016_transient_error_retries_with_backoff_and_new_link(
    send_school: Any, api: Any, admin_engine: Engine, fake_state: Any
) -> None:
    school = send_school
    _reset(school)
    guardian, mobile = _queue_notice(api, school)
    STATE.failing["91" + mobile] = 130429  # throughput: transient
    out = sends.send_due(school.tenant_id, now=S.at(12))
    assert out.get("retry", 0) == 1
    row = S.message_rows(admin_engine, guardian_id=guardian)[0]
    assert (row["status"], row["attempts"]) == ("queued", 1)
    first_handle = row["public_handle_id"]
    assert S.handle(admin_engine, first_handle)["revoked_at"] is not None  # never delivered
    assert row["next_attempt_at"] > dt.datetime.now(dt.UTC) + dt.timedelta(minutes=4)
    STATE.failing.clear()
    sends.send_due(school.tenant_id, now=S.at(12) + dt.timedelta(minutes=10))
    sends.send_due(school.tenant_id, now=dt.datetime.now(dt.UTC) + dt.timedelta(minutes=6))
    row = S.message_rows(admin_engine, guardian_id=guardian)[0]
    assert row["status"] == "sent"
    assert row["attempts"] == 2
    assert row["public_handle_id"] != first_handle  # one token per delivered message


def test_FR_MSG_016_permanent_error_is_final(
    send_school: Any, api: Any, admin_engine: Engine, fake_state: Any
) -> None:
    school = send_school
    _reset(school)
    guardian, mobile = _queue_notice(api, school)
    STATE.failing["91" + mobile] = 131026  # not on WhatsApp
    sends.send_due(school.tenant_id, now=S.at(12))
    row = S.message_rows(admin_engine, guardian_id=guardian)[0]
    assert (row["status"], row["error_code"]) == ("failed", "meta:131026")
    assert S.handle(admin_engine, row["public_handle_id"])["revoked_at"] is not None


def test_D31_nothing_is_sent_while_the_school_is_suspended(
    send_school: Any, api: Any, admin_engine: Engine, fake_state: Any
) -> None:
    school = send_school
    _reset(school)
    guardian, mobile = _queue_notice(api, school)
    S.set_status(admin_engine, school.tenant_id, "suspended")
    try:
        assert sends.send_due(school.tenant_id, now=S.at(12)) == {"held": 1}
        assert sends.run_absence_alerts(school.tenant_id, S.at(11, 30)) == {"ran": 0}
    finally:
        S.set_status(admin_engine, school.tenant_id, "active")
    assert _sent_to(mobile) == []
    assert S.message_rows(admin_engine, guardian_id=guardian)[0]["status"] == "queued"
    sends.send_due(school.tenant_id, now=S.at(12))
    assert len(_sent_to(mobile)) == 1


def test_FR_MSG_011_purpose_switched_off_skips(
    send_school: Any, api: Any, admin_engine: Engine, fake_state: Any
) -> None:
    school = send_school
    _reset(school)
    guardian, _ = _queue_notice(api, school)
    S.settings(school, notices_enabled=False)
    sends.send_due(school.tenant_id, now=S.at(12))
    row = S.message_rows(admin_engine, guardian_id=guardian)[0]
    assert (row["status"], row["skip_reason"]) == ("skipped", "purpose_off")
    S.settings(school)


# --- automatic messages --------------------------------------------------------------------------


def test_US_2207_absence_alerts_once_a_day_for_opted_in_guardians(
    send_school: Any, api: Any, admin_engine: Engine, fake_state: Any
) -> None:
    school = send_school
    _reset(school)
    student, guardian, mobile = S.opted_guardian(
        api, school, "attendance", section_key="section_9c"
    )
    late_student, late_guardian, _ = S.opted_guardian(
        api, school, "attendance", section_key="section_9c"
    )
    with tenant_session(school.tenant_id, school.people["owner"].user_id) as db:
        academics.record_attendance(
            db,
            S.ctx(school, "office_admin"),
            school.ids["section_9c"],
            AttendanceWrite(
                entries=[
                    AttendanceEntryIn(student_id=student, on_date=S.today(), status="absent"),
                    AttendanceEntryIn(student_id=late_student, on_date=S.today(), status="late"),
                ]
            ),
        )
    # Before the school's time, and on a no-message date, nothing runs.
    assert sends.run_absence_alerts(school.tenant_id, S.at(10, 30)) == {"ran": 0}
    S.settings(school, no_message_dates=[S.today()])
    assert sends.run_absence_alerts(school.tenant_id, S.at(11, 30)) == {"ran": 0}
    S.settings(school)
    out = sends.run_absence_alerts(school.tenant_id, S.at(11, 30))
    assert out["ran"] == 1
    assert sends.run_absence_alerts(school.tenant_id, S.at(11, 40)) == {"ran": 0}  # once a day
    rows = S.message_rows(admin_engine, guardian_id=guardian, purpose="absence_alert")
    assert len(rows) == 1
    assert rows[0]["dedupe_key"] == S.today().isoformat()
    assert S.message_rows(admin_engine, guardian_id=late_guardian, purpose="absence_alert") == []
    sends.send_due(school.tenant_id, now=S.at(11, 45))
    sent = _sent_to(mobile)
    assert len(sent) == 1
    assert sent[0]["name"].startswith("sos_absence_alert_v2_")
    assert sent[0]["values"][1] == "Synthetica"  # the first name only
    assert sent[0]["values"][2] == S.today().strftime("%d/%m/%Y")
    auto = api.call(school.people["principal"], "GET", "/api/v1/messaging/automatic")
    assert auto.status_code == 200
    runs = [r for r in auto.json()["runs"] if r["job"] == "absence_alerts"]
    assert runs[0]["queued"] >= 1
    assert runs[0]["counts"].get("sent", 0) >= 1


def test_US_2207_absence_alerts_after_the_evening_close_wait_for_tomorrow(
    send_school: Any, api: Any, admin_engine: Engine
) -> None:
    school = send_school
    _reset(school)
    # A queued absence alert reaching the sender after 20:00 is not sent that night and is
    # skipped the next day (too late: it was about yesterday).
    student, guardian, _ = S.opted_guardian(api, school, "attendance")
    with tenant_session(school.tenant_id) as db:
        queued = messaging.queue_message(
            db,
            purpose="absence_alert",
            guardian_id=guardian,
            student_id=student,
            source_type="student",
            source_id=student,
            dedupe_key="2026-01-01",
            variables={"school_name": "S", "child_first_name": "Synthetica", "date": "01/01/2026"},
        )
    assert queued is not None
    sends.send_due(school.tenant_id, now=S.at(20, 30))
    row = S.message_rows(admin_engine, id=queued.message_id)[0]
    assert row["status"] == "queued"
    sends.send_due(school.tenant_id, now=S.at(7, 30, days=1))
    row = S.message_rows(admin_engine, id=queued.message_id)[0]
    assert (row["status"], row["skip_reason"]) == ("skipped", "too_late")


def test_US_2205_fee_reminders_on_the_offsets_with_a_dues_link(
    send_school: Any, api: Any, admin_engine: Engine, fake_state: Any
) -> None:
    school = send_school
    _reset(school)
    student, guardian, mobile = S.opted_guardian(api, school, "fees")
    account_id = F.account(school, student)
    setup = F.ensure_setup(school)
    offset = (S.today() - setup.first_due).days
    with tenant_session(school.tenant_id, school.people["accountant"].user_id) as db:
        c = S.ctx(school, "accountant")
        current = fees.get_settings(db, c)
        fees.put_settings(
            db,
            c,
            FeeSettingsIn(
                paper_size=current.paper_size,
                gst_note=current.gst_note,
                reminder_offsets=[offset],
                min_reminder_amount=Decimal("1.00"),
            ),
            expected_version=current.version,
        )
    out = sends.run_fee_reminders(school.tenant_id, S.at(10, 5))
    assert out["ran"] == 1
    rows = S.message_rows(admin_engine, guardian_id=guardian, purpose="fee_reminder")
    assert len(rows) == 1
    assert rows[0]["dedupe_key"] == f"i1:o{offset}"
    assert rows[0]["source_id"] == account_id
    sends.send_due(school.tenant_id, now=S.at(10, 10))
    sent = _sent_to(mobile)
    assert len(sent) == 1
    # Term 1 collectable: tuition 3000 + exam 500 (transport is taxable: invoiced in Tally).
    assert sent[0]["values"][1] == "3,500.00"
    row = S.message_rows(admin_engine, guardian_id=guardian, purpose="fee_reminder")[0]
    h = S.handle(admin_engine, row["public_handle_id"])
    assert (h["kind"], h["target_type"], h["target_id"]) == (
        "parent_dues",
        "fee_account",
        account_id,
    )


def test_US_2206_receipt_message_from_the_outbox_event(
    send_school: Any, api: Any, admin_engine: Engine, fake_state: Any
) -> None:
    school = send_school
    _reset(school)
    student, guardian, mobile = S.opted_guardian(api, school, "fees")
    account_id = F.account(school, student)
    receipt = F.issue(school, account_id, "500.00")
    with admin_engine.connect() as c:
        events: int = c.execute(
            text(
                "SELECT count(*) FROM ops.outbox WHERE tenant_id = :t "
                "AND event_type = 'fee.receipt.message' AND payload->>'receipt_id' = :r"
            ),
            {"t": school.tenant_id, "r": str(receipt.id)},
        ).scalar_one()
    assert events == 1
    out = sends.queue_receipt_message(school.tenant_id, receipt.id)
    assert out["queued"] == 1
    assert sends.queue_receipt_message(school.tenant_id, receipt.id)["queued"] == 0  # idempotent
    sends.send_due(school.tenant_id, now=S.at(12))
    sent = _sent_to(mobile)
    assert len(sent) == 1
    assert sent[0]["values"][3] == receipt.receipt_number
    row = S.message_rows(admin_engine, guardian_id=guardian, purpose="fee_receipt")[0]
    h = S.handle(admin_engine, row["public_handle_id"])
    assert (h["kind"], h["target_type"], h["target_id"]) == (
        "parent_receipt",
        "fee_receipt",
        receipt.id,
    )

    # FR-PUB-007: staff with message.send revoke the link at once.
    path = f"/api/v1/messages/{row['id']}/revoke-link"
    assert api.call(school.people["office_staff"], "POST", path).status_code == 403
    res = api.call(school.people["office_admin"], "POST", path)
    assert res.status_code == 200
    assert res.json()["revoked"] is True
    assert public.resolve("parent_receipt", sent[0]["url_suffix"]) is None
    assert api.call(school.people["office_admin"], "POST", path).json()["revoked"] is False


def test_SEC_008_sending_logs_no_phone_or_token(
    send_school: Any, api: Any, capsys: pytest.CaptureFixture[str], fake_state: Any
) -> None:
    school = send_school
    _reset(school)
    capsys.readouterr()
    _, mobile = _queue_notice(api, school)
    sends.send_due(school.tenant_id, now=S.at(12))
    sent = _sent_to(mobile)
    logs = "".join(capsys.readouterr())
    for secret in (mobile, "91" + mobile, sent[0]["url_suffix"]):
        assert secret not in logs
