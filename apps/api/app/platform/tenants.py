"""Schools as seen by the control plane: provisioning, registry, lifecycle (FR-PLT-001..005).

No student data is read or returned: the control plane knows a school by ID, code, public name,
plan, statuses and counts (BR-09). Tenant rows are created and changed only through the
allowlisted definer functions via ``app.tenancy.service`` (ADR-0013).

Provisioning (shared and dedicated) lives in ``app.platform.provisioning``: a persisted,
resumable state machine (``platform.provisioning_runs``, docs/16 §5.4). Go-live
(:func:`activate`) is refused until that run is ``completed``.

Lifecycle changes (activate, suspend, reactivate, offboard) queue their school-chain copy in
the same platform transaction as the change; delivery is tried right after commit and
guaranteed by the ``platform.deliver_tenant_audit`` task.
"""

from __future__ import annotations

import base64
import datetime as dt
import secrets
import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy import RowMapping, and_, func, or_, select
from sqlalchemy.orm import Session

from app.core.crypto import KeyWrapper, wrap_bound
from app.core.db import platform_session
from app.core.errors import Conflict, NotFound
from app.core.logging import get_logger
from app.platform import billing, offboarding, tenant_audit
from app.platform import models as m
from app.platform import repository as repo
from app.platform.common import (
    Actor,
    audit_platform,
    clamp_limit,
    db_errors,
    must,
    now,
    parse_cursor,
)
from app.platform.schemas import (
    InvoiceOut,
    ProvisioningOut,
    SubscriptionOut,
    TenantDetailOut,
    TenantSummaryOut,
    UsageCountsOut,
)
from app.tenancy import service as tenancy

log = get_logger(__name__)
HEARTBEAT_KEY_BYTES = 32
_KEY_ID_ALPHABET = "abcdefghjkmnpqrstuvwxyz"


def heartbeat_key_resource(deployment_id: uuid.UUID) -> str:
    """The row a heartbeat key is bound to in its KMS context (data-protection audit H-03)."""
    return f"deployment/{deployment_id}"


def new_heartbeat_key(
    tenant_id: uuid.UUID, deployment_id: uuid.UUID, wrapper: KeyWrapper
) -> tuple[str, bytes, str]:
    """Return (key_id, wrapped key, plaintext key as base64url). Plaintext is shown once. The
    key is wrapped bound to the school AND the deployment row (H-03)."""
    raw = secrets.token_bytes(HEARTBEAT_KEY_BYTES)
    # Letters only: key IDs appear in audit summaries, which reject long digit runs.
    key_id = "hb-" + "".join(secrets.choice(_KEY_ID_ALPHABET) for _ in range(16))
    wrapped = wrap_bound(
        wrapper, raw, tenant_id=tenant_id, resource=heartbeat_key_resource(deployment_id)
    )
    return key_id, wrapped, base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _summary_query() -> Any:
    live = (
        select(
            m.subscriptions.c.tenant_id,
            m.subscriptions.c.status.label("subscription_status"),
            m.subscriptions.c.trial_ends_at,
            m.plans.c.code.label("plan_code"),
        )
        .join(m.plans, m.plans.c.id == m.subscriptions.c.plan_id)
        .where(m.subscriptions.c.status != "cancelled")
        .subquery()
    )
    stmt = select(
        m.deployments.c.id,
        m.deployments.c.tenant_id,
        m.deployments.c.tenant_code.label("code"),
        m.deployments.c.school_name,
        m.deployments.c.mode.label("tier"),
        m.deployments.c.tenant_status,
        m.deployments.c.status.label("deployment_status"),
        m.deployments.c.app_version,
        m.deployments.c.last_heartbeat_at,
        m.deployments.c.created_at,
        live.c.plan_code,
        live.c.subscription_status,
        live.c.trial_ends_at,
    ).outerjoin(live, live.c.tenant_id == m.deployments.c.tenant_id)
    return stmt, live


