"""R2-3J messaging finish-up: manual fee reminders from the dues list (FR-MSG-025), "Send on
WhatsApp" for a receipt (FR-MSG-026), the reminder days in the fee settings (FR-MSG-014) and a
withdrawn notice's links and queued messages (FR-NOTICE-009, FR-PUB-007).

Synthetic data only: the separate synthetic sending school (``send_school``), guardians with
synthetic 90xxxxxxxx numbers, the fake provider (no network). Times are pinned with
``S.at(...)`` (IST).
"""

from __future__ import annotations

import sys
import uuid
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import Engine

from app.circulars import service as circulars
from app.circulars.schemas import NoticeWithdrawIn
from app.core.db import tenant_session
from app.fees import service as fees
from app.fees.schemas import FeeSettingsIn
from app.messaging.providers.fake import STATE
from app.messaging.sends import service as sends
from app.public import service as public

pytestmark = pytest.mark.db
S = sys.modules["sos_test_messaging_sends_support"]
F = sys.modules["sos_test_fees_support"]
W = sys.modules["sos_test_api_world"]

PREVIEW = "/api/v1/messaging/fee-reminders/preview"
SEND = "/api/v1/messaging/fee-reminders"


def _sent_to(mobile: str) -> list[dict[str, Any]]:
    return [m for m in STATE.sent if m["to"] == "91" + mobile]


def _reset(school: Any) -> None:
    S.settings(school)
    sends.send_due(school.tenant_id, now=S.at(12, days=1))


def _idem() -> dict[str, str]:
    return {"Idempotency-Key": str(uuid.uuid4())}


def _owing(api: Any, school: Any, *categories: str) -> tuple[uuid.UUID, uuid.UUID, str, uuid.UUID]:
    """A student of 9A with an overdue Term 1 (3,500 collectable) and one guardian opted in
    for ``categories``. Returns (student, guardian, mobile, account)."""
    student, guardian, mobile = S.opted_guardian(api, school, *categories)
    return student, guardian, mobile, F.account(school, student)


# --- FR-MSG-025: manual fee reminders from the dues list ---------------------------------------


def test_FR_MSG_025_preview_confirm_and_send_chosen_rows(
    send_school: Any, api: Any, admin_engine: Engine, fake_state: Any
) -> None:
    school = send_school
    _reset(school)
    S.settings(school, fee_reminders_enabled=False)  # automatic reminders off: manual still go
    _, guardian, mobile, account = _owing(api, school, "fees")
    _, _, other_mobile, not_opted = _owing(api, school, "notices")  # no fee opt-in
    principal = school.people["principal"]
    body = {"account_ids": [str(account), str(not_opted)]}

    res = api.call(principal, "POST", PREVIEW, json=body)
    assert res.status_code == 200, res.text
    preview = res.json()
    assert res.headers["Cache-Control"] == "no-store"
    assert (preview["accounts"], preview["recipients"], preview["guardians"]) == (2, 1, 1)
    assert preview["accounts_reminded"] == 1
    assert preview["skipped_guardians"] == {"no_optin": 1}
    assert preview["skipped_accounts"] == {}
    assert preview["blocked_reason"] is None
    expected = (
        Decimal(preview["utility_rate_inr"]) * (1 + Decimal(preview["gst_percent"]) / 100)
    ).quantize(Decimal("0.01"))
    assert Decimal(preview["estimated_cost_inr"]) == expected

    sent = api.call(
        principal,
        "POST",
        SEND,
        json={**body, "preview_token": preview["preview_token"]},
        headers=_idem(),
    )
    assert sent.status_code == 202, sent.text
    assert sent.json()["queued"] == 1
    rows = S.message_rows(admin_engine, guardian_id=guardian, purpose="fee_reminder")
    assert len(rows) == 1
    assert rows[0]["dedupe_key"] == f"manual:{S.today().isoformat()}"
    assert rows[0]["source_id"] == account
    assert rows[0]["created_by"] == principal.user_id
    sends.send_due(school.tenant_id, now=S.at(12))
    out = _sent_to(mobile)
    assert len(out) == 1
    assert out[0]["name"].startswith("sos_fee_reminder_v2_")
    assert out[0]["values"][1] == "3,500.00"  # collectable Term 1 (transport is taxable)
    assert _sent_to(other_mobile) == []
    row = S.message_rows(admin_engine, guardian_id=guardian, purpose="fee_reminder")[0]
    assert row["status"] == "sent"  # not skipped as purpose_off: a person sent it
    h = S.handle(admin_engine, row["public_handle_id"])
    assert (h["kind"], h["target_type"], h["target_id"]) == ("parent_dues", "fee_account", account)

    events = W.audit_events(admin_engine, school.tenant_id, "messaging.fee_reminders.sent")
    assert events[-1]["summary"]["queued"] == 1
    assert mobile not in str(events[-1]["summary"])

    # Once a day: the same rows again are counted as already reminded today.
    again = api.call(principal, "POST", PREVIEW, json=body).json()
    assert again["recipients"] == 0
    assert again["skipped_guardians"].get("already_sent") == 1
    S.settings(school)


