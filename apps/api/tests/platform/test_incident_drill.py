"""Incident drill: the whole SchoolOS incident flow on a frozen clock, end to end (SEC-024;
FR-DPDP-030..035; ADR-0050, ADR-0054 / D48; docs/security/incident-drill.md).

The automated half of the quarterly incident drill (docs/11 §10). One synthetic shared-tier
school (provisioned through the operator API, so it has its own keys, owner and breach register)
and one dedicated-tier deployment are hit by a P1 incident:

T0 noticed → incident opened (CERT-In clock T0 + 6 h shown) → two schools added → two
different operators confirm (C) → the shared school's worker pulls the notice (its clocks run
from C: Board notice C + 24 h, detailed report C + 72 h) → the dedicated school is told by phone
and recorded → CERT-In reported inside 6 h → reminders at C + 24 h and C + 60 h, once each →
the school records the Board notice, people notified and the detailed report, then closes its
breach → SchoolOS closes the incident (refused 409 while CERT-In is unreported or a school is
not notified; D49) → PATCH, schools and confirm refused 409 ``incident_closed`` → reopen asked
by one operator, confirmed by another → the school's next pull reopens its closed breach for
review, once (D49) → the school closes it again → audit on both chains, codes and counts only.

The clock: every ``now()`` the flow reads (the incident service, the two-person rules, the
breach register's DB clock) is pinned to ``clock.at``, which the drill moves forward. The
timeline sits a week in the past so that every "not in the future" check uses the pinned time
and real time agrees. Synthetic data only.
"""

from __future__ import annotations

import datetime as dt
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.core.crypto import LocalDevKeyWrapper
from app.core.db import platform_session
from app.platform import incidents, two_person
from app.privacy import breach_repository, breach_service, breach_sync
from app.students import crypto

from .conftest import Api, MakeOperator, Operator, letters, provision_payload

pytestmark = pytest.mark.db

H = dt.timedelta(hours=1)
M = dt.timedelta(minutes=1)
SUMMARY = (
    "Attendance and contact records of two schools readable through a misconfigured export "
    "link for about three hours"
)
REMINDER = "privacy.breach.reminder"


@dataclass
class Clock:
    at: dt.datetime

    def set(self, at: dt.datetime) -> dt.datetime:
        assert at >= self.at, "the drill clock only moves forward"
        self.at = at
        return at


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch, wrapper: LocalDevKeyWrapper) -> Clock:
    """T0 a week ago, on the minute; every clock the flow reads follows ``clock.at``. The
    school-side worker's keyring uses the synthetic wrapper that provisioned the school."""
    monkeypatch.setattr(crypto, "_keyring", crypto.TenantKeyring(wrapper))
    t0 = (dt.datetime.now(dt.UTC) - dt.timedelta(days=7)).replace(second=0, microsecond=0)
    c = Clock(t0)
    monkeypatch.setattr(incidents, "now", lambda: c.at)
    monkeypatch.setattr(two_person, "now", lambda: c.at)
    monkeypatch.setattr(breach_repository, "now", lambda _session: c.at)
    return c


# --- helpers ----------------------------------------------------------------------------------


def _shared_school(
    api: Api, owner: Operator, plan: uuid.UUID, admin: Engine
) -> tuple[uuid.UUID, str]:
    """A live shared-tier school provisioned through the operator API (own keys and owner)."""
    body = provision_payload(plan)
    out = api.call("POST", "/tenants", owner, json=body)
    assert out.status_code in (200, 201, 202), out.text
    tid = out.json()["tenant_id"]
    assert api.call("POST", f"/tenants/{tid}/activate", owner).status_code == 200
    with admin.begin() as c:
        c.execute(
            text("UPDATE core.memberships SET status = 'active' WHERE tenant_id = :t"), {"t": tid}
        )
    return uuid.UUID(tid), body["owner"]["idp_subject"]


def _dedicated_school() -> uuid.UUID:
    """A dedicated-tier deployment row (no pull: the operator records the phone notice)."""
    tenant_id = uuid.uuid4()
    with platform_session() as s:
        s.execute(
            text(
                "INSERT INTO platform.deployments (id, tenant_id, tenant_code, school_name, mode, "
                "tenant_status, status, heartbeat_key_id, heartbeat_key_ciphertext) VALUES "
                "(:i, :t, :c, 'Synthetic Dedicated Drill School', 'dedicated', 'active', "
                "'healthy', 'hb-1', :kc)"
            ),
            {"i": uuid.uuid4(), "t": tenant_id, "c": f"t-{letters(12)}", "kc": b"\x01" * 16},
        )
    return tenant_id


