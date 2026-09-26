"""School provisioning and lifecycle through the control plane (FR-PLT-001..005, SEC-026, SEC-029).

Shared tier: tenant + keys + subscription + deployment + owner invite, audited on both chains;
atomic first transaction; idempotent retries. Dedicated tier: deployment with custom domain and a
heartbeat key shown once. Suspend/reactivate/offboard (two-person). Responses carry no student or
personal data about school users.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.core.db import platform_session
from app.main import create_app

from .conftest import Api, MakeOperator, Operator, letters, provision_payload

pytestmark = pytest.mark.db


def _provision(api: Api, op: Operator, plan_id: uuid.UUID, **overrides: Any) -> dict[str, Any]:
    res = api.call("POST", "/tenants", op, json=provision_payload(plan_id, **overrides))
    assert res.status_code == 201, res.text
    body: dict[str, Any] = res.json()
    return body


def _platform_events(tenant_id: str) -> list[str]:
    with platform_session() as s:
        return list(
            s.execute(
                text(
                    "SELECT action FROM platform.audit_events WHERE subject_tenant_id = :t "
                    "ORDER BY seq"
                ),
                {"t": tenant_id},
            ).scalars()
        )


def _tenant_events(admin: Engine, tenant_id: str) -> list[tuple[str, str]]:
    with admin.connect() as c:
        return [
            (r.action, r.actor_type)
            for r in c.execute(
                text(
                    "SELECT action, actor_type FROM audit.events WHERE tenant_id = :t ORDER BY seq"
                ),
                {"t": tenant_id},
            )
        ]


def test_FR_PLT_002_shared_provisioning_end_to_end(
    api: Api, owner: Operator, make_plan: Callable[..., uuid.UUID], admin_engine: Engine
) -> None:
    plan = make_plan()
    out = _provision(api, owner, plan)
    tid = out["tenant_id"]
    assert out["tier"] == "shared"
    assert out["tenant_status"] == "provisioning"
    assert out["owner_invite"] == "created"  # system roles were cloned by the authz hook
    assert out["heartbeat_key"] is None
    with admin_engine.connect() as c:
        tenant = c.execute(
            text("SELECT status, deployment_mode FROM core.tenants WHERE id = :t"), {"t": tid}
        ).one()
        keys: Any = c.execute(
            text("SELECT count(*) FROM core.tenant_keys WHERE tenant_id = :t"), {"t": tid}
        ).scalar_one()
        membership = c.execute(
            text("SELECT status, mfa_required FROM core.memberships WHERE tenant_id = :t"),
            {"t": tid},
        ).one()
        owner_roles = c.execute(
            text(
                "SELECT r.key FROM core.membership_roles mr JOIN core.roles r "
                "ON r.tenant_id = mr.tenant_id AND r.id = mr.role_id WHERE mr.tenant_id = :t"
            ),
            {"t": tid},
        ).scalars()
        assert list(owner_roles) == ["owner"]
    assert tuple(tenant) == ("provisioning", "shared")
    assert keys == 1
    assert tuple(membership) == ("invited", True)
    with platform_session() as s:
        sub = s.execute(
            text("SELECT status, trial_ends_at FROM platform.subscriptions WHERE tenant_id = :t"),
            {"t": tid},
        ).one()
        dep = s.execute(
            text(
                "SELECT mode, status, tenant_status FROM platform.deployments WHERE tenant_id = :t"
            ),
            {"t": tid},
        ).one()
    assert sub.status == "trial"
    assert sub.trial_ends_at is not None
    assert tuple(dep) == ("shared", "healthy", "provisioning")
    assert _platform_events(tid) == ["tenant.provisioned", "tenant.owner_invite_created"]
    assert ("tenant.provisioned", "platform") in _tenant_events(admin_engine, tid)


def test_FR_PLT_002_provisioning_is_idempotent_per_key(
    api: Api, owner: Operator, make_plan: Callable[..., uuid.UUID]
) -> None:
    body = provision_payload(make_plan())
    first = api.call("POST", "/tenants", owner, json=body, idem="idem-provision-0001")
    again = api.call("POST", "/tenants", owner, json=body, idem="idem-provision-0001")
    assert first.status_code == again.status_code == 201
    assert first.json()["tenant_id"] == again.json()["tenant_id"]
    assert again.headers["Location"] == first.headers["Location"]
    other = dict(body, school_name="Another Synthetic School")
    reused = api.call("POST", "/tenants", owner, json=other, idem="idem-provision-0001")
    assert (reused.status_code, reused.json()["errors"][0]["code"]) == (
        422,
        "idempotency_key_reused",
    )
    missing = api.call("POST", "/tenants", owner, json=body, idem=None)
    assert missing.status_code == 400


def test_FR_PLT_002_first_transaction_is_atomic(
    api: Api,
    owner: Operator,
    make_plan: Callable[..., uuid.UUID],
    admin_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failure after the tenant row is created leaves no tenant, deployment or account."""
    from app.platform import billing

    def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("synthetic failure after core.provision_tenant")

    monkeypatch.setattr(billing, "insert_subscription", boom)
    code = f"s-{letters(12)}"
    with pytest.raises(RuntimeError, match="synthetic failure"):
        api.call("POST", "/tenants", owner, json=provision_payload(make_plan(), code=code))
    with admin_engine.connect() as c:
        for sql in (
            "SELECT count(*) FROM core.tenants WHERE code = :c",
            "SELECT count(*) FROM platform.deployments WHERE tenant_code = :c",
        ):
            assert c.execute(text(sql), {"c": code}).scalar() == 0
    monkeypatch.undo()
    retry = api.call("POST", "/tenants", owner, json=provision_payload(make_plan(), code=code))
    assert retry.status_code == 201, retry.text


