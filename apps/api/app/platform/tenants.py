"""Schools as seen by the control plane: provisioning, registry, lifecycle (FR-PLT-001..005).

No student data is read or returned: the control plane knows a school by ID, code, public name,
plan, statuses and counts (BR-09). Tenant rows are created and changed only through the
allowlisted definer functions via ``app.tenancy.service`` (ADR-0013).

Shared-tier provisioning (docs/16 §5.4):
1. one ``platform_session`` transaction: ``core.provision_tenant`` (tenant row), deployment,
   billing account, subscription, platform audit event (atomic: a failure leaves none of them);
2. ``tenancy.initialise_tenant`` (wrapped DEK + HMAC key, post-provision hooks such as role
   templates) in the new tenant's own session;
3. ``core.create_owner_invite`` (invited owner membership, owner role when present) and, in the
   same platform transaction, ``tenant.provisioned`` queued for the school's own audit chain
   (``platform.tenant_audit``; actor_type ``platform``, delivered exactly once, ADR-0020).
Steps 2-3 are idempotent; a retry with the same code and school resumes them.

Lifecycle changes (activate, suspend, reactivate, offboard) queue their school-chain copy in
the same platform transaction as the change; delivery is tried right after commit and
guaranteed by the ``platform.deliver_tenant_audit`` task.

Dedicated tier: the deployment (status ``provisioning``), billing account, subscription and a
per-deployment heartbeat key (shown once) are created here; the tenant row is created on the
host by the runbook with the same tenant ID.
"""

from __future__ import annotations

import base64
import datetime as dt
import secrets
import uuid
from typing import Any

from sqlalchemy import and_, func, or_, select

from app.core.crypto import KeyWrapper
from app.core.db import platform_session
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.ids import new_id
from app.core.logging import get_logger
from app.platform import billing, tenant_audit
from app.platform import models as m
from app.platform import repository as repo
from app.platform.common import (
    SYSTEM,
    Actor,
    audit_platform,
    clamp_limit,
    db_errors,
    must,
    now,
    parse_cursor,
    today_ist,
)
from app.platform.schemas import (
    InvoiceOut,
    ProvisionIn,
    ProvisionOut,
    SubscriptionOut,
    TenantDetailOut,
    TenantSummaryOut,
    UsageCountsOut,
)
from app.tenancy import service as tenancy
from app.tenancy.schemas import TenantProvisionIn

log = get_logger(__name__)
HEARTBEAT_KEY_BYTES = 32
_KEY_ID_ALPHABET = "abcdefghjkmnpqrstuvwxyz"


def new_heartbeat_key(tenant_id: uuid.UUID, wrapper: KeyWrapper) -> tuple[str, bytes, str]:
    """Return (key_id, wrapped key, plaintext key as base64url). Plaintext is shown once."""
    raw = secrets.token_bytes(HEARTBEAT_KEY_BYTES)
    # Letters only: key IDs appear in audit summaries, which reject long digit runs.
    key_id = "hb-" + "".join(secrets.choice(_KEY_ID_ALPHABET) for _ in range(16))
    wrapped = wrapper.wrap(raw, tenant_id=tenant_id)
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
            "offboard_requested_at": dep["offboard_requested_at"],
            "offboard_approved_at": dep["offboard_approved_at"],
            "subscription": SubscriptionOut.model_validate(dict(sub)) if sub else None,
            "counts": counts,
            "open_tickets": int(open_tickets),
            "invoices": invoices,
            "flag_overrides": overrides,
        }
    )


def provision(actor: Actor, data: ProvisionIn, *, wrapper: KeyWrapper) -> ProvisionOut:
    today = today_ist()
    with platform_session() as s:
        existing = repo.get_by(s, m.deployments, m.deployments.c.tenant_code == data.code)
    if existing is not None:
        return _resume(actor, data, existing, wrapper=wrapper)

    tenant_id = new_id()
    heartbeat: tuple[str, bytes, str] | None = None
    if data.tier == "dedicated":
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
        if data.tier == "shared":
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
                "status": "healthy" if data.tier == "shared" else "provisioning",
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
    invite = "not_applicable"
    if data.tier == "shared":
        invite = _finish_shared(actor, tenant_id, data, wrapper=wrapper)
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


