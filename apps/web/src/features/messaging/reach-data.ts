"use client";

import type { ApiClient, components } from "@schoolos/api-client";
import * as z from "@/lib/zod";
import { unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import type { Loadable } from "@/lib/loadable";
import type { Category } from "./data";

/**
 * FP-01 (opt-in coverage and reach follow-up, FR-MSG-039..046) and FP-02 (urgent closure
 * message, FR-MSG-047..055; ADR-0043 amendment, lead decision D85).
 *
 * No list carries a phone number; names appear only when the reader may read them.
 */

/** `message.closure.send`: the principal only, step-up MFA (FR-MSG-052). */
export const CLOSURE_SEND = "message.closure.send";
export const CLOSURE_REASONS = ["weather", "government_order", "local_situation", "other"] as const;
export type ClosureReason = (typeof CLOSURE_REASONS)[number];

type S = components["schemas"];
export type Coverage = S["CoverageOut"];
export type SectionCoverage = S["SectionCoverageOut"];
export type GuardianReach = S["GuardianReachOut"];
export type NotReached = S["NotReachedOut"];
export type NotReachedStudent = S["NotReachedStudentOut"];
export type FollowUp = S["BroadcastFollowUpOut"];
export type FollowUpStudent = S["FollowUpStudentOut"];
export type ClosurePreview = S["ClosurePreviewOut"];

/*
 * FP-02 follow-up (FR-MSG-056..065; D86): "the school reopens" and the order text. These shapes
 * are hand-written (zod) until the lead regenerates apps/api/openapi.json and the client; then
 * ClosureReopeningPreviewOut / ClosureReopeningOut and ClosureOut.notice_text/reopening come
 * from `components["schemas"]` and the loose POST below becomes `api.POST(...)`.
 */
const counts = z.record(z.string(), z.number());
export const closureReopeningSchema = z.object({
  id: z.string(),
  closure_id: z.string(),
  reopens_on: z.string(),
  recipients: z.number(),
  queued: z.number(),
  skipped: counts,
  languages: counts,
  estimated_cost_inr: z.string(),
  counts,
  sent_by: z.string(),
  sent_at: z.string(),
});
export const closureReopeningPreviewSchema = z.object({
  preview_token: z.string(),
  closure_id: z.string(),
  reopens_on: z.string(),
  guardians: z.number(),
  recipients: z.number(),
  skipped: counts,
  languages: counts,
  language_fallbacks: z.number(),
  estimated_cost_inr: z.string(),
  message_en: z.string(),
  sends_at: z.string().nullable(),
  window_start: z.string(),
  window_end: z.string(),
  blocked_reason: z.string().nullable(),
});
export type ClosureReopening = z.infer<typeof closureReopeningSchema>;
export type ClosureReopeningPreview = z.infer<typeof closureReopeningPreviewSchema>;
export type Closure = S["ClosureOut"] & {
  notice_text?: string | null;
  reopening?: ClosureReopening | null;
};
export type ClosurePage = Omit<S["ClosurePage"], "items"> & { items: Closure[] };

/** The closure form (FR-MSG-048): whole school or chosen sections, dates, a reason code and
 * (FR-MSG-056) the optional order text for the closure's parent page. */
export interface ClosureInput {
  whole_school: boolean;
  section_ids?: string[];
  closed_from: string;
  closed_to?: string;
  reason: ClosureReason;
  notice_text?: string;
}

/** The order text's limit (msg.closures CHECK, ClosureIn.notice_text). */
export const NOTICE_TEXT_MAX = 1000;

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
    () => unwrap(api.GET("/api/v1/messaging/coverage", { params: { query: { category } } })),
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
      unwrap(
        api.GET("/api/v1/sections/{section_id}/optins/not-reached", {
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
      unwrap(
        api.GET("/api/v1/messaging/broadcasts/{broadcast_id}/follow-up", {
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
    async () =>
      (await unwrap(
        api.GET("/api/v1/messaging/closures", { params: { query: { limit: 20 } } }),
      )) as ClosurePage,
    { enabled },
  );
}

export function previewClosure(api: ApiClient, body: ClosureInput): Promise<ClosurePreview> {
  return unwrap(
    api.POST("/api/v1/messaging/closures/preview", {
      body: body as S["ClosureIn"],
    }),
  );
}

export function sendClosure(
  api: ApiClient,
  body: ClosureInput & { preview_token: string },
  idempotencyKey: string,
): Promise<Closure> {
  return unwrap(
    api.POST("/api/v1/messaging/closures", {
      body: body as S["ClosureConfirmIn"],
      headers: { "Idempotency-Key": idempotencyKey },
    }),
  );
}

type LoosePost = (
  path: string,
  init: {
    params: { path: Record<string, string> };
    body: unknown;
    headers?: Record<string, string>;
  },
) => Promise<{ data?: unknown; error?: unknown; response: Response }>;

/** POST on a route the generated client does not know yet (see the note above). */
function loosePost(api: ApiClient): LoosePost {
  return api.POST as unknown as LoosePost;
}

const REOPENING = "/api/v1/messaging/closures/{closure_id}/reopening";

export async function previewReopening(
  api: ApiClient,
  closureId: string,
  reopensOn: string,
): Promise<ClosureReopeningPreview> {
  const data = await unwrap(
    loosePost(api)(`${REOPENING}/preview`, {
      params: { path: { closure_id: closureId } },
      body: { reopens_on: reopensOn },
    }),
  );
  return closureReopeningPreviewSchema.parse(data);
}

export async function sendReopening(
  api: ApiClient,
  closureId: string,
  body: { reopens_on: string; preview_token: string },
  idempotencyKey: string,
): Promise<ClosureReopening> {
  const data = await unwrap(
    loosePost(api)(REOPENING, {
      params: { path: { closure_id: closureId } },
      body,
      headers: { "Idempotency-Key": idempotencyKey },
    }),
  );
  return closureReopeningSchema.parse(data);
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
