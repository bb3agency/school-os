"""Pydantic IO models for the control plane (docs/16 §5, §8).

Inputs forbid unknown fields and NFC-normalise text. Outputs never carry student data: schools
are described by ID, code, public name, plan, status and counts only (BR-09). Money is
``Decimal`` (serialised as a string), INR, two decimal places.
"""

from __future__ import annotations

import datetime as dt
import re
import unicodedata
import uuid
from decimal import Decimal
from typing import Annotated, Any, Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)


def _nfc(value: Any) -> Any:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value).strip()
    return value


_NO_CONTROL = r"^[^\x00-\x1f\x7f]+$"

Text200 = Annotated[
    str, BeforeValidator(_nfc), StringConstraints(min_length=1, max_length=200, pattern=_NO_CONTROL)
]
Text100 = Annotated[
    str, BeforeValidator(_nfc), StringConstraints(min_length=1, max_length=100, pattern=_NO_CONTROL)
]
Reason = Annotated[
    str,
    BeforeValidator(_nfc),
    StringConstraints(min_length=10, max_length=500, pattern=_NO_CONTROL),
]
TenantCode = Annotated[
    str, BeforeValidator(_nfc), StringConstraints(pattern=r"^[a-z][a-z0-9-]{1,31}$")
]
BoardCode = Annotated[
    str, BeforeValidator(_nfc), StringConstraints(pattern=r"^[A-Z][A-Z0-9_]{1,15}$")
]
PlanCode = Annotated[
    str, BeforeValidator(_nfc), StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{1,40}$")
]
StateCode = Annotated[str, StringConstraints(pattern=r"^[0-9]{2}$")]
Gstin = Annotated[
    str,
    BeforeValidator(lambda v: v.strip().upper() if isinstance(v, str) else v),
    StringConstraints(pattern=r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$"),
]
Pan = Annotated[
    str,
    BeforeValidator(lambda v: v.strip().upper() if isinstance(v, str) else v),
    StringConstraints(pattern=r"^[A-Z]{5}[0-9]{4}[A-Z]$"),
]
# Deliberately simple (no extra dependency): one @, a dot in the domain, no spaces.
EmailStr = Annotated[
    str,
    BeforeValidator(lambda v: v.strip().lower() if isinstance(v, str) else v),
    StringConstraints(max_length=254, pattern=r"^[^@\s]{1,64}@[a-z0-9.-]+\.[a-z]{2,63}$"),
]
Money = Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=2)]
PlanDescription = Annotated[
    str,
    BeforeValidator(_nfc),
    StringConstraints(strip_whitespace=True, min_length=1, max_length=300, pattern=_NO_CONTROL),
]
Version = Annotated[str, StringConstraints(pattern=r"^[0-9A-Za-z][0-9A-Za-z.+-]{0,39}$")]
Domain = Annotated[
    str,
    BeforeValidator(lambda v: v.strip().lower() if isinstance(v, str) else v),
    StringConstraints(min_length=4, max_length=253, pattern=r"^([a-z0-9-]{1,63}\.)+[a-z]{2,63}$"),
]
FlagKey = Annotated[str, StringConstraints(pattern=r"^[a-z0-9_]+(\.[a-z0-9_]+)+$", max_length=100)]
Tier = Literal["shared", "dedicated"]
PlatformRole = Literal[
    "platform_owner", "platform_engineer", "support_agent", "billing_admin", "platform_viewer"
]
TenantStatus = Literal["provisioning", "active", "suspended", "offboarding", "deleted"]
SubscriptionStatus = Literal["trial", "active", "past_due", "suspended", "cancelled"]
InvoiceStatus = Literal["draft", "issued", "paid", "void"]


