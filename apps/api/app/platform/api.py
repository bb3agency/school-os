"""Control-plane routes ``/api/v1/platform/*`` and the fleet heartbeat (docs/16 §8).

Mounted only when ``SOS_DEPLOYMENT_MODE=shared`` (ADR-0017). Every route declares
``require_platform(...)`` (operators) or ``require_fleet_signature()`` (heartbeat). Handlers are
thin: parse, call a service, shape the response. Creating POSTs honour ``Idempotency-Key``.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from app.authz.kv import kv_store
from app.core.crypto import KeyWrapper
from app.core.errors import BadRequest
from app.ops.idempotency import (
    IdempotencyRecord,
    IdempotencyStore,
    KVIdempotencyStore,
    check_key,
    request_hash,
    resolve_existing,
)
from app.platform import (
    announcements,
    audit_view,
    billing,
    breakglass,
    dashboard,
    flags,
    fleet,
    invoice_files,
    offboarding,
    operators,
    provisioning,
    support,
    tenants,
    usage,
)
from app.platform.auth import OperatorContext, require_platform
from app.platform.common import Actor, must, today_ist
from app.platform.permissions import ANY_OPERATOR
from app.platform.schemas import (
    AiBundleIn,
    AiBundleOut,
    AnnouncementIn,
    AnnouncementOut,
    AuditVerifyOut,
    BillingAccountIn,
    BillingAccountOut,
    BreakGlassIn,
    BreakGlassOut,
    CertificateDownloadOut,
    ChangePlanIn,
    ConfirmExportIn,
    ConfirmTeardownIn,
    DashboardOut,
    DeploymentOut,
    DeploymentPatch,
    ExtendTrialIn,
    FlagIn,
    FlagKey,
    FlagOut,
    FlagOverrideIn,
    FleetVersionOut,
    HeartbeatKeyOut,
    HeartbeatOut,
    InvoiceCreate,
    InvoiceOut,
    InvoicePatch,
    InvoicePdfDownloadOut,
    InvoiceRunIn,
    JobOut,
    MeOut,
    OffboardingOut,
    OffboardRequestIn,
    OperatorInvite,
    OperatorOut,
    OperatorRolesIn,
    Page,
    PaymentIn,
    PaymentOut,
    PlanIn,
    PlanOut,
    PlanPatch,
    PlatformAuditEventOut,
    PriceOverrideIn,
    ProvisionIn,
    ProvisionOut,
    Reasoned,
    SubscriptionOut,
    SuspendSubscriptionIn,
    TenantDetailOut,
    TenantSummaryOut,
    TicketCreateOperator,
    TicketMessageIn,
    TicketOut,
    TicketPatch,
    UsageDailyOut,
)

router = APIRouter(prefix="/api/v1/platform", tags=["platform"])
fleet_router = APIRouter(prefix="/api/v1/fleet", tags=["fleet"])

Ctx = OperatorContext
Limit = Annotated[int, Query(ge=1, le=200)]
IdemKey = Annotated[str | None, Header(alias="Idempotency-Key")]
IfMatch = Annotated[str | None, Header(alias="If-Match")]


def _actor(ctx: OperatorContext) -> Actor:
    return Actor(ctx.operator_id, ctx.request_id)


def _version(if_match: str | None) -> int | None:
    if if_match is None:
        return None
    raw = if_match.removeprefix("W/").strip('"')
    if not raw.isdigit():
        raise BadRequest("If-Match must be an ETag returned by the API.", code="bad_if_match")
    return int(raw)


def _etag(response: Response, version: int) -> None:
    response.headers["ETag"] = f'"{version}"'


# --- dependencies that tests override ---------------------------------------------------------


def get_idempotency_store() -> IdempotencyStore:
    """Per-operator Idempotency-Key records in the shared Valkey store (app.authz.kv)."""
    return KVIdempotencyStore(kv_store(), prefix="sos:idem:platform:")


def get_key_wrapper() -> KeyWrapper:
    return fleet.get_fleet_key_wrapper()


Store = Annotated[IdempotencyStore, Depends(get_idempotency_store)]
Wrapper = Annotated[KeyWrapper, Depends(get_key_wrapper)]


def _idempotent(
    *,
    ctx: OperatorContext,
    request: Request,
    response: Response,
    store: IdempotencyStore,
    key: str | None,
    body: BaseModel | None,
    run: Callable[[], tuple[uuid.UUID, BaseModel]],
    replay: Callable[[uuid.UUID], BaseModel],
    resource_type: str,
    location: Callable[[uuid.UUID], str],
    status_code: int = 201,
) -> Any:
    """Run ``run`` once per (operator, Idempotency-Key); replays return the same resource."""
    if key is None:
        raise BadRequest(
            "Send an Idempotency-Key header with this request.", code="idempotency_key_required"
        )
    check_key(key)
    digest = request_hash(
        request.method,
        request.url.path,
        body.model_dump_json().encode() if body is not None else b"",
    )
    scope = f"op:{ctx.operator_id}"
    existing = store.begin(scope, key, digest)
    if existing is not None:
        record = resolve_existing(existing, digest)
        rid = uuid.UUID(must(record.resource_id))
        response.status_code = record.status_code or status_code
        if record.location:
            response.headers["Location"] = record.location
        return replay(rid)
    try:
        rid, result = run()
    except Exception:
        store.abandon(scope, key)
        raise
    loc = location(rid)
    store.complete(
        scope,
        key,
        IdempotencyRecord(digest, "completed", status_code, resource_type, str(rid), loc),
    )
    response.status_code = status_code
    response.headers["Location"] = loc
    return result


# --- operator session / dashboard -------------------------------------------------------------


@router.get("/me", response_model=MeOut)
def me(*, ctx: Annotated[Ctx, Depends(require_platform(ANY_OPERATOR))]) -> MeOut:
    return MeOut(
        operator_id=ctx.operator_id,
        roles=sorted(ctx.roles),
        permissions=sorted(ctx.permissions),
        step_up_fresh=ctx.step_up_fresh,
    )


@router.get("/dashboard", response_model=DashboardOut)
def get_dashboard(*, ctx: Annotated[Ctx, Depends(require_platform(ANY_OPERATOR))]) -> DashboardOut:
    return dashboard.build(ctx.permissions)


# --- tenants ----------------------------------------------------------------------------------


@router.get("/tenants", response_model=Page[TenantSummaryOut])
def list_tenants(
    *,
    ctx: Annotated[Ctx, Depends(require_platform("platform.tenants.read"))],
    status: str | None = None,
    tier: str | None = None,
    plan: str | None = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    trial_ending: bool = False,
    past_due: bool = False,
    limit: Limit = 50,
    cursor: str | None = None,
) -> Page[TenantSummaryOut]:
    items, nxt = tenants.list_tenants(
        status=status,
        tier=tier,
        plan=plan,
        q=q,
        trial_ending=trial_ending,
        past_due=past_due,
        limit=limit,
        cursor=cursor,
    )
    return Page[TenantSummaryOut](data=items, next_cursor=nxt)


@router.post("/tenants", response_model=ProvisionOut, status_code=201)
def provision_tenant(
    *,
    request: Request,
    response: Response,
    data: ProvisionIn,
    ctx: Annotated[Ctx, Depends(require_platform("platform.tenants.provision"))],
    store: Store,
    wrapper: Wrapper,
    idempotency_key: IdemKey = None,
) -> Any:
    """Provision a school on the shared tier or register a dedicated host (FR-PLT-002/003).

    Submitting the same request again (any Idempotency-Key) resumes an unfinished provisioning
    or replays the finished one; the same code with a different request is 409 ``duplicate``;
    a provisioning another request is running is 409 ``provisioning_in_progress``. The
    dedicated heartbeat key is returned only in the first response."""

    def run() -> tuple[uuid.UUID, BaseModel]:
        out = provisioning.provision(_actor(ctx), data, wrapper=wrapper)
        return out.tenant_id, out

    def replay(tid: uuid.UUID) -> BaseModel:
        return provisioning.provision_result(tid)

    return _idempotent(
        ctx=ctx,
        request=request,
        response=response,
        store=store,
        key=idempotency_key,
        body=data,
        run=run,
        replay=replay,
        resource_type="tenant",
        location=lambda t: f"/api/v1/platform/tenants/{t}",
    )


@router.get("/tenants/{tenant_id}", response_model=TenantDetailOut)
def get_tenant(
    *, tenant_id: uuid.UUID, ctx: Annotated[Ctx, Depends(require_platform("platform.tenants.read"))]
) -> TenantDetailOut:
    return tenants.get_tenant(tenant_id)


@router.post("/tenants/{tenant_id}/provisioning:resume", response_model=ProvisionOut)
def resume_provisioning(
    *,
    tenant_id: uuid.UUID,
    ctx: Annotated[Ctx, Depends(require_platform("platform.tenants.provision"))],
    wrapper: Wrapper,
) -> ProvisionOut:
    """Resume an unfinished or failed provisioning (FR-PLT-002, docs/16 §5.4).

    Idempotent: a finished provisioning is returned as it is. 409 ``provisioning_in_progress``
    while another request holds it; 409 ``resume_needs_request`` for a provisioning started
    before resumable provisioning (submit the same request again)."""
    return provisioning.resume(_actor(ctx), tenant_id, wrapper=wrapper)


@router.post("/tenants/{tenant_id}/activate", response_model=TenantDetailOut)
def activate_tenant(
    *,
    tenant_id: uuid.UUID,
    ctx: Annotated[Ctx, Depends(require_platform("platform.tenants.provision"))],
) -> TenantDetailOut:
    """Go-live (provisioning -> active). 409 ``provisioning_incomplete`` until provisioning has
    finished; also refused by the database without a data key."""
    return tenants.activate(_actor(ctx), tenant_id)


@router.post("/tenants/{tenant_id}/owner-invite:resend", status_code=202)
def resend_owner_invite(
    *,
    tenant_id: uuid.UUID,
    ctx: Annotated[Ctx, Depends(require_platform("platform.tenants.provision"))],
) -> dict[str, str]:
    tenants.resend_owner_invite(_actor(ctx), tenant_id)
    return {"status": "queued"}


@router.post("/tenants/{tenant_id}/suspend", response_model=TenantDetailOut)
def suspend_tenant(
    *,
    tenant_id: uuid.UUID,
    data: Reasoned,
    ctx: Annotated[Ctx, Depends(require_platform("platform.tenants.suspend"))],
) -> TenantDetailOut:
    return tenants.suspend(_actor(ctx), tenant_id, data.reason)


@router.post("/tenants/{tenant_id}/reactivate", response_model=TenantDetailOut)
def reactivate_tenant(
    *,
    tenant_id: uuid.UUID,
    data: Reasoned,
    ctx: Annotated[Ctx, Depends(require_platform("platform.tenants.suspend"))],
) -> TenantDetailOut:
    return tenants.reactivate(_actor(ctx), tenant_id, data.reason)


@router.post("/tenants/{tenant_id}/offboarding", response_model=TenantDetailOut, status_code=202)
def request_offboarding(
    *,
    tenant_id: uuid.UUID,
    data: OffboardRequestIn,
    ctx: Annotated[Ctx, Depends(require_platform("platform.tenants.offboard"))],
) -> TenantDetailOut:
    """Two-person rule step 1 (SEC-029)."""
    return tenants.request_offboarding(_actor(ctx), tenant_id, data.reason)


@router.post("/tenants/{tenant_id}/offboarding:approve", response_model=TenantDetailOut)
def approve_offboarding(
    *,
    tenant_id: uuid.UUID,
    ctx: Annotated[Ctx, Depends(require_platform("platform.tenants.offboard"))],
) -> TenantDetailOut:
    """Two-person rule step 2: a different operator (409 ``same_operator`` otherwise)."""
    return tenants.approve_offboarding(_actor(ctx), tenant_id)


@router.get("/tenants/{tenant_id}/offboarding", response_model=OffboardingOut)
def get_offboarding(
    *,
    tenant_id: uuid.UUID,
    ctx: Annotated[Ctx, Depends(require_platform("platform.tenants.read"))],
) -> OffboardingOut:
    """Offboarding progress (docs/16 §5.5, FR-PLT-005): state, 30-day deadline, counts per
    category before deletion, what verification still finds, keys, certificate. Counts and codes
    only. ``409 not_offboarding`` when offboarding was never approved."""
    del ctx
    return offboarding.get_offboarding(tenant_id)


@router.post("/tenants/{tenant_id}/offboarding:confirm-export", response_model=OffboardingOut)
def confirm_offboarding_export(
    *,
    tenant_id: uuid.UUID,
    data: ConfirmExportIn,
    ctx: Annotated[Ctx, Depends(require_platform("platform.tenants.offboard"))],
) -> OffboardingOut:
    """The export gate: the school confirmed it has its data export (``school_confirmed``) or we
    delivered it (``delivered_by_us``, runbook R8), with a short reference. The deletion job
    starts only after this (``409 export_already_confirmed`` on a second call)."""
    return offboarding.confirm_export(_actor(ctx), tenant_id, data.basis, data.reference)


@router.post("/tenants/{tenant_id}/offboarding:confirm-teardown", response_model=OffboardingOut)
def confirm_offboarding_teardown(
    *,
    tenant_id: uuid.UUID,
    data: ConfirmTeardownIn,
    ctx: Annotated[Ctx, Depends(require_platform("platform.tenants.offboard"))],
) -> OffboardingOut:
    """Dedicated tier: the host's KMS key is scheduled for deletion and the host destroyed
    (Terraform, docs/16 §13.4); the certificate follows. ``409 not_dedicated`` for shared."""
    return offboarding.confirm_teardown(
        _actor(ctx), tenant_id, data.kms_deletion_reference, data.host_teardown_reference
    )


@router.get(
    "/tenants/{tenant_id}/deletion-certificate/download-url",
    response_model=CertificateDownloadOut,
)
def deletion_certificate_download_url(
    *,
    tenant_id: uuid.UUID,
    response: Response,
    ctx: Annotated[Ctx, Depends(require_platform("platform.tenants.read"))],
) -> CertificateDownloadOut:
    """A presigned GET (at most 5 minutes, attachment) for the certificate of deletion (English;
    Telugu too only while Telugu is shown, ADR-0036). ``409 certificate_pending`` until it is
    issued. Audited as ``tenant.deletion_certificate_downloaded``."""
    response.headers["Cache-Control"] = "no-store"
    return offboarding.download_url(_actor(ctx), tenant_id)


def _range(start: dt.date | None, end: dt.date | None) -> tuple[dt.date, dt.date]:
    end = end or today_ist()
    start = start or end - dt.timedelta(days=89)
    if start > end or (end - start).days > 400:
        raise BadRequest("Choose a range of at most 400 days.", code="bad_range")
    return start, end


@router.get("/tenants/{tenant_id}/usage", response_model=list[UsageDailyOut])
def tenant_usage(
    *,
    tenant_id: uuid.UUID,
    ctx: Annotated[Ctx, Depends(require_platform("platform.usage.read"))],
    start: Annotated[dt.date | None, Query(alias="from")] = None,
    end: Annotated[dt.date | None, Query(alias="to")] = None,
) -> list[UsageDailyOut]:
    return usage.usage_for(tenant_id, *_range(start, end))


@router.get("/usage", response_model=list[UsageDailyOut])
def all_usage(
    *,
    ctx: Annotated[Ctx, Depends(require_platform("platform.usage.read"))],
    start: Annotated[dt.date | None, Query(alias="from")] = None,
    end: Annotated[dt.date | None, Query(alias="to")] = None,
    tenant_id: uuid.UUID | None = None,
) -> list[UsageDailyOut]:
    return usage.usage_for(tenant_id, *_range(start, end))


# --- billing accounts -------------------------------------------------------------------------


@router.get("/tenants/{tenant_id}/billing-account", response_model=BillingAccountOut)
def get_billing_account(
    *,
    tenant_id: uuid.UUID,
    response: Response,
    ctx: Annotated[
        Ctx,
        Depends(
            require_platform("platform.subscriptions.read", any_of=("platform.invoices.read",))
        ),
    ],
) -> BillingAccountOut:
    out = billing.get_billing_account(tenant_id)
    _etag(response, out.version)
    return out


@router.put("/tenants/{tenant_id}/billing-account", response_model=BillingAccountOut)
def put_billing_account(
    *,
    tenant_id: uuid.UUID,
    data: BillingAccountIn,
    response: Response,
    ctx: Annotated[Ctx, Depends(require_platform("platform.subscriptions.manage"))],
    if_match: IfMatch = None,
) -> BillingAccountOut:
    out = billing.put_billing_account(
        _actor(ctx), tenant_id, data, expected_version=_version(if_match)
    )
    _etag(response, out.version)
    return out


# --- plans ------------------------------------------------------------------------------------

PlanRead = Depends(
    require_platform("platform.subscriptions.read", any_of=("platform.plans.manage",))
)


@router.get("/plans", response_model=Page[PlanOut])
def list_plans(*, ctx: Annotated[Ctx, PlanRead], status: str | None = None) -> Page[PlanOut]:
    return Page[PlanOut](data=billing.list_plans(status))


@router.get("/plans/{plan_id}", response_model=PlanOut)
def get_plan(
    *, plan_id: uuid.UUID, response: Response, ctx: Annotated[Ctx, PlanRead]
) -> PlanOut:
    out = billing.get_plan(plan_id)
    _etag(response, out.row_version)
    return out


@router.post("/plans", response_model=PlanOut, status_code=201)
def create_plan(
    *,
    request: Request,
    response: Response,
    data: PlanIn,
    ctx: Annotated[Ctx, Depends(require_platform("platform.plans.manage"))],
    store: Store,
    idempotency_key: IdemKey = None,
) -> Any:
    def run() -> tuple[uuid.UUID, BaseModel]:
        out = billing.create_plan(_actor(ctx), data)
        return out.id, out

    return _idempotent(
        ctx=ctx,
        request=request,
        response=response,
        store=store,
        key=idempotency_key,
        body=data,
        run=run,
        replay=billing.get_plan,
        resource_type="plan",
        location=lambda i: f"/api/v1/platform/plans/{i}",
    )


@router.patch("/plans/{plan_id}", response_model=PlanOut)
def update_plan(
    *,
    plan_id: uuid.UUID,
    data: PlanPatch,
    response: Response,
    ctx: Annotated[Ctx, Depends(require_platform("platform.plans.manage"))],
    if_match: IfMatch = None,
) -> PlanOut:
    """Edit a draft plan. Optional If-Match with the ETag (``row_version``): 412
    ``precondition_failed`` when stale; 409 ``plan_published`` once published."""
    out = billing.update_plan(_actor(ctx), plan_id, data, expected_version=_version(if_match))
    _etag(response, out.row_version)
    return out


@router.post("/plans/{plan_id}/publish", response_model=PlanOut)
def publish_plan(
    *, plan_id: uuid.UUID, ctx: Annotated[Ctx, Depends(require_platform("platform.plans.manage"))]
) -> PlanOut:
    return billing.set_plan_status(_actor(ctx), plan_id, "published")


@router.post("/plans/{plan_id}/retire", response_model=PlanOut)
def retire_plan(
    *, plan_id: uuid.UUID, ctx: Annotated[Ctx, Depends(require_platform("platform.plans.manage"))]
) -> PlanOut:
    return billing.set_plan_status(_actor(ctx), plan_id, "retired")


@router.get("/ai-bundles", response_model=Page[AiBundleOut])
def list_ai_bundles(
    *,
    ctx: Annotated[Ctx, PlanRead],
    status: Annotated[str | None, Query(pattern=r"^(published|retired)$")] = None,
) -> Page[AiBundleOut]:
    """AI answer bundles: a monthly add-on with an included number of answers and a price per
    extra answer (docs/16 §5.6). Seeded by catalogue migrations; never unlimited."""
    return Page[AiBundleOut](data=billing.list_ai_bundles(status))


# --- subscriptions ----------------------------------------------------------------------------

SubManage = Depends(require_platform("platform.subscriptions.manage"))


@router.get("/subscriptions", response_model=Page[SubscriptionOut])
def list_subscriptions(
    *,
    ctx: Annotated[Ctx, Depends(require_platform("platform.subscriptions.read"))],
    status: str | None = None,
    limit: Limit = 50,
    cursor: str | None = None,
) -> Page[SubscriptionOut]:
    items, nxt = billing.list_subscriptions(status, limit, cursor)
    return Page[SubscriptionOut](data=items, next_cursor=nxt)


@router.get("/subscriptions/{sub_id}", response_model=SubscriptionOut)
def get_subscription(
    *,
    sub_id: uuid.UUID,
    ctx: Annotated[Ctx, Depends(require_platform("platform.subscriptions.read"))],
) -> SubscriptionOut:
    return billing.get_subscription(sub_id)


@router.post("/subscriptions/{sub_id}/activate", response_model=SubscriptionOut)
def activate_subscription(*, sub_id: uuid.UUID, ctx: Annotated[Ctx, SubManage]) -> SubscriptionOut:
    return billing.activate_subscription(_actor(ctx), sub_id)


@router.post("/subscriptions/{sub_id}/extend-trial", response_model=SubscriptionOut)
def extend_trial(
    *, sub_id: uuid.UUID, data: ExtendTrialIn, ctx: Annotated[Ctx, SubManage]
) -> SubscriptionOut:
    return billing.extend_trial(_actor(ctx), sub_id, data.trial_ends_at)


@router.post("/subscriptions/{sub_id}/change-plan", response_model=SubscriptionOut)
def change_plan(
    *, sub_id: uuid.UUID, data: ChangePlanIn, ctx: Annotated[Ctx, SubManage]
) -> SubscriptionOut:
    """Takes effect at the next period (no proration in M0); immediately for a trial."""
    return billing.change_plan(_actor(ctx), sub_id, data.plan_id)


@router.post("/subscriptions/{sub_id}/cancel", response_model=SubscriptionOut)
def cancel_subscription(
    *, sub_id: uuid.UUID, data: Reasoned, ctx: Annotated[Ctx, SubManage]
) -> SubscriptionOut:
    """Cancels at period end (a trial is cancelled at once)."""
    return billing.cancel_subscription(_actor(ctx), sub_id, data.reason)


@router.put("/subscriptions/{sub_id}/price-override", response_model=SubscriptionOut)
def set_price_override(
    *, sub_id: uuid.UUID, data: PriceOverrideIn, ctx: Annotated[Ctx, SubManage]
) -> SubscriptionOut:
    return billing.set_price_override(_actor(ctx), sub_id, data.price_override_inr, data.reason)


@router.delete("/subscriptions/{sub_id}/price-override", response_model=SubscriptionOut)
def clear_price_override(*, sub_id: uuid.UUID, ctx: Annotated[Ctx, SubManage]) -> SubscriptionOut:
    return billing.set_price_override(_actor(ctx), sub_id, None, None)


@router.put("/subscriptions/{sub_id}/ai-bundle", response_model=SubscriptionOut)
def set_ai_bundle(
    *, sub_id: uuid.UUID, data: AiBundleIn, ctx: Annotated[Ctx, SubManage]
) -> SubscriptionOut:
    """Choose or change the AI answer bundle (monthly plans only; ``409
    ai_bundle_needs_monthly_plan``). It counts from the first full calendar month after today
    (a trial's from the month after activation); answers above the quota are billed on the next
    invoice at the bundle's price per extra answer."""
    return billing.set_ai_bundle(_actor(ctx), sub_id, data.ai_bundle_id)


@router.delete("/subscriptions/{sub_id}/ai-bundle", response_model=SubscriptionOut)
def remove_ai_bundle(*, sub_id: uuid.UUID, ctx: Annotated[Ctx, SubManage]) -> SubscriptionOut:
    """Remove the AI answer bundle: no bundle line and no overage from the next invoice."""
    return billing.set_ai_bundle(_actor(ctx), sub_id, None)


@router.post("/subscriptions/{sub_id}/suspend", response_model=SubscriptionOut)
def suspend_subscription(
    *, sub_id: uuid.UUID, data: SuspendSubscriptionIn, ctx: Annotated[Ctx, SubManage]
) -> SubscriptionOut:
    """Never automatic: only past_due after the 15-day grace; exam windows need an owner."""
    return billing.suspend_subscription(
        _actor(ctx),
        sub_id,
        data.reason,
        actor_is_owner="platform_owner" in ctx.roles,
        exam_window_override=data.exam_window_override,
    )


@router.post("/subscriptions/{sub_id}/reactivate", response_model=SubscriptionOut)
def reactivate_subscription(
    *, sub_id: uuid.UUID, ctx: Annotated[Ctx, SubManage]
) -> SubscriptionOut:
    return billing.reactivate_subscription(_actor(ctx), sub_id)


# --- invoices and payments --------------------------------------------------------------------

InvRead = Depends(require_platform("platform.invoices.read"))
InvManage = Depends(require_platform("platform.invoices.manage"))


@router.get("/invoices", response_model=Page[InvoiceOut])
def list_invoices(
    *,
    ctx: Annotated[Ctx, InvRead],
    status: str | None = None,
    financial_year: Annotated[str | None, Query(pattern=r"^[0-9]{4}-[0-9]{2}$")] = None,
    tenant_id: uuid.UUID | None = None,
    limit: Limit = 50,
    cursor: str | None = None,
) -> Page[InvoiceOut]:
    items, nxt = billing.list_invoices(
        status=status, fy=financial_year, tenant_id=tenant_id, limit=limit, cursor=cursor
    )
    return Page[InvoiceOut](data=items, next_cursor=nxt)


@router.get("/invoices/{invoice_id}", response_model=InvoiceOut)
def get_invoice(
    *, invoice_id: uuid.UUID, response: Response, ctx: Annotated[Ctx, InvRead]
) -> InvoiceOut:
    out = billing.get_invoice(invoice_id)
    _etag(response, out.version)
    return out


@router.get("/invoices/{invoice_id}/download-url", response_model=InvoicePdfDownloadOut)
def invoice_download_url(
    *, invoice_id: uuid.UUID, response: Response, ctx: Annotated[Ctx, InvRead]
) -> InvoicePdfDownloadOut:
    """A presigned GET (at most 5 minutes, attachment) for an issued invoice's PDF (docs/16
    §5.8). ``409 invoice_draft`` for a draft, ``409 invoice_pdf_pending`` until the worker has
    rendered it (usually within a minute of issue). Audited as ``invoice.pdf_downloaded``."""
    response.headers["Cache-Control"] = "no-store"
    return invoice_files.download_url(_actor(ctx), invoice_id)


@router.post("/invoices", response_model=InvoiceOut, status_code=201)
def create_invoice(
    *,
    request: Request,
    response: Response,
    data: InvoiceCreate,
    ctx: Annotated[Ctx, InvManage],
    store: Store,
    idempotency_key: IdemKey = None,
) -> Any:
    def run() -> tuple[uuid.UUID, BaseModel]:
        out = billing.create_manual_draft(_actor(ctx), data.subscription_id, data.period_start)
        return out.id, out

    return _idempotent(
        ctx=ctx,
        request=request,
        response=response,
        store=store,
        key=idempotency_key,
        body=data,
        run=run,
        replay=billing.get_invoice,
        resource_type="invoice",
        location=lambda i: f"/api/v1/platform/invoices/{i}",
    )


@router.patch("/invoices/{invoice_id}", response_model=InvoiceOut)
def update_invoice(
    *,
    invoice_id: uuid.UUID,
    data: InvoicePatch,
    ctx: Annotated[Ctx, InvManage],
    if_match: IfMatch = None,
) -> InvoiceOut:
    return billing.update_draft(
        _actor(ctx),
        invoice_id,
        lines=data.lines,
        notes=data.notes,
        notes_set="notes" in data.model_fields_set,
        expected_version=_version(if_match),
    )


@router.delete("/invoices/{invoice_id}", status_code=204)
def discard_invoice(*, invoice_id: uuid.UUID, ctx: Annotated[Ctx, InvManage]) -> Response:
    billing.discard_draft(_actor(ctx), invoice_id)
    return Response(status_code=204)


@router.post("/invoices/{invoice_id}/issue", response_model=InvoiceOut)
def issue_invoice(*, invoice_id: uuid.UUID, ctx: Annotated[Ctx, InvManage]) -> InvoiceOut:
    """Assigns the next gapless number of the financial year (e.g. SOS/26-27/000123)."""
    return billing.issue_invoice(_actor(ctx), invoice_id)


@router.post("/invoices/{invoice_id}/void", response_model=InvoiceOut)
def void_invoice(
    *, invoice_id: uuid.UUID, data: Reasoned, ctx: Annotated[Ctx, InvManage]
) -> InvoiceOut:
    return billing.void_invoice(_actor(ctx), invoice_id, data.reason)


@router.post("/invoices/{invoice_id}/payments", response_model=PaymentOut, status_code=201)
def record_payment(
    *,
    invoice_id: uuid.UUID,
    request: Request,
    response: Response,
    data: PaymentIn,
    ctx: Annotated[Ctx, InvManage],
    store: Store,
    idempotency_key: IdemKey = None,
) -> Any:
    def run() -> tuple[uuid.UUID, BaseModel]:
        out = billing.record_payment(_actor(ctx), invoice_id, data)
        return out.id, out

    def replay(pid: uuid.UUID) -> BaseModel:
        return billing.get_payment(pid)

    return _idempotent(
        ctx=ctx,
        request=request,
        response=response,
        store=store,
        key=idempotency_key,
        body=data,
        run=run,
        replay=replay,
        resource_type="payment",
        location=lambda _p: f"/api/v1/platform/invoices/{invoice_id}",
    )


@router.post("/payments/{payment_id}/reverse", response_model=PaymentOut)
def reverse_payment(
    *, payment_id: uuid.UUID, data: Reasoned, ctx: Annotated[Ctx, InvManage]
) -> PaymentOut:
    return billing.reverse_payment(_actor(ctx), payment_id, data.reason)


@router.post("/invoice-runs", response_model=JobOut, status_code=202)
def invoice_run(
    *, data: InvoiceRunIn, response: Response, ctx: Annotated[Ctx, InvManage]
) -> JobOut:
    """Generate drafts for a month now (idempotent per month; the beat job does the same)."""
    job = billing.generate_invoices(data.month, _actor(ctx))
    response.headers["Location"] = f"/api/v1/platform/jobs/{job.id}"
    return job


# --- feature flags ----------------------------------------------------------------------------

FlagManage = Depends(require_platform("platform.flags.manage"))


@router.get("/flags", response_model=Page[FlagOut])
def list_flags(
    *,
    ctx: Annotated[Ctx, Depends(require_platform("platform.flags.read"))],
) -> Page[FlagOut]:
    return Page[FlagOut](data=flags.list_flags())


@router.put("/flags/{key}", response_model=FlagOut)
def put_flag(*, key: FlagKey, data: FlagIn, ctx: Annotated[Ctx, FlagManage]) -> FlagOut:
    return flags.set_global(_actor(ctx), key, data)


@router.put("/flags/{key}/tenants/{tenant_id}", response_model=FlagOut)
def put_flag_override(
    *, key: FlagKey, tenant_id: uuid.UUID, data: FlagOverrideIn, ctx: Annotated[Ctx, FlagManage]
) -> FlagOut:
    return flags.set_override(_actor(ctx), key, tenant_id, data.enabled)


@router.delete("/flags/{key}/tenants/{tenant_id}", status_code=204)
def delete_flag_override(
    *, key: FlagKey, tenant_id: uuid.UUID, ctx: Annotated[Ctx, FlagManage]
) -> Response:
    flags.remove_override(_actor(ctx), key, tenant_id)
    return Response(status_code=204)


# --- fleet ------------------------------------------------------------------------------------

FleetRead = Depends(require_platform("platform.fleet.read"))
FleetManage = Depends(require_platform("platform.fleet.manage"))


@router.get("/deployments", response_model=Page[DeploymentOut])
def list_deployments(
    *, ctx: Annotated[Ctx, FleetRead], status: str | None = None
) -> Page[DeploymentOut]:
    return Page[DeploymentOut](data=fleet.list_deployments(status))


@router.get("/deployments/{deployment_id}", response_model=DeploymentOut)
def get_deployment(
    *, deployment_id: uuid.UUID, response: Response, ctx: Annotated[Ctx, FleetRead]
) -> DeploymentOut:
    out = fleet.get_deployment(deployment_id)
    _etag(response, out.version)
    return out


@router.patch("/deployments/{deployment_id}", response_model=DeploymentOut)
def update_deployment(
    *,
    deployment_id: uuid.UUID,
    data: DeploymentPatch,
    ctx: Annotated[Ctx, FleetManage],
    if_match: IfMatch = None,
) -> DeploymentOut:
    return fleet.update_deployment(
        _actor(ctx), deployment_id, data, expected_version=_version(if_match)
    )


@router.post("/deployments/{deployment_id}/heartbeat-key:rotate", response_model=HeartbeatKeyOut)
def rotate_heartbeat_key(
    *, deployment_id: uuid.UUID, ctx: Annotated[Ctx, FleetManage], wrapper: Wrapper
) -> HeartbeatKeyOut:
    """Returns the new key ONCE for the runbook (SSM Parameter Store)."""
    return fleet.rotate_key(_actor(ctx), deployment_id, wrapper=wrapper)


@router.post("/deployments/{deployment_id}/decommission", response_model=DeploymentOut)
def decommission_deployment(
    *, deployment_id: uuid.UUID, ctx: Annotated[Ctx, FleetManage]
) -> DeploymentOut:
    return fleet.decommission(_actor(ctx), deployment_id)


@router.get("/fleet/versions", response_model=list[FleetVersionOut])
def fleet_versions(*, ctx: Annotated[Ctx, FleetRead]) -> list[FleetVersionOut]:
    return fleet.versions()


# --- announcements ----------------------------------------------------------------------------

AnnManage = Depends(require_platform("platform.announcements.manage"))


@router.get("/announcements", response_model=Page[AnnouncementOut])
def list_announcements(
    *,
    ctx: Annotated[Ctx, Depends(require_platform(ANY_OPERATOR))],
) -> Page[AnnouncementOut]:
    return Page[AnnouncementOut](data=announcements.list_announcements())


@router.post("/announcements", response_model=AnnouncementOut, status_code=201)
def create_announcement(
    *,
    request: Request,
    response: Response,
    data: AnnouncementIn,
    ctx: Annotated[Ctx, AnnManage],
    store: Store,
    idempotency_key: IdemKey = None,
) -> Any:
    def run() -> tuple[uuid.UUID, BaseModel]:
        out = announcements.create(_actor(ctx), data)
        return out.id, out

    def replay(aid: uuid.UUID) -> BaseModel:
        return announcements.get(aid)

    result = _idempotent(
        ctx=ctx,
        request=request,
        response=response,
        store=store,
        key=idempotency_key,
        body=data,
        run=run,
        replay=replay,
        resource_type="announcement",
        location=lambda i: f"/api/v1/platform/announcements/{i}",
    )
    announcements.publish()
    return result


@router.patch("/announcements/{announcement_id}", response_model=AnnouncementOut)
def update_announcement(
    *,
    announcement_id: uuid.UUID,
    data: AnnouncementIn,
    ctx: Annotated[Ctx, AnnManage],
    if_match: IfMatch = None,
) -> AnnouncementOut:
    out = announcements.update(
        _actor(ctx), announcement_id, data, expected_version=_version(if_match)
    )
    announcements.publish()
    return out


@router.post("/announcements/{announcement_id}/cancel", response_model=AnnouncementOut)
def cancel_announcement(
    *, announcement_id: uuid.UUID, ctx: Annotated[Ctx, AnnManage]
) -> AnnouncementOut:
    out = announcements.cancel(_actor(ctx), announcement_id)
    announcements.publish()
    return out


# --- support ----------------------------------------------------------------------------------

SupRead = Depends(require_platform("platform.support.read"))
SupManage = Depends(require_platform("platform.support.manage"))


@router.get("/support/tickets", response_model=Page[TicketOut])
def list_tickets(
    *,
    ctx: Annotated[Ctx, SupRead],
    status: str | None = None,
    priority: str | None = None,
    tenant_id: uuid.UUID | None = None,
    assigned_to: uuid.UUID | None = None,
    limit: Limit = 50,
    cursor: str | None = None,
) -> Page[TicketOut]:
    items, nxt = support.list_tickets(
        status=status,
        priority=priority,
        tenant_id=tenant_id,
        assigned_to=assigned_to,
        limit=limit,
        cursor=cursor,
    )
    return Page[TicketOut](data=items, next_cursor=nxt)


@router.get("/support/tickets/{ticket_id}", response_model=TicketOut)
def get_ticket(
    *, ticket_id: uuid.UUID, response: Response, ctx: Annotated[Ctx, SupRead]
) -> TicketOut:
    out = support.get_ticket(ticket_id)
    _etag(response, out.version)
    return out


@router.post("/support/tickets", response_model=TicketOut, status_code=201)
def open_ticket(
    *,
    request: Request,
    response: Response,
    data: TicketCreateOperator,
    ctx: Annotated[Ctx, SupManage],
    store: Store,
    idempotency_key: IdemKey = None,
) -> Any:
    def run() -> tuple[uuid.UUID, BaseModel]:
        out = support.open_ticket_by_operator(_actor(ctx), data)
        return out.id, out

    return _idempotent(
        ctx=ctx,
        request=request,
        response=response,
        store=store,
        key=idempotency_key,
        body=data,
        run=run,
        replay=support.get_ticket,
        resource_type="support_ticket",
        location=lambda i: f"/api/v1/platform/support/tickets/{i}",
    )


@router.post("/support/tickets/{ticket_id}/messages", response_model=TicketOut, status_code=201)
def add_ticket_message(
    *, ticket_id: uuid.UUID, data: TicketMessageIn, ctx: Annotated[Ctx, SupManage]
) -> TicketOut:
    return support.add_operator_message(
        _actor(ctx), ticket_id, data.body, internal_note=data.internal_note
    )


@router.patch("/support/tickets/{ticket_id}", response_model=TicketOut)
def update_ticket(
    *,
    ticket_id: uuid.UUID,
    data: TicketPatch,
    ctx: Annotated[Ctx, SupManage],
    if_match: IfMatch = None,
) -> TicketOut:
    return support.update_ticket(_actor(ctx), ticket_id, data, expected_version=_version(if_match))


# --- break-glass ------------------------------------------------------------------------------


@router.get("/break-glass-requests", response_model=Page[BreakGlassOut])
def list_breakglass(
    *,
    ctx: Annotated[Ctx, Depends(require_platform(ANY_OPERATOR))],
    tenant_id: uuid.UUID | None = None,
) -> Page[BreakGlassOut]:
    return Page[BreakGlassOut](data=breakglass.list_requests(tenant_id))


@router.post("/break-glass-requests", response_model=BreakGlassOut, status_code=201)
def create_breakglass(
    *,
    data: BreakGlassIn,
    ctx: Annotated[Ctx, Depends(require_platform("platform.breakglass.request"))],
) -> BreakGlassOut:
    return breakglass.create_request(_actor(ctx), data)


@router.post("/break-glass-requests/{request_id}/emergency-confirm", response_model=BreakGlassOut)
def confirm_breakglass(
    *,
    request_id: uuid.UUID,
    ctx: Annotated[Ctx, Depends(require_platform("platform.breakglass.emergency"))],
) -> BreakGlassOut:
    """Two different operators must confirm emergency access (SEC-029)."""
    return breakglass.emergency_confirm(_actor(ctx), request_id)


# --- operators --------------------------------------------------------------------------------

OpManage = Depends(require_platform("platform.operators.manage"))


@router.get("/operators", response_model=Page[OperatorOut])
def list_operators(
    *,
    ctx: Annotated[Ctx, Depends(require_platform("platform.operators.manage", step_up=False))],
    limit: Limit = 50,
    cursor: str | None = None,
) -> Page[OperatorOut]:
    items, nxt = operators.list_operators(limit, cursor)
    return Page[OperatorOut](data=items, next_cursor=nxt)


@router.post("/operators", response_model=OperatorOut, status_code=201)
def invite_operator(
    *,
    request: Request,
    response: Response,
    data: OperatorInvite,
    ctx: Annotated[Ctx, OpManage],
    store: Store,
    idempotency_key: IdemKey = None,
) -> Any:
    def run() -> tuple[uuid.UUID, BaseModel]:
        out = operators.invite_operator(_actor(ctx), data)
        return out.id, out

    return _idempotent(
        ctx=ctx,
        request=request,
        response=response,
        store=store,
        key=idempotency_key,
        body=data,
        run=run,
        replay=operators.get_operator,
        resource_type="operator",
        location=lambda i: f"/api/v1/platform/operators/{i}",
    )


@router.put("/operators/{operator_id}/roles", response_model=OperatorOut)
def set_operator_roles(
    *, operator_id: uuid.UUID, data: OperatorRolesIn, ctx: Annotated[Ctx, OpManage]
) -> OperatorOut:
    return operators.set_roles(_actor(ctx), operator_id, data.roles)


@router.post("/operators/{operator_id}/deactivate", response_model=OperatorOut)
def deactivate_operator(*, operator_id: uuid.UUID, ctx: Annotated[Ctx, OpManage]) -> OperatorOut:
    return operators.deactivate(_actor(ctx), operator_id)


# --- platform audit and jobs ------------------------------------------------------------------

AuditRead = Depends(require_platform("platform.audit.read"))


@router.get("/audit/events", response_model=Page[PlatformAuditEventOut])
def list_audit_events(
    *,
    request: Request,
    ctx: Annotated[Ctx, AuditRead],
    actor: uuid.UUID | None = None,
    action: Annotated[str | None, Query(max_length=100)] = None,
    tenant_id: uuid.UUID | None = None,
    start: Annotated[dt.datetime | None, Query(alias="from")] = None,
    end: Annotated[dt.datetime | None, Query(alias="to")] = None,
    limit: Limit = 50,
    cursor: Annotated[int | None, Query(ge=1)] = None,
) -> Any:
    """Filters: actor, action, tenant_id, from, to. ``Accept: text/csv`` returns CSV."""
    items, nxt = audit_view.list_events(
        actor_id=actor,
        action=action,
        tenant_id=tenant_id,
        start=start,
        end=end,
        limit=limit,
        cursor=cursor,
    )
    if "text/csv" in request.headers.get("accept", ""):
        return PlainTextResponse(audit_view.to_csv(items), media_type="text/csv")
    return Page[PlatformAuditEventOut](data=items, next_cursor=nxt)


@router.post("/audit/verify", response_model=AuditVerifyOut, status_code=202)
def verify_audit(*, response: Response, ctx: Annotated[Ctx, AuditRead]) -> AuditVerifyOut:
    out = audit_view.verify(_actor(ctx))
    response.headers["Location"] = f"/api/v1/platform/jobs/{out.job_id}"
    return out


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(
    *, job_id: uuid.UUID, ctx: Annotated[Ctx, Depends(require_platform(ANY_OPERATOR))]
) -> JobOut:
    return audit_view.get_job(
        job_id, operator_id=ctx.operator_id, can_read_all=ctx.has("platform.audit.read")
    )


# --- fleet heartbeat (machine to machine) -----------------------------------------------------


@fleet_router.post("/heartbeat", response_model=HeartbeatOut)
def heartbeat(
    *,
    verified: Annotated[fleet.VerifiedHeartbeat, Depends(fleet.require_fleet_signature())],
) -> HeartbeatOut:
    """HMAC-authenticated heartbeat from a dedicated host (SEC-028; docs/16 §12)."""
    return fleet.accept_heartbeat(verified)
