"use client";

import type { ApiClient, components } from "@schoolos/api-client";
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

/** FP-02 follow-up (FR-MSG-056..065; D86): "the school reopens" and the order text. */
export type ClosureReopening = S["ClosureReopeningOut"];
export type ClosureReopeningPreview = S["ClosureReopeningPreviewOut"];
export type Closure = S["ClosureOut"];
export type ClosurePage = S["ClosurePage"];

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
    () => unwrap(api.GET("/api/v1/messaging/closures", { params: { query: { limit: 20 } } })),
    { enabled },
  );
}

export function previewClosure(api: ApiClient, body: ClosureInput): Promise<ClosurePreview> {
  return unwrap(
    api.POST("/api/v1/messaging/closures/preview", {
      body,
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
      body,
      headers: { "Idempotency-Key": idempotencyKey },
    }),
  );
}

export function previewReopening(
  api: ApiClient,
  closureId: string,
  reopensOn: string,
): Promise<ClosureReopeningPreview> {
  return unwrap(
    api.POST("/api/v1/messaging/closures/{closure_id}/reopening/preview", {
      params: { path: { closure_id: closureId } },
      body: { reopens_on: reopensOn },
    }),
  );
}

export function sendReopening(
  api: ApiClient,
  closureId: string,
  body: S["ClosureReopeningConfirmIn"],
  idempotencyKey: string,
): Promise<ClosureReopening> {
  return unwrap(
    api.POST("/api/v1/messaging/closures/{closure_id}/reopening", {
      params: { path: { closure_id: closureId } },
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
