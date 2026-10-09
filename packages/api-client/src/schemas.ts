/**
 * API types, all GENERATED from apps/api/openapi.json (`npm run generate -w
 * @schoolos/api-client` → src/generated/schema.ts) and re-exported under stable names.
 *
 * The only hand-written types are protocol envelopes the OpenAPI document does not describe
 * (RFC 9457 problem details) and small unions for status strings the API documents as plain
 * `string` (the database CHECK constraints in the migrations list the values). Field names are
 * the API's snake_case JSON. Money is a decimal string (numeric(14,2), INR). No type here
 * carries student data.
 */
import type { components } from "./generated/schema";

type Schemas = components["schemas"];

/** RFC 9457 problem details (docs/09 §3). Not described in the OpenAPI document. */
export interface Problem {
  type: string;
  title: string;
  status: number;
  code: string;
  detail?: string;
  instance?: string;
  request_id?: string;
  step_up_url?: string;
  /** Field errors: `field` is the dotted body path (e.g. `billing_account.gstin`). */
  errors?: Array<{ field: string; code: string; message_key: string }>;
}

/* ---------------------------------------------------------------- tenant (school) */

export type AcademicYear = Schemas["AcademicYearOut"];
/** Class: `code`, `display_en` / `display_te` (show the one for the UI language), `sort_order`. */
export type SchoolClass = Schemas["ClassOut"];
export type Section = Schemas["SectionOut"];
export type TenantUser = Schemas["UserOut"];
export type MemberStatus = TenantUser["status"];
export type Scope = Schemas["ScopeOut"];
export type TenantRole = Schemas["RoleOut"];
/** Audit event; `summary` is an object of IDs, field names, counts and codes (no PII). */
export type AuditEvent = Schemas["AuditEventOut"];
export type AuditVerify = Schemas["AuditVerificationOut"];
export type Me = Schemas["app__identity__schemas__MeOut"];
export type TenantProfile = Schemas["TenantOut"];
export type SchoolChoice = Schemas["SchoolChoiceOut"];
export type SchoolChoices = Schemas["SchoolChoicesOut"];
export type AcceptedInvitations = Schemas["AcceptedInvitationsOut"];
/** GET /tenant/billing via core.current_subscription() (FR-PLT-030). */
export type TenantBilling = Schemas["TenantBillingOut"];
export type SchoolAiBundle = Schemas["SchoolAiBundle"];
export type UsageAgainstLimit = Schemas["UsageAgainstLimit"];
export type TenantInvoice = Schemas["TenantInvoice"];
export type AnnouncementBrief = Schemas["AnnouncementBrief"];
export type SupportTicket = Schemas["TicketOut"];
export type SupportMessage = Schemas["TicketMessageOut"];
export type TicketCreateSchool = Schemas["TicketCreateSchool"];
export type SchoolTicketMessage = Schemas["SchoolTicketMessageIn"];
export type TicketCategory = TicketCreateSchool["category"];
export type TicketStatus = SupportTicket["status"];
export type TicketPriority = NonNullable<Schemas["TicketPatch"]["priority"]>;

/* ---------------------------------------------------------------- platform (C14) */

export type OperatorMe = Schemas["app__platform__schemas__MeOut"];
export type PlatformDashboard = Schemas["DashboardOut"];
export type TenantSummary = Schemas["TenantSummaryOut"];
export type TenantDetail = Schemas["TenantDetailOut"];
export type Offboarding = Schemas["OffboardingOut"];
export type OffboardingState = Offboarding["state"];
export type DeletionCertificate = Schemas["DeletionCertificateOut"];
export type CertificateDownload = Schemas["CertificateDownloadOut"];
export type ConfirmExport = Schemas["ConfirmExportIn"];
export type ConfirmTeardown = Schemas["ConfirmTeardownIn"];
export type TenantStatus = TenantSummary["tenant_status"];
export type Tier = TenantSummary["tier"];
export type ProvisionRequest = Schemas["ProvisionIn"];
export type ProvisionResult = Schemas["ProvisionOut"];
export type OwnerInvite = Schemas["OwnerIn"];
export type BillingAccount = Schemas["BillingAccountOut"];
export type BillingAccountInput = Schemas["BillingAccountIn"];
export type Plan = Schemas["PlanOut"];
export type PlanInput = Schemas["PlanIn"];
export type PlanPatch = Schemas["PlanPatch"];
export type PlanLimits = Schemas["PlanLimits"];
export type PlanStatus = Plan["status"];
export type AiBundle = Schemas["AiBundleOut"];
export type Subscription = Schemas["SubscriptionOut"];
export type SubscriptionStatus = Subscription["status"];
export type Invoice = Schemas["InvoiceOut"];
export type InvoiceLine = Schemas["InvoiceLineOut"];
export type InvoiceStatus = Invoice["status"];
export type Payment = Schemas["PaymentOut"];
export type PaymentInput = Schemas["PaymentIn"];
export type PaymentMethod = PaymentInput["method"];
export type Job = Schemas["JobOut"];
export type UsageDaily = Schemas["UsageDailyOut"];
export type FeatureFlag = Schemas["FlagOut"];
export type Deployment = Schemas["DeploymentOut"];
export type DeploymentPatch = Schemas["DeploymentPatch"];
export type HeartbeatKey = Schemas["HeartbeatKeyOut"];
export type FleetVersion = Schemas["FleetVersionOut"];
export type Announcement = Schemas["AnnouncementOut"];
export type AnnouncementInput = Schemas["AnnouncementIn"];
export type AnnouncementSeverity = NonNullable<AnnouncementInput["severity"]>;
export type AnnouncementAudience = NonNullable<AnnouncementInput["audience"]>;
export type BreakGlassRequest = Schemas["BreakGlassOut"];
export type BreakGlassInput = Schemas["BreakGlassIn"];
export type Operator = Schemas["OperatorOut"];
export type OperatorInvite = Schemas["OperatorInvite"];
export type PlatformRole = Operator["roles"][number];
export type PersonStatus = Operator["status"];
export type PlatformAuditEvent = Schemas["PlatformAuditEventOut"];
export type PlatformAuditVerify = Schemas["AuditVerifyOut"];