def test_FR_PLT_002_plan_tier_must_match(
    api: Api, owner: Operator, make_plan: Callable[..., uuid.UUID]
) -> None:
    res = api.call("POST", "/tenants", owner, json=provision_payload(make_plan(tier="dedicated")))
    assert res.status_code == 422


def test_FR_PLT_003_dedicated_provisioning_records_domain_and_key_once(
    api: Api, owner: Operator, make_plan: Callable[..., uuid.UUID], admin_engine: Engine
) -> None:
    body = provision_payload(
        make_plan(tier="dedicated"),
        tier="dedicated",
        custom_domain=f"office.{uuid.uuid4().hex[:8]}.edu.in",
        owner=None,
    )
    first = api.call("POST", "/tenants", owner, json=body, idem="idem-dedicated-01")
    assert first.status_code == 201, first.text
    out = first.json()
    assert out["heartbeat_key"]
    assert out["heartbeat_key_id"].startswith("hb-")
    assert out["owner_invite"] == "not_applicable"
    with platform_session() as s:
        dep = s.execute(
            text(
                "SELECT mode, status, custom_domain, heartbeat_key_ciphertext "
                "FROM platform.deployments WHERE tenant_id = :t"
            ),
            {"t": out["tenant_id"]},
        ).one()
    assert (dep.mode, dep.status, dep.custom_domain) == (
        "dedicated",
        "provisioning",
        body["custom_domain"],
    )
    assert out["heartbeat_key"].encode() not in bytes(dep.heartbeat_key_ciphertext)
    with admin_engine.connect() as c:  # the tenant row is created on the host, not here
        assert (
            c.execute(
                text("SELECT count(*) FROM core.tenants WHERE id = :t"), {"t": out["tenant_id"]}
            ).scalar()
            == 0
        )
    replay = api.call("POST", "/tenants", owner, json=body, idem="idem-dedicated-01")
    assert replay.status_code == 201
    assert replay.json()["heartbeat_key"] is None  # never shown twice
    shared_domain = provision_payload(make_plan(), custom_domain="office.example.edu.in")
    assert api.call("POST", "/tenants", owner, json=shared_domain).status_code == 422