def test_FR_MSG_025_all_matching_the_filter_and_stale_preview(
    send_school: Any, api: Any, admin_engine: Engine
) -> None:
    school = send_school
    _reset(school)
    _, guardian, _, account = _owing(api, school, "fees")
    principal = school.people["principal"]
    body = {"all_matching": True, "section_id": str(school.ids["section_9a"])}
    preview = api.call(principal, "POST", PREVIEW, json=body).json()
    assert preview["accounts"] >= 1
    assert preview["recipients"] >= 1
    # Who gets one changed after the preview (a new opted-in guardian with dues): stale.
    _owing(api, school, "fees")
    stale = api.call(
        principal,
        "POST",
        SEND,
        json={**body, "preview_token": preview["preview_token"]},
        headers=_idem(),
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "preview_stale"
    fresh = api.call(principal, "POST", PREVIEW, json=body).json()
    ok = api.call(
        principal,
        "POST",
        SEND,
        json={**body, "preview_token": fresh["preview_token"]},
        headers=_idem(),
    )
    assert ok.status_code == 202, ok.text
    assert S.message_rows(admin_engine, guardian_id=guardian, source_id=account)


def test_FR_MSG_025_nothing_due_minimum_and_validation(send_school: Any, api: Any) -> None:
    school = send_school
    _reset(school)
    principal = school.people["principal"]
    _, _, _, account = _owing(api, school, "fees")
    F.issue(school, account, "3500.00")  # Term 1 paid: nothing overdue now
    res = api.call(principal, "POST", PREVIEW, json={"account_ids": [str(account)]})
    assert res.status_code == 200, res.text
    assert res.json()["skipped_accounts"] == {"nothing_due": 1}
    assert res.json()["recipients"] == 0

    _, _, _, small = _owing(api, school, "fees")
    with tenant_session(school.tenant_id, school.people["accountant"].user_id) as db:
        c = S.ctx(school, "accountant")
        current = fees.get_settings(db, c)
        fees.put_settings(
            db,
            c,
            FeeSettingsIn(
                paper_size=current.paper_size,
                gst_note=current.gst_note,
                reminder_offsets=current.reminder_offsets,
                min_reminder_amount=Decimal("10000.00"),
            ),
            expected_version=current.version,
        )
    try:
        below = api.call(principal, "POST", PREVIEW, json={"account_ids": [str(small)]})
        assert below.json()["skipped_accounts"] == {"below_minimum": 1}
    finally:
        with tenant_session(school.tenant_id, school.people["accountant"].user_id) as db:
            c = S.ctx(school, "accountant")
            current = fees.get_settings(db, c)
            fees.put_settings(
                db,
                c,
                current_in(current, Decimal("0.00")),
                expected_version=current.version,
            )

    # Either rows or all_matching, never both or neither; a filter only with all_matching.
    for bad in (
        {},
        {"account_ids": [str(account)], "all_matching": True},
        {"account_ids": [str(account)], "section_id": str(school.ids["section_9a"])},
        {"account_ids": []},
    ):
        assert api.call(principal, "POST", PREVIEW, json=bad).status_code == 422, bad


def current_in(current: Any, minimum: Decimal) -> FeeSettingsIn:
    return FeeSettingsIn(
        paper_size=current.paper_size,
        gst_note=current.gst_note,
        reminder_offsets=current.reminder_offsets,
        min_reminder_amount=minimum,
    )


def test_FR_MSG_025_authz_bola_and_other_school(
    world: Any, send_school: Any, api: Any, admin_engine: Engine
) -> None:
    school = send_school
    _, _, _, account = _owing(api, school, "fees")
    body = {"account_ids": [str(account)]}
    # message.send is needed (route), and finance.read school-wide (fees service): the office
    # admin sends notices but does not read fee accounts.
    for role in (
        "office_staff",
        "accountant",
        "teacher",
        "class_teacher",
        "auditor_readonly",
        "office_admin",
    ):
        res = api.call(school.people[role], "POST", PREVIEW, json=body)
        assert res.status_code == 403, (role, res.text)
    assert api.call(school.people["principal"], "POST", PREVIEW, json=body).status_code == 200
    # Another school's account id (BOLA): 404, nothing revealed; an unknown id too.
    other = world.person("principal")
    assert api.call(other, "POST", PREVIEW, json=body).status_code == 404
    unknown = {"account_ids": [str(uuid.uuid4())]}
    assert api.call(school.people["principal"], "POST", PREVIEW, json=unknown).status_code == 404
    # The confirmation is guarded the same way.
    res = api.call(
        school.people["accountant"],
        "POST",
        SEND,
        json={**body, "preview_token": "fr_" + "0" * 40},
        headers=_idem(),
    )
    assert res.status_code == 403


def test_FR_MSG_025_paused_account_blocks(send_school: Any, api: Any, admin_engine: Engine) -> None:
    school = send_school
    _, _, _, account = _owing(api, school, "fees")
    principal = school.people["principal"]
    body = {"account_ids": [str(account)]}
    preview = api.call(principal, "POST", PREVIEW, json=body).json()
    with admin_engine.begin() as c:
        from sqlalchemy import text

        c.execute(
            text(
                "UPDATE msg.accounts SET status = 'paused', pause_reason = 'quality_flagged' "
                "WHERE tenant_id = :t AND status = 'active'"
            ),
            {"t": school.tenant_id},
        )
    try:
        blocked = api.call(principal, "POST", PREVIEW, json=body).json()
        assert blocked["blocked_reason"] == "messaging_paused"
        res = api.call(
            principal,
            "POST",
            SEND,
            json={**body, "preview_token": preview["preview_token"]},
            headers=_idem(),
        )
        assert res.status_code == 409
        assert res.json()["code"] == "messaging_paused"
    finally:
        with admin_engine.begin() as c:
            from sqlalchemy import text

            c.execute(
                text(
                    "UPDATE msg.accounts SET status = 'active', pause_reason = NULL "
                    "WHERE tenant_id = :t AND status = 'paused'"
                ),
                {"t": school.tenant_id},
            )


# --- FR-MSG-026: "Send on WhatsApp" for one receipt ----------------------------------------------


def test_FR_MSG_026_counter_sends_the_receipt_once(
    send_school: Any, api: Any, admin_engine: Engine, fake_state: Any
) -> None:
    school = send_school
    _reset(school)
    S.settings(school, fee_receipts_enabled=False)  # automatic receipts off
    _, guardian, mobile, account = _owing(api, school, "fees")
    receipt = F.issue(school, account, "500.00")
    # The automatic path queues nothing while the switch is off.
    assert sends.queue_receipt_message(school.tenant_id, receipt.id) == {"queued": 0}
    clerk = school.people["office_staff"]  # fee.collect, no message.send
    path = f"/api/v1/fees/receipts/{receipt.id}/whatsapp"
    state = api.call(clerk, "GET", path)
    assert state.status_code == 200, state.text
    assert state.json()["can_send"] is True
    assert state.json()["opted_in_guardians"] == 1
    assert state.json()["messages"] == []

    res = api.call(clerk, "POST", path, headers=_idem())
    assert res.status_code == 202, res.text
    body = res.json()
    assert body["can_send"] is False
    assert body["blocked_reason"] == "already_queued"
    assert [m["status"] for m in body["messages"]] == ["queued"]
    again = api.call(clerk, "POST", path, headers=_idem())
    assert again.status_code == 409
    assert again.json()["code"] == "already_queued"
    sends.send_due(school.tenant_id, now=S.at(12))
    assert len(_sent_to(mobile)) == 1
    assert _sent_to(mobile)[0]["values"][3] == receipt.receipt_number
    row = S.message_rows(admin_engine, guardian_id=guardian, purpose="fee_receipt")[0]
    assert row["status"] == "sent"
    assert api.call(clerk, "GET", path).json()["messages"][0]["status"] == "sent"
    events = W.audit_events(admin_engine, school.tenant_id, "messaging.receipt.sent")
    assert any(e["resource_id"] == receipt.id for e in events)
    # With the switch back on, the automatic path finds it already sent (dedupe per receipt).
    S.settings(school)
    assert sends.queue_receipt_message(school.tenant_id, receipt.id) == {"queued": 0}


def test_FR_MSG_026_refusals_authz_and_other_school(world: Any, send_school: Any, api: Any) -> None:
    school = send_school
    _reset(school)
    _, _, _, account = _owing(api, school, "notices")  # no fee opt-in
    receipt = F.issue(school, account, "500.00")
    path = f"/api/v1/fees/receipts/{receipt.id}/whatsapp"
    clerk = school.people["office_staff"]
    state = api.call(clerk, "GET", path).json()
    assert state["blocked_reason"] == "no_opted_in_guardian"
    res = api.call(clerk, "POST", path, headers=_idem())
    assert res.status_code == 409
    assert res.json()["code"] == "no_opted_in_guardian"
    for role in ("teacher", "class_teacher", "auditor_readonly"):
        assert api.call(school.people[role], "GET", path).status_code == 403, role
        assert api.call(school.people[role], "POST", path, headers=_idem()).status_code == 403
    for role in ("accountant", "office_admin", "principal"):
        assert api.call(school.people[role], "GET", path).status_code == 200, role
    other = world.person("office_staff")
    assert api.call(other, "GET", path).status_code == 404
    assert api.call(other, "POST", path, headers=_idem()).status_code == 404
    unknown = f"/api/v1/fees/receipts/{uuid.uuid4()}/whatsapp"
    assert api.call(clerk, "GET", unknown).status_code == 404


# --- FR-MSG-014: reminder days in the fee settings ----------------------------------------------


def test_FR_MSG_014_reminder_days_are_validated_versioned_and_audited(
    send_school: Any, api: Any, admin_engine: Engine
) -> None:
    school = send_school
    F.ensure_setup(school)
    accountant = school.people["accountant"]
    res = api.call(accountant, "GET", "/api/v1/fees/settings")
    assert res.status_code == 200, res.text
    current = res.json()
    etag = res.headers["ETag"]
    body = {
        "paper_size": current["paper_size"],
        "gst_note": current["gst_note"],
        "reminder_offsets": [-5, 0, 3],
        "min_reminder_amount": "100.00",
    }
    for bad in ([-61], [0, 0], [1, 2, 3, 4, 5, 6]):
        res = api.call(
            accountant,
            "PUT",
            "/api/v1/fees/settings",
            json={**body, "reminder_offsets": bad},
            headers={"If-Match": etag},
        )
        assert res.status_code == 422, bad
    assert (
        api.call(
            accountant, "PUT", "/api/v1/fees/settings", json=body, headers={"If-Match": '"999"'}
        ).status_code
        == 412
    )
    # fee.manage only (the accountant holds it; office staff collect but do not configure).
    office = school.people["office_staff"]
    res = api.call(office, "PUT", "/api/v1/fees/settings", json=body, headers={"If-Match": etag})
    assert res.status_code == 403
    ok = api.call(accountant, "PUT", "/api/v1/fees/settings", json=body, headers={"If-Match": etag})
    assert ok.status_code == 200, ok.text
    assert ok.json()["reminder_offsets"] == [-5, 0, 3]
    events = W.audit_events(admin_engine, school.tenant_id, "fee.settings.updated")
    assert events[-1]["summary"]["reminder_offsets"] == [-5, 0, 3]
    assert events[-1]["summary"]["min_reminder_amount"] == "100.00"
    restore = {**body, "reminder_offsets": [-3, 0, 7], "min_reminder_amount": "0.00"}
    api.call(
        accountant,
        "PUT",
        "/api/v1/fees/settings",
        json=restore,
        headers={"If-Match": ok.headers["ETag"]},
    )


# --- FR-NOTICE-009 / FR-PUB-007: withdrawing a notice -------------------------------------------


def _withdraw(
    api: Any, school: Any, notice_id: uuid.UUID, version: int, role: str = "owner"
) -> Any:
    return api.call(
        school.people[role],
        "POST",
        f"/api/v1/notices/{notice_id}/withdraw",
        json={"reason": "The date in this notice was wrong"},
        headers={"If-Match": f'"{version}"'},
    )


def test_FR_NOTICE_009_withdrawal_revokes_links_and_cancels_queued_messages(
    send_school: Any, api: Any, admin_engine: Engine, fake_state: Any
) -> None:
    school = send_school
    _reset(school)
    _, sent_guardian, sent_mobile = S.opted_guardian(api, school, "notices")
    notice_id = S.notice(school)
    admin = school.people["office_admin"]
    sections = {"section_ids": [str(school.ids["section_9a"])]}
    preview = api.call(
        admin, "POST", f"/api/v1/notices/{notice_id}/broadcasts/preview", json=sections
    ).json()
    confirmed = api.call(
        admin,
        "POST",
        f"/api/v1/notices/{notice_id}/broadcasts",
        json={**sections, "preview_token": preview["preview_token"]},
        headers=_idem(),
    )
    assert confirmed.status_code == 202, confirmed.text
    # Send to the first guardian, then let the rest wait for the morning (quiet hours).
    sends.send_due(school.tenant_id, now=S.at(12))
    token = _sent_to(sent_mobile)[0]["url_suffix"]
    assert public.resolve("parent_notice", token) is not None
    _, waiting_guardian, waiting_mobile = S.opted_guardian(api, school, "notices")
    resend = api.call(
        admin,
        "POST",
        f"/api/v1/notices/{notice_id}/broadcasts/preview",
        json={**sections, "resend": False},
    ).json()
    assert resend["recipients"] >= 1
    assert (
        api.call(
            admin,
            "POST",
            f"/api/v1/notices/{notice_id}/broadcasts",
            json={**sections, "preview_token": resend["preview_token"]},
            headers=_idem(),
        ).status_code
        == 202
    )
    sends.send_due(school.tenant_id, now=S.at(22, 30))  # deferred to 07:00

    version = circulars_version(school, notice_id)
    res = _withdraw(api, school, notice_id, version)
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["status"] == "withdrawn"
    assert out["withdraw_reason"] == "The date in this notice was wrong"
    assert out["withdrawn_at"] is not None
    # The link stopped working in the same transaction: the parent page is the uniform 404.
    assert public.resolve("parent_notice", token) is None
    with tenant_session(school.tenant_id) as db:
        assert circulars.parent_notice(db, notice_id, "en") is None
    events = W.audit_events(admin_engine, school.tenant_id, "notice.withdrawn")
    assert any(e["resource_id"] == notice_id for e in events)

    # The outbox consumer cancels what was still queued; nothing more is sent.
    result = sends.cancel_notice_messages(school.tenant_id, notice_id)
    assert result["cancelled"] >= 1
    row = S.message_rows(admin_engine, guardian_id=waiting_guardian, purpose="school_notice")[0]
    assert (row["status"], row["skip_reason"]) == ("skipped", "notice_withdrawn")
    sends.send_due(school.tenant_id, now=S.at(7, 30, days=1))
    assert _sent_to(waiting_mobile) == []
    sent_row = S.message_rows(admin_engine, guardian_id=sent_guardian, purpose="school_notice")
    assert sent_row[0]["status"] == "sent"  # what went out stays on record
    cancelled = W.audit_events(
        admin_engine, school.tenant_id, "messaging.notice_messages.cancelled"
    )
    assert any(e["resource_id"] == notice_id for e in cancelled)

    # A withdrawn notice cannot be sent, edited, approved or withdrawn again.
    again = api.call(
        admin, "POST", f"/api/v1/notices/{notice_id}/broadcasts/preview", json=sections
    )
    assert again.status_code == 409
    assert again.json()["code"] == "notice_not_approved"
    twice = _withdraw(api, school, notice_id, out["version"])
    assert twice.status_code == 409
    assert twice.json()["code"] == "notice_withdrawn"


def test_FR_NOTICE_009_sender_skips_a_withdrawn_notice_message(
    send_school: Any, api: Any, admin_engine: Engine, fake_state: Any
) -> None:
    """A message still queued when the consumer has not run yet is skipped by the sender."""
    school = send_school
    _reset(school)
    _, guardian, mobile = S.opted_guardian(api, school, "notices")
    notice_id = S.notice(school)
    admin = school.people["office_admin"]
    sections = {"section_ids": [str(school.ids["section_9a"])]}
    preview = api.call(
        admin, "POST", f"/api/v1/notices/{notice_id}/broadcasts/preview", json=sections
    ).json()
    api.call(
        admin,
        "POST",
        f"/api/v1/notices/{notice_id}/broadcasts",
        json={**sections, "preview_token": preview["preview_token"]},
        headers=_idem(),
    )
    assert (
        _withdraw(api, school, notice_id, circulars_version(school, notice_id)).status_code == 200
    )
    sends.send_due(school.tenant_id, now=S.at(12))
    assert _sent_to(mobile) == []
    row = S.message_rows(admin_engine, guardian_id=guardian, purpose="school_notice")[0]
    assert (row["status"], row["skip_reason"]) == ("skipped", "notice_withdrawn")


def test_FR_NOTICE_009_authz_states_and_other_school(
    world: Any, send_school: Any, api: Any
) -> None:
    school = send_school
    notice_id = S.notice(school)
    version = circulars_version(school, notice_id)
    for role in ("office_staff", "accountant", "teacher", "class_teacher", "auditor_readonly"):
        assert _withdraw(api, school, notice_id, version, role).status_code == 403, role
    other = world.person("owner")
    res = api.call(
        other,
        "POST",
        f"/api/v1/notices/{notice_id}/withdraw",
        json={"reason": "The date in this notice was wrong"},
        headers={"If-Match": f'"{version}"'},
    )
    assert res.status_code == 404
    assert _withdraw(api, school, notice_id, version + 5).status_code == 412
    short = api.call(
        school.people["owner"],
        "POST",
        f"/api/v1/notices/{notice_id}/withdraw",
        json={"reason": "wrong"},
        headers={"If-Match": f'"{version}"'},
    )
    assert short.status_code == 422
    draft_id, draft_version = S.C.complete_notice(school, approved=False)
    draft = _withdraw(api, school, draft_id, draft_version)
    assert draft.status_code == 409
    assert draft.json()["code"] == "notice_not_approved"
    # The withdrawal by the service directly, as the route does (owner: notice.approve).
    owner = S.ctx(school, "owner")
    with tenant_session(school.tenant_id, owner.user_id) as db:
        out = circulars.withdraw_notice(
            db, owner, notice_id, NoticeWithdrawIn(reason="Sent to the wrong classes"), version
        )
    assert out.status == "withdrawn"


def circulars_version(school: Any, notice_id: uuid.UUID) -> int:
    owner = S.ctx(school, "owner")
    with tenant_session(school.tenant_id, owner.user_id) as db:
        version: int = circulars.get_notice(db, owner, notice_id).version
    return version
