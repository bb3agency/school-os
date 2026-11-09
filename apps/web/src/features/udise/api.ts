import type { ApiClient } from "@schoolos/api-client";
import { unwrap } from "@/lib/bff/query";

/**
 * UDISE+ correction requests to the MEO (FO-02, FR-UDI-001..010) and the UDISE+ list import's
 * match decisions (FO-04, FR-UDI-014).
 *
 * LOCALLY TYPED WRAPPER: these routes are new in wave F2 and not yet in the generated client
 * (`packages/api-client`). The types below mirror `app/dq/udise_schemas.py` and
 * `app/imports/schemas.py` (RowMatchIn, ImportRowOut.match_basis). After `make openapi` the lead
 * swaps them for `components["schemas"]["Udise…"]` and the typed `api.GET/POST/PUT` calls.
 * The calls still go through the BFF client, so CSRF, step-up (428) and sign-in handling apply.
 */

export type UdiseRequestStatus = "prepared" | "sent" | "done" | "rejected";
export type UdiseLineState = "open" | "fixed" | "unavailable";
export type AadhaarMatch = "yes" | "no" | "not_recorded";

export interface UdiseRecipient {
  key: string;
  label: string;
}

export interface UdiseLine {
  student: { id: string; display_name: string | null; admission_no: string | null };
  class_section: string | null;
  pen: string | null;
  attribute_key: string;
  field_label: string;
  udise_value: string | null;
  correct_value: string | null;
  correct_source: string;
  evidence: string[];
  matches_aadhaar: AadhaarMatch;
  state: UdiseLineState;
  requested_in?: string | null;
}

export interface UdiseGroup {
  key: string;
  label: string;
  lines: UdiseLine[];
}

export interface UdiseCandidates {
  profile_key: string;
  verified: boolean;
  groups: UdiseGroup[];
  lines_count: number;
  students_count: number;
  already_requested: number;
  recipients: UdiseRecipient[];
}

export interface UdiseRequest {
  id: string;
  profile_key: string;
  status: UdiseRequestStatus;
  lines_count: number;
  students_count: number;
  prepared_by: string;
  prepared_at: string;
  sent_on: string | null;
  sent_to: string | null;
  sent_to_label: string | null;
  closed_at: string | null;
  version: number;
}

export interface UdiseRequestDetail extends UdiseRequest {
  verified: boolean;
  groups: UdiseGroup[];
  fixed_count: number;
  open_count: number;
  recipients: UdiseRecipient[];
}

export type MatchDecision = "student" | "skip" | "clear";

type ClientResult = { data?: unknown; error?: unknown; response: Response };
type Init = {
  params?: { query?: Record<string, unknown> };
  body?: unknown;
  headers?: Record<string, string>;
  parseAs?: "json" | "text";
};
/** The generated client without its path typing (new routes only; see the note above). */
interface LooseClient {
  GET(path: string, init?: Init): Promise<ClientResult>;
  POST(path: string, init?: Init): Promise<ClientResult>;
  PUT(path: string, init?: Init): Promise<ClientResult>;
}

function loose(api: ApiClient): LooseClient {
  return api as unknown as LooseClient;
}

export const UDISE_MANAGE = "dq.readiness.manage";
export const UDISE_PRINT = "export.portal";
export const UDISE_PROFILES = ["bseap-ssc-2027", "apaar"] as const;
const BASE = "/api/v1/dq/udise-corrections";

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
  return `/bff${BASE}/${encodeURIComponent(requestId)}/letter`;
}

export const udiseApi = {
  candidates: (api: ApiClient, profileKey: string) =>
    unwrap(
      loose(api).GET(`${BASE}/candidates`, { params: { query: { profile_key: profileKey } } }),
    ) as Promise<UdiseCandidates>,
  list: (api: ApiClient) => unwrap(loose(api).GET(BASE)) as Promise<UdiseRequest[]>,
  detail: (api: ApiClient, id: string) =>
    unwrap(loose(api).GET(`${BASE}/${encodeURIComponent(id)}`)) as Promise<UdiseRequestDetail>,
  prepare: (api: ApiClient, profileKey: string, idempotencyKey: string) =>
    unwrap(
      loose(api).POST(BASE, {
        body: { profile_key: profileKey },
        headers: { "Idempotency-Key": idempotencyKey },
      }),
    ) as Promise<UdiseRequestDetail>,
  markSent: (api: ApiClient, request: UdiseRequest, sentOn: string, sentTo: string) =>
    unwrap(
      loose(api).POST(`${BASE}/${encodeURIComponent(request.id)}/sent`, {
        body: { sent_on: sentOn, sent_to: sentTo },
        headers: { "If-Match": ifMatch(request.version) },
      }),
    ) as Promise<UdiseRequestDetail>,
  close: (api: ApiClient, request: UdiseRequest, outcome: "done" | "rejected") =>
    unwrap(
      loose(api).POST(`${BASE}/${encodeURIComponent(request.id)}/close`, {
        body: { outcome },
        headers: { "If-Match": ifMatch(request.version) },
      }),
    ) as Promise<UdiseRequestDetail>,
  /** Checks the letter can be printed now (export.portal, step-up); 204, nothing audited. */
  checkLetter: (api: ApiClient, id: string) =>
    unwrap(
      loose(api).GET(`${BASE}/${encodeURIComponent(id)}/letter`, {
        params: { query: { check: true } },
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
      loose(api).PUT(`/api/v1/imports/${encodeURIComponent(importId)}/rows/${rowNo}/match`, {
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
