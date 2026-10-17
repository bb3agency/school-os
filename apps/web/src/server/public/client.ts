import "server-only";
import { requestIdFrom } from "@/lib/problem";
import { clientIp } from "@/server/auth/rate-limit";
import { mintServiceToken, SERVICE_TOKEN_HEADER } from "@/server/bff/service-token";
import type { AuthRuntime } from "@/server/runtime";

/**
 * The public BFF client (FR-PUB-001; ADR-0046, ADR-0047; docs/09 §4 "Public").
 *
 * The `(public)` route group calls the API's `/api/v1/public/*` routes only through here: a
 * fresh service token, the parent's address for the API's per-IP limits (only the hop our own
 * trusted proxy vouches for), a request id, and the parent link token in
 * `X-SOS-Public-Token` when there is one. Never a user token, never the browser's cookies, never
 * another API path: the staff BFF refuses `/api/v1/public/*` and `/api/v1/webhooks/*`, and this
 * client refuses everything else. The token never goes into a URL or a log line.
 */

export const PUBLIC_TOKEN_HEADER = "X-SOS-Public-Token";
const PUBLIC_API_PREFIX = "/api/v1/public/";

export type PublicRuntime = Pick<AuthRuntime, "config" | "apiFetch" | "now">;

export interface PublicApiCall {
  method: "GET" | "POST";
  /** Starts with /api/v1/public/. */
  path: string;
  /** The parent link token (from the path on exchange, from the cookie afterwards). */
  token?: string | undefined;
  /** JSON body (POST). */
  body?: unknown;
  incomingHeaders: Headers;
  headersTimeoutMs?: number;
}

export class PublicPathError extends Error {}

export async function callPublicApi(
  runtime: PublicRuntime,
  call: PublicApiCall,
): Promise<Response> {
  if (!call.path.startsWith(PUBLIC_API_PREFIX) || /[?#\\]|%|\/\.\.?(\/|$)/.test(call.path)) {
    throw new PublicPathError("public client: only /api/v1/public/* paths");
  }
  const headers = new Headers({ accept: "application/json" });
  headers.set(
    SERVICE_TOKEN_HEADER,
    await mintServiceToken(runtime.config.serviceTokenKey, runtime.now),
  );
  headers.set("x-request-id", requestIdFrom(call.incomingHeaders));
  const ip = clientIp(call.incomingHeaders, runtime.config.trustedProxyHops);
  if (ip !== "unknown") headers.set("x-forwarded-for", ip);
  if (call.token !== undefined) headers.set(PUBLIC_TOKEN_HEADER, call.token);
  let body: string | null = null;
  if (call.body !== undefined) {
    headers.set("content-type", "application/json");
    body = JSON.stringify(call.body);
  }
  const url = new URL(call.path, runtime.config.apiInternalUrl);
  const timeout = AbortSignal.timeout(call.headersTimeoutMs ?? 15_000);
  return runtime.apiFetch(url, {
    method: call.method,
    headers,
    body,
    redirect: "manual",
    signal: timeout,
    cache: "no-store",
  });
}
