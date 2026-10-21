"""R2-4: "Send payment link" from the dues list (FR-MSG-027, US-2108, US-2110 AC3; ADR-0042,
ADR-0043, ADR-0046; owner decisions D33, D34).

The same preview/confirm as the manual fee reminders (``kind: payment_link`` on
``/fees/reminders/preview`` and ``/fees/reminders``): the fee message whose dues page offers "Pay
online" (the payment link is made when the parent taps it, through the parent page's path,
FR-PAY-005). Needs online payments connected (``payments_not_connected``); its own dedupe
(``paylink:<IST date>``) apart from reminders; audited as ``messaging.payment_links.sent``. Who
may send is unchanged (D33: ``message.send`` and ``finance.read``, principals; accountants and
office admins get 403). Synthetic data only, fake providers.
"""

from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy import Engine

from app.messaging.providers.fake import STATE
from app.messaging.sends import service as sends

pytestmark = pytest.mark.db
S = sys.modules["sos_test_messaging_sends_support"]
F = sys.modules["sos_test_fees_support"]
W = sys.modules["sos_test_api_world"]


def _load(name: str, path: Path) -> ModuleType:
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


O = _load(  # noqa: E741
    "sos_test_fees_online_support",
    Path(__file__).resolve().parents[1] / "fees" / "online_support.py",
)

PREVIEW = "/api/v1/fees/reminders/preview"
SEND = "/api/v1/fees/reminders"


def _idem() -> dict[str, str]:
    return {"Idempotency-Key": str(uuid.uuid4())}


def _owing(api: Any, school: Any) -> tuple[uuid.UUID, str, uuid.UUID]:
    student, guardian, mobile = S.opted_guardian(api, school, "fees")
    return guardian, mobile, F.account(school, student)


def test_FR_MSG_027_payment_link_needs_online_payments_connected(
    send_school: Any, api: Any
) -> None:
    school = send_school
    S.settings(school)
    O.disconnect_if_connected(school)
    _, _, account = _owing(api, school)
    principal = school.people["principal"]
    body = {"account_ids": [str(account)], "kind": "payment_link"}
    preview = api.call(principal, "POST", PREVIEW, json=body)
    assert preview.status_code == 200, preview.text
    assert preview.json()["blocked_reason"] == "payments_not_connected"
    res = api.call(
        principal,
        "POST",
        SEND,
        json={**body, "preview_token": preview.json()["preview_token"]},
        headers=_idem(),
    )
    assert res.status_code == 409
    assert res.json()["code"] == "payments_not_connected"
    # A reminder for the same row is not blocked by payments.
    reminder = api.call(principal, "POST", PREVIEW, json={"account_ids": [str(account)]})
    assert reminder.json()["blocked_reason"] is None


def test_FR_MSG_027_single_row_payment_link_is_sent_apart_from_reminders(
    send_school: Any, api: Any, admin_engine: Engine, fake_state: Any
) -> None:
    school = send_school
    S.settings(school)
    sends.send_due(school.tenant_id, now=S.at(12, days=1))
    O.connect(school)
    try:
        guardian, mobile, account = _owing(api, school)
        principal = school.people["principal"]
        body = {"account_ids": [str(account)], "kind": "payment_link"}
        preview = api.call(principal, "POST", PREVIEW, json=body).json()
        assert (preview["recipients"], preview["blocked_reason"]) == (1, None)
        # The token pins the kind: a reminder's token does not confirm a payment link.
        reminder = api.call(principal, "POST", PREVIEW, json={"account_ids": [str(account)]})
        assert reminder.json()["preview_token"] != preview["preview_token"]
        stale = api.call(
            principal,
            "POST",
            SEND,
            json={**body, "preview_token": reminder.json()["preview_token"]},
            headers=_idem(),
        )
        assert stale.status_code == 409
        assert stale.json()["code"] == "preview_stale"
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
        assert [r["dedupe_key"] for r in rows] == [f"paylink:{S.today().isoformat()}"]
        events = W.audit_events(admin_engine, school.tenant_id, "messaging.payment_links.sent")
        assert events[-1]["summary"]["queued"] == 1
        assert mobile not in str(events[-1]["summary"])
        sends.send_due(school.tenant_id, now=S.at(12))
        out = [m for m in STATE.sent if m["to"] == "91" + mobile]
        assert len(out) == 1
        row = S.message_rows(admin_engine, guardian_id=guardian, purpose="fee_reminder")[0]
        h = S.handle(admin_engine, row["public_handle_id"])
        # The dues page, whose "Pay online" makes the payment link (FR-PAY-005).
        assert (h["kind"], h["target_type"], h["target_id"]) == (
            "parent_dues",
            "fee_account",
            account,
        )
        # Once a day per kind: a second payment link is already_sent, a reminder still goes.
        again = api.call(principal, "POST", PREVIEW, json=body).json()
        assert again["skipped_guardians"].get("already_sent") == 1
        reminder = api.call(principal, "POST", PREVIEW, json={"account_ids": [str(account)]})
        assert reminder.json()["recipients"] == 1
    finally:
        O.disconnect_if_connected(school)
        S.settings(school)


def test_D33_payment_links_stay_with_message_send_holders(
    world: Any, send_school: Any, api: Any
) -> None:
    school = send_school
    _, _, account = _owing(api, school)
    body = {"account_ids": [str(account)], "kind": "payment_link"}
    for role in ("accountant", "office_admin", "office_staff", "class_teacher", "teacher"):
        res = api.call(school.people[role], "POST", PREVIEW, json=body)
        assert res.status_code == 403, (role, res.text)
    assert api.call(world.person("principal"), "POST", PREVIEW, json=body).status_code == 404
    bad = api.call(school.people["principal"], "POST", PREVIEW, json={**body, "kind": "sms"})
    assert bad.status_code == 422
