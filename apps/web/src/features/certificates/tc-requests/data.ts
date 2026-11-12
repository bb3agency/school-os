import type { ApiClient } from "@schoolos/api-client";
import { unwrap } from "@/lib/bff/query";
import * as z from "@/lib/zod";

/**
 * TC request log (FO-09; FR-CERT-026..FR-CERT-045): transfer certificate requests that reached
 * the office on paper, by phone or in person. Reading needs `certificate.read`, `.issue` or
 * `.approve`; logging and moving a request need `certificate.issue`.
 *
 * LOCAL WRAPPER: the routes under /api/v1/tc-requests are new in wave F3 and not yet in the
 * generated client (`packages/api-client`). The types below are written by hand from
 * `app/certificates/schemas.py` (TcRequestOut, TcRequestList) and the answers are checked with
 * zod. After `make openapi` the lead swaps `looseClient` and these types for
 * `components["schemas"]["TcRequestOut"]` and `api.GET("/api/v1/tc-requests")`.
 */

export const TC_REQUEST_STATUSES = [
  "received",
  "in_preparation",
  "ready",
  "issued",
  "cancelled",
] as const;
export type TcRequestStatus = (typeof TC_REQUEST_STATUSES)[number];
export const TC_REQUEST_CHANNELS = ["paper", "phone", "in_person", "other"] as const;
export const LEAVING_REASONS = [
  "parent_request",
  "parent_transferred",
  "course_completed",
  "other",
] as const;

export const TC_REQUEST_KEYS = {
  all: ["staff", "certificates", "tc-requests"] as const,
  list: (filter: string) => ["staff", "certificates", "tc-requests", "list", filter] as const,
};
const BASE = "/api/v1/tc-requests";

const feeNoteSchema = z.object({
  balance: z.union([z.string(), z.number()]),
  overdue: z.union([z.string(), z.number()]),
  oldest_due_on: z.string().nullable(),
  last_payment_on: z.string().nullable(),
  account_id: z.string(),
});

const requestSchema = z.object({
  id: z.string(),
  student_id: z.string(),
  student_name: z.string().nullable(),
  admission_no: z.string().nullable(),
  class_label: z.string().nullable(),
  status: z.enum(TC_REQUEST_STATUSES),
  received_on: z.string(),
  channel: z.enum(TC_REQUEST_CHANNELS),
  leaving_date: z.string().nullable(),
  leaving_reason: z.enum(LEAVING_REASONS).nullable(),
  certificate_id: z.string().nullable(),
  certificate_status: z.string().nullable(),
  certificate_serial: z.string().nullable(),
  received_by_name: z.string().nullable(),
  received_at: z.string(),
  preparing_by_name: z.string().nullable(),
  preparing_at: z.string().nullable(),
  ready_by_name: z.string().nullable(),
  ready_at: z.string().nullable(),
  issued_by_name: z.string().nullable(),
  issued_at: z.string().nullable(),
  cancelled_by_name: z.string().nullable(),
  cancelled_at: z.string().nullable(),
  cancel_reason: z.string().nullable(),
  days_open: z.number(),
  overdue: z.boolean(),
  fee_note: feeNoteSchema.nullable().optional(),
  can_prepare: z.boolean(),
  can_hand_over: z.boolean(),
  can_cancel: z.boolean(),
  version: z.number(),
});

const listSchema = z.object({
  data: z.array(requestSchema),
  next_cursor: z.string().nullable(),
  overdue_count: z.number(),
  overdue_after_days: z.number(),
  overdue_verified: z.boolean(),
});

export type TcRequest = z.output<typeof requestSchema>;
export type TcRequestList = z.output<typeof listSchema>;

/** The list filter: a status, only overdue requests, or everything (""). */
export type TcRequestFilter = "" | "overdue" | TcRequestStatus;
export const TC_REQUEST_FILTERS: readonly TcRequestFilter[] = [
  "",
  "overdue",
  ...TC_REQUEST_STATUSES,
];

/** The log dialog's fields; empty strings mean "not given". */
export const logRequestSchema = z.object({
  student_id: z.string().min(1, { error: "required" }),
  received_on: z.iso.date({ error: "invalidDate" }),
  channel: z.enum(TC_REQUEST_CHANNELS, { error: "chooseOption" }),
  leaving_date: z.union([z.literal(""), z.iso.date({ error: "invalidDate" })]).optional(),
  leaving_reason: z.union([z.literal(""), z.enum(LEAVING_REASONS)]).optional(),
});
export type LogRequestInput = z.output<typeof logRequestSchema>;

/** The body the API takes (FR-CERT-026): optional values left out when empty. */
export function logRequestBody(input: LogRequestInput) {
  return {
    student_id: input.student_id,
    received_on: input.received_on,
    channel: input.channel,
    ...(input.leaving_date ? { leaving_date: input.leaving_date } : {}),
    ...(input.leaving_reason ? { leaving_reason: input.leaving_reason } : {}),
  };
}

export function ifMatch(version: number): string {
  return `W/"${version}"`;
}

/** Where the TC for a request is prepared: the issue screen, with the request's suggestions. */
export function prepareHref(request: Pick<TcRequest, "student_id">): string {
  return `/students/${request.student_id}/certificates/new?type=transfer`;
}

// --- local wrapper (see the note at the top) ------------------------------------------------

type ClientResult = { data?: unknown; error?: unknown; response: Response };
interface LooseInit {
  body?: unknown;
  headers?: Record<string, string>;
  params?: { query?: Record<string, string | number | boolean> };
}
interface LooseClient {
  GET(path: string, init?: LooseInit): Promise<ClientResult>;
  POST(path: string, init?: LooseInit): Promise<ClientResult>;
}

function looseClient(api: ApiClient): LooseClient {
  return api as unknown as LooseClient;
}

export async function fetchRequests(
  api: ApiClient,
  filter: TcRequestFilter,
  cursor: string | null,
  limit = 50,
): Promise<TcRequestList> {
  const query: Record<string, string | number | boolean> = { limit };
  if (filter === "overdue") query.overdue = true;
  else if (filter) query.status = filter;
  if (cursor) query.cursor = cursor;
  return listSchema.parse(await unwrap(looseClient(api).GET(BASE, { params: { query } })));
}

export async function logRequest(
  api: ApiClient,
  body: ReturnType<typeof logRequestBody>,
  idempotencyKey: string,
): Promise<TcRequest> {
  return requestSchema.parse(
    await unwrap(
      looseClient(api).POST(BASE, { body, headers: { "Idempotency-Key": idempotencyKey } }),
    ),
  );
}

export async function moveRequest(
  api: ApiClient,
  request: Pick<TcRequest, "id" | "version">,
  body: { status: "in_preparation" | "issued" | "cancelled"; reason?: string },
): Promise<TcRequest> {
  return requestSchema.parse(
    await unwrap(
      looseClient(api).POST(`${BASE}/${request.id}/status`, {
        body,
        headers: { "If-Match": ifMatch(request.version) },
      }),
    ),
  );
}
