/**
 * HAND-WRITTEN PLACEHOLDER TYPES.
 *
 * These mirror the endpoints agreed in the build contract (§5, FR-PLT-001..030) and
 * docs/09 so the web shells compile before the API exists. Once `apps/api/openapi.json`
 * is exported, run `npm run generate -w @schoolos/api-client` and switch `src/paths.ts`
 * to re-export the generated `paths`/`components`. Field names follow the API's
 * snake_case JSON. Money is a decimal string (numeric(14,2), INR). Dates are
 * `YYYY-MM-DD`; timestamps are RFC 3339 UTC. No type here carries student data.
 */

/** RFC 9457 problem details (docs/09 §3). */
export interface Problem {
  type: string;
  title: string;
  status: number;
  code: string;
  detail?: string;
  instance?: string;
  request_id?: string;
  errors?: Array<{ field: string; code: string; message_key: string }>;
}

/** Cursor pagination envelope (docs/09 §2). */
export interface Page<T> {
  data: T[];
  next_cursor: string | null;
}

export type DeploymentMode = "shared" | "dedicated";
export type TenantStatus = "provisioning" | "active" | "suspended" | "offboarding";
export type SubscriptionStatus = "trial" | "active" | "past_due" | "suspended" | "cancelled";
export type InvoiceStatus = "draft" | "issued" | "paid" | "void";
export type DeploymentStatus = "healthy" | "degraded" | "down" | "provisioning" | "unknown";
export type TicketStatus = "open" | "pending" | "resolved" | "closed";
export type TicketPriority = "low" | "normal" | "high" | "urgent";
export type PersonStatus = "invited" | "active" | "deactivated";
export type PlanStatus = "draft" | "active" | "retired";
export type AnnouncementStatus = "draft" | "scheduled" | "live" | "ended";
export type AnnouncementAudience = "all" | "shared" | "dedicated" | "tenants";
export type BreakGlassStatus =
  "pending" | "approved" | "active" | "expired" | "rejected" | "revoked";

export const PLATFORM_ROLES = [
  "platform_owner",
  "platform_engineer",
  "support_agent",
  "billing_admin",
  "platform_viewer",
] as const;
export type PlatformRole = (typeof PLATFORM_ROLES)[number];

export const TENANT_ROLES = [
  "owner",
  "principal",
  "office_admin",
  "office_staff",
  "accountant",
  "exam_coordinator",
  "class_teacher",
  "teacher",
  "auditor_readonly",
] as const;
export type TenantRoleKey = (typeof TENANT_ROLES)[number];

/* ---------------------------------------------------------------- platform */

/** FR-PLT dashboard. Every value may be null when not computable yet. */
export interface PlatformKpis {
  mrr_inr: string | null;
  arr_inr: string | null;
  active_schools: number | null;
  trial_schools: number | null;
  past_due_subscriptions: number | null;
  fleet_healthy: number | null;
  fleet_total: number | null;
  ai_spend_month_inr: string | null;
}

export interface TenantSummary {
  id: string;
  name: string;
  code: string;
  status: TenantStatus;
  plan_key: string | null;
  plan_name: string | null;
  deployment_mode: DeploymentMode;
  region: string;
  created_at: string;
}

export interface BillingAccount {
  legal_name: string;
  gstin: string | null;
  billing_email: string;
  state_code: string;
  address: string | null;
}

export interface TenantDetail extends TenantSummary {
  billing_account: BillingAccount | null;
  subscription: Subscription | null;
  deployment: Deployment | null;
}

/** POST /platform/tenants (FR-PLT-001). */
export interface ProvisionTenantRequest {
  school_name: string;
  legal_name: string;
  state_code: string;
  billing_email: string;
  gstin: string | null;
  plan_key: string;
  deployment_mode: DeploymentMode;
  custom_domain: string | null;
  owner_name: string;
  owner_email: string;
}

export interface ProvisionTenantAccepted {
  tenant_id: string;
  job_id: string;
}

export interface PlanLimits {
  students: number | null;
  active_users: number | null;
  storage_bytes: number | null;
  documents: number | null;
  ai_questions_per_month: number | null;
}

export interface Plan {
  id: string;
  key: string;
  name: string;
  version: number;
  deployment_mode: DeploymentMode;
  status: PlanStatus;
  monthly_price_inr: string;
  annual_price_inr: string;
  gst_rate_percent: string;
  limits: PlanLimits;
}

