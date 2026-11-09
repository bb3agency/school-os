import type { ApiClient, components } from "@schoolos/api-client";
import { unwrap } from "@/lib/bff/query";

/**
 * UDISE+ correction requests to the MEO (FO-02, FR-UDI-001..010) and the UDISE+ list import's
 * match decisions (FO-04, FR-UDI-014).
 * The calls go through the BFF client, so CSRF, step-up (428) and sign-in handling apply.
 */

type S = components["schemas"];
export type UdiseRequestStatus = S["UdiseRequestOut"]["status"];
export type UdiseLineState = S["UdiseLineOut"]["state"];
export type AadhaarMatch = S["UdiseLineOut"]["matches_aadhaar"];
export type UdiseRecipient = S["UdiseRecipientOut"];
export type UdiseLine = S["UdiseLineOut"];
export type UdiseGroup = S["UdiseGroupOut"];
export type UdiseCandidates = S["UdiseCandidatesOut"];
export type UdiseRequest = S["UdiseRequestOut"];
export type UdiseRequestDetail = S["UdiseRequestDetailOut"];
export type MatchDecision = S["RowMatchIn"]["decision"];

export const UDISE_MANAGE = "dq.readiness.manage";
export const UDISE_PRINT = "export.portal";
export const UDISE_PROFILES = ["bseap-ssc-2027", "apaar"] as const;

export const UDISE_KEYS = {
  all: ["staff", "dq", "udise"] as const,
  candidates: (profile: string) => ["staff", "dq", "udise", "candidates", profile] as const,
  list: ["staff", "dq", "udise", "list"] as const,
  detail: (id: string) => ["staff", "dq", "udise", "request", id] as const,
};

export function ifMatch(version: number): string {
  return `W/"${version}"`;
}

/** The letter (an API page with its own strict CSP) through the BFF, opened in a new tab. */
export function letterHref(requestId: string): string {
  return `/bff/api/v1/dq/udise-corrections/${encodeURIComponent(requestId)}/letter`;
}

const ONE = { path: (correction_id: string) => ({ path: { correction_id } }) };

export const udiseApi = {
  candidates: (api: ApiClient, profileKey: string) =>
    unwrap(
      api.GET("/api/v1/dq/udise-corrections/candidates", {
        params: { query: { profile_key: profileKey } },
      }),
    ),
  list: (api: ApiClient) => unwrap(api.GET("/api/v1/dq/udise-corrections")),
  detail: (api: ApiClient, id: string) =>
    unwrap(api.GET("/api/v1/dq/udise-corrections/{correction_id}", { params: ONE.path(id) })),
  prepare: (api: ApiClient, profileKey: string, idempotencyKey: string) =>
    unwrap(
      api.POST("/api/v1/dq/udise-corrections", {
        body: { profile_key: profileKey },
        headers: { "Idempotency-Key": idempotencyKey },
      }),
    ),
  markSent: (api: ApiClient, request: UdiseRequest, sentOn: string, sentTo: string) =>
    unwrap(
      api.POST("/api/v1/dq/udise-corrections/{correction_id}/sent", {
        params: ONE.path(request.id),
        body: { sent_on: sentOn, sent_to: sentTo },
        headers: { "If-Match": ifMatch(request.version) },
      }),
    ),
  close: (api: ApiClient, request: UdiseRequest, outcome: "done" | "rejected") =>
    unwrap(
      api.POST("/api/v1/dq/udise-corrections/{correction_id}/close", {
        params: ONE.path(request.id),
        body: { outcome },
        headers: { "If-Match": ifMatch(request.version) },
      }),
    ),
  /** Checks the letter can be printed now (export.portal, step-up); 204, nothing audited. */
  checkLetter: (api: ApiClient, id: string) =>
    unwrap(
      api.GET("/api/v1/dq/udise-corrections/{correction_id}/letter", {
        params: { ...ONE.path(id), query: { check: true } },
        parseAs: "text",
      }),
    ),
  /** FO-04: a person's decision for an unmatched UDISE+ row (`If-Match`: the import's ETag). */
  matchRow: (
    api: ApiClient,
    importId: string,
    rowNo: number,
    version: number,
    decision: MatchDecision,
    studentId?: string,
  ) =>
    unwrap(
      api.PUT("/api/v1/imports/{import_id}/rows/{row_no}/match", {
        params: { path: { import_id: importId, row_no: rowNo } },
        body: studentId ? { decision, student_id: studentId } : { decision },
        headers: { "If-Match": ifMatch(version) },
      }),
    ),
};

/** Row error codes of a UDISE+ import that wait for a person's decision (FO-04). */
export const REVIEW_CODES = new Set([
  "udise_unmatched",
  "udise_match_ambiguous",
  "picked_student_unavailable",
]);

export function needsReview(errors: readonly { code: string }[]): boolean {
  return errors.some((e) => REVIEW_CODES.has(e.code));
}