class In(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Out(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)


class Page[T](Out):
    """Cursor pagination envelope (docs/09 §2)."""

    data: list[T]
    next_cursor: str | None = None


class Reasoned(In):
    reason: Reason


# --- operators --------------------------------------------------------------------------------


class OperatorOut(Out):
    id: uuid.UUID
    email: str
    display_name: str
    status: Literal["invited", "active", "deactivated"]
    mfa_enrolled: bool
    roles: list[PlatformRole]
    last_login_at: dt.datetime | None
    created_at: dt.datetime | None


class OperatorInvite(In):
    email: EmailStr
    display_name: Text200
    # The operator user is created first in the operator user pool (MFA ON); its ``sub`` links
    # the account on first sign-in (runbook).
    idp_subject: Annotated[str, StringConstraints(min_length=1, max_length=255, pattern=r"^\S+$")]
    roles: list[PlatformRole] = Field(min_length=1, max_length=5)


class OperatorRolesIn(In):
    roles: list[PlatformRole] = Field(max_length=5)


class MeOut(Out):
    operator_id: uuid.UUID
    roles: list[str]
    permissions: list[str]
    step_up_fresh: bool


# --- plans ------------------------------------------------------------------------------------


class PlanLimits(In):
    students: int | None = Field(default=None, ge=0)
    staff_users: int | None = Field(default=None, ge=0)
    storage_gb: int | None = Field(default=None, ge=0)
    documents: int | None = Field(default=None, ge=0)
    ai_tokens_month: int | None = Field(default=None, ge=0)
    ai_budget_inr: Money | None = None


class PlanIn(In):
    code: PlanCode
    name: Text100
    tier: Tier = "shared"
    billing_period: Literal["monthly", "annual"] = "monthly"
    pricing_model: Literal["flat", "per_student"] = "flat"
    base_price_inr: Money
    per_student_price_inr: Money | None = None
    included_students: int | None = Field(default=None, ge=0)
    gst_rate: Literal["0", "5", "12", "18", "28"] = "18"
    sac_code: Annotated[str, StringConstraints(pattern=r"^[0-9]{6}$")] | None = None
    trial_days: int = Field(default=30, ge=0, le=365)
    limits: PlanLimits = Field(default_factory=PlanLimits)
    features: dict[FlagKey, bool] = Field(default_factory=dict, max_length=50)
    # "Implementation and data verification", ex-GST, on the subscription's first invoice.
    one_time_fee_inr: Money | None = None  # None = 0 (no fee)
    description: PlanDescription | None = None

    @model_validator(mode="after")
    def _pricing(self) -> Self:
        if (self.pricing_model == "per_student") != (self.per_student_price_inr is not None):
            raise ValueError("per_student_price_inr is required exactly for per-student plans")
        return self


class PlanPatch(In):
    name: Text100 | None = None
    base_price_inr: Money | None = None
    per_student_price_inr: Money | None = None
    included_students: int | None = Field(default=None, ge=0)
    trial_days: int | None = Field(default=None, ge=0, le=365)
    limits: PlanLimits | None = None
    features: dict[FlagKey, bool] | None = None
    one_time_fee_inr: Money | None = None
    description: PlanDescription | None = None


class PlanOut(Out):
    id: uuid.UUID
    code: str
    version: int
    name: str
    tier: Tier
    billing_period: str
    pricing_model: str
    base_price_inr: Decimal
    per_student_price_inr: Decimal | None
    included_students: int | None
    gst_rate: Decimal
    sac_code: str
    trial_days: int
    limits: dict[str, Any]
    features: dict[str, Any]
    status: Literal["draft", "published", "retired"]
    published_at: dt.datetime | None
    created_at: dt.datetime | None
    one_time_fee_inr: Decimal
    description: str | None


class AiBundleOut(Out):
    """An AI answer bundle: a monthly add-on with an included answer quota (never unlimited)."""

    id: uuid.UUID
    code: str
    version: int
    name: str
    included_answers: int
    price_inr: Decimal
    overage_rate_inr: Decimal
    status: Literal["published", "retired"]
    published_at: dt.datetime | None


class SchoolAiBundle(Out):
    """The school's own AI answer bundle on its "Plan and billing" page (FR-PLT-030, ADR-0038).

    Prices are ex-GST INR from the catalogue row. ``month_start`` is the current calendar month
    (IST). ``answers_used`` is the month's billable answers counted so far (whole IST days up to
    ``answers_counted_to``, collected the next morning), or ``null`` while the month does not
    count against the bundle (``counts_from`` is later). No tokens, no cost estimate."""

    code: str
    name: str
    included_answers: int
    price_inr: Decimal
    overage_rate_inr: Decimal
    counts_from: dt.date
    month_start: dt.date
    answers_used: int | None
    answers_counted_to: dt.date | None


# --- billing accounts -------------------------------------------------------------------------


class BillingAccountIn(In):
    legal_name: Text200
    gstin: Gstin | None = None
    pan: Pan | None = None
    billing_email: EmailStr
    billing_contact_name: Text200 | None = None
    billing_phone: Annotated[str, StringConstraints(pattern=r"^\+?[0-9]{10,13}$")] | None = None
    address_line1: Text200
    address_line2: Text200 | None = None
    city: Text100
    district: Text100 | None = None
    postal_code: Annotated[str, StringConstraints(pattern=r"^[1-9][0-9]{5}$")]
    state_code: StateCode = "37"
    po_reference: Text100 | None = None

    @model_validator(mode="after")
    def _gst(self) -> Self:
        if self.gstin is not None and self.gstin[:2] != self.state_code:
            raise ValueError("GSTIN must start with the state code")
        if self.gstin is not None and self.pan is not None and self.gstin[2:12] != self.pan:
            raise ValueError("GSTIN must contain the PAN")
        return self


class BillingAccountOut(Out):
    """School business contact (not student data; 08 §14)."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    legal_name: str
    gstin: str | None
    pan: str | None
    billing_email: str
    billing_contact_name: str | None
    billing_phone: str | None
    address_line1: str
    address_line2: str | None
    city: str
    district: str | None
    postal_code: str
    state_code: str
    po_reference: str | None
    version: int


# --- subscriptions ----------------------------------------------------------------------------


class SubscriptionOut(Out):
    id: uuid.UUID
    tenant_id: uuid.UUID
    billing_account_id: uuid.UUID
    plan_id: uuid.UUID
    pending_plan_id: uuid.UUID | None
    status: SubscriptionStatus
    trial_ends_at: dt.datetime | None
    current_period_start: dt.date
    current_period_end: dt.date
    price_override_inr: Decimal | None
    # Operator-written reason for the negotiated price (docs/16 §5.3); never student data. Set
    # exactly when ``price_override_inr`` is (DB check). Operators only: the school's own
    # billing page uses its own schema.
    override_reason: str | None
    past_due_since: dt.date | None
    grace_ends_on: dt.date | None
    cancel_at_period_end: bool
    cancelled_at: dt.datetime | None
    # AI answer bundle; answers count from the first day of ``ai_bundle_from`` (a month start).
    ai_bundle_id: uuid.UUID | None = None
    ai_bundle_from: dt.date | None = None
    version: int


class AiBundleIn(In):
    ai_bundle_id: uuid.UUID


class ExtendTrialIn(In):
    trial_ends_at: AwareDatetime


class ChangePlanIn(In):
    plan_id: uuid.UUID


class PriceOverrideIn(In):
    price_override_inr: Money
    reason: Reason


class SuspendSubscriptionIn(Reasoned):
    exam_window_override: bool = False


# --- invoices ---------------------------------------------------------------------------------


InvoiceLineKind = Literal[
    "subscription",
    "per_student",
    "one_time_fee",
    "addon",
    "usage_overage",
    "discount",
    "adjustment",
]


class InvoiceLineIn(In):
    kind: InvoiceLineKind
    description: Text200
    quantity: Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=3)] = Decimal("1")
    unit_price_inr: Annotated[Decimal, Field(max_digits=14, decimal_places=2)]
    # The calendar month (its first day) an AI overage line bills; it stops a second charge.
    usage_month: dt.date | None = None

    @model_validator(mode="after")
    def _usage_month(self) -> Self:
        if self.usage_month is not None and (
            self.kind != "usage_overage" or self.usage_month.day != 1
        ):
            raise ValueError("usage_month is the first day of a month, on overage lines only")
        return self


class InvoiceLineOut(Out):
    line_no: int
    kind: str
    description: str
    sac_code: str
    quantity: Decimal
    unit_price_inr: Decimal
    amount_inr: Decimal
    gst_rate: Decimal
    usage_month: dt.date | None = None


class InvoiceCreate(In):
    subscription_id: uuid.UUID
    period_start: dt.date


class InvoicePatch(In):
    lines: list[InvoiceLineIn] | None = Field(default=None, min_length=1, max_length=50)
    notes: Annotated[str, BeforeValidator(_nfc), StringConstraints(max_length=1000)] | None = None


class InvoiceOut(Out):
    id: uuid.UUID
    tenant_id: uuid.UUID
    subscription_id: uuid.UUID
    status: InvoiceStatus
    invoice_number: str | None
    financial_year: str | None
    period_start: dt.date
    period_end: dt.date
    issue_date: dt.date | None
    due_date: dt.date | None
    supplier_legal_name: str
    supplier_gstin: str
    supplier_state_code: str
    recipient_legal_name: str
    recipient_gstin: str | None
    place_of_supply_state_code: str
    tax_type: Literal["cgst_sgst", "igst"]
    taxable_value_inr: Decimal
    cgst_inr: Decimal
    sgst_inr: Decimal
    igst_inr: Decimal
    total_inr: Decimal
    amount_paid_inr: Decimal
    tds_inr: Decimal
    balance_due_inr: Decimal
    notes: str | None
    void_reason: str | None
    version: int
    lines: list[InvoiceLineOut] = Field(default_factory=list)


class InvoicePdfDownloadOut(Out):
    """A presigned GET (at most 5 minutes, attachment) for an issued invoice's PDF."""

    url: str
    expires_at: dt.datetime
    filename: str
    content_type: Literal["application/pdf"] = "application/pdf"
    template_version: str
    size_bytes: int
    sha256: str


class PaymentIn(In):
    method: Literal["bank_transfer", "upi", "cheque", "other"]
    amount_inr: Annotated[Decimal, Field(gt=0, max_digits=14, decimal_places=2)]
    tds_inr: Money = Decimal("0")
    received_on: dt.date
    reference: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9/_.-]{1,64}$")]
    notes: Annotated[str, BeforeValidator(_nfc), StringConstraints(max_length=500)] | None = None


