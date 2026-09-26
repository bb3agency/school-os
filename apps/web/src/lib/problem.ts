import type { Problem } from "@schoolos/api-client";

const REQUEST_ID = /^[A-Za-z0-9_.:-]{1,128}$/;

/** Reuse a well-formed incoming X-Request-Id, otherwise mint one (docs/09 §2). */
export function requestIdFrom(headers: Headers): string {
  const incoming = headers.get("x-request-id");
  return incoming && REQUEST_ID.test(incoming) ? incoming : `req_${crypto.randomUUID()}`;
}

/** RFC 9457 problem+json response (docs/09 §3). Never includes stack traces. */
export function problemResponse(problem: Problem, requestId: string): Response {
  return new Response(JSON.stringify({ ...problem, request_id: requestId }), {
    status: problem.status,
    headers: {
      "Content-Type": "application/problem+json; charset=utf-8",
      "Cache-Control": "no-store",
      "X-Request-Id": requestId,
    },
  });
}