export interface Subscription {
  id: string;
  tenant_id: string;
  school_name: string;
  plan_key: string;
  plan_name: string;
  status: SubscriptionStatus;
  current_period_end: string | null;
  trial_ends_at: string | null;
}

export interface Invoice {
  id: string;
  number: string | null;
  tenant_id: string;
  school_name: string;
  status: InvoiceStatus;
  issue_date: string | null;
  due_date: string | null;
  subtotal_inr: string;
  gst_inr: string;
  total_inr: string;
}

export interface UsageDaily {
  tenant_id: string;
  school_name: string;
  date: string;
  active_users: number;
  students: number;
  storage_bytes: number;
  documents: number;
  ai_tokens: number;
  ai_cost_inr: string;
}

export interface FeatureFlag {
  key: string;
  description: string;
  enabled_globally: boolean;
  rollout_percent: number;
  tenant_override_count: number;
}

export interface Deployment {
  id: string;
  tenant_id: string;
  school_name: string;
  mode: DeploymentMode;
  region: string;
  host: string | null;
  custom_domain: string | null;
  version: string | null;
  last_heartbeat_at: string | null;
  status: DeploymentStatus;
}

export interface FleetVersion {
  version: string;
  deployments: number;
}

export interface Announcement {
  id: string;
  title_en: string;
  title_te: string;
  body_en: string;
  body_te: string;
  audience: AnnouncementAudience;
  starts_at: string;
  ends_at: string | null;
  status: AnnouncementStatus;
}

export interface AnnouncementCreate {
  title_en: string;
  title_te: string;
  body_en: string;
  body_te: string;
  audience: AnnouncementAudience;
  starts_at: string;
  ends_at: string | null;
}

export interface SupportTicket {
  id: string;
  number: string;
  tenant_id: string;
  school_name: string;
  subject: string;
  status: TicketStatus;
  priority: TicketPriority;
  sla_due_at: string | null;
  updated_at: string;
}

export interface BreakGlassRequest {
  id: string;
  tenant_id: string;
  school_name: string;
  requested_by: string;
  reason: string;
  duration_hours: number;
  status: BreakGlassStatus;
  expires_at: string | null;
}

export interface Operator {
  id: string;
  display_name: string;
  email: string;
  roles: PlatformRole[];
  status: PersonStatus;
  mfa_enrolled: boolean;
  last_sign_in_at: string | null;
}

export interface OperatorInvite {
  email: string;
  role: PlatformRole;
}

export interface PlatformAuditEvent {
  id: string;
  seq: number;
  occurred_at: string;
  operator_id: string | null;
  operator_name: string | null;
  action: string;
  resource_type: string;
  resource_id: string | null;
  summary: string;
}

export interface AuditVerifyResult {
  ok: boolean;
  checked: number;
  first_bad_seq: number | null;
}

/* ------------------------------------------------------------------ tenant */

export interface UsageAgainstLimit {
  used: number;
  limit: number | null;
}

/** GET /billing/subscription via core.current_subscription() (FR-PLT-030). */
export interface CurrentSubscription {
  plan_name: string;
  status: SubscriptionStatus;
  current_period_end: string | null;
  trial_ends_at: string | null;
  usage: {
    students: UsageAgainstLimit;
    active_users: UsageAgainstLimit;
    storage_bytes: UsageAgainstLimit;
    documents: UsageAgainstLimit;
    ai_questions: UsageAgainstLimit;
  };
}

export interface TenantInvoice {
  id: string;
  number: string;
  issue_date: string;
  due_date: string | null;
  total_inr: string;
  status: InvoiceStatus;
}

export interface AcademicYear {
  id: string;
  label: string;
  starts_on: string;
  ends_on: string;
  is_current: boolean;
}

export interface SchoolClass {
  id: string;
  name: string;
  sort_order: number;
  section_count: number;
}

export interface Section {
  id: string;
  class_id: string;
  class_name: string;
  name: string;
}

export interface TenantUser {
  id: string;
  display_name: string;
  login: string;
  roles: TenantRoleKey[];
  scope_summary: string | null;
  status: PersonStatus;
  last_sign_in_at: string | null;
}

export interface TenantRole {
  key: string;
  name: string;
  is_system: boolean;
}

export interface AuditEvent {
  id: string;
  seq: number;
  occurred_at: string;
  actor_name: string | null;
  action: string;
  resource_type: string;
  resource_id: string | null;
  summary: string;
}