class PaymentOut(Out):
    id: uuid.UUID
    invoice_id: uuid.UUID
    provider: str
    method: str
    amount_inr: Decimal
    tds_inr: Decimal
    received_on: dt.date
    reference: str
    status: Literal["recorded", "reversed"]
    recorded_at: dt.datetime | None


class InvoiceRunIn(In):
    month: Annotated[str, StringConstraints(pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$")]


class JobOut(Out):
    id: uuid.UUID
    task_name: str
    idempotency_key: str
    status: str
    progress: dict[str, Any] | None
    error: str | None
    started_at: dt.datetime | None
    finished_at: dt.datetime | None


# --- tenants ----------------------------------------------------------------------------------


class OwnerIn(In):
    display_name: Text200
    email: EmailStr | None = None
    # Sign-in subject created in the staff user pool by the provisioning runbook.
    idp_subject: Annotated[str, StringConstraints(min_length=1, max_length=255, pattern=r"^\S+$")]
    language: Literal["en", "te"] = "en"


class ProvisionIn(In):
    code: TenantCode
    school_name: Text200
    boards: list[BoardCode] = Field(default_factory=list, max_length=8)
    tier: Tier = "shared"
    custom_domain: Domain | None = None
    plan_id: uuid.UUID
    start_as: Literal["trial", "active"] = "trial"
    price_override_inr: Money | None = None
    override_reason: Reason | None = None
    owner: OwnerIn | None = None
    billing_account: BillingAccountIn

    @model_validator(mode="after")
    def _rules(self) -> Self:
        if self.tier == "shared" and self.custom_domain is not None:
            raise ValueError("custom domains are only available on the dedicated tier")
        if self.tier == "shared" and self.owner is None:
            raise ValueError("an owner is required for a shared-tier school")
        if (self.price_override_inr is None) != (self.override_reason is None):
            raise ValueError("a negotiated price needs a reason")
        return self


class ProvisionOut(Out):
    tenant_id: uuid.UUID
    deployment_id: uuid.UUID
    subscription_id: uuid.UUID
    billing_account_id: uuid.UUID
    tier: Tier
    tenant_status: TenantStatus
    owner_invite: Literal["created", "pending_role", "not_applicable", "existing"]
    # Dedicated tier only, returned ONCE at creation for the provisioning runbook (SSM).
    heartbeat_key_id: str | None = None
    heartbeat_key: str | None = None


class TenantSummaryOut(Out):
    """A school as the control plane sees it: IDs, codes, public name, statuses, counts."""

    tenant_id: uuid.UUID
    code: str
    school_name: str
    tier: Tier
    tenant_status: TenantStatus
    deployment_status: str
    plan_code: str | None
    subscription_status: SubscriptionStatus | None
    app_version: str | None
    last_heartbeat_at: dt.datetime | None
    created_at: dt.datetime | None


class UsageCountsOut(Out):
    active_memberships: int
    users: int
    sections: int
    academic_years: int


ProvisioningState = Literal["registered", "initialised", "completed", "failed"]


class ProvisioningOut(Out):
    """Where a school's provisioning stands (FR-PLT-002, docs/16 §5.4). Codes only.

    ``resumable``: not completed and no runner holds it; an operator may resume it
    (``POST /platform/tenants/{id}/provisioning:resume``).
    """

    state: ProvisioningState
    failed_step: Literal["initialise", "owner_invite"] | None
    last_error: str | None
    attempts: int
    in_progress: bool
    resumable: bool
    updated_at: dt.datetime | None


# --- offboarding (FR-PLT-005, ADR-0029) --------------------------------------------------------

# A short reference to the school's written confirmation or our delivery record (e.g. a ticket
# or letter number). Not free text: no personal data (docs/16 §5.5).
OffboardingReference = Annotated[
    str,
    BeforeValidator(_nfc),
    StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9 ._/#:-]{0,79}$"),
]
OffboardingState = Literal[
    "awaiting_export", "scheduled", "deleting", "keys_destroyed", "completed"
]


class ConfirmExportIn(In):
    """The school confirmed it has its data export, or we delivered it (runbook R8)."""

    basis: Literal["school_confirmed", "delivered_by_us"]
    reference: OffboardingReference


class ConfirmTeardownIn(In):
    """Dedicated tier: the host's KMS key is scheduled for deletion and the host destroyed
    (Terraform, docs/16 §13.4); references to those runs."""

    kms_deletion_reference: OffboardingReference
    host_teardown_reference: OffboardingReference


class DeletionCertificateOut(Out):
    id: uuid.UUID
    issued_at: dt.datetime
    template_version: str
    content_sha256: str
    pdf_sha256: str
    size_bytes: int


class CertificateDownloadOut(Out):
    """A presigned GET (at most 5 minutes, attachment) for a certificate of deletion."""

    url: str
    expires_at: dt.datetime
    filename: str
    content_type: Literal["application/pdf"] = "application/pdf"
    size_bytes: int
    sha256: str


class OffboardingOut(Out):
    """Progress of a school's offboarding (docs/16 §5.5). Codes, counts and IDs only.

    ``inventory``: rows per category before deletion; ``remaining``: rows per table still found
    by the last verification (empty when complete). ``overdue``: the 30-day deadline passed
    before the certificate was issued.
    """

    tenant_id: uuid.UUID
    tier: Tier
    state: OffboardingState
    approved_at: dt.datetime
    deadline_at: dt.datetime
    overdue: bool
    due_soon: bool
    export_basis: Literal["school_confirmed", "delivered_by_us"] | None
    export_reference: str | None
    export_confirmed_at: dt.datetime | None
    kms_deletion_reference: str | None
    host_teardown_reference: str | None
    teardown_confirmed_at: dt.datetime | None
    inventory: dict[str, int] | None
    objects_before: int | None
    remaining: dict[str, int] | None
    objects_deleted: int | None
    profiles_cleared: int | None
    keys_destroyed: int | None
    deletion_started_at: dt.datetime | None
    data_deleted_at: dt.datetime | None
    keys_destroyed_at: dt.datetime | None
    completed_at: dt.datetime | None
    audit_delete_after: dt.datetime | None
    audit_deleted_at: dt.datetime | None
    failed_step: Literal["inventory", "purge", "verify", "keys", "certificate", "audit"] | None
    last_error: str | None
    attempts: int
    in_progress: bool
    certificate: DeletionCertificateOut | None


class TenantDetailOut(TenantSummaryOut):
    boards: list[str]
    tenant_status_reason: str | None
    offboard_requested_at: dt.datetime | None
    offboard_approved_at: dt.datetime | None
    subscription: SubscriptionOut | None
    counts: UsageCountsOut | None
    open_tickets: int
    invoices: list[InvoiceOut]
    flag_overrides: dict[str, bool]
    provisioning: ProvisioningOut | None = None
    offboarding: OffboardingOut | None = None


# --- usage ------------------------------------------------------------------------------------


class UsageDailyOut(Out):
    tenant_id: uuid.UUID
    usage_date: dt.date
    source: str
    active_users: int
    staff_users: int
    students_active: int
    storage_bytes: int
    documents: int
    ai_queries: int
    ai_input_tokens: int
    ai_output_tokens: int
    ai_cost_inr: Decimal
    ai_answers: int = 0


# --- flags ------------------------------------------------------------------------------------


class FlagIn(In):
    enabled: bool
    rollout_percent: int | None = Field(default=None, ge=0, le=100)
    description: Annotated[str, BeforeValidator(_nfc), StringConstraints(max_length=300)] | None = (
        None
    )


class FlagOverrideIn(In):
    enabled: bool


class FlagOut(Out):
    key: str
    tenant_id: uuid.UUID | None
    enabled: bool
    rollout_percent: int | None
    description: str | None
    updated_at: dt.datetime | None
    version: int


# --- fleet ------------------------------------------------------------------------------------


class DeploymentOut(Out):
    id: uuid.UUID
    tenant_id: uuid.UUID
    tenant_code: str
    school_name: str
    mode: Tier
    region: str
    backup_region: str
    host_ref: str | None
    hostname: str | None
    custom_domain: str | None
    tenant_status: TenantStatus
    status: str
    app_version: str | None
    target_version: str | None
    last_heartbeat_at: dt.datetime | None
    heartbeat_key_id: str | None
    heartbeat_next_key_id: str | None
    version: int


class DeploymentPatch(In):
    target_version: Version | None = None
    custom_domain: Domain | None = None
    hostname: Domain | None = None
    host_ref: Annotated[str, StringConstraints(pattern=r"^i-[0-9a-f]{8,17}$")] | None = None


class HeartbeatKeyOut(Out):
    deployment_id: uuid.UUID
    heartbeat_key_id: str
    heartbeat_key: str


class FleetVersionOut(Out):
    version: str
    deployments: int


# --- heartbeat (SEC-028): strict, no free text ------------------------------------------------

HealthValue = Literal["ok", "degraded", "down", "unknown"]
QueueName = Annotated[str, StringConstraints(pattern=r"^[a-z_]{1,32}$")]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=False)


class HbHealth(_Strict):
    api: HealthValue
    worker: HealthValue
    beat: HealthValue
    db: HealthValue
    valkey: HealthValue
    s3: HealthValue


class HbQueue(_Strict):
    depth: int = Field(ge=0, le=10_000_000)
    oldest_s: int = Field(ge=0, le=100_000_000)


class HbHost(_Strict):
    disk_used_pct: float = Field(ge=0, le=100)
    mem_used_pct: float = Field(ge=0, le=100)
    load_1m: float = Field(ge=0, le=10_000)
    uptime_s: int = Field(ge=0)


class HbDb(_Strict):
    size_bytes: int = Field(ge=0)
    connections: int = Field(ge=0, le=100_000)


class HbBackup(_Strict):
    last_base_backup_at: AwareDatetime | None = None
    wal_archive_lag_s: int | None = Field(default=None, ge=0)
    last_pg_dump_at: AwareDatetime | None = None
    status: Literal["ok", "failing", "unknown"] = "unknown"


class HbTls(_Strict):
    cert_expires_at: AwareDatetime | None = None


class HbAudit(_Strict):
    last_verified_at: AwareDatetime | None = None
    result: Literal["ok", "broken", "unknown"] = "unknown"


class HbUsage(_Strict):
    date: dt.date
    active_users: int = Field(ge=0)
    staff_users: int = Field(ge=0)
    students_active: int = Field(ge=0)
    storage_bytes: int = Field(ge=0)
    documents: int = Field(ge=0)
    ai_queries: int = Field(ge=0)
    ai_input_tokens: int = Field(ge=0)
    ai_output_tokens: int = Field(ge=0)
    ai_cost_usd: Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=4)]
    # Billable AI answers that day (docs/16 §11); absent from hosts older than 0041 (0).
    ai_answers: int = Field(default=0, ge=0)


