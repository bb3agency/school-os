"""TC request log (FO-09; FR-CERT-026..FR-CERT-045). Synthetic data only.

Staff log requests that arrive on paper, by phone or in person; the request follows its TC
(prepared -> in preparation, issued -> ready, rejected/withdrawn/cancelled -> prepared again),
is handed over or cancelled by the office, and is overdue after the configured number of days.
The TC preview suggests the day received, the leaving date and reason, and the last conduct.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.certificates import service as certificates
from app.certificates import tc_requests
from app.certificates.schemas import ReasonIn

pytestmark = pytest.mark.db
C = sys.modules["sos_test_certificates_support"]
BASE = "/api/v1/tc-requests"
CANCEL_REASON = "Kothapalli family decided to stay at this school"


def _log(api: Any, school: Any, sid: uuid.UUID, role: str = "office_admin", **body: Any) -> Any:
    payload = {
        "student_id": str(sid),
        "received_on": C.today().isoformat(),
        "channel": "paper",
        **body,
    }
    return api.call(school.people[role], "POST", BASE, json=payload)


def _move(api: Any, school: Any, out: dict[str, Any], status: str, **kw: Any) -> Any:
    body: dict[str, Any] = {"status": status}
    if "reason" in kw:
        body["reason"] = kw.pop("reason")
    return api.call(
        school.people[kw.pop("role", "office_admin")],
        "POST",
        f"{BASE}/{out['id']}/status",
        json=body,
        headers={"If-Match": f'W/"{kw.pop("version", out["version"])}"'},
    )


def _get(api: Any, school: Any, rid: str, role: str = "office_admin") -> Any:
    return api.call(school.people[role], "GET", f"{BASE}/{rid}")


def _actions(admin: Engine, tenant_id: uuid.UUID, rid: str) -> list[tuple[str, dict[str, Any]]]:
    with admin.connect() as c:
        rows = c.execute(
            text(
                "SELECT action, summary FROM audit.events WHERE tenant_id = :t "
                "AND resource_type = 'tc_request' AND resource_id = :r ORDER BY seq"
            ),
            {"t": tenant_id, "r": rid},
        )
        return [(r.action, r.summary) for r in rows]


def test_FR_CERT_026_log_a_request_returns_location_etag_and_is_audited(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    sid = C.student(school)
    res = _log(
        api,
        school,
        sid,
        channel="in_person",
        leaving_date=C.today().isoformat(),
        leaving_reason="parent_transferred",
    )
    assert res.status_code == 201, res.text
    out = res.json()
    assert out["status"] == "received"
    assert out["channel"] == "in_person"
    assert out["received_by_name"] == "Synthetic Clerk Lakshmi"
    assert out["student_name"].startswith("Synthetica Certificate Student")
    assert out["class_label"]
    assert out["days_open"] == 0
    assert out["overdue"] is False
    assert out["can_prepare"] is True
    assert out["can_cancel"] is True
    assert res.headers["Location"] == f"{BASE}/{out['id']}"
    assert res.headers["ETag"] == 'W/"1"'
    events = _actions(admin_engine, school.tenant_id, out["id"])
    assert [a for a, _ in events] == ["tc_request.logged"]
    summary = events[0][1]
    assert summary["student_id"] == str(sid)
    assert "Synthetica" not in json.dumps(summary)


def test_FR_CERT_027_one_open_request_per_student_and_no_future_dates(
    school: Any, api: Any
) -> None:
    sid = C.student(school)
    assert _log(api, school, sid).status_code == 201
    again = _log(api, school, sid)
    assert again.status_code == 409
    assert again.json()["code"] == "tc_request_open_exists"
    later = (C.today() + dt.timedelta(days=1)).isoformat()
    future = _log(api, school, C.student(school), received_on=later)
    assert future.status_code == 422
    assert future.json()["errors"][0]["code"] == "date_in_future"


def test_FR_CERT_043_permissions_scope_and_other_school(school: Any, api: Any, world: Any) -> None:
    sid = C.student(school, "section_10a")
    out = _log(api, school, sid).json()
    # Allowed: reader (auditor), issuer, approver.
    for role in ("auditor_readonly", "office_staff", "principal"):
        assert _get(api, school, out["id"], role).status_code == 200, role
    # Disallowed role: 403 on read and write.
    assert _get(api, school, out["id"], "accountant").status_code == 403
    assert _log(api, school, C.student(school), role="accountant").status_code == 403
    assert _log(api, school, C.student(school), role="auditor_readonly").status_code == 403
    # Scoped clerk (9A only): a 10A student's request is 404 and not listed.
    assert _get(api, school, out["id"], "scoped_clerk").status_code == 404
    listed = api.call(school.people["scoped_clerk"], "GET", BASE).json()
    assert out["id"] not in {r["id"] for r in listed["data"]}
    assert _log(api, school, C.student(school, "section_10a"), role="scoped_clerk").status_code == (
        404
    )
    # Another school: 404, never 403.
    other = world.a.people["office_admin"]
    assert api.call(other, "GET", f"{BASE}/{out['id']}").status_code == 404
    moved = api.call(
        other,
        "POST",
        f"{BASE}/{out['id']}/status",
        json={"status": "in_preparation"},
        headers={"If-Match": 'W/"1"'},
    )
    assert moved.status_code == 404


def test_FR_CERT_031_032_request_follows_the_tc_to_ready_and_is_handed_over(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    sid = C.student(school)
    out = _log(api, school, sid).json()
    pending = C.pending_tc(school, sid)
    now = _get(api, school, out["id"]).json()
    assert now["status"] == "in_preparation"
    assert now["certificate_id"] == str(pending.id)
    assert now["certificate_status"] == "pending"
    assert now["preparing_by_name"] == "Synthetic Clerk Lakshmi"
    # A TC waiting for approval: the request cannot be cancelled or handed over.
    assert now["can_cancel"] is False
    refused = _move(api, school, now, "cancelled", reason=CANCEL_REASON)
    assert refused.status_code == 409
    assert refused.json()["code"] == "tc_request_certificate_pending"
    early = _move(api, school, now, "issued")
    assert early.status_code == 409
    assert early.json()["code"] == "tc_request_wrong_status"
    issued = C.approve(school, pending)
    ready = _get(api, school, out["id"]).json()
    assert ready["status"] == "ready"
    assert ready["ready_by_name"] == "Synthetic Principal Suresh"
    assert ready["certificate_serial"] == issued.serial
    assert ready["can_hand_over"] is True
    stale = _move(api, school, ready, "issued", version=ready["version"] - 1)
    assert stale.status_code == 412
    handed = _move(api, school, ready, "issued", role="office_staff")
    assert handed.status_code == 200, handed.text
    assert handed.json()["status"] == "issued"
    assert handed.json()["issued_at"] is not None
    assert handed.headers["ETag"] == f'W/"{handed.json()["version"]}"'
    steps = [
        (s["from"], s["to"], s["automatic"])
        for a, s in _actions(admin_engine, school.tenant_id, out["id"])
        if a == "tc_request.status_changed"
    ]
    assert steps == [
        ("received", "in_preparation", True),
        ("in_preparation", "ready", True),
        ("ready", "issued", False),
    ]
    # Closed: a new request may be logged for the same student.
    assert _log(api, school, sid).status_code == 201


def test_FR_CERT_033_rejected_tc_sends_the_request_back(school: Any, api: Any) -> None:
    sid = C.student(school)
    out = _log(api, school, sid).json()
    pending = C.pending_tc(school, sid)
    C.call(
        school,
        school.people["principal"],
        "principal",
        certificates.reject,
        pending.id,
        ReasonIn(reason="The leaving date does not match the letter"),
        expected_version=pending.version,
    )
    now = _get(api, school, out["id"]).json()
    assert now["status"] == "in_preparation"
    assert now["certificate_id"] is None
    # A second TC links again.
    again = C.pending_tc(school, sid)
    assert _get(api, school, out["id"]).json()["certificate_id"] == str(again.id)


def test_FR_CERT_027_a_live_tc_is_linked_when_the_request_is_logged_late(
    school: Any, api: Any
) -> None:
    sid = C.student(school)
    C.issued_tc(school, sid)
    out = _log(api, school, sid, received_on=(C.today() - dt.timedelta(days=2)).isoformat())
    assert out.status_code == 201, out.text
    body = out.json()
    assert body["status"] == "ready"
    assert body["days_open"] == 2
    assert body["can_hand_over"] is True


def test_FR_CERT_034_manual_prepare_and_cancel_with_reason(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    out = _log(api, school, C.student(school)).json()
    prepared = _move(api, school, out, "in_preparation")
    assert prepared.status_code == 200, prepared.text
    body = prepared.json()
    assert body["status"] == "in_preparation"
    assert body["can_prepare"] is False
    no_reason = _move(api, school, body, "cancelled")
    assert no_reason.status_code == 422
    short = _move(api, school, body, "cancelled", reason="short")
    assert short.status_code == 422
    done = _move(api, school, body, "cancelled", reason=CANCEL_REASON)
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "cancelled"
    assert done.json()["cancel_reason"] == CANCEL_REASON
    assert done.json()["cancelled_by_name"] == "Synthetic Clerk Lakshmi"
    again = _move(api, school, done.json(), "in_preparation")
    assert again.status_code == 409
    # The reason is staff text: never in the audit summary.
    assert CANCEL_REASON not in json.dumps(
        [s for _, s in _actions(admin_engine, school.tenant_id, out["id"])]
    )


def test_FR_CERT_036_037_overdue_after_the_configured_days(school: Any, api: Any) -> None:
    days = tc_requests.settings().overdue_after_days
    assert days == 7
    assert tc_requests.settings().verified is False
    late = _log(
        api,
        school,
        C.student(school),
        received_on=(C.today() - dt.timedelta(days=days + 1)).isoformat(),
    ).json()
    edge = _log(
        api,
        school,
        C.student(school),
        received_on=(C.today() - dt.timedelta(days=days)).isoformat(),
    ).json()
    assert late["overdue"] is True
    assert late["days_open"] == days + 1
    assert edge["overdue"] is False
    listed = api.call(school.people["principal"], "GET", BASE, params={"overdue": "true"})
    assert listed.status_code == 200, listed.text
    body = listed.json()
    ids = {r["id"] for r in body["data"]}
    assert late["id"] in ids
    assert edge["id"] not in ids
    assert all(r["overdue"] for r in body["data"])
    assert body["overdue_count"] >= 1
    assert body["overdue_after_days"] == days
    assert body["overdue_verified"] is False
    # Cancelled requests are not overdue.
    _move(api, school, late, "cancelled", reason=CANCEL_REASON)
    after = api.call(school.people["principal"], "GET", BASE, params={"overdue": "true"}).json()
    assert late["id"] not in {r["id"] for r in after["data"]}


def test_FR_CERT_035_list_filters_and_pages(school: Any, api: Any) -> None:
    sid = C.student(school)
    out = _log(api, school, sid).json()
    assert _log(api, school, C.student(school)).status_code == 201
    one = api.call(
        school.people["office_admin"], "GET", BASE, params={"student_id": str(sid)}
    ).json()
    assert [r["id"] for r in one["data"]] == [out["id"]]
    received = api.call(
        school.people["office_admin"], "GET", BASE, params={"status": "received", "limit": 1}
    ).json()
    assert len(received["data"]) == 1
    assert received["next_cursor"]
    nxt = api.call(
        school.people["office_admin"],
        "GET",
        BASE,
        params={"status": "received", "limit": 1, "cursor": received["next_cursor"]},
    ).json()
    assert nxt["data"][0]["id"] != received["data"][0]["id"]


@pytest.fixture
def ap_format(school: Any, admin_engine: Engine) -> Iterator[None]:
    def put(value: str) -> None:
        with admin_engine.begin() as c:
            c.execute(
                text(
                    "UPDATE core.tenants SET settings = COALESCE(settings, '{}'::jsonb) "
                    "|| CAST(:p AS jsonb) WHERE id = :t"
                ),
                {
                    "p": json.dumps({"certificate_format": {"transfer": value}}),
                    "t": school.tenant_id,
                },
            )

    put("ap-tc-2027")
    yield
    put("generic-v1")


def _suggested(school: Any, sid: uuid.UUID) -> dict[str, tuple[str, str]]:
    preview = C.call(
        school,
        school.people["office_admin"],
        "office_admin",
        certificates.preview,
        sid,
        "transfer",
    )
    return {s.key: (s.value, s.source) for s in preview.input_suggestions}


def test_FR_CERT_028_preview_suggests_from_the_request(school: Any, api: Any) -> None:
    sid = C.student(school)
    assert "leaving_date" not in _suggested(school, sid)
    leaving = (C.today() - dt.timedelta(days=1)).isoformat()
    _log(api, school, sid, leaving_date=leaving, leaving_reason="parent_request")
    got = _suggested(school, sid)
    assert got["leaving_date"] == (leaving, "tc_request")
    assert got["leaving_reason"] == ("parent_request", "tc_request")
    # The generic format has no application date input: never suggested.
    assert "application_date" not in got


def test_FR_CERT_028_029_ap_format_gets_the_application_date_and_conduct(
    school: Any, api: Any, ap_format: None
) -> None:
    sid = C.student(school)
    C.issue(school, sid, "conduct", inputs={"conduct": "very_good"})
    received = (C.today() - dt.timedelta(days=3)).isoformat()
    ahead = (C.today() + dt.timedelta(days=10)).isoformat()
    _log(api, school, sid, received_on=received, leaving_date=ahead)
    got = _suggested(school, sid)
    assert got["application_date"] == (received, "tc_request")
    assert got["conduct"] == ("very_good", "conduct_certificate")
    # A leaving date still ahead is not suggested (the TC refuses future dates).
    assert "leaving_date" not in got


def test_FR_CERT_038_fee_note_is_information_only(school: Any, api: Any) -> None:
    """Fees are off for this school: no note, and nothing blocks (D16)."""
    out = _log(api, school, C.student(school)).json()
    body = _get(api, school, out["id"], "owner").json()
    assert body["fee_note"] is None
    assert body["can_prepare"] is False  # the owner holds no certificate.issue
