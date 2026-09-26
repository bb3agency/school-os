import "server-only";
import type { AuthRuntime } from "@/server/runtime";
import type { Session } from "@/server/session/store";
import { mintServiceToken, SERVICE_TOKEN_HEADER } from "./service-token";

/**
 * One call from the BFF to the API (docs/09 §1): user access token + fresh service token,
 * request id, active tenant and language. Only allowlisted request headers are copied:
 * never Cookie, Host, Authorization from the browser, or hop-by-hop headers.
 */

const FORWARDED_REQUEST_HEADERS = [
  "accept",
  "content-type",
  "if-match",
  "if-none-match",
  "idempotency-key",
] as const;

const SUPPORTED_LANGUAGES = ["en", "te"] as const;

/** First supported language in an Accept-Language header; English otherwise. */
export function negotiateLanguage(header: string | null): "en" | "te" {
  for (const part of (header ?? "").split(",")) {
    const tag = part.split(";")[0]?.trim().toLowerCase() ?? "";
    const primary = tag.split("-")[0];
    const match = SUPPORTED_LANGUAGES.find((language) => language === primary);
    if (match) return match;
  }
  return "en";
}

export interface ApiCall {
  session: Session;
  accessToken: string;
  method: string;
  /** Path starting with /api/v1/, already validated. */
  path: string;
  search?: string;
  incomingHeaders?: Headers;
  body?: Uint8Array | null;
  requestId: string;
  signal?: AbortSignal;
  /** Abort if the API has not sent response headers within this time. */
  headersTimeoutMs?: number;
}

export async function callApi(runtime: AuthRuntime, call: ApiCall): Promise<Response> {
  const headers = new Headers();
  for (const name of FORWARDED_REQUEST_HEADERS) {
    const value = call.incomingHeaders?.get(name);
    if (value) headers.set(name, value);
  }
  if (!headers.has("accept")) headers.set("accept", "application/json");
  headers.set(
    "accept-language",
    negotiateLanguage(call.incomingHeaders?.get("accept-language") ?? null),
  );
  headers.set("authorization", `Bearer ${call.accessToken}`);
  headers.set(
    SERVICE_TOKEN_HEADER,
    await mintServiceToken(runtime.config.serviceTokenKey, runtime.now),
  );
  headers.set("x-request-id", call.requestId);
  if (call.session.kind === "staff" && call.session.activeTenantId) {
    headers.set("x-active-tenant", call.session.activeTenantId);
  }

  const url = new URL(`${call.path}${call.search ?? ""}`, runtime.config.apiInternalUrl);
  const timeout = new AbortController();
  const timer = setTimeout(() => timeout.abort(), call.headersTimeoutMs ?? 30_000);
  const signal = call.signal ? AbortSignal.any([call.signal, timeout.signal]) : timeout.signal;
  try {
    const hasBody = call.body && call.body.byteLength > 0;
    return await runtime.apiFetch(url, {
      method: call.method,
      headers,
      body: hasBody ? new Blob([call.body as Uint8Array<ArrayBuffer>]) : null,
      redirect: "manual",
      signal,
      cache: "no-store",
    });
  } finally {
    // Headers have arrived (or the call failed): the body may stream for as long as needed.
    clearTimeout(timer);
  }
}