def list_tenants(
    *,
    status: str | None = None,
    tier: str | None = None,
    plan: str | None = None,
    q: str | None = None,
    trial_ending: bool = False,
    past_due: bool = False,
    limit: int = 50,
    cursor: str | None = None,
) -> tuple[list[TenantSummaryOut], str | None]:
    limit = clamp_limit(limit)
    stmt, live = _summary_query()
    conds = []
    if status:
        conds.append(m.deployments.c.tenant_status == status)
    if tier:
        conds.append(m.deployments.c.mode == tier)
    if plan:
        conds.append(live.c.plan_code == plan)
    if q:
        pattern = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        conds.append(
            or_(
                m.deployments.c.tenant_code.ilike(pattern),
                m.deployments.c.school_name.ilike(pattern),
            )
        )
    if trial_ending:
        conds.append(
            and_(
                live.c.subscription_status == "trial",
                live.c.trial_ends_at < now() + dt.timedelta(days=14),
            )
        )
    if past_due:
        conds.append(live.c.subscription_status == "past_due")
    after = parse_cursor(cursor)
    if after is not None:
        conds.append(m.deployments.c.id < after)
    with platform_session() as s:
        rows = list(
            s.execute(
                stmt.where(*conds).order_by(m.deployments.c.id.desc()).limit(limit + 1)
            ).mappings()
        )
    items = [TenantSummaryOut.model_validate(dict(r)) for r in rows[:limit]]
    return items, (str(rows[limit - 1]["id"]) if len(rows) > limit else None)


def get_tenant(tenant_id: uuid.UUID, *, with_counts: bool = True) -> TenantDetailOut:
    stmt, _live = _summary_query()
    with platform_session() as s:
        row = s.execute(stmt.where(m.deployments.c.tenant_id == tenant_id)).mappings().one_or_none()
        if row is None:
            raise NotFound("School not found")
        dep = repo.get_by(s, m.deployments, m.deployments.c.tenant_id == tenant_id)
        dep = must(dep)
        sub = repo.live_subscription(s, tenant_id)
        counts = None
        if with_counts and dep["mode"] == "shared":
            usage = tenancy.tenant_usage(s, tenant_id)
            counts = UsageCountsOut.model_validate(usage.model_dump())
        open_tickets = s.execute(
            select(func.count())
            .select_from(m.support_tickets)
            .where(
                m.support_tickets.c.tenant_id == tenant_id,
                m.support_tickets.c.status.not_in(("resolved", "closed")),
            )
        ).scalar_one()
        run = repo.get_by(s, m.provisioning_runs, m.provisioning_runs.c.tenant_id == tenant_id)
        off_run = repo.get_by(s, m.offboarding_runs, m.offboarding_runs.c.tenant_id == tenant_id)
        cert = repo.get_by(
            s, m.deletion_certificates, m.deletion_certificates.c.tenant_id == tenant_id
        )
        overrides = {
            r["key"]: bool(r["enabled"])
            for r in s.execute(
                select(m.feature_flags.c.key, m.feature_flags.c.enabled).where(
                    m.feature_flags.c.tenant_id == tenant_id
                )
            ).mappings()
        }
    invoices: list[InvoiceOut] = billing.tenant_invoice_summaries(tenant_id)
    return TenantDetailOut.model_validate(
        {
            **dict(row),
            "boards": list(dep["boards"]),
            "tenant_status_reason": dep["tenant_status_reason"],
            "security_hold": bool(dep["security_hold"]),
            "offboard_requested_at": dep["offboard_requested_at"],
            "offboard_approved_at": dep["offboard_approved_at"],
            "subscription": SubscriptionOut.model_validate(dict(sub)) if sub else None,
            "counts": counts,
            "open_tickets": int(open_tickets),
            "invoices": invoices,
            "flag_overrides": overrides,
            "provisioning": _provisioning_view(run),
            "offboarding": offboarding.view(off_run, cert, at=now()),
        }
    )


def _provisioning_view(run: Any) -> ProvisioningOut | None:
    """Codes and counts only; the owner invite parameters are never shown."""
    if run is None:
        return None
    leased = run["lease_expires_at"] is not None and run["lease_expires_at"] > now()
    return ProvisioningOut(
        state=run["state"],
        failed_step=run["failed_step"],
        last_error=run["last_error"],
        attempts=run["attempts"],
        in_progress=leased,
        resumable=run["state"] != "completed" and not leased,
        updated_at=run["updated_at"],
    )


def _deployment(tenant_id: uuid.UUID) -> Any:
    with platform_session() as s:
        dep = repo.get_by(s, m.deployments, m.deployments.c.tenant_id == tenant_id)
    if dep is None:
        raise NotFound("School not found")
    return dep


