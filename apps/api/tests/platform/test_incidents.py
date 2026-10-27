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
