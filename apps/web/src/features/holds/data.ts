import type { ApiClient } from "@schoolos/api-client";
import { unwrap } from "@/lib/bff/query";
import * as z from "@/lib/zod";

/**
 * Legal holds (FC-12, ADR-0059, FR-DPDP-039..052). The routes are new in this release, so the
 * generated client does not know them yet: the shapes below mirror
 * `app/privacy/holds_schemas.py` and every answer is checked with zod. After the lead
 * regenerates `packages/api-client`, swap `loose()` for the typed client.
 */

export const HOLD_MANAGE = "privacy.hold.manage";
export const HOLD_KEYS = { list: ["staff", "privacy", "holds"] as const };
const BASE = "/api/v1/privacy/holds";

export const HOLD_STATUSES = ["pending", "active", "lift_pending", "lifted", "rejected"] as const;
export type HoldStatus = (typeof HOLD_STATUSES)[number];

const holdSchema = z.object({
  id: z.string(),
  scope: z.enum(["student", "record_class"]),
  student_id: z.string().nullable(),
  subject_label: z.string(),
  record_class: z.string().nullable(),
  reason_code: z.string(),
  reason_label: z.string(),
  reference: z.string().nullable(),
  review_on: z.string(),
  review_overdue: z.boolean(),
  status: z.enum(HOLD_STATUSES),
  effective: z.boolean(),
  placed_by_name: z.string().nullable(),
  placed_at: z.string(),
  approved_by_name: z.string().nullable(),
  approved_at: z.string().nullable(),
  lift_requested_by_name: z.string().nullable(),
  lift_requested_at: z.string().nullable(),
  lifted_by_name: z.string().nullable(),
  lifted_at: z.string().nullable(),
  rejected_at: z.string().nullable(),
  can_approve: z.boolean(),
  version: z.number(),
});
export type Hold = z.infer<typeof holdSchema>;

const optionSchema = z.object({ code: z.string(), label: z.string() });
export type HoldOption = z.infer<typeof optionSchema>;

const listSchema = z.object({
  items: z.array(holdSchema),
  reason_codes: z.array(optionSchema),
  record_classes: z.array(optionSchema),
  max_review_on: z.string(),
});
export type HoldList = z.infer<typeof listSchema>;

/** The place dialog's fields (the API checks them again: FR-DPDP-039..041). */
export const placeSchema = z.object({
  scope: z.enum(["student", "record_class"], { error: "chooseOption" }),
  student_id: z.string().optional(),
  record_class: z.string().optional(),
  reason_code: z.string().min(1, { error: "chooseOption" }),
  reference: z.string().max(200, { error: "tooLong" }).optional(),
  review_on: z.string().min(1, { error: "required" }),
});
export type PlaceInput = z.output<typeof placeSchema>;

export type HoldAction = "approve" | "reject" | "request-lift" | "approve-lift" | "cancel-lift";

/** Which actions a row offers (the API decides again; `can_approve` is false for the asker). */
export function actionsFor(hold: Hold): HoldAction[] {
  if (hold.status === "pending") return hold.can_approve ? ["approve", "reject"] : ["reject"];
  if (hold.status === "active") return ["request-lift"];
  if (hold.status === "lift_pending")
    return hold.can_approve ? ["approve-lift", "cancel-lift"] : ["cancel-lift"];
  return [];
}

export function ifMatch(version: number): string {
  return `W/"${version}"`;
}

/** The body the API takes: only the field of the chosen scope, no empty reference. */
export function placeBody(input: PlaceInput): Record<string, string> {
  const body: Record<string, string> = {
    scope: input.scope,
    reason_code: input.reason_code,
    review_on: input.review_on,
  };
  if (input.scope === "student" && input.student_id) body.student_id = input.student_id;
  if (input.scope === "record_class" && input.record_class) body.record_class = input.record_class;
  const reference = input.reference?.trim();
  if (reference) body.reference = reference;
  return body;
}

type Result = { data?: unknown; error?: unknown; response: Response };
interface LooseClient {
  GET(path: string): Promise<Result>;
  POST(path: string, init?: { body?: unknown; headers?: Record<string, string> }): Promise<Result>;
}

function loose(api: ApiClient): LooseClient {
  return api as unknown as LooseClient;
}

export async function fetchHolds(api: ApiClient): Promise<HoldList> {
  return listSchema.parse(await unwrap(loose(api).GET(BASE)));
}

export async function placeHold(api: ApiClient, input: PlaceInput): Promise<Hold> {
  return holdSchema.parse(await unwrap(loose(api).POST(BASE, { body: placeBody(input) })));
}

export async function actOnHold(api: ApiClient, hold: Hold, action: HoldAction): Promise<Hold> {
  const path = `${BASE}/${encodeURIComponent(hold.id)}:${action}`;
  return holdSchema.parse(
    await unwrap(loose(api).POST(path, { headers: { "If-Match": ifMatch(hold.version) } })),
  );
}