def _finish_shared(
    actor: Actor, tenant_id: uuid.UUID, data: ProvisionIn, *, wrapper: KeyWrapper
) -> str:
    key_version, _key_id = tenancy.initialise_tenant(tenant_id, wrapper=wrapper)
    invite = "existing"
    owner = data.owner
    owner = must(owner)
    try:
        with platform_session() as s, db_errors():
            row = repo.call_create_owner_invite(
                s,
                tenant_id=tenant_id,
                subject=owner.idp_subject,
                display_name=owner.display_name,
                email=owner.email,
                language=owner.language,
            )
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
    except Conflict:
        invite = "existing"  # resumed: the owner membership was created by an earlier attempt
    tenant_audit.deliver_now(tenant_id)
    return invite


def _resume(actor: Actor, data: ProvisionIn, dep: Any, *, wrapper: KeyWrapper) -> ProvisionOut:
    """Retry of a shared provisioning whose first transaction committed (same code and school)."""
    same = (
        dep["mode"] == data.tier == "shared"
        and dep["school_name"] == data.school_name
        and dep["tenant_status"] == "provisioning"
    )
    if not same:
        raise Conflict("This school code is already used.", code="duplicate")
    invite = _finish_shared(actor, dep["tenant_id"], data, wrapper=wrapper)
    with platform_session() as s:
        sub = repo.live_subscription(s, dep["tenant_id"])
        account = repo.get_by(
            s, m.billing_accounts, m.billing_accounts.c.tenant_id == dep["tenant_id"]
        )
    sub = must(sub)
    account = must(account)
    return ProvisionOut(
        tenant_id=dep["tenant_id"],
        deployment_id=dep["id"],
        subscription_id=sub["id"],
        billing_account_id=account["id"],
        tier="shared",
        tenant_status="provisioning",
        owner_invite=invite,
    )


def provision_result(tenant_id: uuid.UUID) -> ProvisionOut:
    """Re-read a provisioning result (idempotent replay; the heartbeat key is never repeated)."""
    with platform_session() as s:
        dep = repo.get_by(s, m.deployments, m.deployments.c.tenant_id == tenant_id)
        sub = repo.live_subscription(s, tenant_id)
        account = repo.get_by(s, m.billing_accounts, m.billing_accounts.c.tenant_id == tenant_id)
    if dep is None or sub is None or account is None:
        raise NotFound("School not found")
    return ProvisionOut(
        tenant_id=tenant_id,
        deployment_id=dep["id"],
        subscription_id=sub["id"],
        billing_account_id=account["id"],
        tier=dep["mode"],
        tenant_status=dep["tenant_status"],
        owner_invite="existing" if dep["mode"] == "shared" else "not_applicable",
        heartbeat_key_id=dep["heartbeat_key_id"],
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
        if shared:  # dedicated schools' chains live on their host
            tenant_audit.enqueue(
                s, tenant_id, actor, action, {"from": dep["tenant_status"], "to": target}
            )
    if shared:
        tenant_audit.deliver_now(tenant_id)
    return get_tenant(tenant_id, with_counts=False)


def activate(actor: Actor, tenant_id: uuid.UUID) -> TenantDetailOut:
    """Go-live: provisioning -> active (the database refuses without a data key)."""
    return _set_status(
        actor,
        tenant_id,
        target="active",
        allowed_from=("provisioning",),
        action="tenant.activated",
        reason=None,
    )


def suspend(actor: Actor, tenant_id: uuid.UUID, reason: str) -> TenantDetailOut:
    """Non-billing suspension (security incident, abuse, school's request). Never automatic."""
    return _set_status(
        actor,
        tenant_id,
        target="suspended",
        allowed_from=("active",),
        action="tenant.suspended",
        reason=reason,
    )


def reactivate(actor: Actor, tenant_id: uuid.UUID, reason: str) -> TenantDetailOut:
    dep = _deployment(tenant_id)
    if dep["tenant_status_reason"] == "billing":
        raise Conflict(
            "Billing suspensions are lifted from the subscription.", code="billing_suspension"
        )
    del reason  # recorded in the platform audit event action only (free text is not audited)
    return _set_status(
        actor,
        tenant_id,
        target="active",
        allowed_from=("suspended",),
        action="tenant.reactivated",
        reason=None,
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
    return _set_status(
        actor,
        tenant_id,
        target="offboarding",
        allowed_from=("active", "suspended"),
        action="tenant.offboard_approved",
        reason="offboarding",
        extra={"offboard_approved_by": actor.operator_id, "offboard_approved_at": now()},
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
