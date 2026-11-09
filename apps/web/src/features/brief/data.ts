import type { ApiClient } from "@schoolos/api-client";
import { unwrap } from "@/lib/bff/query";
import * as z from "@/lib/zod";

/**
 * Weekly management brief (FA-05, FR-BRF-001..015): counts only, for the owner and the
 * principal (`brief.read`).
 *
 * The routes are new in this release and not yet in the generated client: until the lead
 * regenerates `packages/api-client` (make openapi), they go through `loose()` and the answers
 * are checked with the zod schemas below (mirroring `app/brief/schemas.py`). After
 * regeneration, swap `loose()` for the typed `api.GET` / `api.PUT` and the schemas for
 * `components["schemas"]["WeeklyBriefOut"]` and friends.
 */

export const BRIEF_READ = "brief.read";
export const BRIEF_KEYS = {
  all: ["staff", "brief"] as const,
  week: (id: string | null) => ["staff", "brief", "week", id ?? "latest"] as const,
  history: ["staff", "brief", "history"] as const,
  preferences: ["staff", "brief", "preferences"] as const,
};
const BASE = "/api/v1/brief";

const count = z.number().int().nullable();

const readinessLine = z.object({
  profile_key: z.string(),
  label: z.string(),
  students: count,
  ready: count,
  needs_parent: count,
  needs_school: count,
  blocked: count,
  ready_change: count,
});

const brief = z.object({
  id: z.string().nullable(),
  stored: z.boolean(),
  week_start: z.string(),
  week_end: z.string(),
  as_of: z.string(),
  built_at: z.string().nullable(),
  previous_week_start: z.string().nullable(),
  small_cell_min: z.number().int(),
  readiness: z.array(readinessLine).nullable(),
  findings: z
    .object({
      blockers: z.number().int(),
      warnings: z.number().int(),
      students_with_blockers: count,
    })
    .nullable(),
  changes_waiting: count,
  certificates_issued: count,
  deadlines: z
    .array(
      z.object({
        event_key: z.string(),
        portal: z.string(),
        label: z.string(),
        closes_on: z.string(),
        days_left: z.number().int(),
        confirmed: z.boolean(),
      }),
    )
    .nullable(),
  deadline_days: z.number().int(),
  fees: z
    .object({
      receipts: z.number().int(),
      collected: z.string(),
      balance: z.string(),
      overdue: z.string(),
      students_owing: count,
    })
    .nullable(),
  ask: z.object({ answers: z.number().int(), included: count }).nullable(),
});

const history = z.object({
  items: z.array(
    z.object({
      id: z.string(),
      week_start: z.string(),
      week_end: z.string(),
      built_at: z.string(),
    }),
  ),
});

const preferences = z.object({
  in_app: z.boolean(),
  email: z.boolean(),
  email_available: z.boolean(),
  version: z.number().int(),
});

export type WeeklyBrief = z.output<typeof brief>;
export type ReadinessLine = z.output<typeof readinessLine>;
export type BriefHistory = z.output<typeof history>;
export type BriefPreferences = z.output<typeof preferences>;
export interface BriefPreferencesInput {
  in_app: boolean;
  email: boolean;
}

type LooseResult = { data?: unknown; error?: unknown; response: Response };
interface LooseInit {
  params?: { query?: Record<string, string | number> };
  body?: unknown;
  headers?: Record<string, string>;
}
interface LooseClient {
  GET(path: string, init?: LooseInit): Promise<LooseResult>;
  PUT(path: string, init?: LooseInit): Promise<LooseResult>;
}

/** The generated client, for paths it does not list yet (see the module comment). */
function loose(api: ApiClient): LooseClient {
  return api as unknown as LooseClient;
}

export function ifMatch(version: number): string {
  return `W/"${version}"`;
}

/** The newest stored week, or one stored week by id (from the history or a notification). */
export async function fetchBrief(api: ApiClient, id: string | null): Promise<WeeklyBrief> {
  const path = id ? `${BASE}/weekly/${encodeURIComponent(id)}` : `${BASE}/weekly`;
  return brief.parse(await unwrap(loose(api).GET(path)));
}

export async function fetchHistory(api: ApiClient): Promise<BriefHistory> {
  return history.parse(await unwrap(loose(api).GET(`${BASE}/weekly/history`)));
}

export async function fetchPreferences(api: ApiClient): Promise<BriefPreferences> {
  return preferences.parse(await unwrap(loose(api).GET(`${BASE}/preferences`)));
}

export async function savePreferences(
  api: ApiClient,
  input: BriefPreferencesInput,
  version: number,
): Promise<BriefPreferences> {
  return preferences.parse(
    await unwrap(
      loose(api).PUT(`${BASE}/preferences`, {
        body: { in_app: input.in_app, email: input.email },
        headers: { "If-Match": ifMatch(version) },
      }),
    ),
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