def _at(value: str | None) -> dt.datetime | None:
    return None if value is None else dt.datetime.fromisoformat(value)


def _patch(api: Api, op: Operator, iid: str, body: dict[str, Any]) -> Any:
    return api.call(
        "PATCH", f"/incidents/{iid}", op, json=body, headers=api.if_match(f"/incidents/{iid}", op)
    )


def _school(api: Api, subject: str, method: str, path: str, **kw: Any) -> Any:
    headers = api.headers(None, tenant_subject=subject)
    headers.update(kw.pop("headers", {}))
    return api.client.request(method, "/api/v1/privacy" + path, headers=headers, **kw)


def _school_patch(api: Api, subject: str, bid: str, body: dict[str, Any]) -> Any:
    etag = _school(api, subject, "GET", f"/breaches/{bid}").headers["ETag"]
    return _school(api, subject, "PATCH", f"/breaches/{bid}", json=body, headers={"If-Match": etag})


def _duties(detail: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {d["key"]: d for d in detail["duties"]}


def _reminders(admin: Engine, bid: str) -> list[str]:
    with admin.connect() as c:
        return list(
            c.execute(
                text("SELECT reminder FROM ops.breach_reminders WHERE breach_id = :b ORDER BY 1"),
                {"b": bid},
            ).scalars()
        )


def _notifications(admin: Engine, tenant_id: uuid.UUID, key: str, bid: str) -> int:
    with admin.connect() as c:
        value: int = c.execute(
            text(
                "SELECT count(*) FROM ops.notifications WHERE tenant_id = :t "
                "AND template_key = :k AND resource_id = :r"
            ),
            {"t": tenant_id, "k": key, "r": bid},
        ).scalar_one()
    return value


def _platform_events(iid: str) -> list[dict[str, Any]]:
    with platform_session() as s:
        return [
            dict(r._mapping)
            for r in s.execute(
                text(
                    "SELECT action, summary, actor_id, subject_tenant_id FROM "
                    "platform.audit_events WHERE resource_id = :r ORDER BY seq"
                ),
                {"r": iid},
            )
        ]


def _school_events(admin: Engine, tenant_id: uuid.UUID, bid: str) -> list[dict[str, Any]]:
    with admin.connect() as c:
        return [
            dict(r._mapping)
            for r in c.execute(
                text(
                    "SELECT action, actor_type, summary FROM audit.events WHERE tenant_id = :t "
                    "AND resource_id = :r ORDER BY seq"
                ),
                {"t": tenant_id, "r": bid},
            )
        ]


_PERSONAL = re.compile(r"\d{6,}|@")


def _no_personal_data(summaries: list[Any]) -> None:
    blob = " ".join(str(s) for s in summaries)
    assert "misconfigured" not in blob, "the incident summary text is not audited"
    assert "export link" not in blob
    assert "Synthetic Owner" not in blob
    for s in summaries:
        # UUIDs carry digit runs only inside hyphenated groups; strip them before the check.
        flat = re.sub(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", "", str(s))
        flat = re.sub(r"\d{4}-\d{2}-\d{2}T[\d:.+]+", "", flat)
        assert not _PERSONAL.search(flat), s


# --- the drill --------------------------------------------------------------------------------


def test_SEC_024_incident_drill_cert_in_6h_school_clocks_and_locked_close(  # noqa: PLR0915, PLR0917 - one timed drill; fixtures
    api: Api,
    make_operator: MakeOperator,
    owner: Operator,
    make_plan: Callable[..., uuid.UUID],
    admin_engine: Engine,
    clock: Clock,
) -> None:
    lead, second = make_operator("platform_owner"), make_operator("platform_engineer")
    school_a, subject_a = _shared_school(api, owner, make_plan(), admin_engine)
    school_b = _dedicated_school()
    t0 = clock.at

    # Step 1 (T0 + 10 min): the incident lead opens the record; T0 is when it was noticed.
    clock.set(t0 + 10 * M)
    opened = api.call(
        "POST",
        "/incidents",
        lead,
        json={
            "opened_at": t0.isoformat(),
            "severity": "P1",
            "summary": SUMMARY,
            "cert_in_required": True,
        },
    )
    assert opened.status_code == 201, opened.text
    iid = opened.json()["id"]
    assert _at(opened.json()["opened_at"]) == t0
    # Step 2: the CERT-In clock operators see is T0 + 6 h, on the record and in the list.
    assert _at(opened.json()["cert_in_due_at"]) == t0 + 6 * H
    listed = {i["id"]: i for i in api.call("GET", "/incidents", second).json()["data"]}
    assert _at(listed[iid]["cert_in_due_at"]) == t0 + 6 * H

    # Step 3 (T0 + 30 min): impact assessed; both schools added (codes and counts only).
    clock.set(t0 + 30 * M)
    for tenant_id, people in ((school_a, 120), (school_b, 45)):
        added = api.call(
            "POST",
            f"/incidents/{iid}/schools",
            lead,
            json={
                "tenant_id": str(tenant_id),
                "categories": ["attendance", "contact"],
                "approx_people": people,
                "window_from": (t0 - 3 * H).isoformat(),
                "window_to": t0.isoformat(),
            },
        )
        assert added.status_code == 200, added.text
    # Nothing reaches a school before two operators confirm.
    assert breach_sync.sync_school(school_a) == 0

    # Step 4 (T0 + 45 min / T0 + 1 h): two different operators confirm the list.
    clock.set(t0 + 45 * M)
    one = api.call("POST", f"/incidents/{iid}/confirm", lead)
    assert one.status_code == 200, one.text
    assert _at(one.json()["confirm_by"]) == t0 + 45 * M + two_person.request_ttl()
    same = api.call("POST", f"/incidents/{iid}/confirm", lead)
    assert (same.status_code, same.json()["code"]) == (409, "same_operator")
    confirmed_at = clock.set(t0 + 1 * H)
    two = api.call("POST", f"/incidents/{iid}/confirm", second)
    assert two.status_code == 200, two.text
    assert _at(two.json()["confirmed_at"]) == confirmed_at
    schools = {s["tenant_id"]: s for s in two.json()["schools"]}
    for tenant_id in (school_a, school_b):
        assert _at(schools[str(tenant_id)]["notice_due_at"]) == confirmed_at + 24 * H

    # Step 5 (C + 1 min): the shared school's worker pulls the notice, once.
    clock.set(confirmed_at + 1 * M)
    assert breach_sync.sync_school(school_a) == 1
    assert breach_sync.sync_school(school_a) == 0, "idempotent on the next sweep"
    detail = api.call("GET", f"/incidents/{iid}", lead).json()
    row_a = {s["tenant_id"]: s for s in detail["schools"]}[str(school_a)]
    assert _at(row_a["pulled_at"]) == confirmed_at + 1 * M
    assert _at(row_a["school_notified_at"]) == confirmed_at + 1 * M
    assert row_a["notice_due_at"] is None
    assert detail["schools_notified"] == 1
    # Step 5b (D49 close gate): closing now is refused; the console already shows why.
    assert detail["close_blocked_by"] == [
        "incident_cert_in_unreported",
        "incident_school_not_notified",
    ]
    early_close = _patch(api, lead, iid, {"status": "closed"})
    assert (early_close.status_code, early_close.json()["code"]) == (
        409,
        "incident_cert_in_unreported",
    )

    # Step 6 (T0 + 2 h): the dedicated school is phoned; the operator records it and contains.
    clock.set(t0 + 2 * H)
    phoned = _patch(api, lead, iid, {"status": "contained", "notified_tenant_ids": [str(school_b)]})
    assert phoned.status_code == 200, phoned.text
    assert phoned.json()["schools_notified"] == 2
    assert all(s["notice_due_at"] is None for s in phoned.json()["schools"])
    assert phoned.json()["close_blocked_by"] == ["incident_cert_in_unreported"]

    # Step 7 (C + 2 h): the school sees the notice in its register with its own clocks.
    clock.set(confirmed_at + 2 * H)
    register = _school(api, subject_a, "GET", "/breaches")
    assert register.status_code == 200, register.text
    (item,) = [b for b in register.json() if b["platform_incident_id"] == iid]
    bid = item["id"]
    breach = _school(api, subject_a, "GET", f"/breaches/{bid}").json()
    assert breach["origin"] == "processor_notice"
    assert _at(breach["noticed_at"]) == confirmed_at, "the school's T0 is SchoolOS's confirmation"
    duties = _duties(breach)
    assert _at(duties["board_notice"]["due_at"]) == confirmed_at + 24 * H
    assert duties["board_notice"]["default_rule"] is True, "24 h is a SchoolOS default"
    assert _at(duties["board_report"]["due_at"]) == confirmed_at + 72 * H
    assert duties["people_notified"]["due_at"] is None, "without delay: no fixed clock"
    assert breach["next_duty"]["key"] == "board_notice"
    assert breach["next_duty"]["hours_left"] == 22
    assert breach["processor_facts"]["approx_people"] == 120
    assert _notifications(admin_engine, school_a, "privacy.breach.notice_received", bid) >= 1

    # Step 8 (T0 + 5 h 30 min): CERT-In reported inside the 6 h clock; the clock clears.
    reported_at = clock.set(t0 + 5 * H + 30 * M)
    cert = _patch(api, second, iid, {"cert_in_reported_at": reported_at.isoformat()})
    assert cert.status_code == 200, cert.text
    assert _at(cert.json()["cert_in_reported_at"]) == reported_at <= t0 + 6 * H
    assert cert.json()["cert_in_due_at"] is None
    assert cert.json()["close_blocked_by"] == [], "nothing blocks a close any more"

    # Step 9 (C + 20 h): the school notifies the Board and the families and records both.
    clock.set(confirmed_at + 20 * H)
    board = _school_patch(
        api,
        subject_a,
        bid,
        {
            "board_notice": {"done_at": (confirmed_at + 19 * H).isoformat(), "method": "portal"},
            "people_notified": {"done_at": (confirmed_at + 20 * H).isoformat(), "method": "letter"},
            "status": "contained",
        },
    )
    assert board.status_code == 200, board.text
    assert _duties(board.json())["board_notice"]["overdue"] is False

    # Step 10: reminders fire once at C + 24 h and once at C + 60 h, not before, not twice.
    for hours, expected in ((23, []), (24, ["board_24h"]), (30, ["board_24h"])):
        clock.set(confirmed_at + hours * H)
        breach_service.send_reminders(school_a)
        assert _reminders(admin_engine, bid) == expected, hours
    sent_24h = _notifications(admin_engine, school_a, REMINDER, bid)
    assert sent_24h >= 1, "the owner (and principal, when there is one) is reminded"
    clock.set(confirmed_at + 60 * H)
    assert breach_service.send_reminders(school_a) >= 1
    assert breach_service.send_reminders(school_a) == 0, "idempotent on rerun"
    assert _reminders(admin_engine, bid) == ["board_24h", "report_60h"]
    assert _notifications(admin_engine, school_a, REMINDER, bid) == 2 * sent_24h

    # Step 11 (C + 66 h): the detailed report goes to the Board inside 72 h; the school closes.
    clock.set(confirmed_at + 66 * H)
    report = _school_patch(
        api,
        subject_a,
        bid,
        {"board_report": {"done_at": (confirmed_at + 65 * H).isoformat(), "method": "portal"}},
    )
    assert report.status_code == 200, report.text
    assert _duties(report.json())["board_report"]["overdue"] is False
    assert report.json()["next_duty"] is None
    closed_breach = _school_patch(api, subject_a, bid, {"status": "closed"})
    assert closed_breach.status_code == 200, closed_breach.text
    clock.set(confirmed_at + 80 * H)
    assert breach_service.send_reminders(school_a) == 0
    assert _reminders(admin_engine, bid) == ["board_24h", "report_60h"]

    # Step 12 (T0 + 4 days): SchoolOS resolves and closes the incident; it is locked (D48).
    clock.set(t0 + 4 * 24 * H)
    assert _patch(api, lead, iid, {"status": "resolved"}).status_code == 200
    closing = _patch(api, lead, iid, {"status": "closed"})
    assert closing.status_code == 200, closing.text
    assert closing.json()["status"] == "closed"
    refused = _patch(api, lead, iid, {"severity": "P2"})
    assert (refused.status_code, refused.json()["code"]) == (409, "incident_closed")
    more = api.call(
        "POST",
        f"/incidents/{iid}/schools",
        lead,
        json={"tenant_id": str(school_b), "categories": ["identity"], "approx_people": 1},
    )
    assert (more.status_code, more.json()["code"]) == (409, "incident_closed")
    reconfirm = api.call("POST", f"/incidents/{iid}/confirm", second)
    assert (reconfirm.status_code, reconfirm.json()["code"]) == (409, "incident_closed")

    # Step 13 (T0 + 4 days 1 h / 2 h): reopen asked by one operator, confirmed by another.
    asked_at = clock.set(t0 + 4 * 24 * H + 1 * H)
    reason = {"reason": "A third school reported the same export link after closure"}
    asked = api.call("POST", f"/incidents/{iid}/reopen", lead, json=reason)
    assert asked.status_code == 200, asked.text
    assert asked.json()["status"] == "closed", "a request alone does not reopen"
    assert _at(asked.json()["reopen_confirm_by"]) == asked_at + two_person.request_ttl()
    self_confirm = api.call("POST", f"/incidents/{iid}/reopen:confirm", lead)
    assert (self_confirm.status_code, self_confirm.json()["code"]) == (409, "same_operator")
    reopened_at = clock.set(t0 + 4 * 24 * H + 2 * H)
    reopened = api.call("POST", f"/incidents/{iid}/reopen:confirm", second)
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["status"] == "open"
    assert reopened.json()["reopen_count"] == 1
    # Step 13b (D49): school A's next pull brings the reopen; its closed breach opens for review.
    clock.set(reopened_at + 1 * M)
    assert breach_sync.sync_school(school_a) == 1
    assert breach_sync.sync_school(school_a) == 0, "repeated pulls add nothing"
    review = _school(api, subject_a, "GET", f"/breaches/{bid}").json()
    assert review["status"] == "open"
    assert review["closed_at"] is None
    assert review["platform_reopens"] == 1
    assert _at(review["platform_reopened_at"]) == reopened_at
    assert review["categories"] == ["attendance", "contact"], "codes and counts as before"
    row_a = {
        s["tenant_id"]: s for s in api.call("GET", f"/incidents/{iid}", lead).json()["schools"]
    }
    assert row_a[str(school_a)]["reopen_pulled"] == 1
    assert row_a[str(school_b)]["reopen_pulled"] == 0, "the dedicated host is told by phone"
    # The school reviews it and closes it again as usual (its duties are already recorded).
    reclosed = _school_patch(api, subject_a, bid, {"status": "closed"})
    assert reclosed.status_code == 200, reclosed.text
    assert breach_sync.sync_school(school_a) == 0, "a re-closed breach stays closed"
    # After a reopen the confirmed list stays frozen: another school needs a follow-up incident.
    late = api.call(
        "POST",
        f"/incidents/{iid}/schools",
        lead,
        json={"tenant_id": str(_dedicated_school()), "categories": ["contact"], "approx_people": 9},
    )
    assert (late.status_code, late.json()["code"]) == (409, "incident_confirmed")
    clock.set(t0 + 4 * 24 * H + 3 * H)
    assert _patch(api, lead, iid, {"status": "closed"}).status_code == 200

    # Step 14: the evidence. Platform chain: every step, by the right operator, no texts.
    events = _platform_events(iid)
    actions = [e["action"] for e in events]
    for action, count in (
        ("incident.opened", 1),
        ("incident.school_added", 2),
        ("incident.confirmed", 2),
        ("incident.school_notified", 1),
        ("incident.reopen_requested", 1),
        ("incident.reopened", 1),
        ("incident.school_reopen_pulled", 1),
    ):
        assert actions.count(action) == count, (action, actions)
    assert actions.count("incident.updated") >= 5
    by_action = {e["action"]: e for e in events}
    assert by_action["incident.opened"]["actor_id"] == lead.id
    assert by_action["incident.reopened"]["actor_id"] == second.id
    assert by_action["incident.school_notified"]["subject_tenant_id"] == school_a
    assert by_action["incident.school_notified"]["summary"] == {"source": "school_pull"}
    assert by_action["incident.school_reopen_pulled"]["subject_tenant_id"] == school_a
    assert by_action["incident.school_reopen_pulled"]["summary"] == {
        "source": "school_pull",
        "reopen": 1,
    }
    confirmers = [e["actor_id"] for e in events if e["action"] == "incident.confirmed"]
    assert confirmers == [lead.id, second.id]
    _no_personal_data([e["summary"] for e in events])
    # School chain: the notice arrived (actor platform), each duty recorded, the close.
    school_events = _school_events(admin_engine, school_a, bid)
    school_actions = [e["action"] for e in school_events]
    assert school_actions[0] == "privacy.breach.notice_received"
    assert school_events[0]["actor_type"] == "platform"
    assert school_events[0]["summary"]["platform_incident_id"] == iid
    # Two duty updates and the close, then the reopen (actor platform) and the second close.
    assert school_actions.count("privacy.breach.updated") == 4
    (reopen_event,) = [
        e for e in school_events if e["action"] == "privacy.breach.incident_reopened"
    ]
    assert reopen_event["actor_type"] == "platform"
    assert reopen_event["summary"]["status_before"] == "closed"
    assert school_actions.index("privacy.breach.incident_reopened") < len(school_actions) - 1
    done = [d for e in school_events for d in e["summary"].get("duties_done", [])]
    assert sorted(done) == ["board_notice", "board_report", "people_notified"]
    _no_personal_data([e["summary"] for e in school_events])
