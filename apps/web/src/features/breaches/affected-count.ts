import type { ApiClient, components } from "@schoolos/api-client";
import { unwrap } from "@/lib/bff/query";

/**
 * The affected-people picker's count (D59): `POST /privacy/breaches/{id}/affected:count`. Ids and
 * role keys go out; three numbers come back. Nothing is stored by the call. The picker always
 * sends every list (empty when unused), so the request is the generated body with all fields.
 */
export type AffectedCountRequest = Required<components["schemas"]["AffectedCountIn"]>;
export type AffectedCounts = components["schemas"]["AffectedCountsOut"];

/** The largest single-student list the API takes in one request. */
export const MAX_PICKED = 5000;

export async function countAffected(
  api: ApiClient,
  breachId: string,
  body: AffectedCountRequest,
): Promise<AffectedCounts> {
  return unwrap(
    api.POST("/api/v1/privacy/breaches/{breach_id}/affected:count", {
      params: { path: { breach_id: breachId } },
      body,
    }),
  );
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
