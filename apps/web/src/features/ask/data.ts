"use client";

import type { components } from "@schoolos/api-client";
import { unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import type { Loadable } from "@/lib/loadable";

type Schemas = components["schemas"];
export type SearchResult = Schemas["SearchResultOut"];
export type SearchBody = Schemas["SearchIn"];
export type FeedbackBody = Schemas["FeedbackIn"];
export type Feedback = Schemas["FeedbackOut"];
export type VerifiedAnswer = Schemas["VerifiedAnswerOut"];
export type VerifiedAnswerBody = Schemas["VerifiedAnswerIn"];
export type VerifiedReviewBody = Schemas["VerifiedAnswerReviewIn"];
export type VerifiedStatus = VerifiedAnswer["status"];

/** Permissions (docs/09 Knowledge). Hiding is UX only: the API checks every call. */
export const ASK_PERM = {
  ask: "kb.ask",
  search: "document.read",
  manageVerified: "kb.verified_answer.manage",
} as const;

export const ASK_KEYS = {
  all: ["staff", "knowledge"],
  verified: (status: VerifiedStatus | null, cursor: string | undefined) =>
    ["staff", "knowledge", "verified", status, cursor ?? null] as const,
  verifiedAll: ["staff", "knowledge", "verified"],
} as const;

/** The API's pinned reason codes (FeedbackIn.reason; anything else is 422). */
type ApiFeedbackReason = NonNullable<FeedbackBody["reason"]>;

/**
 * Reason codes for "not helpful" (a code, never free text), exactly the API's list: the
 * `satisfies` and the check below fail typecheck if either side gains or loses a code.
 */
export const FEEDBACK_REASONS = [
  "wrong_source",
  "outdated",
  "incomplete",
  "not_found_but_exists",
  "wrong_language",
] as const satisfies readonly ApiFeedbackReason[];
export type FeedbackReason = (typeof FEEDBACK_REASONS)[number];
// Every API code is offered (compile-time: `true` only when the two sets are equal).
const ALL_REASONS_OFFERED: ApiFeedbackReason extends FeedbackReason ? true : never = true;
void ALL_REASONS_OFFERED;

/** `W/"3"` for If-Match from a resource's `version` (the API's ETag format). */
export function ifMatch(version: number): string {
  return `W/"${version}"`;
}

export interface VerifiedPage {
  data: VerifiedAnswer[];
  next_cursor: string | null;
}

/** GET /knowledge/verified-answers (kb.ask): only answers whose sources you can all read. */
export function useVerifiedAnswers(
  status: VerifiedStatus | null,
  cursor: string | undefined,
  enabled: boolean,
): Loadable<VerifiedPage> {
  const api = useBffClient("staff");
  return useApiQuery(
    ASK_KEYS.verified(status, cursor),
    () =>
      unwrap(
        api.GET("/api/v1/knowledge/verified-answers", {
          params: {
            query: { limit: 20, ...(cursor ? { cursor } : {}), ...(status ? { status } : {}) },
          },
        }),
      ),
    { enabled },
  );
}

/** The API calls used by the Ask screens (question and search text only in JSON bodies). */
export function useKnowledgeApi() {
  const api = useBffClient("staff");
  return {
    search: (body: SearchBody) => unwrap(api.POST("/api/v1/knowledge/search", { body })),
    feedback: (queryId: string, body: FeedbackBody) =>
      unwrap(
        api.POST("/api/v1/knowledge/queries/{query_id}/feedback", {
          params: { path: { query_id: queryId } },
          body,
        }),
      ),
    createVerified: (body: VerifiedAnswerBody, idempotencyKey: string) =>
      unwrap(
        api.POST("/api/v1/knowledge/verified-answers", {
          body,
          headers: { "Idempotency-Key": idempotencyKey },
        }),
      ),
    /** Confirm again (`kb.verified_answer.manage`, If-Match; an empty body keeps what is stored). */
    reviewVerified: (answer: VerifiedAnswer, body: VerifiedReviewBody) =>
      unwrap(
        api.POST("/api/v1/knowledge/verified-answers/{answer_id}/review", {
          params: { path: { answer_id: answer.id } },
          headers: { "If-Match": ifMatch(answer.version) },
          body,
        }),
      ),
    /** Withdraw from Ask, kept for the record (409 verified_answer_retired when it already is). */
    retireVerified: (answer: VerifiedAnswer) =>
      unwrap(
        api.POST("/api/v1/knowledge/verified-answers/{answer_id}/retire", {
          params: { path: { answer_id: answer.id } },
          headers: { "If-Match": ifMatch(answer.version) },
        }),
      ),
  };
}