def _set_status(
    actor: Actor,
    tenant_id: uuid.UUID,
    *,
    target: str,
    allowed_from: tuple[str, ...],
    action: str,
    reason: str | None,
    extra: dict[str, Any] | None = None,
    require_provisioned: bool = False,
    after: Callable[[Session, RowMapping], None] | None = None,
    check: Callable[[Session, RowMapping], None] | None = None,
) -> TenantDetailOut:
    dep0 = _deployment(tenant_id)
    shared = dep0["mode"] == "shared"
    with platform_session() as s, db_errors():
        dep = repo.get(s, m.deployments, dep0["id"], for_update=True)
        dep = must(dep)
        if dep["tenant_status"] not in allowed_from:
            raise Conflict(
                f"A school that is {dep['tenant_status']} cannot become {target}.",
                code="invalid_state",
            )
        if check is not None:  # under the deployment lock
            check(s, dep)
        if require_provisioned:
            run = repo.get_by(s, m.provisioning_runs, m.provisioning_runs.c.tenant_id == tenant_id)
            if run is not None and run["state"] != "completed":
                raise Conflict(
                    "Provisioning has not finished. Resume it before making the school live.",
                    code="provisioning_incomplete",
                )
        if shared:
            tenancy.set_tenant_status(s, tenant_id, target)  # type: ignore[arg-type]
        repo.update_row(
            s,
            m.deployments,
            dep["id"],
            {"tenant_status": target, "tenant_status_reason": reason, **(extra or {})},
        )
        audit_platform(
            s,
            actor,
            action,
            "tenant",
            tenant_id,
            {"from": dep["tenant_status"], "to": target, "tier": dep["mode"]},
            tenant_id=tenant_id,
        )
        if after is not None:  # more rows in the same transaction (the offboarding run)
            after(s, dep)
        if shared:  # dedicated schools' chains live on their host
            tenant_audit.enqueue(
                s, tenant_id, actor, action, {"from": dep["tenant_status"], "to": target}
            )
    if shared:
        tenant_audit.deliver_now(tenant_id)
    return get_tenant(tenant_id, with_counts=False)


def activate(actor: Actor, tenant_id: uuid.UUID) -> TenantDetailOut:
    """Go-live: provisioning -> active, only once provisioning completed (FR-PLT-002; the
    database also refuses without a data key)."""
    return _set_status(
        actor,
        tenant_id,
        target="active",
        allowed_from=("provisioning",),
        action="tenant.activated",
        reason=None,
        require_provisioned=True,
    )


def suspend(actor: Actor, tenant_id: uuid.UUID, reason: str) -> TenantDetailOut:
    """Security hold (security incident, abuse, school's request). Never automatic. Immediate,
    also inside a protected board-exam window, which guards billing suspensions only (docs/16
    principle 5 and §9.3; owner decision 2026-10-07, audit AA-16).

    Independent of billing (audit 2026-10-06 R-18): an active school is suspended with the hold;
    a school already suspended for billing stays suspended and gains the hold, so paying the
    invoice cannot lift a block the operator wanted to keep. 409 ``already_on_hold`` twice."""
    dep0 = _deployment(tenant_id)
    if dep0["tenant_status"] == "suspended":
        return _change_hold(actor, tenant_id, place=True, reason=reason)
    return _set_status(
        actor,
        tenant_id,
        target="suspended",
        allowed_from=("active",),
        action="tenant.suspended",
        reason=reason,
        extra={"security_hold": True},
    )


def _billing_suspended(s: Session, dep: RowMapping) -> bool:
    """The school is suspended for billing, or its subscription is (docs/16 §9)."""
    sub = repo.live_subscription(s, dep["tenant_id"])
    return dep["tenant_status_reason"] == "billing" or (
        sub is not None and sub["status"] == "suspended"
    )


def _not_billing_suspended(s: Session, dep: RowMapping) -> None:
    """A billing suspension is lifted only from the subscription (docs/16 §9). That covers a
    school suspended for billing, and a school already held for another reason whose
    subscription was suspended meanwhile (audit 2026-10-05 A-08: reactivating it made the
    school live while its subscription stayed suspended and was no longer invoiced)."""
    if _billing_suspended(s, dep):
        raise Conflict(
            "Billing suspensions are lifted from the subscription.", code="billing_suspension"
        )


