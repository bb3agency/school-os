import type { ApiClient, components } from "@schoolos/api-client";
import { unwrap } from "@/lib/bff/query";
import * as z from "@/lib/zod";

/**
 * Safety drills and periodic checks (FC-09; FR-COMP-046..FR-COMP-055) and the policy library
 * with staff acknowledgement (FC-10; FR-COMP-056..FR-COMP-070).
 * The calls go through the BFF client (CSRF, session and step-up handling).
 */

export const COMPLIANCE_READ = "compliance.read";
export const COMPLIANCE_MANAGE = "compliance.manage";
export const SAFETY_KEYS = {
  checks: ["staff", "compliance", "checks"] as const,
  policies: ["staff", "compliance", "policies"] as const,
  policy: (id: string) => ["staff", "compliance", "policies", id] as const,
  mine: ["staff", "compliance", "my-policies"] as const,
};

export function ifMatch(version: number): string {
  return `W/"${version}"`;
}

function text(value: string | undefined): string | null {
  const out = value?.trim();
  return out ? out : null;
}

// --- answers --------------------------------------------------------------------------------

type Schemas = components["schemas"];

export type CheckState = Schemas["CheckStatusOut"]["state"];
export type CheckEntry = Schemas["ComplianceCheckOut"];
export type CheckStatus = Schemas["CheckStatusOut"];
export type CheckKind = Schemas["CheckKindOut"];
export type ChecksOverview = Schemas["ChecksOverviewOut"];
export type ReviewState = Schemas["CompliancePolicyOut"]["review_state"];
export type PolicyRole = Schemas["PolicyRoleOut"];
export type Policy = Schemas["CompliancePolicyOut"];
export type PolicyDetail = Schemas["PolicyDetailOut"];
export type PolicyMember = Schemas["PolicyMemberOut"];
export type Policies = Schemas["PoliciesOut"];
export type MyPolicy = Schemas["MyPolicyOut"];
export type MyPolicies = Schemas["MyPoliciesOut"];
export type PolicyFile = Schemas["PolicyFileOut"];

// --- forms ----------------------------------------------------------------------------------

const count = (max: number) =>
  z
    .string()
    .optional()
    .refine((v) => !v || (/^\d+$/.test(v) && Number(v) <= max), { error: "invalidNumber" });

/** The "log a check" dialog (FR-COMP-048); empty strings mean "not given". */
export const checkFormSchema = z.object({
  kind: z.string().min(1, { error: "chooseOption" }),
  label: z.string().max(120, { error: "tooLong" }).optional(),
  done_on: z.string().min(1, { error: "required" }),
  led_by_membership_id: z.string().optional(),
  participants_count: count(20000),
  duration_minutes: count(1440),
  result: z.enum(["ok", "action_needed"]).optional(),
  observations: z.string().max(500, { error: "tooLong" }).optional(),
  document_id: z.string().optional(),
});
export type CheckInput = z.output<typeof checkFormSchema>;

function whole(value: string | undefined): number | null {
  const out = text(value);
  return out === null ? null : Number(out);
}

/** The body of POST /compliance/checks/entries: drill counts only for drill kinds. */
export function checkBody(input: CheckInput, drill: boolean): Schemas["CheckCreate"] {
  return {
    kind: input.kind,
    label: input.kind === "other" ? text(input.label) : null,
    done_on: input.done_on,
    led_by_membership_id: text(input.led_by_membership_id),
    participants_count: drill || input.kind === "other" ? whole(input.participants_count) : null,
    duration_minutes: drill || input.kind === "other" ? whole(input.duration_minutes) : null,
    result: input.result ?? "ok",
    observations: text(input.observations),
    document_id: text(input.document_id),
  };
}

/** The add / correct policy dialog (FR-COMP-056, FR-COMP-057). */
export const policyFormSchema = z.object({
  title: z.string().min(3, { error: "tooShort" }).max(120, { error: "tooLong" }),
  document_id: z.string().min(1, { error: "chooseOption" }),
  review_due: z.string().optional(),
  ack_roles: z.array(z.string()),
});
export type PolicyInput = z.output<typeof policyFormSchema>;

// --- calls ----------------------------------------------------------------------------------

