"use client";

import type { ApiClient } from "@schoolos/api-client";
import * as z from "@/lib/zod";
import { unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import type { Loadable } from "@/lib/loadable";
import type { Category } from "./data";

/**
 * FP-01 (opt-in coverage and reach follow-up, FR-MSG-039..046) and FP-02 (urgent closure
 * message, FR-MSG-047..055; ADR-0043 amendment, lead decision D85).
 *
 * LOCALLY TYPED until the lead regenerates apps/api/openapi.json and the API client (wave F2):
 * these routes are not in `@schoolos/api-client` yet, so the calls go through `loose()` and the
 * answers are checked with the zod schemas below. After regeneration, replace `loose(api)` by
 * `api` and the types by `components["schemas"][...]` (CoverageOut, NotReachedOut,
 * BroadcastFollowUpOut, ClosurePreviewOut, ClosureOut, ClosurePage, ClosureIn).
 *
 * No list carries a phone number; names appear only when the reader may read them.
 */

/** `message.closure.send`: the principal only, step-up MFA (FR-MSG-052). */
export const CLOSURE_SEND = "message.closure.send";
export const CLOSURE_REASONS = ["weather", "government_order", "local_situation", "other"] as const;
export type ClosureReason = (typeof CLOSURE_REASONS)[number];

type LooseResult = { data?: unknown; error?: unknown; response: Response };
type LooseInit = {
  params?: { path?: Record<string, string>; query?: Record<string, string | number> };
  body?: unknown;
  headers?: Record<string, string>;
};
interface LooseClient {
  GET(path: string, init?: LooseInit): Promise<LooseResult>;
  POST(path: string, init?: LooseInit): Promise<LooseResult>;
}

/** The generated client without its path typing (see the note above). */
function loose(api: ApiClient): LooseClient {
  return api as unknown as LooseClient;
}

async function parsed<T>(schema: z.ZodType<T>, pending: Promise<LooseResult>): Promise<T> {
  return schema.parse(await unwrap(pending));
}

const counts = z.record(z.string(), z.number());

const sectionCoverage = z.object({
  section_id: z.string(),
  label: z.string(),
  students: z.number(),
  students_reached: z.number(),
  guardians: z.number(),
  guardians_opted_in: z.number(),
  guardians_without_mobile: z.number(),
});
export const coverageSchema = z.object({
  category: z.enum(["notices", "attendance", "fees"]),
  students: z.number(),
  students_reached: z.number(),
  guardians: z.number(),
  guardians_opted_in: z.number(),
  sections: z.array(sectionCoverage),
});
export type Coverage = z.infer<typeof coverageSchema>;
export type SectionCoverage = z.infer<typeof sectionCoverage>;

const guardianReach = z.object({
  guardian_id: z.string(),
  relationship: z.string(),
  is_primary: z.boolean(),
  reason: z.string(),
});
export type GuardianReach = z.infer<typeof guardianReach>;
const notReachedStudent = z.object({
  student_id: z.string(),
  name: z.string().nullable(),
  class_section: z.string().nullable(),
  guardians: z.array(guardianReach),
});
export const notReachedSchema = z.object({
  section_id: z.string(),
  label: z.string(),
  category: z.enum(["notices", "attendance", "fees"]),
  students: z.number(),
  items: z.array(notReachedStudent),
  truncated: z.boolean(),
});
export type NotReached = z.infer<typeof notReachedSchema>;
export type NotReachedStudent = z.infer<typeof notReachedStudent>;

const followUpStudent = notReachedStudent.extend({
  outcome: z.enum(["not_sent", "failed", "not_opened"]),
});
export const followUpSchema = z.object({
  broadcast_id: z.string(),
  notice_id: z.string(),
  not_opened_hours: z.number(),
  outcomes: counts,
  items: z.array(followUpStudent),
  truncated: z.boolean(),
});
export type FollowUp = z.infer<typeof followUpSchema>;
export type FollowUpStudent = z.infer<typeof followUpStudent>;

export const closurePreviewSchema = z.object({
  preview_token: z.string(),
  guardians: z.number(),
  recipients: z.number(),
  skipped: counts,
  languages: counts,
  language_fallbacks: z.number(),
  estimated_cost_inr: z.string(),
  utility_rate_inr: z.string(),
  gst_percent: z.string(),
  message_en: z.string(),
  sends_at: z.string().nullable(),
  window_start: z.string(),
  window_end: z.string(),
  blocked_reason: z.string().nullable(),
  recent_closures: z.number(),
});
export type ClosurePreview = z.infer<typeof closurePreviewSchema>;

export const closureSchema = z.object({
  id: z.string(),
  whole_school: z.boolean(),
  section_ids: z.array(z.string()),
  closed_from: z.string(),
  closed_to: z.string(),
  reason: z.string(),
  recipients: z.number(),
  queued: z.number(),
  skipped: counts,
  languages: counts,
  estimated_cost_inr: z.string(),
  counts,
  sent_by: z.string(),
  sent_at: z.string(),
});
export type Closure = z.infer<typeof closureSchema>;
export const closurePageSchema = z.object({
  items: z.array(closureSchema),
  uses: z.number(),
  uses_window_days: z.number(),
  window_start: z.string(),
  window_end: z.string(),
  reasons: z.array(z.string()),
});
export type ClosurePage = z.infer<typeof closurePageSchema>;

/** The closure form (FR-MSG-048): whole school or chosen sections, dates, a reason code. */
export interface ClosureInput {
  whole_school: boolean;
  section_ids?: string[];
  closed_from: string;
  closed_to?: string;
  reason: ClosureReason;
}

export const REACH_KEYS = {
  coverage: (category: Category) => ["staff", "messaging", "coverage", category] as const,
  notReached: (sectionId: string, category: Category) =>
    ["staff", "messaging", "not-reached", sectionId, category] as const,
  followUp: (broadcastId: string) => ["staff", "messaging", "follow-up", broadcastId] as const,
  closures: ["staff", "messaging", "closures"] as const,
};

export function useCoverage(category: Category, enabled = true): Loadable<Coverage> {
  const api = useBffClient("staff");
  return useApiQuery(
    REACH_KEYS.coverage(category),
    () =>
      parsed(
        coverageSchema,
        loose(api).GET("/api/v1/messaging/coverage", { params: { query: { category } } }),
      ),
    { enabled },
  );
}

export function useNotReached(
  sectionId: string,
  category: Category,
  enabled = true,
): Loadable<NotReached> {
  const api = useBffClient("staff");
  return useApiQuery(
    REACH_KEYS.notReached(sectionId, category),
    () =>
      parsed(
        notReachedSchema,
        loose(api).GET("/api/v1/sections/{section_id}/optins/not-reached", {
          params: { path: { section_id: sectionId }, query: { category } },
        }),
      ),
    { enabled: enabled && sectionId !== "" },
  );
}

export function useFollowUp(broadcastId: string, enabled = true): Loadable<FollowUp> {
  const api = useBffClient("staff");
  return useApiQuery(
    REACH_KEYS.followUp(broadcastId),
    () =>
      parsed(
        followUpSchema,
        loose(api).GET("/api/v1/messaging/broadcasts/{broadcast_id}/follow-up", {
          params: { path: { broadcast_id: broadcastId } },
        }),
      ),
    { enabled },
  );
}

export function useClosures(enabled = true): Loadable<ClosurePage> {
  const api = useBffClient("staff");
  return useApiQuery(
    REACH_KEYS.closures,
    () =>
      parsed(
        closurePageSchema,
        loose(api).GET("/api/v1/messaging/closures", { params: { query: { limit: 20 } } }),
      ),
    { enabled },
  );
}

export function previewClosure(api: ApiClient, body: ClosureInput): Promise<ClosurePreview> {
  return parsed(
    closurePreviewSchema,
    loose(api).POST("/api/v1/messaging/closures/preview", { body }),
  );
}

export function sendClosure(
  api: ApiClient,
  body: ClosureInput & { preview_token: string },
  idempotencyKey: string,
): Promise<Closure> {
  return parsed(
    closureSchema,
    loose(api).POST("/api/v1/messaging/closures", {
      body,
      headers: { "Idempotency-Key": idempotencyKey },
    }),
  );
}

/** Print view of join slips for the families a category does not reach (FR-MSG-043). */
export function missingSlipsHref(sectionId: string, category: Category): string {
  return (
    `/bff/api/v1/sections/${encodeURIComponent(sectionId)}/join-slips` +
    `?categories=${encodeURIComponent(category)}&missing_only=true`
  );
}

/** Students reached as a whole percentage (0 when the section has no students). */
export function reachedPercent(reached: number, total: number): number {
  return total > 0 ? Math.round((reached * 100) / total) : 0;
}
