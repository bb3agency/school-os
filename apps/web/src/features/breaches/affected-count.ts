import type { ApiClient } from "@schoolos/api-client";
import { unwrap } from "@/lib/bff/query";
import * as z from "@/lib/zod";

/**
 * The affected-people picker's count (D59): `POST /privacy/breaches/{id}/affected:count`. The
 * route is not in the generated client yet, so the request and answer are typed here and the
 * answer is checked with a local schema that matches `AffectedCountsOut`. Ids and role keys go
 * out; three numbers come back. Nothing is stored by the call.
 */
export interface AffectedCountRequest {
  all_students: boolean;
  class_ids: string[];
  section_ids: string[];
  student_ids: string[];
  all_staff: boolean;
  staff_role_keys: string[];
  membership_ids: string[];
}

export const affectedCountsSchema = z.object({
  students: z.number().int().nonnegative(),
  guardians: z.number().int().nonnegative(),
  staff: z.number().int().nonnegative(),
});
export type AffectedCounts = z.infer<typeof affectedCountsSchema>;

/** The largest single-student list the API takes in one request. */
export const MAX_PICKED = 5000;

type CountPost = (
  path: "/api/v1/privacy/breaches/{breach_id}/affected:count",
  init: { params: { path: { breach_id: string } }; body: AffectedCountRequest },
) => Promise<{ data?: unknown; error?: unknown; response: Response }>;

export async function countAffected(
  api: ApiClient,
  breachId: string,
  body: AffectedCountRequest,
): Promise<AffectedCounts> {
  // Untyped until the lead regenerates the client with the D59 route.
  const client = api as unknown as { POST: CountPost };
  const data = await unwrap(
    client.POST("/api/v1/privacy/breaches/{breach_id}/affected:count", {
      params: { path: { breach_id: breachId } },
      body,
    }),
  );
  return affectedCountsSchema.parse(data);
}

/** True when the selection names nobody (the Count button stays off). */
export function isEmptySelection(body: AffectedCountRequest): boolean {
  return (
    !body.all_students &&
    !body.all_staff &&
    body.class_ids.length === 0 &&
    body.section_ids.length === 0 &&
    body.student_ids.length === 0 &&
    body.staff_role_keys.length === 0 &&
    body.membership_ids.length === 0
  );
}
