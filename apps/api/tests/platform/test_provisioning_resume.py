"""Resumable, idempotent provisioning (FR-PLT-002, FR-PLT-003; docs/16 §5.4; ADR-0020).

Crash injection at every step boundary, retries that converge on one school, concurrent double
submission, lease fencing, the operator's resume path and the go-live guard. All data is
synthetic. The control plane touches tenant data only through the pinned lifecycle functions;
these tests read tenant tables with the admin engine (test setup only).
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.core.crypto import LocalDevKeyWrapper
from app.core.db import platform_session
from app.core.errors import Conflict
from app.platform import provisioning, tenant_audit
from app.platform import repository as repo
from app.platform.schemas import ProvisionIn, ProvisionOut
from app.tenancy import service as tenancy

from .conftest import Api, MakeOperator, Operator, provision_payload

pytestmark = pytest.mark.db


class Crash(BaseException):
    """A process dying mid-step: nothing in the app catches it (not an ``Exception``)."""


# --- helpers ---------------------------------------------------------------------------------


def _run(code: str) -> Any:
    with platform_session() as s:
        return (
            s.execute(
                text("SELECT * FROM platform.provisioning_runs WHERE tenant_code = :c"), {"c": code}
            )
            .mappings()
            .one_or_none()
        )


def _scalar(engine: Engine, sql: str, **params: Any) -> Any:
    with engine.connect() as c:
        return c.execute(text(sql), params).scalar()


def _school_counts(admin: Engine, code: str) -> dict[str, int]:
    """How many of each thing exist for this code (should be one of each at the end)."""
    tid = _scalar(admin, "SELECT id FROM core.tenants WHERE code = :c", c=code)
    return {
        "tenants": _scalar(admin, "SELECT count(*) FROM core.tenants WHERE code = :c", c=code),
        "deployments": _scalar(
            admin, "SELECT count(*) FROM platform.deployments WHERE tenant_code = :c", c=code
        ),
        "subscriptions": _scalar(
            admin,
            "SELECT count(*) FROM platform.subscriptions s JOIN platform.deployments d "
            "ON d.tenant_id = s.tenant_id WHERE d.tenant_code = :c",
            c=code,
        ),
        "keys": _scalar(admin, "SELECT count(*) FROM core.tenant_keys WHERE tenant_id = :t", t=tid),
        "roles": _scalar(
            admin,
            "SELECT count(*) FROM core.roles WHERE tenant_id = :t AND key = 'owner'",
            t=tid,
        ),
        "memberships": _scalar(
            admin, "SELECT count(*) FROM core.memberships WHERE tenant_id = :t", t=tid
        ),
        "school_chain_queued": _scalar(
            admin,
            "SELECT count(*) FROM platform.tenant_audit_outbox WHERE tenant_id = :t "
            "AND action = 'tenant.provisioned'",
            t=tid,
        ),
        "school_chain_events": _scalar(
            admin,
            "SELECT count(*) FROM audit.events WHERE tenant_id = :t "
            "AND action = 'tenant.provisioned'",
            t=tid,
        ),
    }


ONE_OF_EACH = {
    "tenants": 1,
    "deployments": 1,
    "subscriptions": 1,
    "keys": 1,
    "roles": 1,
    "memberships": 1,
    "school_chain_queued": 1,
    "school_chain_events": 1,
}


def _platform_actions(tenant_id: Any) -> list[str]:
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


def _expire_lease(code: str) -> None:
    with platform_session() as s:
        s.execute(
            text(
                "UPDATE platform.provisioning_runs SET lease_expires_at = now() - interval '1 s' "
                "WHERE tenant_code = :c AND lease_id IS NOT NULL"
            ),
            {"c": code},
        )


def _assert_completed(code: str) -> None:
    run = _run(code)
    assert run["state"] == "completed"
    assert run["completed_at"] is not None
    assert (run["lease_id"], run["failed_step"], run["last_error"]) == (None, None, None)
    # The owner invite parameters are held only until the invite exists (docs/16 §5.4).
    assert (
        run["owner_subject"],
        run["owner_display_name"],
        run["owner_email"],
        run["owner_language"],
    ) == (None, None, None, None)


@pytest.fixture
def body(make_plan: Callable[..., uuid.UUID]) -> dict[str, Any]:
    return provision_payload(make_plan())


@pytest.fixture
def restore_hooks() -> Iterator[None]:
    saved = list(tenancy.POST_PROVISION_HOOKS)
    yield
    tenancy.POST_PROVISION_HOOKS[:] = saved


# --- happy path records the run ---------------------------------------------------------------


def test_FR_PLT_002_run_is_completed_and_owner_parameters_cleared(
    api: Api, owner: Operator, body: dict[str, Any], admin_engine: Engine
) -> None:
    res = api.call("POST", "/tenants", owner, json=body)
    assert res.status_code == 201, res.text
    _assert_completed(body["code"])
    assert _run(body["code"])["request_sha256"] == provisioning.request_fingerprint(
        ProvisionIn.model_validate(body)
    )
    assert _school_counts(admin_engine, body["code"]) == ONE_OF_EACH
    detail = api.call("GET", f"/tenants/{res.json()['tenant_id']}", owner).json()
    assert detail["provisioning"]["state"] == "completed"
    assert detail["provisioning"]["resumable"] is False


# --- crash injection at each step --------------------------------------------------------------


def test_FR_PLT_002_failure_in_key_initialisation_is_recorded_and_retry_converges(
    api: Api,
    owner: Operator,
    body: dict[str, Any],
    admin_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("synthetic KMS outage")

    monkeypatch.setattr(tenancy, "initialise_tenant", boom)
    res = api.call("POST", "/tenants", owner, json=body)
    assert (res.status_code, res.json()["code"]) == (503, "provisioning_failed")
    run = _run(body["code"])
    assert (run["state"], run["failed_step"], run["last_error"]) == (
        "failed",
        "initialise",
        "unexpected_error",
    )
    assert run["lease_id"] is None
    tid = run["tenant_id"]
    assert _platform_actions(tid) == ["tenant.provisioned", "tenant.provisioning_failed"]
    assert (
        _scalar(admin_engine, "SELECT count(*) FROM core.tenant_keys WHERE tenant_id = :t", t=tid)
        == 0
    )

    monkeypatch.undo()
    again = api.call("POST", "/tenants", owner, json=body)  # same request, new Idempotency-Key
    assert again.status_code == 201, again.text
    assert again.json()["tenant_id"] == str(tid)
    assert again.json()["owner_invite"] == "created"
    _assert_completed(body["code"])
    assert _school_counts(admin_engine, body["code"]) == ONE_OF_EACH
    assert _platform_actions(tid) == [
        "tenant.provisioned",
        "tenant.provisioning_failed",
        "tenant.provisioning_resumed",
        "tenant.owner_invite_created",
    ]
    assert _run(body["code"])["attempts"] == 2


def test_FR_PLT_002_failing_post_provision_hook_rolls_back_the_keys_and_resumes(
    api: Api,
    owner: Operator,
    body: dict[str, Any],
    admin_engine: Engine,
    restore_hooks: None,
) -> None:
    calls: list[uuid.UUID] = []

    def flaky_hook(_session: object, tenant_id: uuid.UUID) -> None:
        calls.append(tenant_id)
        if len(calls) == 1:
            raise RuntimeError("synthetic hook failure")

    tenancy.POST_PROVISION_HOOKS.append(flaky_hook)
    res = api.call("POST", "/tenants", owner, json=body)
    assert res.status_code == 503
    tid = _run(body["code"])["tenant_id"]
    # Keys and hooks share the school's transaction: nothing half-done is left behind.
    assert (
        _scalar(admin_engine, "SELECT count(*) FROM core.tenant_keys WHERE tenant_id = :t", t=tid)
        == 0
    )
    resumed = api.call("POST", f"/tenants/{tid}/provisioning:resume", owner)
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["owner_invite"] == "created"
    assert _school_counts(admin_engine, body["code"]) == ONE_OF_EACH
    assert calls == [tid, tid]


def test_FR_PLT_002_failure_after_keys_before_invite_resumes_without_a_second_key(
    api: Api,
    owner: Operator,
    body: dict[str, Any],
    admin_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*_a: object, **_k: object) -> str:
        raise RuntimeError("synthetic failure between steps")

    monkeypatch.setattr(provisioning, "_invite_and_finish", boom)
    assert api.call("POST", "/tenants", owner, json=body).status_code == 503
    run = _run(body["code"])
    assert (run["state"], run["failed_step"]) == ("failed", "owner_invite")
    tid = run["tenant_id"]
    assert (
        _scalar(admin_engine, "SELECT count(*) FROM core.tenant_keys WHERE tenant_id = :t", t=tid)
        == 1
    )
    assert (
        _scalar(admin_engine, "SELECT count(*) FROM core.memberships WHERE tenant_id = :t", t=tid)
        == 0
    )
    # Owner parameters are kept for the resume.
    assert run["owner_subject"] == body["owner"]["idp_subject"]

    monkeypatch.undo()
    resumed = api.call("POST", f"/tenants/{tid}/provisioning:resume", owner)
    assert resumed.status_code == 200, resumed.text
    _assert_completed(body["code"])
    assert _school_counts(admin_engine, body["code"]) == ONE_OF_EACH


def test_FR_PLT_002_failure_inside_the_invite_transaction_leaves_no_invite_or_event(
    api: Api,
    owner: Operator,
    body: dict[str, Any],
    admin_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invite, school-chain event and completion commit together: a failure after the invite
    is written rolls all of them back, so the resume queues the school-chain event once."""

    def boom(*_a: object, **_k: object) -> uuid.UUID:
        raise RuntimeError("synthetic failure after core.create_owner_invite")

    monkeypatch.setattr(tenant_audit, "enqueue", boom)
    assert api.call("POST", "/tenants", owner, json=body).status_code == 503
    tid = _run(body["code"])["tenant_id"]
    assert (
        _scalar(admin_engine, "SELECT count(*) FROM core.memberships WHERE tenant_id = :t", t=tid)
        == 0
    )
    assert "tenant.owner_invite_created" not in _platform_actions(tid)

    monkeypatch.undo()
    assert api.call("POST", f"/tenants/{tid}/provisioning:resume", owner).status_code == 200
    assert _school_counts(admin_engine, body["code"]) == ONE_OF_EACH
    assert _platform_actions(tid).count("tenant.owner_invite_created") == 1