class HeartbeatIn(_Strict):
    schema_version: Literal[1]
    deployment_id: uuid.UUID
    tenant_id: uuid.UUID
    sent_at: AwareDatetime
    nonce: uuid.UUID
    app_version: Version
    git_sha: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{7,40}$")] | None = None
    db_revision: Annotated[str, StringConstraints(pattern=r"^[0-9a-z_]{1,64}$")] | None = None
    health: HbHealth
    queues: dict[QueueName, HbQueue] = Field(default_factory=dict, max_length=16)
    errors_5xx_rate_15m: float = Field(default=0.0, ge=0, le=1)
    host: HbHost | None = None
    db: HbDb | None = None
    backup: HbBackup | None = None
    tls: HbTls | None = None
    audit: HbAudit | None = None
    usage: HbUsage | None = None


class AnnouncementBrief(Out):
    id: uuid.UUID
    severity: str
    title_en: str
    title_te: str
    body_en: str
    body_te: str
    starts_at: dt.datetime
    ends_at: dt.datetime


class HeartbeatOut(Out):
    received_at: dt.datetime
    target_version: str | None
    min_supported_version: str
    announcements: list[AnnouncementBrief]


# --- announcements ----------------------------------------------------------------------------


class AnnouncementIn(In):
    """``title_te`` and ``body_te`` are optional while Telugu is hidden (ADR-0036): left out or
    empty, the English text is stored in their place (a new announcement) or the stored text is
    kept (an update). While Telugu is shown they are required (422)."""

    title_en: Annotated[str, BeforeValidator(_nfc), StringConstraints(min_length=1, max_length=120)]
    title_te: Annotated[str, BeforeValidator(_nfc), StringConstraints(max_length=120)] = ""
    body_en: Annotated[str, BeforeValidator(_nfc), StringConstraints(min_length=1, max_length=1000)]
    body_te: Annotated[str, BeforeValidator(_nfc), StringConstraints(max_length=1000)] = ""
    severity: Literal["info", "maintenance", "warning", "critical"] = "info"
    audience: Literal["all", "tier", "tenants"] = "all"
    audience_tier: Tier | None = None
    audience_tenant_ids: list[uuid.UUID] = Field(default_factory=list, max_length=500)
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    status: Literal["draft", "scheduled"] = "scheduled"

    @model_validator(mode="after")
    def _rules(self) -> Self:
        if self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be after starts_at")
        if (self.audience == "tier") != (self.audience_tier is not None):
            raise ValueError("audience_tier is required exactly for audience 'tier'")
        if (self.audience == "tenants") != bool(self.audience_tenant_ids):
            raise ValueError("audience_tenant_ids is required exactly for audience 'tenants'")
        return self