export async function fetchChecks(api: ApiClient): Promise<ChecksOverview> {
  return unwrap(api.GET("/api/v1/compliance/checks"));
}

export async function recordCheck(
  api: ApiClient,
  input: CheckInput,
  drill: boolean,
  idempotencyKey: string,
): Promise<CheckEntry> {
  return unwrap(
    api.POST("/api/v1/compliance/checks/entries", {
      body: checkBody(input, drill),
      headers: { "Idempotency-Key": idempotencyKey },
    }),
  );
}

export async function removeCheck(api: ApiClient, entry: CheckEntry): Promise<CheckEntry> {
  return unwrap(
    api.POST("/api/v1/compliance/checks/entries/{check_id}:remove", {
      params: { path: { check_id: entry.id } },
      headers: { "If-Match": ifMatch(entry.version) },
    }),
  );
}

export async function fetchPolicies(api: ApiClient): Promise<Policies> {
  return unwrap(api.GET("/api/v1/compliance/policies"));
}

export async function fetchPolicy(api: ApiClient, id: string): Promise<PolicyDetail> {
  return unwrap(
    api.GET("/api/v1/compliance/policies/{policy_id}", {
      params: { path: { policy_id: id } },
    }),
  );
}

export async function createPolicy(
  api: ApiClient,
  input: PolicyInput,
  idempotencyKey: string,
): Promise<Policy> {
  return unwrap(
    api.POST("/api/v1/compliance/policies", {
      body: {
        title: input.title.trim(),
        document_id: input.document_id,
        review_due: text(input.review_due),
        ack_roles: input.ack_roles,
        notify: true,
      },
      headers: { "Idempotency-Key": idempotencyKey },
    }),
  );
}

export async function updatePolicy(
  api: ApiClient,
  policy: Policy,
  input: PolicyInput,
  status: Policy["status"],
): Promise<Policy> {
  return unwrap(
    api.PUT("/api/v1/compliance/policies/{policy_id}", {
      params: { path: { policy_id: policy.id } },
      body: {
        title: input.title.trim(),
        document_id: input.document_id,
        review_due: text(input.review_due) ?? policy.review_due,
        ack_roles: input.ack_roles,
        status,
      },
      headers: { "If-Match": ifMatch(policy.version) },
    }),
  );
}

/** Retire or bring back a policy: the same PUT with every field unchanged but the status. */
export async function setPolicyStatus(
  api: ApiClient,
  policy: Policy,
  status: Policy["status"],
): Promise<Policy> {
  return updatePolicy(
    api,
    policy,
    {
      title: policy.title,
      document_id: policy.document?.id ?? "",
      review_due: policy.review_due,
      ack_roles: policy.ack_roles.map((r) => r.role),
    },
    status,
  );
}

export async function reviewPolicy(
  api: ApiClient,
  policy: Policy,
  nextReviewDue: string | undefined,
): Promise<Policy> {
  return unwrap(
    api.POST("/api/v1/compliance/policies/{policy_id}:review", {
      params: { path: { policy_id: policy.id } },
      body: { next_review_due: text(nextReviewDue) },
      headers: { "If-Match": ifMatch(policy.version) },
    }),
  );
}

export async function askAgain(
  api: ApiClient,
  policy: Policy,
): Promise<Schemas["PolicyAckRequestOut"]> {
  return unwrap(
    api.POST("/api/v1/compliance/policies/{policy_id}:request-acknowledgement", {
      params: { path: { policy_id: policy.id } },
    }),
  );
}

export async function fetchMyPolicies(api: ApiClient): Promise<MyPolicies> {
  return unwrap(api.GET("/api/v1/compliance/my-policies"));
}

export async function openMyPolicy(api: ApiClient, id: string): Promise<PolicyFile> {
  return unwrap(
    api.GET("/api/v1/compliance/my-policies/{policy_id}/file", {
      params: { path: { policy_id: id } },
    }),
  );
}

export async function acknowledgePolicy(
  api: ApiClient,
  id: string,
  documentVersionId: string,
): Promise<MyPolicy> {
  return unwrap(
    api.POST("/api/v1/compliance/my-policies/{policy_id}:acknowledge", {
      params: { path: { policy_id: id } },
      body: { document_version_id: documentVersionId },
    }),
  );
}
