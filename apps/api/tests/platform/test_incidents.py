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
    """An incident closed in one PATCH that also records the CERT-In report (D49 close gate)."""
    iid: str = _open(api, op)["id"]
    res = api.call(
        "PATCH",
        f"/incidents/{iid}",
        op,
        json={"status": "closed", "cert_in_reported_at": dt.datetime.now(dt.UTC).isoformat()},
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


# --- close gate and reopen propagation (D49; FR-DPDP-030, FR-DPDP-031, FR-DPDP-032) -------------


def _patch(api: Api, op: Any, iid: str, body: dict[str, Any]) -> Any:
    return api.call(
        "PATCH", f"/incidents/{iid}", op, json=body, headers=api.if_match(f"/incidents/{iid}", op)
    )


def _confirmed_with_school(
    api: Api, first: Any, second: Any, **extra: Any
) -> tuple[str, uuid.UUID]:
    iid: str = _open(api, first, **extra)["id"]
    school = _school()
    payload = {"tenant_id": str(school), "categories": ["contact"], "approx_people": 12}
    assert api.call("POST", f"/incidents/{iid}/schools", first, json=payload).status_code == 200
    assert api.call("POST", f"/incidents/{iid}/confirm", first).status_code == 200
    assert api.call("POST", f"/incidents/{iid}/confirm", second).status_code == 200
    return iid, school


def test_D49_FR_DPDP_030_close_refused_while_cert_in_is_not_reported(
    api: Api, make_operator: MakeOperator
) -> None:
    op = make_operator("platform_owner")
    iid = _open(api, op)["id"]
    shown = api.call("GET", f"/incidents/{iid}", op).json()
    assert shown["close_blocked_by"] == ["incident_cert_in_unreported"]
    listed = {i["id"]: i for i in api.call("GET", "/incidents", op).json()["data"]}
    assert listed[iid]["close_blocked_by"] == ["incident_cert_in_unreported"]
    refused = _patch(api, op, iid, {"status": "closed"})
    assert refused.status_code == 409, refused.text
    assert refused.json()["code"] == "incident_cert_in_unreported"
    assert "CERT-In" in refused.json()["detail"], "the error says what to do"
    assert api.call("GET", f"/incidents/{iid}", op).json() == shown, "nothing changed"
    assert _events("incident.updated", iid) == []
    # Recording the report in the same request satisfies the gate.
    reported = dt.datetime.now(dt.UTC).isoformat()
    done = _patch(api, op, iid, {"status": "closed", "cert_in_reported_at": reported})
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "closed"
    assert done.json()["close_blocked_by"] == []


def test_D49_FR_DPDP_030_not_reportable_incident_closes_without_cert_in(
    api: Api, make_operator: MakeOperator
) -> None:
    op = make_operator("platform_owner")
    iid = _open(api, op, cert_in_required=False)["id"]
    assert api.call("GET", f"/incidents/{iid}", op).json()["close_blocked_by"] == []
    assert _patch(api, op, iid, {"status": "closed"}).status_code == 200
    # Marking it not reportable in the closing request also lets it close.
    other = _open(api, op)["id"]
    res = _patch(api, op, other, {"status": "closed", "cert_in_required": False})
    assert res.status_code == 200, res.text


def test_D49_FR_DPDP_031_close_refused_while_a_school_is_not_notified(
    api: Api, make_operator: MakeOperator
) -> None:
    first, second = make_operator("platform_owner"), make_operator("platform_engineer")
    iid, school = _confirmed_with_school(api, first, second, cert_in_required=False)
    assert api.call("GET", f"/incidents/{iid}", first).json()["close_blocked_by"] == [
        "incident_school_not_notified"
    ]
    refused = _patch(api, first, iid, {"status": "closed"})
    assert refused.status_code == 409, refused.text
    assert refused.json()["code"] == "incident_school_not_notified"
    assert "notified" in refused.json()["detail"]
    # Both blockers: CERT-In is reported first.
    iid2, _ = _confirmed_with_school(api, first, second)
    assert api.call("GET", f"/incidents/{iid2}", first).json()["close_blocked_by"] == [
        "incident_cert_in_unreported",
        "incident_school_not_notified",
    ]
    both = _patch(api, first, iid2, {"status": "closed"})
    assert (both.status_code, both.json()["code"]) == (409, "incident_cert_in_unreported")
    # The school's pull notifies it; then the incident closes.
    assert incidents.mark_notice_pulled(school, uuid.UUID(iid))
    assert api.call("GET", f"/incidents/{iid}", first).json()["close_blocked_by"] == []
    assert _patch(api, first, iid, {"status": "closed"}).status_code == 200


def test_D49_FR_DPDP_031_phone_notice_in_the_closing_request_satisfies_the_gate(
    api: Api, make_operator: MakeOperator
) -> None:
    first, second = make_operator("platform_owner"), make_operator("platform_engineer")
    iid, school = _confirmed_with_school(api, first, second, cert_in_required=False)
    res = _patch(api, first, iid, {"status": "closed", "notified_tenant_ids": [str(school)]})
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "closed"
    assert res.json()["schools_notified"] == 1


def test_D49_unconfirmed_school_list_does_not_block_closing(
    api: Api, make_operator: MakeOperator
) -> None:
    """A list two operators never confirmed was never sent to a school (e.g. a false alarm)."""
    op = make_operator("platform_owner")
    iid = _open(api, op, cert_in_required=False)["id"]
    payload = {"tenant_id": str(_school()), "categories": ["contact"], "approx_people": 3}
    assert api.call("POST", f"/incidents/{iid}/schools", op, json=payload).status_code == 200
    assert api.call("POST", f"/incidents/{iid}/confirm", op).status_code == 200
    assert api.call("GET", f"/incidents/{iid}", op).json()["close_blocked_by"] == []
    assert _patch(api, op, iid, {"status": "closed"}).status_code == 200


def test_D49_FR_DPDP_032_a_reopen_is_offered_to_each_school_once(
    api: Api, make_operator: MakeOperator
) -> None:
    first, second = make_operator("platform_owner"), make_operator("platform_engineer")
    iid, school = _confirmed_with_school(api, first, second, cert_in_required=False)
    incident_id = uuid.UUID(iid)
    (notice,) = incidents.notices_for_school(school)
    assert (notice.reopen_seq, notice.reopened_at) == (0, None)
    assert incidents.mark_notice_pulled(school, incident_id)
    assert incidents.notices_for_school(school) == []
    assert incidents.mark_reopen_pulled(school, incident_id, 1) is False, "never reopened"
    assert _patch(api, first, iid, {"status": "closed"}).status_code == 200
    reason = {"reason": "Another kind of record found exposed after closure"}
    assert api.call("POST", f"/incidents/{iid}/reopen", first, json=reason).status_code == 200
    reopened = api.call("POST", f"/incidents/{iid}/reopen:confirm", second)
    assert reopened.status_code == 200, reopened.text
    body = reopened.json()
    assert body["reopen_count"] == 1
    assert body["last_reopened_at"] is not None
    assert body["schools"][0]["reopen_pulled"] == 0
    (event,) = _events("incident.reopened", iid)
    assert event["summary"]["reopen"] == 1
    (again,) = incidents.notices_for_school(school)
    assert again.incident_id == incident_id
    assert again.reopen_seq == 1
    assert again.reopened_at is not None
    assert again.categories == ["contact"]
    # Other schools never see it.
    assert incidents.mark_reopen_pulled(_school(), incident_id, 1) is False
    assert incidents.mark_reopen_pulled(school, incident_id, 2) is False, "not reached yet"
    assert incidents.mark_reopen_pulled(school, incident_id, 1) is True
    assert incidents.mark_reopen_pulled(school, incident_id, 1) is False, "idempotent"
    assert incidents.notices_for_school(school) == []
    detail = api.call("GET", f"/incidents/{iid}", first).json()
    assert detail["schools"][0]["reopen_pulled"] == 1
    assert detail["schools"][0]["reopen_pulled_at"] is not None
    (pulled,) = _events("incident.school_reopen_pulled", iid)
    assert pulled["summary"] == {"source": "school_pull", "reopen": 1}
    assert pulled["subject_tenant_id"] == school
    assert pulled["actor_id"] is None


def test_D49_the_database_keeps_reopen_counts_whole() -> None:
    with pytest.raises(Exception, match="incidents_reopened"), platform_session() as s:
        s.execute(
            text(
                "INSERT INTO platform.incidents (id, opened_at, severity, summary, "
                "lead_operator_id, created_by, reopen_count) SELECT :i, now(), 'P2', "
                "'Attendance records exposed briefly', id, id, 1 FROM platform.operators LIMIT 1"
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
        json={"status": "closed", "cert_in_reported_at": dt.datetime.now(dt.UTC).isoformat()},
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