def test_FR_PLT_002_lost_response_after_completion_replays_without_side_effects(
    api: Api, owner: Operator, body: dict[str, Any], admin_engine: Engine
) -> None:
    first = api.call("POST", "/tenants", owner, json=body)
    assert first.status_code == 201
    tid = first.json()["tenant_id"]
    before = _platform_actions(tid)
    again = api.call("POST", "/tenants", owner, json=body)  # the client never saw the 201
    assert again.status_code == 201
    assert again.json()["tenant_id"] == tid
    assert again.json()["owner_invite"] == "existing"
    assert _platform_actions(tid) == before
    assert _school_counts(admin_engine, body["code"]) == ONE_OF_EACH
    resumed = api.call("POST", f"/tenants/{tid}/provisioning:resume", owner)
    assert (resumed.status_code, resumed.json()["tenant_id"]) == (200, tid)
    assert _platform_actions(tid) == before


def test_FR_PLT_002_hard_crash_holds_the_lease_until_it_expires_then_converges(
    owner: Operator,
    body: dict[str, Any],
    admin_engine: Engine,
    wrapper: LocalDevKeyWrapper,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = ProvisionIn.model_validate(body)

    def die(*_a: object, **_k: object) -> None:
        raise Crash

    monkeypatch.setattr(tenancy, "initialise_tenant", die)
    with pytest.raises(Crash):
        provisioning.provision(owner.actor, data, wrapper=wrapper)
    run = _run(data.code)
    # Nothing ran the failure handler: the run is as step 1 left it, lease still live.
    assert (run["state"], run["failed_step"]) == ("registered", None)
    assert run["lease_id"] is not None
    monkeypatch.undo()

    with pytest.raises(Conflict) as busy:
        provisioning.provision(owner.actor, data, wrapper=wrapper)
    assert busy.value.code == "provisioning_in_progress"

    _expire_lease(data.code)
    out = provisioning.provision(owner.actor, data, wrapper=wrapper)
    assert out.tenant_id == run["tenant_id"]
    assert out.owner_invite == "created"
    _assert_completed(data.code)
    assert _school_counts(admin_engine, data.code) == ONE_OF_EACH


def test_FR_PLT_002_a_runner_whose_lease_was_taken_over_cannot_change_the_run(
    owner: Operator,
    body: dict[str, Any],
    wrapper: LocalDevKeyWrapper,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = ProvisionIn.model_validate(body)

    def die(*_a: object, **_k: object) -> None:
        raise Crash

    monkeypatch.setattr(tenancy, "initialise_tenant", die)
    with pytest.raises(Crash):
        provisioning.provision(owner.actor, data, wrapper=wrapper)
    stale = _run(data.code)
    _expire_lease(data.code)
    fresh_lease = uuid.uuid4()
    with platform_session() as s:
        taken = repo.claim_provisioning_run(
            s, stale["tenant_id"], fresh_lease, provisioning.lease_duration()
        )
    assert taken is not None
    with platform_session() as s:
        assert (
            repo.update_provisioning_run(s, stale["id"], stale["lease_id"], {"state": "failed"})
            is None
        )
        assert repo.lock_provisioning_run(s, stale["id"], stale["lease_id"]) is None
        assert repo.lock_provisioning_run(s, stale["id"], fresh_lease) is not None


# --- double submit -----------------------------------------------------------------------------


def test_FR_PLT_002_double_submit_by_two_operators_yields_one_school(
    api: Api,
    make_operator: MakeOperator,
    body: dict[str, Any],
    admin_engine: Engine,
) -> None:
    first, second = make_operator("platform_owner"), make_operator("platform_engineer")
    a = api.call("POST", "/tenants", first, json=body, idem="idem-double-submit-a")
    b = api.call("POST", "/tenants", second, json=body, idem="idem-double-submit-b")
    assert a.status_code == b.status_code == 201
    assert a.json()["tenant_id"] == b.json()["tenant_id"]
    assert _school_counts(admin_engine, body["code"]) == ONE_OF_EACH


def test_FR_PLT_002_concurrent_double_submit_yields_one_school(
    owner: Operator,
    body: dict[str, Any],
    admin_engine: Engine,
    wrapper: LocalDevKeyWrapper,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both requests pass the "no run yet" check, then race on registration."""
    data = ProvisionIn.model_validate(body)
    barrier = threading.Barrier(2, timeout=20)
    real_register = tenancy.register_tenant

    def register_together(*args: Any, **kwargs: Any) -> uuid.UUID:
        barrier.wait()
        return real_register(*args, **kwargs)

    monkeypatch.setattr(tenancy, "register_tenant", register_together)
    results: list[ProvisionOut | BaseException] = []

    def submit() -> None:
        try:
            results.append(provisioning.provision(owner.actor, data, wrapper=wrapper))
        except BaseException as exc:  # collected and asserted below
            results.append(exc)

    threads = [threading.Thread(target=submit) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert len(results) == 2
    ok = [r for r in results if isinstance(r, ProvisionOut)]
    errors = [r for r in results if not isinstance(r, ProvisionOut)]
    assert ok, results
    assert len({r.tenant_id for r in ok}) == 1
    for err in errors:  # the loser either joined the winner or was told it is running
        assert isinstance(err, Conflict), repr(err)
        assert err.code == "provisioning_in_progress"
    monkeypatch.undo()
    final = provisioning.provision(owner.actor, data, wrapper=wrapper)
    assert final.tenant_id == ok[0].tenant_id
    _assert_completed(data.code)
    assert _school_counts(admin_engine, data.code) == ONE_OF_EACH


def test_FR_PLT_002_same_code_different_request_is_a_conflict(
    api: Api, owner: Operator, body: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(tenancy, "initialise_tenant", boom)
    assert api.call("POST", "/tenants", owner, json=body).status_code == 503
    monkeypatch.undo()
    other_owner = dict(body, owner=dict(body["owner"], idp_subject=f"owner-{uuid.uuid4()}"))
    res = api.call("POST", "/tenants", owner, json=other_owner)
    assert (res.status_code, res.json()["code"]) == (409, "duplicate")
    # The first request's owner is still the one that gets invited.
    assert _run(body["code"])["owner_subject"] == body["owner"]["idp_subject"]


# --- operator view, resume route and go-live guard --------------------------------------------


def test_FR_PLT_002_failed_run_is_visible_blocks_go_live_and_resumes(
    api: Api, owner: Operator, body: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_a: object, **_k: object) -> str:
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(provisioning, "_invite_and_finish", boom)
    assert api.call("POST", "/tenants", owner, json=body).status_code == 503
    tid = _run(body["code"])["tenant_id"]
    detail = api.call("GET", f"/tenants/{tid}", owner)
    view = detail.json()["provisioning"]
    assert view["state"] == "failed"
    assert view["failed_step"] == "owner_invite"
    assert view["last_error"] == "unexpected_error"
    assert (view["resumable"], view["in_progress"]) == (True, False)
    assert body["owner"]["idp_subject"] not in detail.text
    assert "owner@school.example.test" not in detail.text

    # Keys exist, so the database would allow go-live; the control plane refuses it.
    live = api.call("POST", f"/tenants/{tid}/activate", owner)
    assert (live.status_code, live.json()["code"]) == (409, "provisioning_incomplete")

    monkeypatch.undo()
    assert api.call("POST", f"/tenants/{tid}/provisioning:resume", owner).status_code == 200
    assert api.call("POST", f"/tenants/{tid}/activate", owner).status_code == 200


def test_FR_PLT_002_resume_of_an_unknown_school_is_404(api: Api, owner: Operator) -> None:
    res = api.call("POST", f"/tenants/{uuid.uuid4()}/provisioning:resume", owner)
    assert res.status_code == 404


def test_FR_PLT_002_resume_is_refused_while_another_runner_holds_the_lease(
    api: Api,
    owner: Operator,
    body: dict[str, Any],
    wrapper: LocalDevKeyWrapper,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def die(*_a: object, **_k: object) -> None:
        raise Crash

    monkeypatch.setattr(tenancy, "initialise_tenant", die)
    with pytest.raises(Crash):
        provisioning.provision(owner.actor, ProvisionIn.model_validate(body), wrapper=wrapper)
    monkeypatch.undo()
    tid = _run(body["code"])["tenant_id"]
    busy = api.call("POST", f"/tenants/{tid}/provisioning:resume", owner)
    assert (busy.status_code, busy.json()["code"]) == (409, "provisioning_in_progress")
    view = api.call("GET", f"/tenants/{tid}", owner).json()["provisioning"]
    assert (view["in_progress"], view["resumable"]) == (True, False)


def test_FR_PLT_002_run_from_before_0020_needs_the_request_to_finish(
    api: Api,
    owner: Operator,
    body: dict[str, Any],
    admin_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A backfilled run (no fingerprint, no owner parameters) resumes from the same request."""

    def boom(*_a: object, **_k: object) -> str:
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(provisioning, "_invite_and_finish", boom)
    assert api.call("POST", "/tenants", owner, json=body).status_code == 503
    monkeypatch.undo()
    with platform_session() as s:
        s.execute(
            text(
                "UPDATE platform.provisioning_runs SET request_sha256 = NULL, "
                "state = 'registered', failed_step = NULL, last_error = NULL, "
                "owner_subject = NULL, owner_display_name = NULL, owner_email = NULL, "
                "owner_language = NULL WHERE tenant_code = :c"
            ),
            {"c": body["code"]},
        )
    tid = _run(body["code"])["tenant_id"]
    res = api.call("POST", f"/tenants/{tid}/provisioning:resume", owner)
    assert (res.status_code, res.json()["code"]) == (409, "resume_needs_request")
    renamed = dict(body, school_name="Another Synthetic School")
    assert api.call("POST", "/tenants", owner, json=renamed).json()["code"] == "duplicate"
    again = api.call("POST", "/tenants", owner, json=body)
    assert again.status_code == 201, again.text
    assert again.json()["tenant_id"] == str(tid)
    _assert_completed(body["code"])
    assert _school_counts(admin_engine, body["code"]) == ONE_OF_EACH


def test_FR_PLT_003_dedicated_replay_with_a_new_key_never_repeats_the_heartbeat_key(
    api: Api, owner: Operator, make_plan: Callable[..., uuid.UUID], admin_engine: Engine
) -> None:
    body = provision_payload(make_plan(tier="dedicated"), tier="dedicated", owner=None)
    first = api.call("POST", "/tenants", owner, json=body)
    assert first.status_code == 201, first.text
    assert first.json()["heartbeat_key"]
    run = _run(body["code"])
    assert (run["state"], run["tier"], run["lease_id"]) == ("completed", "dedicated", None)
    again = api.call("POST", "/tenants", owner, json=body)
    assert again.status_code == 201
    assert again.json()["tenant_id"] == first.json()["tenant_id"]
    assert again.json()["heartbeat_key"] is None
    assert again.json()["heartbeat_key_id"] == first.json()["heartbeat_key_id"]
    assert (
        _scalar(
            admin_engine,
            "SELECT count(*) FROM platform.deployments WHERE tenant_code = :c",
            c=body["code"],
        )
        == 1
    )
