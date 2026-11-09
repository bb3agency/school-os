import type { ApiClient, components } from "@schoolos/api-client";
import { unwrap } from "@/lib/bff/query";

/**
 * Weekly management brief (FA-05, FR-BRF-001..015): counts only, for the owner and the
 * principal (`brief.read`).
 */

export const BRIEF_READ = "brief.read";
export const BRIEF_KEYS = {
  all: ["staff", "brief"] as const,
  week: (id: string | null) => ["staff", "brief", "week", id ?? "latest"] as const,
  history: ["staff", "brief", "history"] as const,
  preferences: ["staff", "brief", "preferences"] as const,
};

type S = components["schemas"];
export type WeeklyBrief = S["WeeklyBriefOut"];
export type ReadinessLine = S["ReadinessLineOut"];
export type BriefHistory = S["BriefHistoryOut"];
export type BriefPreferences = S["BriefPreferencesOut"];
export interface BriefPreferencesInput {
  in_app: boolean;
  email: boolean;
}

export function ifMatch(version: number): string {
  return `W/"${version}"`;
}

/** The newest stored week, or one stored week by id (from the history or a notification). */
export async function fetchBrief(api: ApiClient, id: string | null): Promise<WeeklyBrief> {
  return unwrap(
    id
      ? api.GET("/api/v1/brief/weekly/{brief_id}", { params: { path: { brief_id: id } } })
      : api.GET("/api/v1/brief/weekly"),
  );
}

export async function fetchHistory(api: ApiClient): Promise<BriefHistory> {
  return unwrap(api.GET("/api/v1/brief/weekly/history"));
}

export async function fetchPreferences(api: ApiClient): Promise<BriefPreferences> {
  return unwrap(api.GET("/api/v1/brief/preferences"));
}

export async function savePreferences(
  api: ApiClient,
  input: BriefPreferencesInput,
  version: number,
): Promise<BriefPreferences> {
  return unwrap(
    api.PUT("/api/v1/brief/preferences", {
      body: { in_app: input.in_app, email: input.email },
      headers: { "If-Match": ifMatch(version) },
    }),
  );
}

/** A UUID from the URL (`?week=`), or null for the newest week. */
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export function weekParam(value: string | null | undefined): string | null {
  return value && UUID.test(value) ? value : null;
}

/** A week-on-week change as read: "+18", "−3", "0" (U+2212 minus). */
export function signed(value: number): string {
  if (value > 0) return `+${value}`;
  if (value < 0) return `−${Math.abs(value)}`;
  return "0";
}

/** "142 of 160 ready" parts: null when a count is hidden (fewer than small_cell_min). */
export function readyShare(line: ReadinessLine): number | null {
  if (line.ready === null || line.students === null || line.students === 0) return null;
  return Math.round((line.ready / line.students) * 100);
}
