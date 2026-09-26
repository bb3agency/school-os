/**
 * `paths` for openapi-fetch = the GENERATED paths (school-side API, from
 * apps/api/openapi.json via `make openapi`) plus HAND-WRITTEN placeholders, in the same
 * shape, for routes the API does not publish yet (control plane /platform/*, school
 * billing). Remove a placeholder as soon as its route appears in the generated file.
 */
import type { paths as GeneratedPaths } from "./generated/schema";
import type * as S from "./schemas";

type Method = "get" | "put" | "post" | "delete" | "options" | "head" | "patch" | "trace";

interface NoParameters {
  query?: never;
  header?: never;
  path?: never;
  cookie?: never;
}

type JsonContent<T> = {
  headers: { [name: string]: unknown };
  content: { "application/json": T };
};

type ProblemContent = {
  headers: { [name: string]: unknown };
  content: { "application/problem+json": S.Problem };
};

type Parameters<TQuery, TPath> = {
  query?: [TQuery] extends [never] ? never : TQuery;
  header?: never;
  cookie?: never;
} & ([TPath] extends [never] ? { path?: never } : { path: TPath });

type RequestBody<TBody> = [TBody] extends [never]
  ? { requestBody?: never }
  : { requestBody: { content: { "application/json": TBody } } };

type Operation<
  TResponse,
  TQuery = never,
  TPath = never,
  TBody = never,
  TStatus extends 200 | 201 | 202 = 200,
> = {
  parameters: Parameters<TQuery, TPath>;
  responses: { [K in TStatus]: JsonContent<TResponse> } & { default: ProblemContent };
} & RequestBody<TBody>;

type PathItem<TOps extends Partial<Record<Method, unknown>>> = TOps & {
  [M in Exclude<Method, keyof TOps>]?: never;
} & { parameters: NoParameters };

type ListQuery = { limit?: number; cursor?: string };
type TenantPath = { tenant_id: string };

interface PlaceholderPaths {
  /* ------------------------------------------------------------ platform */
  "/api/v1/platform/dashboard": PathItem<{ get: Operation<S.PlatformKpis> }>;
  "/api/v1/platform/tenants": PathItem<{
    get: Operation<S.Page<S.TenantSummary>, ListQuery & { q?: string; status?: S.TenantStatus }>;
    post: Operation<S.ProvisionTenantAccepted, never, never, S.ProvisionTenantRequest, 202>;
  }>;
  "/api/v1/platform/tenants/{tenant_id}": PathItem<{
    get: Operation<S.TenantDetail, never, TenantPath>;
  }>;
  "/api/v1/platform/tenants/{tenant_id}/suspend": PathItem<{
    post: Operation<S.TenantDetail, never, TenantPath>;
  }>;
  "/api/v1/platform/tenants/{tenant_id}/reactivate": PathItem<{
    post: Operation<S.TenantDetail, never, TenantPath>;
  }>;
  "/api/v1/platform/tenants/{tenant_id}/offboard": PathItem<{
    post: Operation<S.TenantDetail, never, TenantPath, never, 202>;
  }>;
  "/api/v1/platform/plans": PathItem<{ get: Operation<S.Page<S.Plan>, ListQuery> }>;
  "/api/v1/platform/subscriptions": PathItem<{
    get: Operation<S.Page<S.Subscription>, ListQuery & { status?: S.SubscriptionStatus }>;
  }>;
  "/api/v1/platform/invoices": PathItem<{
    get: Operation<S.Page<S.Invoice>, ListQuery & { status?: S.InvoiceStatus; tenant_id?: string }>;
  }>;
  "/api/v1/platform/usage": PathItem<{
    get: Operation<
      S.Page<S.UsageDaily>,
      ListQuery & { tenant_id?: string; from?: string; to?: string }
    >;
  }>;
  "/api/v1/platform/flags": PathItem<{ get: Operation<S.Page<S.FeatureFlag>, ListQuery> }>;
  "/api/v1/platform/deployments": PathItem<{
    get: Operation<S.Page<S.Deployment>, ListQuery & { status?: S.DeploymentStatus }>;
  }>;
  "/api/v1/platform/fleet/versions": PathItem<{ get: Operation<S.FleetVersion[]> }>;
  "/api/v1/platform/announcements": PathItem<{
    get: Operation<S.Page<S.Announcement>, ListQuery>;
    post: Operation<S.Announcement, never, never, S.AnnouncementCreate, 201>;
  }>;
  "/api/v1/platform/support/tickets": PathItem<{
    get: Operation<S.Page<S.SupportTicket>, ListQuery & { status?: S.TicketStatus }>;
  }>;
  "/api/v1/platform/breakglass": PathItem<{
    get: Operation<S.Page<S.BreakGlassRequest>, ListQuery>;
  }>;
  "/api/v1/platform/operators": PathItem<{
    get: Operation<S.Page<S.Operator>, ListQuery>;
    post: Operation<S.Operator, never, never, S.OperatorInvite, 201>;
  }>;
  "/api/v1/platform/audit/events": PathItem<{
    get: Operation<S.Page<S.PlatformAuditEvent>, ListQuery & { from?: string; to?: string }>;
  }>;
  "/api/v1/platform/audit/verify": PathItem<{ post: Operation<S.AuditVerifyResult> }>;

  /* -------------------------------------------------------------- tenant */
  "/api/v1/billing/subscription": PathItem<{ get: Operation<S.CurrentSubscription> }>;
  "/api/v1/billing/invoices": PathItem<{ get: Operation<S.Page<S.TenantInvoice>, ListQuery> }>;
}

// A placeholder must never shadow a generated route: this fails to compile if one does.
type NoOverlap<T extends never> = T;
export type PlaceholderOverlap = NoOverlap<keyof PlaceholderPaths & keyof GeneratedPaths>;

export type paths = GeneratedPaths & PlaceholderPaths;
