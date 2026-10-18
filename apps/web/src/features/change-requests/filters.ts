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

/**
 * The inbox views: `waiting` = `?waiting_for=me` (requests the caller may decide now, never
 * their own), `mine` = `?requested_by=me`. The URL uses the API's own parameter names.
 */
export type ChangeRequestInbox = "waiting" | "mine";

export interface ChangeRequestFilters {
  status: ChangeRequestStatus | null;
  studentId: string | null;
  inbox: ChangeRequestInbox | null;
}

export function parseChangeRequestFilters(params: SearchParams): ChangeRequestFilters {
  const status = first(params.status);
  return {
    status:
      status && (CHANGE_REQUEST_STATUSES as readonly string[]).includes(status)
        ? (status as ChangeRequestStatus)
        : null,
    studentId: uuidOrNull(params.student_id),
    inbox:
      first(params.waiting_for) === "me"
        ? "waiting"
        : first(params.requested_by) === "me"
          ? "mine"
          : null,
  };
}

/** API query parameters for these filters (`GET /api/v1/change-requests`). */
export function changeRequestsQuery(filters: ChangeRequestFilters) {
  return {
    ...(filters.status ? { status: filters.status } : {}),
    ...(filters.studentId ? { student_id: filters.studentId } : {}),
    ...(filters.inbox === "waiting" ? { waiting_for: "me" as const } : {}),
    ...(filters.inbox === "mine" ? { requested_by: "me" as const } : {}),
  };
}

/** `/change-requests?…` for these filters (tab links and filter chips keep the others). */
export function changeRequestsHref(filters: ChangeRequestFilters): string {
  const params = new URLSearchParams();
  if (filters.inbox === "waiting") params.set("waiting_for", "me");
  if (filters.inbox === "mine") params.set("requested_by", "me");
  if (filters.status) params.set("status", filters.status);
  if (filters.studentId) params.set("student_id", filters.studentId);
  const query = params.toString();
  return query ? `/change-requests?${query}` : "/change-requests";
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