def test_FR_PLT_004_activate_suspend_reactivate_on_both_chains(
    api: Api, make_operator: MakeOperator, make_plan: Callable[..., uuid.UUID], admin_engine: Engine
) -> None:
    engineer = make_operator("platform_engineer")
    tid = _provision(api, engineer, make_plan())["tenant_id"]
    assert api.call("POST", f"/tenants/{tid}/activate", engineer).status_code == 200
    reason = {"reason": "Security incident reported by the school"}
    res = api.call("POST", f"/tenants/{tid}/suspend", engineer, json=reason)
    assert res.status_code == 200, res.text
    assert res.json()["tenant_status"] == "suspended"
    with admin_engine.connect() as c:
        assert (
            c.execute(text("SELECT status FROM core.tenants WHERE id = :t"), {"t": tid}).scalar()
            == "suspended"
        )
    assert api.call("POST", f"/tenants/{tid}/suspend", engineer, json=reason).status_code == 409
    back = api.call("POST", f"/tenants/{tid}/reactivate", engineer, json=reason)
    assert back.json()["tenant_status"] == "active"
    actions = [a for a, _ in _tenant_events(admin_engine, tid)]
    assert actions[-3:] == ["tenant.activated", "tenant.suspended", "tenant.reactivated"]
    assert {"tenant.activated", "tenant.suspended", "tenant.reactivated"} <= set(
        _platform_events(tid)
    )


def test_SEC_029_offboarding_needs_a_second_operator(
    api: Api, make_operator: MakeOperator, make_plan: Callable[..., uuid.UUID], admin_engine: Engine
) -> None:
    first, second = make_operator("platform_owner"), make_operator("platform_owner")
    tid = _provision(api, first, make_plan())["tenant_id"]
    api.call("POST", f"/tenants/{tid}/activate", first)
    assert (
        api.call("POST", f"/tenants/{tid}/offboarding:approve", first).json()["code"]
        == "not_requested"
    )
    req = api.call(
        "POST", f"/tenants/{tid}/offboarding", first, json={"reason": "School's written request"}
    )
    assert req.status_code == 202, req.text
    same = api.call("POST", f"/tenants/{tid}/offboarding:approve", first)
    assert (same.status_code, same.json()["code"]) == (409, "same_operator")
    ok = api.call("POST", f"/tenants/{tid}/offboarding:approve", second)
    assert ok.status_code == 200, ok.text
    assert ok.json()["tenant_status"] == "offboarding"
    with admin_engine.connect() as c:
        assert (
            c.execute(text("SELECT status FROM core.tenants WHERE id = :t"), {"t": tid}).scalar()
            == "offboarding"
        )
    assert "tenant.offboard_approved" in [a for a, _ in _tenant_events(admin_engine, tid)]


def test_BR_09_tenant_views_carry_no_personal_or_student_fields(
    api: Api, owner: Operator, make_plan: Callable[..., uuid.UUID]
) -> None:
    tid = _provision(api, owner, make_plan())["tenant_id"]
    detail = api.call("GET", f"/tenants/{tid}", owner)
    listing = api.call("GET", "/tenants?limit=5", owner)
    assert detail.status_code == listing.status_code == 200
    forbidden = (
        "dob",
        "birth",
        "phone",
        "mobile",
        "email",
        "aadhaar",
        "guardian",
        "student_name",
        "display_name",
        "owner",
    )

    def keys(value: Any) -> set[str]:
        if isinstance(value, dict):
            return set(value) | {k for v in value.values() for k in keys(v)}
        if isinstance(value, list):
            return {k for v in value for k in keys(v)}
        return set()

    for key in keys(detail.json()) | keys(listing.json()):
        assert not any(f in key for f in forbidden), key
    assert detail.json()["counts"]["active_memberships"] == 0
    assert "owner@school.example.test" not in detail.text


def test_BR_09_platform_response_schemas_never_describe_students() -> None:
    schema = create_app().openapi()
    student_like = ("dob", "date_of_birth", "aadhaar", "guardian", "student_name", "admission_no")
    for name, component in schema["components"]["schemas"].items():
        for prop in component.get("properties", {}):
            assert not any(s in prop for s in student_like), f"{name}.{prop}"
