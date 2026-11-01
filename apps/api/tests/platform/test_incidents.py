"""SchoolOS incident records in the control plane (R3-1B; ADR-0050 decision 1; US-2801;
FR-DPDP-030, FR-DPDP-031, SEC-029). Categories and counts only. Synthetic data only."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import pytest
from sqlalchemy import text

from app.core.db import platform_session
from app.core.redaction import contains_full_aadhaar
from app.platform import incidents

from .conftest import Api, MakeOperator, letters

pytestmark = pytest.mark.db


def _school(mode: str = "shared") -> uuid.UUID:
    tenant_id = uuid.uuid4()
    with platform_session() as s:
        s.execute(
            text(
                "INSERT INTO platform.deployments (id, tenant_id, tenant_code, school_name, mode, "
                "tenant_status, status, heartbeat_key_id, heartbeat_key_ciphertext) "
                "VALUES (:i, :t, :c, 'Synthetic Incident School', :m, 'active', 'healthy', :k, "
                ":kc)"
            ),
            {
                "i": uuid.uuid4(),
                "t": tenant_id,
                "c": f"t-{letters(12)}",
                "m": mode,
                "k": "hb-1" if mode == "dedicated" else None,
                "kc": b"\x01" * 16 if mode == "dedicated" else None,
            },
        )
    return tenant_id


def _body(**extra: Any) -> dict[str, Any]:
    return {
        "opened_at": (dt.datetime.now(dt.UTC) - dt.timedelta(hours=2)).isoformat(),
        "severity": "P1",
        "summary": "Attendance and contact records of two schools exposed for about one hour",
        "cert_in_required": True,
        **extra,
    }


def _open(api: Api, op: Any, **extra: Any) -> dict[str, Any]:
    res = api.call("POST", "/incidents", op, json=_body(**extra))
    assert res.status_code == 201, res.text
    out: dict[str, Any] = res.json()
    return out


def _events(action: str, incident_id: str) -> list[dict[str, Any]]:
    with platform_session() as s:
        return [
            dict(r._mapping)
            for r in s.execute(
                text(
                    "SELECT action, summary, actor_id, subject_tenant_id "
                    "FROM platform.audit_events WHERE action = :a AND resource_id = :r "
                    "ORDER BY seq"
                ),
                {"a": action, "r": incident_id},
            )
        ]


@pytest.mark.parametrize(
    "summary",
    [
        "Student 987654321012 record was exposed to the wrong school",
        "Parent mail ravi.kumar@example.test leaked in the export",
        "x" * 501,
    ],
)
def test_FR_DPDP_030_summary_with_numbers_emails_or_too_long_is_refused(
    api: Api, make_operator: MakeOperator, summary: str
) -> None:
    op = make_operator("platform_engineer")
    res = api.call("POST", "/incidents", op, json=_body(summary=summary))
    assert res.status_code == 422, res.text


def test_FR_DPDP_030_the_database_refuses_digit_runs_too() -> None:
    with pytest.raises(ValueError, match="long numbers"):
        incidents.check_summary("Records of child 1234567 exposed")
    with pytest.raises(Exception, match="incidents_summary_check"), platform_session() as s:
        s.execute(
            text(
                "INSERT INTO platform.incidents (id, opened_at, severity, summary, "
                "lead_operator_id, created_by) SELECT :i, now(), 'P2', "
                "'Records of child 1234567 exposed', id, id FROM platform.operators LIMIT 1"
            ),
            {"i": uuid.uuid4()},
        )


def test_FR_DPDP_031_two_operators_confirm_the_affected_schools(
    api: Api, make_operator: MakeOperator
) -> None:
    first, second = make_operator("platform_owner"), make_operator("platform_engineer")
    out = _open(api, first)
    assert out["cert_in_due_at"] is not None
    iid = out["id"]
    none = api.call("POST", f"/incidents/{iid}/confirm", first)
    assert none.status_code == 409
    assert none.json()["code"] == "no_schools"
    school = _school()
    added = api.call(
        "POST",
        f"/incidents/{iid}/schools",
        first,
        json={
            "tenant_id": str(school),
            "categories": ["attendance", "contact"],
            "approx_people": 120,
        },
    )
    assert added.status_code == 200, added.text
    one = api.call("POST", f"/incidents/{iid}/confirm", first)
    assert one.status_code == 200, one.text
    assert one.json()["confirm_by"] is not None
    same = api.call("POST", f"/incidents/{iid}/confirm", first)
    assert same.status_code == 409
    assert same.json()["code"] == "same_operator"
    two = api.call("POST", f"/incidents/{iid}/confirm", second)
    assert two.status_code == 200, two.text
    body = two.json()
    assert body["confirmed_at"] is not None
    (row,) = body["schools"]
    assert row["notice_due_at"] is not None
    assert row["school_notified_at"] is None
    late = api.call(
        "POST",
        f"/incidents/{iid}/schools",
        first,
        json={"tenant_id": str(_school()), "categories": ["identity"], "approx_people": 1},
    )
    assert late.status_code == 409
    assert late.json()["code"] == "incident_confirmed"
    confirmations = _events("incident.confirmed", iid)
    assert [e["summary"]["confirmation"] for e in confirmations] == [1, 2]


def test_FR_DPDP_031_adding_a_school_clears_a_first_confirmation(
    api: Api, make_operator: MakeOperator
) -> None:
    op = make_operator("platform_owner")
    iid = _open(api, op)["id"]
    payload = {"tenant_id": str(_school()), "categories": ["financial"], "approx_people": 5}
    assert api.call("POST", f"/incidents/{iid}/schools", op, json=payload).status_code == 200
    assert api.call("POST", f"/incidents/{iid}/confirm", op).json()["confirmed_by"] == str(op.id)
    payload2 = {"tenant_id": str(_school()), "categories": ["identity"], "approx_people": 9}
    again = api.call("POST", f"/incidents/{iid}/schools", op, json=payload2)
    assert again.status_code == 200, again.text
    assert again.json()["confirmed_by"] is None
    assert len(again.json()["schools"]) == 2


def test_FR_DPDP_031_young_role_cannot_be_the_second_operator(
    api: Api, make_operator: MakeOperator
) -> None:
    first = make_operator("platform_owner")
    young = make_operator("platform_engineer", role_age_days=1)
    iid = _open(api, first)["id"]
    payload = {"tenant_id": str(_school()), "categories": ["documents"], "approx_people": 3}
    api.call("POST", f"/incidents/{iid}/schools", first, json=payload)
    api.call("POST", f"/incidents/{iid}/confirm", first)
    res = api.call("POST", f"/incidents/{iid}/confirm", young)
    assert res.status_code == 409
    assert res.json()["code"] == "approver_not_eligible"


def test_FR_DPDP_031_cert_in_and_dedicated_school_notice_are_recorded(
    api: Api, make_operator: MakeOperator
) -> None:
    first, second = make_operator("platform_owner"), make_operator("platform_owner")
    iid = _open(api, first)["id"]
    dedicated = _school("dedicated")
    payload = {"tenant_id": str(dedicated), "categories": ["identity"], "approx_people": 30}
    api.call("POST", f"/incidents/{iid}/schools", first, json=payload)
    early = api.call(
        "PATCH",
        f"/incidents/{iid}",
        first,
        json={"notified_tenant_ids": [str(dedicated)]},
        headers=api.if_match(f"/incidents/{iid}", first),
    )
    assert early.status_code == 409
    assert early.json()["code"] == "not_confirmed"
    api.call("POST", f"/incidents/{iid}/confirm", first)
    api.call("POST", f"/incidents/{iid}/confirm", second)
    reported = dt.datetime.now(dt.UTC).isoformat()
    res = api.call(
        "PATCH",
        f"/incidents/{iid}",
        first,
        json={
            "status": "contained",
            "cert_in_reported_at": reported,
            "notified_tenant_ids": [str(dedicated)],
        },
        headers=api.if_match(f"/incidents/{iid}", first),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "contained"
    assert body["cert_in_due_at"] is None
    assert body["schools_notified"] == 1
    assert body["schools"][0]["notice_due_at"] is None
    no_if_match = api.call("PATCH", f"/incidents/{iid}", first, json={"status": "resolved"})
    assert no_if_match.status_code == 400
    (event,) = _events("incident.updated", iid)
    assert event["summary"]["schools_notified"] == 1


def test_FR_DPDP_030_readers_and_writers(api: Api, make_operator: MakeOperator) -> None:
    viewer, billing, support = (
        make_operator("platform_viewer"),
        make_operator("billing_admin"),
        make_operator("support_agent"),
    )
    owner = make_operator("platform_owner")
    iid = _open(api, owner)["id"]
    assert api.call("GET", "/incidents", viewer).status_code == 200
    assert api.call("GET", f"/incidents/{iid}", support).status_code == 200
    assert api.call("GET", "/incidents", billing).status_code == 403
    assert api.call("POST", "/incidents", support, json=_body()).status_code == 403
    stale = api.call("POST", "/incidents", owner, json=_body(), fresh=False)
    assert stale.status_code == 428
    listed = api.call("GET", "/incidents", viewer).json()["data"]
    assert iid in {i["id"] for i in listed}
    (opened,) = _events("incident.opened", iid)
    assert "exposed" not in str(opened["summary"]), "the summary text is not audited"


def test_FR_DPDP_030_naive_times_are_refused_not_a_server_error(
    api: Api, make_operator: MakeOperator
) -> None:
    op = make_operator("platform_owner")
    naive = (dt.datetime.now(dt.UTC) - dt.timedelta(hours=1)).replace(tzinfo=None).isoformat()
    res = api.call("POST", "/incidents", op, json=_body(opened_at=naive))
    assert res.status_code == 422, res.text
    iid = _open(api, op)["id"]
    patch = api.call(
        "PATCH",
        f"/incidents/{iid}",
        op,
        json={"cert_in_reported_at": naive},
        headers=api.if_match(f"/incidents/{iid}", op),
    )
    assert patch.status_code == 422, patch.text


def test_FR_DPDP_030_spaced_aadhaar_in_summary_is_refused() -> None:
    number = next(
        f"{n:012d}" for n in range(234123412340, 234123412350) if contains_full_aadhaar(f"{n:012d}")
    )
    spaced = f"{number[:4]} {number[4:8]} {number[8:]}"
    with pytest.raises(ValueError, match="long numbers"):
        incidents.check_summary(f"Card {spaced} of one child was exposed")


# --- D48: a closed incident is locked; reopening takes two operators (ADR-0050; docs/16) -----


def _closed(api: Api, op: Any) -> str:
    iid: str = _open(api, op)["id"]
    res = api.call(
        "PATCH",
        f"/incidents/{iid}",
        op,
        json={"status": "closed"},
        headers=api.if_match(f"/incidents/{iid}", op),
    )
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "closed"
    return iid


@pytest.mark.parametrize(
    "patch",
    [
        {"status": "open"},
        {"severity": "P4"},
        {"summary": "Attendance records of one school exposed for a few minutes"},
        {"cert_in_required": False},
        {"status": "closed"},
    ],
)
def test_D48_closed_incident_refuses_every_patch(
    api: Api, make_operator: MakeOperator, patch: dict[str, Any]
) -> None:
    op = make_operator("platform_owner")
    iid = _closed(api, op)
    before = api.call("GET", f"/incidents/{iid}", op).json()
    res = api.call(
        "PATCH", f"/incidents/{iid}", op, json=patch, headers=api.if_match(f"/incidents/{iid}", op)
    )
    assert res.status_code == 409, res.text
    assert res.json()["code"] == "incident_closed"
    after = api.call("GET", f"/incidents/{iid}", op).json()
    assert after == before


def test_D48_closed_incident_refuses_schools_and_confirmations(
    api: Api, make_operator: MakeOperator
) -> None:
    op = make_operator("platform_owner")
    iid = _closed(api, op)
    payload = {"tenant_id": str(_school()), "categories": ["identity"], "approx_people": 2}
    added = api.call("POST", f"/incidents/{iid}/schools", op, json=payload)
    assert added.status_code == 409
    assert added.json()["code"] == "incident_closed"
    confirmed = api.call("POST", f"/incidents/{iid}/confirm", op)
    assert confirmed.status_code == 409
    assert confirmed.json()["code"] == "incident_closed"


def test_D48_reopen_needs_a_second_operator_and_is_audited(
    api: Api, make_operator: MakeOperator
) -> None:
    first, second = make_operator("platform_owner"), make_operator("platform_engineer")
    iid = _closed(api, first)
    reason = {"reason": "A second school reported the same exposure after closure"}
    requested = api.call("POST", f"/incidents/{iid}/reopen", first, json=reason)
    assert requested.status_code == 200, requested.text
    body = requested.json()
    assert body["status"] == "closed", "a request alone does not reopen"
    assert body["reopen_requested_by"] == str(first.id)
    assert body["reopen_reason"] == reason["reason"]
    assert body["reopen_confirm_by"] is not None
    again = api.call("POST", f"/incidents/{iid}/reopen", second, json=reason)
    assert again.status_code == 409
    assert again.json()["code"] == "reopen_requested"
    same = api.call("POST", f"/incidents/{iid}/reopen:confirm", first)
    assert same.status_code == 409
    assert same.json()["code"] == "same_operator"
    done = api.call("POST", f"/incidents/{iid}/reopen:confirm", second)
    assert done.status_code == 200, done.text
    out = done.json()
    assert out["status"] == "open"
    assert out["reopen_requested_by"] is None
    assert out["reopen_reason"] is None
    edited = api.call(
        "PATCH",
        f"/incidents/{iid}",
        first,
        json={"severity": "P2"},
        headers=api.if_match(f"/incidents/{iid}", first),
    )
    assert edited.status_code == 200, edited.text
    (req,) = _events("incident.reopen_requested", iid)
    (reopened,) = _events("incident.reopened", iid)
    assert req["actor_id"] == first.id
    assert reopened["actor_id"] == second.id
    assert reopened["summary"]["requested_by"] == str(first.id)
    assert "second school" not in str(req["summary"]), "the reason text is not audited"


def test_D48_reopen_only_for_closed_and_reason_is_checked(
    api: Api, make_operator: MakeOperator
) -> None:
    op = make_operator("platform_owner")
    open_iid = _open(api, op)["id"]
    reason = {"reason": "Closed by mistake before the CERT-In report"}
    res = api.call("POST", f"/incidents/{open_iid}/reopen", op, json=reason)
    assert res.status_code == 409
    assert res.json()["code"] == "incident_not_closed"
    iid = _closed(api, op)
    for bad in (
        "Parent 9876543210 called again",
        "Mail from ravi.kumar@example.test",
        "short",
    ):
        refused = api.call("POST", f"/incidents/{iid}/reopen", op, json={"reason": bad})
        assert refused.status_code == 422, bad
    nothing = api.call("POST", f"/incidents/{iid}/reopen:confirm", op)
    assert nothing.status_code == 409
    assert nothing.json()["code"] == "no_reopen_request"


def test_D48_reopen_request_can_be_withdrawn(api: Api, make_operator: MakeOperator) -> None:
    first, second = make_operator("platform_owner"), make_operator("platform_owner")
    iid = _closed(api, first)
    reason = {"reason": "Need to add the CERT-In report time recorded late"}
    assert api.call("POST", f"/incidents/{iid}/reopen", first, json=reason).status_code == 200
    gone = api.call("POST", f"/incidents/{iid}/reopen:withdraw", first)
    assert gone.status_code == 200, gone.text
    assert gone.json()["reopen_requested_by"] is None
    assert gone.json()["status"] == "closed"
    late = api.call("POST", f"/incidents/{iid}/reopen:confirm", second)
    assert late.status_code == 409
    assert late.json()["code"] == "no_reopen_request"
    assert len(_events("incident.reopen_withdrawn", iid)) == 1


def test_D48_young_role_cannot_confirm_a_reopen(api: Api, make_operator: MakeOperator) -> None:
    first = make_operator("platform_owner")
    young = make_operator("platform_engineer", role_age_days=1)
    iid = _closed(api, first)
    reason = {"reason": "Another affected school found after closure"}
    api.call("POST", f"/incidents/{iid}/reopen", first, json=reason)
    res = api.call("POST", f"/incidents/{iid}/reopen:confirm", young)
    assert res.status_code == 409
    assert res.json()["code"] == "approver_not_eligible"


def test_D48_reopen_routes_need_manage(api: Api, make_operator: MakeOperator) -> None:
    owner, viewer = make_operator("platform_owner"), make_operator("platform_viewer")
    iid = _closed(api, owner)
    reason = {"reason": "Another affected school found after closure"}
    assert api.call("POST", f"/incidents/{iid}/reopen", viewer, json=reason).status_code == 403
    stale = api.call("POST", f"/incidents/{iid}/reopen", owner, json=reason, fresh=False)
    assert stale.status_code == 428
    for verb in ("confirm", "withdraw"):
        assert api.call("POST", f"/incidents/{iid}/reopen:{verb}", viewer).status_code == 403


def test_D48_the_database_keeps_the_reopen_request_whole() -> None:
    with pytest.raises(Exception, match="incidents_reopen"), platform_session() as s:
        s.execute(
            text(
                "INSERT INTO platform.incidents (id, opened_at, severity, summary, "
                "lead_operator_id, created_by, reopen_requested_by) SELECT :i, now(), 'P2', "
                "'Attendance records exposed briefly', id, id, id FROM platform.operators LIMIT 1"
            ),
            {"i": uuid.uuid4()},
        )


# --- no personal data in logs (SEC-008, invariant 5; NFR-SEC-008) --------------------------------


def _spaced_aadhaar() -> str:
    number = next(
        f"{n:012d}" for n in range(234123412340, 234123412350) if contains_full_aadhaar(f"{n:012d}")
    )
    return f"{number[:4]} {number[4:8]} {number[8:]}"


def test_SEC_008_incident_texts_and_the_school_pull_never_reach_logs(
    api: Api,
    make_operator: MakeOperator,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A name typed into a summary or reopen reason (the validator cannot catch names) and a
    refused number, e-mail or spaced Aadhaar never appear in a log line, and neither does the
    payload a school pulls."""
    first, second = make_operator("platform_owner"), make_operator("platform_engineer")
    name = "Kothapalli Synthetica"
    aadhaar = _spaced_aadhaar()
    capsys.readouterr()
    caplog.clear()
    iid = _open(api, first, summary=f"Contact list of {name} and 40 parents exposed")["id"]
    school = _school()
    payload = {"tenant_id": str(school), "categories": ["contact"], "approx_people": 41}
    api.call("POST", f"/incidents/{iid}/schools", first, json=payload)
    api.call("POST", f"/incidents/{iid}/confirm", first)
    assert api.call("POST", f"/incidents/{iid}/confirm", second).status_code == 200
    for refused in (
        f"Card {aadhaar} of one child was exposed",
        "Phone 9876543210 of one parent was exposed",
        "Mail synthetica.parent@example.test was exposed",
    ):
        bad = api.call("POST", "/incidents", first, json=_body(summary=refused))
        assert bad.status_code == 422
        assert aadhaar not in bad.text
        assert "9876543210" not in bad.text
        assert "synthetica.parent@example.test" not in bad.text
    (notice,) = incidents.notices_for_school(school)
    assert name in notice.summary
    assert incidents.mark_notice_pulled(school, uuid.UUID(iid))
    closing = api.call(
        "PATCH",
        f"/incidents/{iid}",
        first,
        json={"status": "closed"},
        headers=api.if_match(f"/incidents/{iid}", first),
    )
    assert closing.status_code == 200, closing.text
    reason = {"reason": f"{name} reported a second exposure after closure"}
    assert api.call("POST", f"/incidents/{iid}/reopen", first, json=reason).status_code == 200
    refused_reason = {"reason": f"Card {aadhaar} found after closure"}
    assert api.call(
        "POST", f"/incidents/{iid}/reopen", second, json=refused_reason
    ).status_code in (
        409,
        422,
    )
    assert api.call("POST", f"/incidents/{iid}/reopen:confirm", second).status_code == 200
    api.call("GET", f"/incidents/{iid}", first)
    api.call("GET", "/incidents", first)
    out, err = capsys.readouterr()
    logs = out + err + caplog.text
    for secret in ("Kothapalli", aadhaar, aadhaar.replace(" ", ""), "9876543210", "@example.test"):
        assert secret not in logs, secret
    summaries = " ".join(
        str(e["summary"])
        for action in (
            "incident.opened",
            "incident.updated",
            "incident.confirmed",
            "incident.school_added",
            "incident.school_notified",
            "incident.reopen_requested",
            "incident.reopened",
        )
        for e in _events(action, iid)
    )
    assert "Kothapalli" not in summaries