class AnnouncementOut(Out):
    id: uuid.UUID
    title_en: str
    title_te: str
    body_en: str
    body_te: str
    severity: str
    audience: str
    audience_tier: str | None
    audience_tenant_ids: list[uuid.UUID]
    starts_at: dt.datetime
    ends_at: dt.datetime
    status: str
    version: int


# --- support ----------------------------------------------------------------------------------

TicketCategory = Literal[
    "access", "import", "data_quality", "exports", "documents", "ask", "billing", "bug", "other"
]
TicketPriority = Literal["p1", "p2", "p3", "p4"]
TicketStatus = Literal["open", "in_progress", "waiting_on_school", "resolved", "closed"]
MessageBody = Annotated[
    str, BeforeValidator(_nfc), StringConstraints(min_length=1, max_length=4000)
]


class TicketCreateOperator(In):
    tenant_id: uuid.UUID
    channel: Literal["email", "phone", "whatsapp"]
    category: TicketCategory
    priority: TicketPriority = "p3"
    subject: Text200
    body: MessageBody


class TicketCreateSchool(In):
    category: TicketCategory
    priority: TicketPriority = "p3"
    subject: Text200
    body: MessageBody


class TicketMessageIn(In):
    body: MessageBody
    internal_note: bool = False


class SchoolTicketMessageIn(In):
    body: MessageBody


