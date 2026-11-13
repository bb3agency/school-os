"use client";

import type { components } from "@schoolos/api-client";
import { unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import type { Loadable } from "@/lib/loadable";
import { ifMatch, type VerifiedAnswerBody } from "./data";

/**
 * Knowledge gaps (FA-06; FR-KB-085..FR-KB-090; docs/09 Knowledge gaps): questions Ask could not
 * answer, for holders of `kb.verified_answer.manage`.
 */

type Schemas = components["schemas"];

export type KnowledgeGap = Schemas["KnowledgeGapOut"];
export type GapStatus = KnowledgeGap["status"];
export type KnowledgeGapPage = Schemas["Page_KnowledgeGapOut_"];
export type KnowledgeGapSummary = Schemas["KnowledgeGapSummaryOut"];

/** The three ways to close a gap (a narrower view of `KnowledgeGapCloseIn`). */
export type GapCloseBody =
  | { resolution: "dismissed" }
  | { resolution: "document"; document_id: string }
  | { resolution: "verified_answer"; verified_answer_id: string };

export const GAP_KEYS = {
  all: ["staff", "knowledge", "gaps"],
  list: (status: GapStatus, cursor: string | undefined) =>
    ["staff", "knowledge", "gaps", "list", status, cursor ?? null] as const,
  summary: ["staff", "knowledge", "gaps", "summary"],
} as const;

/** GET /knowledge/gaps (open: most asked first; closed and dismissed: newest first). */
export function useKnowledgeGaps(
  status: GapStatus,
  cursor: string | undefined,
  enabled: boolean,
): Loadable<KnowledgeGapPage> {
  const api = useBffClient("staff");
  return useApiQuery(
    GAP_KEYS.list(status, cursor),
    () =>
      unwrap(
        api.GET("/api/v1/knowledge/gaps", {
          params: { query: { status, limit: 20, ...(cursor ? { cursor } : {}) } },
        }),
      ),
    { enabled },
  );
}

/** GET /knowledge/gaps/summary (counts only). */
export function useGapSummary(enabled: boolean): Loadable<KnowledgeGapSummary> {
  const api = useBffClient("staff");
  return useApiQuery(GAP_KEYS.summary, () => unwrap(api.GET("/api/v1/knowledge/gaps/summary")), {
    enabled,
  });
}

/** Close, dismiss or answer a gap (If-Match with the version listed). */
export function useGapApi() {
  const api = useBffClient("staff");
  return {
    close: (gap: KnowledgeGap, body: GapCloseBody) =>
      unwrap(
        api.POST("/api/v1/knowledge/gaps/{gap_id}/close", {
          params: { path: { gap_id: gap.id } },
          headers: { "If-Match": ifMatch(gap.version) },
          body,
        }),
      ),
    answer: (gap: KnowledgeGap, body: VerifiedAnswerBody) =>
      unwrap(
        api.POST("/api/v1/knowledge/gaps/{gap_id}/verified-answer", {
          params: { path: { gap_id: gap.id } },
          headers: { "If-Match": ifMatch(gap.version) },
          body,
        }),
      ),
  };
}

const UUID = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i;

/** The document id in a pasted document page link (`/documents/<id>`) or a bare id. */
export function documentIdFrom(value: string): string | null {
  const match = UUID.exec(value.trim());
  return match ? match[0].toLowerCase() : null;
}