/* Status strings the API documents as `string` (values from the 0005_platform CHECKs). */
export type DeploymentStatus =
  "provisioning" | "healthy" | "degraded" | "unreachable" | "decommissioned";
export type AnnouncementStatus = "draft" | "pending_approval" | "scheduled" | "cancelled";
export type BreakGlassStatus =
  "requested" | "approved" | "active" | "expired" | "revoked" | "denied";

/* Runtime lists for forms. The `satisfies` checks fail to compile if the API adds a value. */

type Exhaustive<T extends string, L extends readonly T[]> = [T] extends [L[number]] ? L : never;

const platformRoles = [
  "platform_owner",
  "platform_engineer",
  "support_agent",
  "billing_admin",
  "platform_viewer",
] as const satisfies readonly PlatformRole[];
export const PLATFORM_ROLES: Exhaustive<PlatformRole, typeof platformRoles> = platformRoles;

const ticketCategories = [
  "access",
  "import",
  "data_quality",
  "exports",
  "documents",
  "ask",
  "billing",
  "bug",
  "other",
] as const satisfies readonly TicketCategory[];
export const TICKET_CATEGORIES: Exhaustive<TicketCategory, typeof ticketCategories> =
  ticketCategories;

const ticketPriorities = ["p1", "p2", "p3", "p4"] as const satisfies readonly TicketPriority[];
export const TICKET_PRIORITIES: Exhaustive<TicketPriority, typeof ticketPriorities> =
  ticketPriorities;

const ticketStatuses = [
  "open",
  "in_progress",
  "waiting_on_school",
  "resolved",
  "closed",
] as const satisfies readonly TicketStatus[];
export const TICKET_STATUSES: Exhaustive<TicketStatus, typeof ticketStatuses> = ticketStatuses;

const subscriptionStatuses = [
  "trial",
  "active",
  "past_due",
  "suspended",
  "cancelled",
] as const satisfies readonly SubscriptionStatus[];
export const SUBSCRIPTION_STATUSES: Exhaustive<SubscriptionStatus, typeof subscriptionStatuses> =
  subscriptionStatuses;

const invoiceStatuses = [
  "draft",
  "issued",
  "paid",
  "void",
] as const satisfies readonly InvoiceStatus[];
export const INVOICE_STATUSES: Exhaustive<InvoiceStatus, typeof invoiceStatuses> = invoiceStatuses;

const tenantStatuses = [
  "provisioning",
  "active",
  "suspended",
  "offboarding",
  "deleted",
] as const satisfies readonly TenantStatus[];
export const TENANT_STATUSES: Exhaustive<TenantStatus, typeof tenantStatuses> = tenantStatuses;

const paymentMethods = [
  "bank_transfer",
  "upi",
  "cheque",
  "other",
] as const satisfies readonly PaymentMethod[];
export const PAYMENT_METHODS: Exhaustive<PaymentMethod, typeof paymentMethods> = paymentMethods;

const announcementSeverities = [
  "info",
  "maintenance",
  "warning",
  "critical",
] as const satisfies readonly AnnouncementSeverity[];
export const ANNOUNCEMENT_SEVERITIES: Exhaustive<
  AnnouncementSeverity,
  typeof announcementSeverities
> = announcementSeverities;

/**
 * System role keys of a school (config: apps/api/app/authz/roles). The API sends role keys
 * as plain strings (schools may add custom roles); this list only drives UI labels.
 */
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
