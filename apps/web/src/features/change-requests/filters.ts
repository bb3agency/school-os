import { UUID_PATTERN } from "@/lib/validation";
import { CHANGE_REQUEST_STATUSES, type ChangeRequestStatus } from "./types";

/**
 * URL parameters of the change-request screens (plain module: server pages call these).
 * Unknown values are dropped instead of being sent to the API.
 */
export type SearchParams = Record<string, string | string[] | undefined>;

function first(value: string | string[] | undefined): string | null {
  const head = Array.isArray(value) ? value[0] : value;
  const trimmed = head?.trim();
  return trimmed ? trimmed : null;
}

function uuidOrNull(value: string | string[] | undefined): string | null {
  const candidate = first(value);
  return candidate && UUID_PATTERN.test(candidate) ? candidate : null;
}

export interface ChangeRequestFilters {
  status: ChangeRequestStatus | null;
  studentId: string | null;
}

export function parseChangeRequestFilters(params: SearchParams): ChangeRequestFilters {
  const status = first(params.status);
  return {
    status:
      status && (CHANGE_REQUEST_STATUSES as readonly string[]).includes(status)
        ? (status as ChangeRequestStatus)
        : null,
    studentId: uuidOrNull(params.student_id),
  };
}

/** `/change-requests/new?student_id=&attribute_key=&finding_id=` (from a finding). */
export interface NewRequestParams {
  studentId: string | null;
  attributeKey: string | null;
  findingId: string | null;
}

export function parseNewRequestParams(params: SearchParams): NewRequestParams {
  const attribute = first(params.attribute_key);
  return {
    studentId: uuidOrNull(params.student_id),
    attributeKey: attribute && /^[a-z][a-z0-9_]{0,63}$/.test(attribute) ? attribute : null,
    findingId: uuidOrNull(params.finding_id),
  };
}
