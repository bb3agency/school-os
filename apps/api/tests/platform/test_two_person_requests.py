"""Two-person requests expire, can be withdrawn and re-check both operators; critical
announcements are two-person; closed schools and overlapping periods are not invoiced.

Audit 2026-10-05 (app-logic) A-13, A-14 and the "Platform" hardening items; owner decision
2026-10-07 (implement the lead's recommendation). Synthetic data only.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Callable
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

from app.core.db import platform_session
from app.platform.permissions import catalog
from app.platform.schemas import BreakGlassIn, InvoiceLineIn

from .conftest import Api, MakeOperator, Operator, provision_payload

pytestmark = pytest.mark.db

TTL = dt.timedelta(hours=catalog().two_person_request_ttl_hours)


def _school(api: Api, owner: Operator, plan: uuid.UUID) -> dict[str, Any]:
    res = api.call("POST", "/tenants", owner, json=provision_payload(plan, start_as="active"))
    assert res.status_code == 201, res.text
    out: dict[str, Any] = res.json()
    return out


def _live_school(api: Api, owner: Operator, plan: uuid.UUID) -> str:
    tid = str(_school(api, owner, plan)["tenant_id"])
    assert api.call("POST", f"/tenants/{tid}/activate", owner).status_code == 200
    return tid


def _request_offboarding(api: Api, op: Operator, tid: str) -> Any:
    return api.call(
        "POST", f"/tenants/{tid}/offboarding", op, json={"reason": "School's written request"}
    )


def _age(admin: Engine, sql: str, ident: Any, by: dt.timedelta) -> None:
    with admin.begin() as c:
        c.execute(text(sql), {"i": ident, "d": by})


# --- A-13: offboarding -------------------------------------------------------------------------


def test_A_13_offboarding_request_expires_and_can_be_made_again(
    api: Api,
    owner: Operator,
    make_operator: MakeOperator,
    make_plan: Callable[..., uuid.UUID],
    admin_engine: Engine,
) -> None:
    first, second = make_operator("platform_owner"), make_operator("platform_owner")
    tid = _live_school(api, owner, make_plan())
    req = _request_offboarding(api, first, tid)
    assert req.status_code == 202, req.text
    expires = dt.datetime.fromisoformat(req.json()["offboard_request_expires_at"])
    asked = dt.datetime.fromisoformat(req.json()["offboard_requested_at"])
    assert expires - asked == TTL
    assert req.json()["offboard_requested_by"] == str(first.id)
    _age(
        admin_engine,
        "UPDATE platform.deployments SET offboard_requested_at = offboard_requested_at - :d "
        "WHERE tenant_id = :i",
        uuid.UUID(tid),
        TTL + dt.timedelta(minutes=1),
    )
    late = api.call("POST", f"/tenants/{tid}/offboarding:approve", second)
    assert (late.status_code, late.json()["code"]) == (409, "request_expired")
    # An expired request does not block a new one.
    again = _request_offboarding(api, second, tid)
    assert again.status_code == 202, again.text
    ok = api.call("POST", f"/tenants/{tid}/offboarding:approve", first)
    assert ok.status_code == 200, ok.text
    assert ok.json()["tenant_status"] == "offboarding"


def test_A_13_offboarding_request_can_be_withdrawn(
    api: Api, owner: Operator, make_operator: MakeOperator, make_plan: Callable[..., uuid.UUID]
) -> None:
    first, second = make_operator("platform_owner"), make_operator("platform_owner")
    viewer = make_operator("platform_viewer")
    tid = _live_school(api, owner, make_plan())
    none = api.call("POST", f"/tenants/{tid}/offboarding:withdraw", second)
    assert (none.status_code, none.json()["code"]) == (409, "not_requested")
    assert _request_offboarding(api, first, tid).status_code == 202
    assert api.call("POST", f"/tenants/{tid}/offboarding:withdraw", viewer).status_code == 403
    gone = api.call("POST", f"/tenants/{tid}/offboarding:withdraw", second)
    assert gone.status_code == 200, gone.text
    assert gone.json()["offboard_requested_at"] is None
    assert gone.json()["offboard_request_expires_at"] is None
    late = api.call("POST", f"/tenants/{tid}/offboarding:approve", second)
    assert (late.status_code, late.json()["code"]) == (409, "not_requested")
    with platform_session() as s:
        actions: list[str] = list(
            s.execute(
                text(
                    "SELECT action FROM platform.audit_events WHERE subject_tenant_id = :t "
                    "ORDER BY seq"
                ),
                {"t": tid},
            ).scalars()
        )
    assert "tenant.offboard_withdrawn" in actions


def test_A_13_offboarding_approval_rechecks_the_requester(
    api: Api, owner: Operator, make_operator: MakeOperator, make_plan: Callable[..., uuid.UUID]
) -> None:
    """Owner A requests, then leaves (deactivated) or loses the role; B alone cannot finish."""
    first, second = make_operator("platform_owner"), make_operator("platform_owner")
    tid = _live_school(api, owner, make_plan())
    assert _request_offboarding(api, first, tid).status_code == 202
    gone = api.call("POST", f"/operators/{first.id}/deactivate", owner)
    assert gone.status_code == 200, gone.text
    res = api.call("POST", f"/tenants/{tid}/offboarding:approve", second)
    assert (res.status_code, res.json()["code"]) == (409, "requester_not_authorised")

    third = make_operator("platform_owner")
    tid2 = _live_school(api, owner, make_plan())
    assert _request_offboarding(api, third, tid2).status_code == 202
    demoted = api.call(
        "PUT", f"/operators/{third.id}/roles", owner, json={"roles": ["platform_viewer"]}
    )
    assert demoted.status_code == 200, demoted.text
    res = api.call("POST", f"/tenants/{tid2}/offboarding:approve", second)
    assert (res.status_code, res.json()["code"]) == (409, "requester_not_authorised")


# --- A-13: emergency break-glass ---------------------------------------------------------------


def _emergency(api: Api, op: Operator, tid: str, reason_code: str = "security_incident") -> Any:
    return api.call(
        "POST",
        "/break-glass-requests",
        op,
        json={
            "tenant_id": tid,
            "reason_code": reason_code,
            "reason": "Suspected account takeover under investigation",
            "scope": {},
            "duration_minutes": 60,
            "emergency": True,
        },
    )


def test_A_13_emergency_needs_an_incident_or_legal_reason() -> None:
    with pytest.raises(ValidationError, match="security_incident or legal_obligation"):
        BreakGlassIn(
            tenant_id=uuid.uuid4(),
            reason_code="support_request",
            reason="The school asked for help with an import",
            duration_minutes=60,
            emergency=True,
        )
    for code in ("security_incident", "legal_obligation"):
        BreakGlassIn(
            tenant_id=uuid.uuid4(),
            reason_code=code,
            reason="Active incident under investigation",
            duration_minutes=60,
            emergency=True,
        )


def test_A_13_emergency_confirmations_expire_recheck_and_can_be_withdrawn(
    api: Api,
    owner: Operator,
    make_operator: MakeOperator,
    make_plan: Callable[..., uuid.UUID],
    admin_engine: Engine,
) -> None:
    agent = make_operator("support_agent")
    first, second = make_operator("platform_owner"), make_operator("platform_owner")
    tid = str(_school(api, owner, make_plan())["tenant_id"])
    support = _emergency(api, agent, tid, reason_code="support_request")
    assert support.status_code == 422, support.text

    # Expiry: the first confirmation does not stay valid for ever.
    rid = _emergency(api, agent, tid).json()["id"]
    one = api.call("POST", f"/break-glass-requests/{rid}/emergency-confirm", first)
    assert one.status_code == 200, one.text
    created = dt.datetime.fromisoformat(one.json()["created_at"])
    assert dt.datetime.fromisoformat(one.json()["confirm_by"]) - created == TTL
    _age(
        admin_engine,
        "UPDATE platform.breakglass_requests SET created_at = created_at - :d WHERE id = :i",
        uuid.UUID(rid),
        TTL + dt.timedelta(minutes=1),
    )
    late = api.call("POST", f"/break-glass-requests/{rid}/emergency-confirm", second)
    assert (late.status_code, late.json()["code"]) == (409, "request_expired")
    # Withdraw: any operator who may request break-glass; it ends revoked and cannot be redone.
    viewer = make_operator("platform_viewer")
    assert api.call("POST", f"/break-glass-requests/{rid}/withdraw", viewer).status_code == 403
    gone = api.call("POST", f"/break-glass-requests/{rid}/withdraw", agent)
    assert gone.status_code == 200, gone.text
    assert (gone.json()["status"], gone.json()["confirm_by"]) == ("revoked", None)
    again = api.call("POST", f"/break-glass-requests/{rid}/withdraw", agent)
    assert (again.status_code, again.json()["code"]) == (409, "invalid_state")

    # Re-check: confirmer 1 deactivated before confirmer 2 acts.
    rid2 = _emergency(api, agent, tid).json()["id"]
    third = make_operator("platform_owner")
    third_ok = api.call("POST", f"/break-glass-requests/{rid2}/emergency-confirm", third)
    assert third_ok.status_code == 200, third_ok.text
    assert api.call("POST", f"/operators/{third.id}/deactivate", owner).status_code == 200
    res = api.call("POST", f"/break-glass-requests/{rid2}/emergency-confirm", second)
    assert (res.status_code, res.json()["code"]) == (409, "requester_not_authorised")


# --- A-14: a second account the first operator controls ----------------------------------------


def test_A_14_new_owner_cannot_be_the_second_person_for_their_inviter(
    api: Api, make_operator: MakeOperator, owner: Operator, make_plan: Callable[..., uuid.UUID]
) -> None:
    """Owner A invites a second account and makes it an owner. It cannot approve A's request:
    its role is too new, and A granted it. After the waiting period it still cannot act for A,
    while an independent long-standing owner can."""
    first = make_operator("platform_owner")
    invited = api.call(
        "POST",
        "/operators",
        first,
        json={
            "email": f"sock-{uuid.uuid4().hex[:6]}@example.test",
            "display_name": "Synthetic Second Account",
            "idp_subject": f"sub-{uuid.uuid4()}",
            "roles": ["platform_owner"],
        },
    )
    assert invited.status_code == 201, invited.text
    sock_id = uuid.UUID(invited.json()["id"])
    with platform_session() as s:
        s.execute(
            text(
                "UPDATE platform.operators SET status = 'active', mfa_enrolled = true WHERE id = :i"
            ),
            {"i": sock_id},
        )
        subject: str = s.execute(
            text("SELECT idp_subject FROM platform.operators WHERE id = :i"), {"i": sock_id}
        ).scalar_one()
    sock = Operator(sock_id, subject, ("platform_owner",))
    tid = _live_school(api, owner, make_plan())
    assert _request_offboarding(api, first, tid).status_code == 202
    res = api.call("POST", f"/tenants/{tid}/offboarding:approve", sock)
    assert (res.status_code, res.json()["code"]) == (409, "approver_not_eligible")
    with platform_session() as s:
        s.execute(
            text(
                "UPDATE platform.operator_roles SET granted_at = now() - interval '60 days' "
                "WHERE operator_id = :i"
            ),
            {"i": sock_id},
        )
    res = api.call("POST", f"/tenants/{tid}/offboarding:approve", sock)
    assert (res.status_code, res.json()["code"]) == (409, "approver_not_eligible")
    # Nor the other way round: the second account asks, its inviter approves.
    tid2 = _live_school(api, owner, make_plan())
    assert _request_offboarding(api, sock, tid2).status_code == 202
    res = api.call("POST", f"/tenants/{tid2}/offboarding:approve", first)
    assert (res.status_code, res.json()["code"]) == (409, "approver_not_eligible")
    # A brand-new owner granted by someone else still waits.
    fresh = make_operator("platform_owner", role_age_days=0, granted_by=owner.id)
    res = api.call("POST", f"/tenants/{tid}/offboarding:approve", fresh)
    assert (res.status_code, res.json()["code"]) == (409, "approver_not_eligible")
    ok = api.call("POST", f"/tenants/{tid}/offboarding:approve", owner)
    assert ok.status_code == 200, ok.text


def test_A_14_role_changes_keep_the_original_grant(
    api: Api, owner: Operator, make_operator: MakeOperator
) -> None:
    """Adding a role must not reset (or launder) when an existing role was granted."""
    op = make_operator("platform_owner", role_age_days=40, granted_by=owner.id)
    other = make_operator("platform_owner")
    res = api.call(
        "PUT",
        f"/operators/{op.id}/roles",
        other,
        json={"roles": ["platform_owner", "billing_admin"]},
    )
    assert res.status_code == 200, res.text
    with platform_session() as s:
        rows = {
            r["role_key"]: r
            for r in s.execute(
                text(
                    "SELECT role_key, granted_by, granted_at FROM platform.operator_roles "
                    "WHERE operator_id = :i"
                ),
                {"i": op.id},
            ).mappings()
        }
    assert rows["platform_owner"]["granted_by"] == owner.id
    assert rows["platform_owner"]["granted_at"] < dt.datetime.now(dt.UTC) - dt.timedelta(days=39)
    assert rows["billing_admin"]["granted_by"] == other.id


# --- hardening: critical announcements are two-person -----------------------------------------


def _announcement(severity: str, **extra: Any) -> dict[str, Any]:
    start = dt.datetime.now(dt.UTC) + dt.timedelta(hours=1)
    return {
        "title_en": "Synthetic notice",
        "body_en": "Synthetic body text",
        "severity": severity,
        "starts_at": start.isoformat(),
        "ends_at": (start + dt.timedelta(days=1)).isoformat(),
        **extra,
    }


def test_hardening_critical_announcement_needs_a_second_operator(
    api: Api, make_operator: MakeOperator, admin_engine: Engine
) -> None:
    agent, other = make_operator("support_agent"), make_operator("support_agent")
    info = api.call("POST", "/announcements", agent, json=_announcement("info"))
    assert info.status_code == 201, info.text
    assert info.json()["status"] == "scheduled"  # non-critical stays single-operator
    crit = api.call("POST", "/announcements", agent, json=_announcement("critical"))
    assert crit.status_code == 201, crit.text
    body = crit.json()
    assert (body["status"], body["submitted_by"]) == ("pending_approval", str(agent.id))
    aid = body["id"]
    same = api.call("POST", f"/announcements/{aid}/approve", agent)
    assert (same.status_code, same.json()["code"]) == (409, "same_operator")
    # Direct DB attempt to publish it without a second operator fails the CHECK.
    with (
        pytest.raises(IntegrityError, match="announcements_critical_two_person"),
        admin_engine.begin() as c,
    ):
        c.execute(
            text("UPDATE platform.announcements SET status = 'scheduled' WHERE id = :i"),
            {"i": uuid.UUID(aid)},
        )
    ok = api.call("POST", f"/announcements/{aid}/approve", other)
    assert ok.status_code == 200, ok.text
    assert (ok.json()["status"], ok.json()["approved_by"]) == ("scheduled", str(other.id))
    # Any change sends it back for approval.
    changed = api.call(
        "PATCH",
        f"/announcements/{aid}",
        agent,
        json=_announcement("critical", title_en="New"),
        headers={"If-Match": f'W/"{ok.json()["version"]}"'},
    )
    assert changed.status_code == 200, changed.text
    assert (changed.json()["status"], changed.json()["approved_by"]) == ("pending_approval", None)
    # Expired requests cannot be approved; cancelling withdraws.
    _age(
        admin_engine,
        "UPDATE platform.announcements SET submitted_at = submitted_at - :d WHERE id = :i",
        uuid.UUID(aid),
        TTL + dt.timedelta(minutes=1),
    )
    late = api.call("POST", f"/announcements/{aid}/approve", other)
    assert (late.status_code, late.json()["code"]) == (409, "request_expired")
    cancelled = api.call("POST", f"/announcements/{aid}/cancel", other)
    assert cancelled.json()["status"] == "cancelled"


# --- hardening: invoices ----------------------------------------------------------------------


def test_hardening_invoice_lines_refuse_negative_prices_except_adjustments() -> None:
    for kind in ("subscription", "per_student", "one_time_fee", "addon", "discount"):
        with pytest.raises(ValidationError, match="adjustment lines only"):
            InvoiceLineIn(kind=kind, description="Synthetic", unit_price_inr="-1.00")
    InvoiceLineIn(kind="adjustment", description="Synthetic credit", unit_price_inr="-1.00")


def test_hardening_manual_draft_refuses_an_overlapping_period(
    api: Api, owner: Operator, make_operator: MakeOperator, make_plan: Callable[..., uuid.UUID]
) -> None:
    billing_admin = make_operator("billing_admin")
    sub = _school(api, owner, make_plan())["subscription_id"]
    with platform_session() as s:
        start: dt.date = s.execute(
            text(
                "SELECT period_start FROM platform.invoices WHERE subscription_id = :s "
                "AND status <> 'void' ORDER BY period_start LIMIT 1"
            ),
            {"s": sub},
        ).scalar_one()
    inside = (start + dt.timedelta(days=10)).isoformat()
    res = api.call(
        "POST", "/invoices", billing_admin, json={"subscription_id": sub, "period_start": inside}
    )
    assert (res.status_code, res.json()["code"]) == (409, "duplicate"), res.text


def test_hardening_offboarding_ends_the_subscription_and_its_invoicing(
    api: Api, owner: Operator, make_operator: MakeOperator, make_plan: Callable[..., uuid.UUID]
) -> None:
    from app.platform import billing
    from app.platform.common import SYSTEM

    first, second = make_operator("platform_owner"), make_operator("platform_owner")
    billing_admin = make_operator("billing_admin")
    school = _school(api, owner, make_plan())
    tid, sub = str(school["tenant_id"]), school["subscription_id"]
    assert api.call("POST", f"/tenants/{tid}/activate", owner).status_code == 200
    assert _request_offboarding(api, first, tid).status_code == 202
    ok = api.call("POST", f"/tenants/{tid}/offboarding:approve", second)
    assert ok.status_code == 200, ok.text
    got = api.call("GET", f"/subscriptions/{sub}", billing_admin).json()
    assert got["status"] == "cancelled"
    # Even a subscription left live (schools offboarded before this change) gets no new drafts.
    with platform_session() as s:
        s.execute(
            text(
                "UPDATE platform.subscriptions SET status = 'active', cancelled_at = NULL, "
                "cancel_reason = NULL WHERE id = :s"
            ),
            {"s": sub},
        )
        row = (
            s.execute(text("SELECT * FROM platform.subscriptions WHERE id = :s"), {"s": sub})
            .mappings()
            .one()
        )
        assert billing.create_draft(s, SYSTEM, row, row["current_period_end"]) is None
    res = api.call(
        "POST",
        "/invoices",
        billing_admin,
        json={"subscription_id": sub, "period_start": str(row["current_period_end"])},
    )
    assert (res.status_code, res.json()["code"]) == (409, "invalid_state"), res.text
