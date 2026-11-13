import type { ApiClient, components } from "@schoolos/api-client";
import { unwrap } from "@/lib/bff/query";
import * as z from "@/lib/zod";

/**
 * TC request log (FO-09; FR-CERT-026..FR-CERT-045): transfer certificate requests that reached
 * the office on paper, by phone or in person. Reading needs `certificate.read`, `.issue` or
 * `.approve`; logging and moving a request need `certificate.issue`.
 */

type Schemas = components["schemas"];

export const TC_REQUEST_STATUSES = [
  "received",
  "in_preparation",
  "ready",
  "issued",
  "cancelled",
] as const satisfies readonly TcRequestStatus[];
export type TcRequestStatus = Schemas["TcRequestOut"]["status"];
export const TC_REQUEST_CHANNELS = [
  "paper",
  "phone",
  "in_person",
  "other",
] as const satisfies readonly Schemas["TcRequestOut"]["channel"][];
export const LEAVING_REASONS = [
  "parent_request",
  "parent_transferred",
  "course_completed",
  "other",
] as const satisfies readonly NonNullable<Schemas["TcRequestOut"]["leaving_reason"]>[];

export const TC_REQUEST_KEYS = {
  all: ["staff", "certificates", "tc-requests"] as const,
  list: (filter: string) => ["staff", "certificates", "tc-requests", "list", filter] as const,
};

export type TcRequest = Schemas["TcRequestOut"];
export type TcRequestList = Schemas["TcRequestList"];

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
export function logRequestBody(input: LogRequestInput): Schemas["TcRequestIn"] {
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

interface ListQuery {
  limit: number;
  overdue?: boolean;
  status?: TcRequestStatus;
  cursor?: string;
}

export function fetchRequests(
  api: ApiClient,
  filter: TcRequestFilter,
  cursor: string | null,
  limit = 50,
): Promise<TcRequestList> {
  const query: ListQuery = { limit };
  if (filter === "overdue") query.overdue = true;
  else if (filter) query.status = filter;
  if (cursor) query.cursor = cursor;
  return unwrap(api.GET("/api/v1/tc-requests", { params: { query } }));
}

export function logRequest(
  api: ApiClient,
  body: Schemas["TcRequestIn"],
  idempotencyKey: string,
): Promise<TcRequest> {
  return unwrap(
    api.POST("/api/v1/tc-requests", { body, headers: { "Idempotency-Key": idempotencyKey } }),
  );
}

export function moveRequest(
  api: ApiClient,
  request: Pick<TcRequest, "id" | "version">,
  body: Schemas["TcRequestStatusIn"],
): Promise<TcRequest> {
  return unwrap(
    api.POST("/api/v1/tc-requests/{tc_request_id}/status", {
      params: { path: { tc_request_id: request.id } },
      body,
      headers: { "If-Match": ifMatch(request.version) },
    }),
  );
}