def _change_hold(
    actor: Actor, tenant_id: uuid.UUID, *, place: bool, reason: str | None
) -> TenantDetailOut:
    """Place or lift the security hold of a school that stays suspended for billing (R-18).

    Placing: the school must be suspended and not yet held. Lifting: it must be held, shared
    (a billing suspension suspends only shared schools) and still billing-suspended; the reason
    goes back to ``billing``. Both are checked under the deployment lock."""
    action = "tenant.security_hold_placed" if place else "tenant.security_hold_lifted"
    with platform_session() as s, db_errors():
        dep = repo.get_by(s, m.deployments, m.deployments.c.tenant_id == tenant_id, for_update=True)
        if dep is None:
            raise NotFound("School not found")
        if dep["tenant_status"] != "suspended":
            raise Conflict("The school changed. Reload and try again.", code="invalid_state")
        if place:
            if dep["security_hold"]:
                raise Conflict("The school is already on a security hold.", code="already_on_hold")
            values: dict[str, Any] = {"security_hold": True, "tenant_status_reason": reason}
        else:
            if not dep["security_hold"] or not _billing_suspended(s, dep):
                raise Conflict("The school changed. Reload and try again.", code="invalid_state")
            values = {"security_hold": False, "tenant_status_reason": "billing"}
        repo.update_row(s, m.deployments, dep["id"], values)
        summary = {"from": "suspended", "to": "suspended"}
        audit_platform(
            s,
            actor,
            action,
            "tenant",
            tenant_id,
            {**summary, "tier": dep["mode"]},
            tenant_id=tenant_id,
        )
        shared = dep["mode"] == "shared"
        if shared:  # dedicated schools' chains live on their host
            tenant_audit.enqueue(s, tenant_id, actor, action, summary)
    if shared:
        tenant_audit.deliver_now(tenant_id)
    return get_tenant(tenant_id, with_counts=False)


def reactivate(actor: Actor, tenant_id: uuid.UUID, reason: str) -> TenantDetailOut:
    """Lift the security hold. A school that is also suspended for billing stays suspended
    (R-18): only the subscription lifts that. Without a hold, a billing suspension answers 409
    ``billing_suspension`` (A-08)."""
    del reason  # recorded in the platform audit event action only (free text is not audited)
    dep0 = _deployment(tenant_id)
    if dep0["security_hold"] and dep0["mode"] == "shared" and dep0["tenant_status"] == "suspended":
        with platform_session() as s:
            billing_held = _billing_suspended(s, dep0)
        if billing_held:
            return _change_hold(actor, tenant_id, place=False, reason=None)
    return _set_status(
        actor,
        tenant_id,
        target="active",
        allowed_from=("suspended",),
        action="tenant.reactivated",
        reason=None,
        extra={"security_hold": False},
        check=_not_billing_suspended,
    )


def request_offboarding(actor: Actor, tenant_id: uuid.UUID, reason: str) -> TenantDetailOut:
    """Two-person rule, step 1 (SEC-029): operator A records the request."""
    with platform_session() as s, db_errors():
        dep = repo.get_by(s, m.deployments, m.deployments.c.tenant_id == tenant_id, for_update=True)
        if dep is None:
            raise NotFound("School not found")
        if dep["tenant_status"] not in ("active", "suspended"):
            raise Conflict(
                "Only an active or suspended school can be offboarded.", code="invalid_state"
            )
        if dep["offboard_requested_by"] is not None:
            raise Conflict("Offboarding was already requested.", code="already_requested")
        repo.update_row(
            s,
            m.deployments,
            dep["id"],
            {
                "offboard_requested_by": actor.operator_id,
                "offboard_requested_at": now(),
                "offboard_reason": reason,
            },
        )
        audit_platform(
            s, actor, "tenant.offboard_requested", "tenant", tenant_id, {}, tenant_id=tenant_id
        )
    return get_tenant(tenant_id, with_counts=False)


def approve_offboarding(actor: Actor, tenant_id: uuid.UUID) -> TenantDetailOut:
    """Two-person rule, step 2: a DIFFERENT operator approves (409 same_operator; DB CHECK)."""
    dep0 = _deployment(tenant_id)
    if dep0["offboard_requested_by"] is None:
        raise Conflict("No offboarding request to approve.", code="not_requested")
    if dep0["offboard_requested_by"] == actor.operator_id:
        raise Conflict("A different operator must approve.", code="same_operator")
    approved_at = now()
    return _set_status(
        actor,
        tenant_id,
        target="offboarding",
        allowed_from=("active", "suspended"),
        action="tenant.offboard_approved",
        reason="offboarding",
        extra={"offboard_approved_by": actor.operator_id, "offboard_approved_at": approved_at},
        after=lambda s, row: offboarding.create_run(s, row, approved_at),
    )


def resend_owner_invite(actor: Actor, tenant_id: uuid.UUID) -> None:
    dep = _deployment(tenant_id)
    if dep["tenant_status"] != "provisioning":
        raise Conflict("The school is already live.", code="invalid_state")
    with platform_session() as s:
        audit_platform(
            s, actor, "tenant.owner_invite_sent", "tenant", tenant_id, {}, tenant_id=tenant_id
        )
    # Delivery (email via the notifications module) is not built yet; the event is the record.
    log.info("platform.owner_invite.resend", tenant_id=str(tenant_id), outcome="queued")