class TicketPatch(In):
    status: TicketStatus | None = None
    priority: TicketPriority | None = None
    assigned_to: uuid.UUID | None = None
    personal_data_flagged: bool | None = None


class TicketMessageOut(Out):
    id: uuid.UUID
    author_type: str
    author_id: uuid.UUID | None
    body: str
    internal_note: bool
    created_at: dt.datetime | None


class TicketOut(Out):
    id: uuid.UUID
    ticket_no: int
    number: str
    tenant_id: uuid.UUID
    channel: str
    category: str
    priority: str
    subject: str
    status: TicketStatus
    assigned_to: uuid.UUID | None
    first_response_due_at: dt.datetime
    resolution_due_at: dt.datetime
    first_responded_at: dt.datetime | None
    resolved_at: dt.datetime | None
    closed_at: dt.datetime | None
    personal_data_flagged: bool
    created_at: dt.datetime | None
    updated_at: dt.datetime | None
    version: int
    messages: list[TicketMessageOut] = Field(default_factory=list)


# --- break-glass ------------------------------------------------------------------------------


class BreakGlassIn(In):
    tenant_id: uuid.UUID
    reason_code: Literal["support_request", "security_incident", "legal_obligation"]
    reason: Reason
    scope: dict[Annotated[str, StringConstraints(pattern=r"^[a-z_]{1,40}$")], uuid.UUID | str] = (
        Field(max_length=10)
    )
    duration_minutes: int = Field(ge=15, le=480)
    emergency: bool = False

    @field_validator("scope")
    @classmethod
    def _scope_values(cls, v: dict[str, uuid.UUID | str]) -> dict[str, uuid.UUID | str]:
        for value in v.values():
            if isinstance(value, str) and not re.fullmatch(r"[a-z_.]{1,60}", value):
                raise ValueError("scope values are resource IDs or permission-like codes")
        return v


