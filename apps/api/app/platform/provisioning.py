"""Resumable, idempotent school provisioning (FR-PLT-002, FR-PLT-003; docs/16 §5.4).

Shared-tier provisioning spans the control plane's own transaction and work that runs in the
new school's ``tenant_session`` (keys, system roles). They cannot share one transaction, so each
provisioning is a persisted state machine in ``platform.provisioning_runs`` (one row per school,
migration ``0020_provisioning_runs``)::

    registered --(keys + roles)--> initialised --(owner invite + school-chain event)--> completed
         \\______________________________\\______________________________> failed --(resume)--^

1. **Register** (one ``platform_session`` transaction, atomic): ``core.provision_tenant`` via
   ``tenancy.register_tenant`` (tenant row, ``provisioning``), deployment, billing account,
   subscription (+ first draft invoice when started ``active``), platform event
   ``tenant.provisioned`` and the run row (``registered``, holding this runner's lease, the
   request fingerprint and the owner invite parameters). A failure leaves none of them.
2. **Initialise**: ``tenancy.initialise_tenant`` (wrapped DEK + HMAC key, post-provision hooks
   such as system roles) in the school's own session. Idempotent: an existing key is kept, the
   hooks are idempotent and serialised per school. The run becomes ``initialised``.
3. **Invite and finish** (one platform transaction): ``core.create_owner_invite``, platform event
   ``tenant.owner_invite_created``, ``tenant.provisioned`` queued for the school's own chain
   (``platform.tenant_audit.enqueue``, ADR-0020) and the run ``completed`` with the owner
   parameters cleared. All or nothing, so the school-chain event is queued exactly once.

Idempotency and retries:

- ``request_sha256`` fingerprints the request. Submitting the same request again (any
  Idempotency-Key, any operator) resumes an unfinished run or replays a finished one; the same
  code with a different request is ``409 duplicate``. Two concurrent submissions create one
  school: the second waits on the code's unique index, then finds the run.
- **Lease**: a runner holds the run's lease (``provisioning.lease_seconds`` in billing.yaml) and
  every state change is fenced on it. A second runner gets ``409 provisioning_in_progress``; a
  crashed runner's lease expires and the next retry takes over.
- **Failure**: a step that raises marks the run ``failed`` (``failed_step``, ``last_error``
  code) with platform event ``tenant.provisioning_failed``; ``resume`` (operator,
  ``POST /platform/tenants/{id}/provisioning:resume``) or the same request again continues it,
  recorded as ``tenant.provisioning_resumed``.
- Activation (go-live) is refused until the run is ``completed`` (``tenants.activate``).

Dedicated tier: step 1 only (deployment, account, subscription, heartbeat key shown once); the
run is ``completed`` at once and the tenant row is created on the host by the runbook. A replay
never shows the heartbeat key again.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy import RowMapping

from app.core.crypto import KeyWrapper
from app.core.db import platform_session
from app.core.errors import Conflict, DomainError, NotFound, ServiceUnavailable, ValidationFailed
from app.core.ids import new_id
from app.core.logging import get_logger
from app.platform import billing, tenant_audit
from app.platform import models as m
from app.platform import repository as repo
from app.platform.common import (
    SYSTEM,
    Actor,
    audit_platform,
    config,
    db_errors,
    must,
    now,
    today_ist,
)
from app.platform.schemas import OwnerIn, ProvisionIn, ProvisionOut
from app.platform.tenants import new_heartbeat_key
from app.tenancy import service as tenancy
from app.tenancy.schemas import TenantProvisionIn

log = get_logger(__name__)

REGISTERED: Final = "registered"
INITIALISED: Final = "initialised"
COMPLETED: Final = "completed"
FAILED: Final = "failed"
_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def lease_duration() -> dt.timedelta:
    return dt.timedelta(seconds=int(config()["provisioning"]["lease_seconds"]))


def request_fingerprint(data: ProvisionIn) -> str:
    """SHA-256 of the canonical request (sorted keys): same request <=> same fingerprint."""
    canonical = json.dumps(data.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


class _LeaseLost(Exception):
    """Another runner took over this run (our lease expired); stop without changing it."""


def _in_progress() -> Conflict:
    return Conflict(
        "This school is being provisioned right now. Try again in a few minutes.",
        code="provisioning_in_progress",
    )


def _duplicate() -> Conflict:
    return Conflict("This school code is already used.", code="duplicate")


@dataclass(frozen=True, slots=True)
class _Owner:
    subject: str
    display_name: str
    email: str | None
    language: str

    @classmethod
    def of(cls, owner: OwnerIn) -> _Owner:
        return cls(owner.idp_subject, owner.display_name, owner.email, owner.language)

    @classmethod
    def from_run(cls, run: RowMapping) -> _Owner | None:
        if run["owner_subject"] is None:
            return None
        return cls(
            run["owner_subject"],
            run["owner_display_name"],
            run["owner_email"],
            run["owner_language"],
        )


# --- entry points ------------------------------------------------------------------------------


def provision(actor: Actor, data: ProvisionIn, *, wrapper: KeyWrapper) -> ProvisionOut:
    """Provision a school, or resume/replay the same request for the same code."""
    digest = request_fingerprint(data)
    with platform_session() as s:
        run = repo.get_by(s, m.provisioning_runs, m.provisioning_runs.c.tenant_code == data.code)
    if run is not None:
        return _same_request_again(actor, data, digest, run, wrapper=wrapper)
    try:
        return _register(actor, data, digest, wrapper=wrapper)
    except Conflict as exc:
        if exc.code != "duplicate":
            raise
        # A concurrent submission with this code committed first (unique index): join it.
        with platform_session() as s:
            run = repo.get_by(
                s, m.provisioning_runs, m.provisioning_runs.c.tenant_code == data.code
            )
        if run is None:
            raise
        return _same_request_again(actor, data, digest, run, wrapper=wrapper)


def resume(actor: Actor, tenant_id: uuid.UUID, *, wrapper: KeyWrapper) -> ProvisionOut:
    """Operator retry of an unfinished provisioning (no request body needed)."""
    return _resume(actor, tenant_id, wrapper=wrapper, owner=None)


def provision_result(tenant_id: uuid.UUID) -> ProvisionOut:
    """Re-read a provisioning result (idempotent replay; the heartbeat key is never repeated)."""
    return _result(tenant_id, owner_invite=None)


# --- step 1: register ----------------------------------------------------------------------------


def _register(actor: Actor, data: ProvisionIn, digest: str, *, wrapper: KeyWrapper) -> ProvisionOut:
    today = today_ist()
    tenant_id = new_id()
    lease = new_id()
    shared = data.tier == "shared"
    heartbeat: tuple[str, bytes, str] | None = None
    if not shared:
        heartbeat = new_heartbeat_key(tenant_id, wrapper)
    with platform_session() as s, db_errors():
        plan = billing.published_plan(s, data.plan_id)
        if plan["tier"] != data.tier:
            raise ValidationFailed(
                [
                    {
                        "field": "plan_id",
                        "code": "tier_mismatch",
                        "message_key": "errors.tier_mismatch",
                    }
                ]
            )
        if shared:
            tenancy.register_tenant(
                s,
                TenantProvisionIn(
                    code=data.code,
                    name=data.school_name,
                    boards=list(data.boards),
                    plan_tier=data.tier,
                    deployment_mode=data.tier,
                ),
                tenant_id=tenant_id,
            )
        dep = repo.insert_row(
            s,
            m.deployments,
            {
                "id": new_id(),
                "tenant_id": tenant_id,
                "tenant_code": data.code,
                "school_name": data.school_name,
                "boards": list(data.boards),
                "mode": data.tier,
                "custom_domain": data.custom_domain,
                "tenant_status": "provisioning",
                "status": "healthy" if shared else "provisioning",
                "heartbeat_key_id": heartbeat[0] if heartbeat else None,
                "heartbeat_key_ciphertext": heartbeat[1] if heartbeat else None,
            },
        )
        account = billing.insert_billing_account(s, tenant_id, data.billing_account)
        sub = billing.insert_subscription(
            s,
            tenant_id=tenant_id,
            account_id=account["id"],
            plan=plan,
            start_as=data.start_as,
            price_override=data.price_override_inr,
            override_reason=data.override_reason,
            today=today,
        )
        if sub["status"] == "active":  # billing in advance: first period's draft invoice
            billing.create_draft(s, SYSTEM, sub, today)
        owner = _Owner.of(must(data.owner)) if shared else None
        run = repo.insert_row(
            s,
            m.provisioning_runs,
            {
                "id": new_id(),
                "tenant_id": tenant_id,
                "tenant_code": data.code,
                "tier": data.tier,
                "request_sha256": digest,
                "state": REGISTERED if shared else COMPLETED,
                "attempts": 1,
                "lease_id": lease if shared else None,
                "lease_expires_at": now() + lease_duration() if shared else None,
                "owner_subject": owner.subject if owner else None,
                "owner_display_name": owner.display_name if owner else None,
                "owner_email": owner.email if owner else None,
                "owner_language": owner.language if owner else None,
                "created_by": actor.operator_id,
                "completed_at": None if shared else now(),
            },
        )
        audit_platform(
            s,
            actor,
            "tenant.provisioned",
            "tenant",
            tenant_id,
            {
                "tier": data.tier,
                "code": data.code,
                "plan_id": str(plan["id"]),
                "subscription_id": str(sub["id"]),
                "deployment_id": str(dep["id"]),
                "custom_domain_set": data.custom_domain is not None,
            },
            tenant_id=tenant_id,
        )
        if heartbeat:
            audit_platform(
                s,
                actor,
                "deployment.created",
                "deployment",
                dep["id"],
                {"mode": "dedicated", "heartbeat_key_id": heartbeat[0]},
                tenant_id=tenant_id,
            )
    invite: Any = "not_applicable"
    if shared:
        invite = _advance(actor, run, lease, wrapper=wrapper, owner=None)
    log.info("platform.tenant.provisioned", tenant_id=str(tenant_id), outcome=data.tier)
    return ProvisionOut(
        tenant_id=tenant_id,
        deployment_id=dep["id"],
        subscription_id=sub["id"],
        billing_account_id=account["id"],
        tier=data.tier,
        tenant_status="provisioning",
        owner_invite=invite,
        heartbeat_key_id=heartbeat[0] if heartbeat else None,
        heartbeat_key=heartbeat[2] if heartbeat else None,
    )


# --- retries -------------------------------------------------------------------------------------


def _same_request_again(
    actor: Actor, data: ProvisionIn, digest: str, run: RowMapping, *, wrapper: KeyWrapper
) -> ProvisionOut:
    """The code already has a run: resume or replay it if this is the same request."""
    if run["request_sha256"] is None:
        # Run backfilled by 0020 (provisioned before runs existed): the rule of that time,
        # same shared tier and school name, and the owner comes from this request.
        with platform_session() as s:
            dep = repo.get_by(s, m.deployments, m.deployments.c.tenant_id == run["tenant_id"])
        same = (
            dep is not None
            and run["tier"] == data.tier == "shared"
            and dep["school_name"] == data.school_name
        )
    else:
        same = run["request_sha256"] == digest
    if not same:
        raise _duplicate()
    if run["state"] == COMPLETED:
        return provision_result(run["tenant_id"])
    return _resume(
        actor,
        run["tenant_id"],
        wrapper=wrapper,
        owner=_Owner.of(data.owner) if data.owner else None,
    )


def _resume(
    actor: Actor, tenant_id: uuid.UUID, *, wrapper: KeyWrapper, owner: _Owner | None
) -> ProvisionOut:
    lease = new_id()
    with platform_session() as s:
        run = repo.claim_provisioning_run(s, tenant_id, lease, lease_duration())
        if run is not None:
            audit_platform(
                s,
                actor,
                "tenant.provisioning_resumed",
                "tenant",
                tenant_id,
                {"from_state": run["state"], "attempt": run["attempts"]},
                tenant_id=tenant_id,
            )
    if run is None:
        with platform_session() as s:
            current = repo.get_by(
                s, m.provisioning_runs, m.provisioning_runs.c.tenant_id == tenant_id
            )
        if current is None:
            raise NotFound("School not found")
        if current["state"] == COMPLETED:
            return provision_result(tenant_id)
        raise _in_progress()
    invite = _advance(actor, run, lease, wrapper=wrapper, owner=owner)
    log.info("platform.tenant.provisioning_resumed", tenant_id=str(tenant_id), outcome="completed")
    return _result(tenant_id, owner_invite=invite)


# --- steps 2 and 3 -------------------------------------------------------------------------------


def _advance(
    actor: Actor,
    run: RowMapping,
    lease: uuid.UUID,
    *,
    wrapper: KeyWrapper,
    owner: _Owner | None,
) -> str:
    """Run the remaining steps under ``lease``; return the owner invite outcome."""
    tenant_id: uuid.UUID = run["tenant_id"]
    step = "initialise"
    try:
        key_version, _key_id = tenancy.initialise_tenant(tenant_id, wrapper=wrapper)
        if run["state"] != INITIALISED:
            with platform_session() as s:
                done = repo.update_provisioning_run(
                    s,
                    run["id"],
                    lease,
                    {"state": INITIALISED, "failed_step": None, "last_error": None},
                )
            if done is None:
                raise _LeaseLost
        step = "owner_invite"
        invite = _invite_and_finish(actor, run, lease, key_version, owner)
    except _LeaseLost:
        raise _in_progress() from None
    except Exception as exc:
        _mark_failed(actor, run, lease, step, exc)
        if isinstance(exc, DomainError):
            raise
        raise ServiceUnavailable(
            "Provisioning stopped before it finished. Resume it from the school's page.",
            code="provisioning_failed",
        ) from exc
    tenant_audit.deliver_now(tenant_id)
    return invite


def _invite_and_finish(
    actor: Actor,
    run: RowMapping,
    lease: uuid.UUID,
    key_version: int,
    owner: _Owner | None,
) -> str:
    """Step 3, one platform transaction: invite, school-chain event, run completed."""
    tenant_id: uuid.UUID = run["tenant_id"]
    with platform_session() as s, db_errors():
        locked = repo.lock_provisioning_run(s, run["id"], lease)
        if locked is None:
            raise _LeaseLost
        params = _Owner.from_run(locked) or owner
        if params is None:
            raise Conflict(
                "This provisioning started before resumable provisioning. Submit the same "
                "provisioning request again to finish it.",
                code="resume_needs_request",
            )
        invite = "existing"
        try:
            with s.begin_nested(), db_errors():
                row = repo.call_create_owner_invite(
                    s,
                    tenant_id=tenant_id,
                    subject=params.subject,
                    display_name=params.display_name,
                    email=params.email,
                    language=params.language,
                )
        except Conflict:
            # The school already has its owner: an earlier attempt committed this step (only
            # possible for runs from before 0020, whose invite and school-chain event were
            # committed together). Finish without a second event.
            row = None
        if row is not None:
            invite = "created" if row["owner_role_assigned"] else "pending_role"
            audit_platform(
                s,
                actor,
                "tenant.owner_invite_created",
                "membership",
                row["membership_id"],
                {"owner_role_assigned": bool(row["owner_role_assigned"])},
                tenant_id=tenant_id,
            )
            tenant_audit.enqueue(
                s,
                tenant_id,
                actor,
                "tenant.provisioned",
                {"tier": "shared", "key_version": key_version},
            )
        finished = repo.update_provisioning_run(
            s,
            run["id"],
            lease,
            {
                "state": COMPLETED,
                "failed_step": None,
                "last_error": None,
                "lease_id": None,
                "lease_expires_at": None,
                "owner_subject": None,
                "owner_display_name": None,
                "owner_email": None,
                "owner_language": None,
                "completed_at": now(),
            },
        )
        if finished is None:  # pragma: no cover - the row is locked with our lease above
            raise _LeaseLost
    return invite


def _error_code(exc: BaseException) -> str:
    code = exc.code if isinstance(exc, DomainError) else ""
    return code if _ERROR_CODE.match(code or "") else "unexpected_error"


def _mark_failed(
    actor: Actor, run: RowMapping, lease: uuid.UUID, step: str, exc: BaseException
) -> None:
    """Record the failure (codes only) and release the lease so an operator can resume."""
    tenant_id: uuid.UUID = run["tenant_id"]
    code = _error_code(exc)
    log.error(
        "platform.tenant.provisioning_failed",
        tenant_id=str(tenant_id),
        step=step,
        error_type=type(exc).__name__,
        outcome=code,
    )
    try:
        with platform_session() as s:
            row = repo.update_provisioning_run(
                s,
                run["id"],
                lease,
                {
                    "state": FAILED,
                    "failed_step": step,
                    "last_error": code,
                    "lease_id": None,
                    "lease_expires_at": None,
                },
            )
            if row is not None:
                audit_platform(
                    s,
                    actor,
                    "tenant.provisioning_failed",
                    "tenant",
                    tenant_id,
                    {"step": step, "error_code": code, "attempt": row["attempts"]},
                    tenant_id=tenant_id,
                )
    except Exception as record_exc:  # the lease still expires; the run stays resumable
        log.error(
            "platform.tenant.provisioning_failure_not_recorded",
            tenant_id=str(tenant_id),
            error_type=type(record_exc).__name__,
        )


# --- results -------------------------------------------------------------------------------------


def _result(tenant_id: uuid.UUID, *, owner_invite: str | None) -> ProvisionOut:
    with platform_session() as s:
        dep = repo.get_by(s, m.deployments, m.deployments.c.tenant_id == tenant_id)
        sub = repo.live_subscription(s, tenant_id)
        account = repo.get_by(s, m.billing_accounts, m.billing_accounts.c.tenant_id == tenant_id)
    if dep is None or sub is None or account is None:
        raise NotFound("School not found")
    shared = dep["mode"] == "shared"
    invite: Any = owner_invite or ("existing" if shared else "not_applicable")
    return ProvisionOut(
        tenant_id=tenant_id,
        deployment_id=dep["id"],
        subscription_id=sub["id"],
        billing_account_id=account["id"],
        tier=dep["mode"],
        tenant_status=dep["tenant_status"],
        owner_invite=invite,
        heartbeat_key_id=dep["heartbeat_key_id"],
    )
