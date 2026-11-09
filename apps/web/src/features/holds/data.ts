import type { ApiClient, components } from "@schoolos/api-client";
import { unwrap } from "@/lib/bff/query";
import * as z from "@/lib/zod";

/**
 * Legal holds (FC-12, ADR-0059, FR-DPDP-039..052), through the generated client
 * (`app/privacy/holds_schemas.py`). Only the place form is checked with zod here.
 */

export const HOLD_MANAGE = "privacy.hold.manage";
export const HOLD_KEYS = { list: ["staff", "privacy", "holds"] as const };
const BASE = "/api/v1/privacy/holds";

type Schemas = components["schemas"];
export type Hold = Schemas["HoldOut"];
export type HoldOption = Schemas["HoldOptionOut"];
export type HoldList = Schemas["HoldListOut"];
type HoldCreate = Schemas["HoldCreate"];
export type HoldStatus = Hold["status"];

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
export function placeBody(input: PlaceInput): HoldCreate {
  const body: HoldCreate = {
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

const ACTION_PATHS = {
  approve: "/api/v1/privacy/holds/{hold_id}:approve",
  reject: "/api/v1/privacy/holds/{hold_id}:reject",
  "request-lift": "/api/v1/privacy/holds/{hold_id}:request-lift",
  "approve-lift": "/api/v1/privacy/holds/{hold_id}:approve-lift",
  "cancel-lift": "/api/v1/privacy/holds/{hold_id}:cancel-lift",
} as const satisfies Record<HoldAction, string>;

export async function fetchHolds(api: ApiClient): Promise<HoldList> {
  return unwrap(api.GET(BASE));
}

export async function placeHold(api: ApiClient, input: PlaceInput): Promise<Hold> {
  return unwrap(api.POST(BASE, { body: placeBody(input) }));
}

export async function actOnHold(api: ApiClient, hold: Hold, action: HoldAction): Promise<Hold> {
  return unwrap(
    api.POST(ACTION_PATHS[action], {
      params: { path: { hold_id: hold.id } },
      headers: { "If-Match": ifMatch(hold.version) },
    }),
  );
}