class BreakGlassOut(Out):
    id: uuid.UUID
    tenant_id: uuid.UUID
    requested_by: uuid.UUID
    reason_code: str
    reason: str
    scope: dict[str, Any]
    duration_minutes: int
    emergency: bool
    status: str
    emergency_confirmed_by_1: uuid.UUID | None
    emergency_confirmed_by_2: uuid.UUID | None
    created_at: dt.datetime | None


# --- dashboard / audit ------------------------------------------------------------------------


class DashboardOut(Out):
    mrr_inr: Decimal | None = None
    arr_inr: Decimal | None = None
    schools_by_status: dict[str, int] | None = None
    schools_by_tier: dict[str, int] | None = None
    trials_running: int | None = None
    trials_ending_14d: int | None = None
    past_due_count: int | None = None
    past_due_amount_inr: Decimal | None = None
    oldest_overdue_due_date: dt.date | None = None
    fleet_by_status: dict[str, int] | None = None
    fleet_versions: dict[str, int] | None = None
    ai_spend_mtd_inr: Decimal | None = None
    ai_spend_top: list[dict[str, Any]] | None = None
    open_tickets_by_priority: dict[str, int] | None = None
    tickets_sla_breached: int | None = None


class PlatformAuditEventOut(Out):
    id: uuid.UUID
    seq: int
    occurred_at: dt.datetime
    actor_type: str
    actor_id: uuid.UUID | None
    action: str
    resource_type: str
    resource_id: uuid.UUID | None
    subject_tenant_id: uuid.UUID | None
    summary: dict[str, Any]
    request_id: str | None


class AuditVerifyOut(Out):
    job_id: uuid.UUID
    ok: bool
    checked: int
    first_bad_seq: int | None
    reason: str | None


class OffboardRequestIn(Reasoned):
    pass
