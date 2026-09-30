import "server-only";
import { requestIdFrom } from "@/lib/problem";
import { nextFromReferer } from "@/server/auth/redirect";
import { RefreshBusyError, SessionEndedError } from "@/server/auth/refresh";
import type { SessionKind } from "@/server/config";
import { logEvent } from "@/server/log";
import type { AuthRuntime } from "@/server/runtime";
import { applySchoolSettingsFromMe } from "@/server/session/school-settings";
import type { Session } from "@/server/session/store";
import {
  csrfFailed,
  csrfOk,
  isUnsafeMethod,
  problem,
  readSchoolSession,
  readSession,
} from "./http";
import { callApi } from "./upstream";

/**
 * BFF proxy: browser → /bff/api/v1/* → API /api/v1/* (docs/09 §1; SEC-004).
 *
 * - Machine-only API paths (the Tally edge agent, /api/v1/edge/*, ADR-0032) are never proxied:
 *   404 before any session work; the agent calls the API directly with its device signature.
 * - Needs a session of the right kind: operators may call only /platform/*, school staff
 *   never /platform/* (403 wrong_session). No session: 401. A SchoolOS support session
 *   (break-glass, ADR-0023) counts as a school session when there is no staff session; the
 *   API decides what it may read.
 * - State-changing methods need the CSRF synchronizer token (403 csrf_failed).
 * - Adds the user's access token and a fresh service token; copies only allowlisted
 *   headers each way (never cookies, never Set-Cookie from the API).
 * - Upstream 401 token_expired: one refresh and one retry. Upstream 428: problem with
 *   `step_up_url` for the UI.
 * - Streams responses (Server-Sent Events are passed through unbuffered).
 * - A staff GET /me answer about the active school sets the session's idle timeout from
 *   `settings.idle_timeout_minutes` (FR-TEN-012), so a changed setting applies without a reload.
 * - A GET marked passive (`x-sos-passive: 1`, background polls such as the notification bell)
 *   reads the session without sliding its idle timeout.
 * - An API page that brings its own strict Content-Security-Policy (the change-request memo)
 *   keeps it; src/proxy.ts leaves those paths' CSP to this handler.
 */

export const MAX_REQUEST_BODY_BYTES = 1024 * 1024;
const BFF_PREFIX = "/bff";
// Encoded slash, backslash or dot could smuggle traversal past the path checks.
const SUSPICIOUS_PATH = /%(2f|5c|2e)|\\|\/\.\.?(\/|$)/i;

const FORWARDED_RESPONSE_HEADERS = [
  "content-type",
  "content-disposition",
  "content-language",
  "cache-control",
  "etag",
  "last-modified",
  "location",
  "retry-after",
  "ratelimit-limit",
  "ratelimit-remaining",
  "ratelimit-reset",
  "deprecation",
  "sunset",
  "x-request-id",
] as const;

/** Same name as PASSIVE_HEADER in src/lib/bff/fetch.ts (browser side). */
const PASSIVE_HEADER = "x-sos-passive";

/** CSP for BFF responses whose API policy is missing or looser than this. */
const STRICT_FALLBACK_CSP =
  "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'";

/** A source that allows nothing, or exactly one inline block pinned by its hash. */
const STRICT_SOURCE = /^('none'|'sha(256|384|512)-[A-Za-z0-9+/]+={0,2}')$/;

/**
 * An API page's own policy is kept only when it is at least as strict as
 * STRICT_FALLBACK_CSP: `default-src 'none'` and `frame-ancestors 'none'` (the API's
 * `_at_least_as_strict`, apps/api/app/core/middleware.py), and, stricter than the API's
 * check, every other directive allows only `'none'` or hash-pinned inline blocks (no hosts,
 * no schemes, no 'self', no 'unsafe-*', no nonces). A policy that could run scripts or load
 * anything from anywhere is replaced.
 */
export function isStrictPolicy(policy: string | null): policy is string {
  if (!policy) return false;
  const directives = new Map<string, string[]>();
  for (const part of policy.split(";")) {
    const [rawName, ...sources] = part.trim().split(/\s+/);
    if (!rawName) continue;
    const name = rawName.toLowerCase();
    if (directives.has(name) || sources.length === 0) return false;
    // Hashes are base64 (case-sensitive), so sources keep their spelling.
    if (!sources.every((source) => STRICT_SOURCE.test(source))) return false;
    directives.set(name, sources);
  }
  const only = (name: string) => directives.get(name)?.join(" ") === "'none'";
  return only("default-src") && only("frame-ancestors");
}

class BodyTooLargeError extends Error {}

async function readBody(request: Request): Promise<Uint8Array | null> {
  if (!request.body) return null;
  const declared = Number(request.headers.get("content-length") ?? "0");
  if (declared > MAX_REQUEST_BODY_BYTES) throw new BodyTooLargeError();
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > MAX_REQUEST_BODY_BYTES) {
      await reader.cancel();
      throw new BodyTooLargeError();
    }
    chunks.push(value);
  }
  const body = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    body.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return body;
}

function responseHeaders(upstream: Response, requestId: string): Headers {
  const headers = new Headers();
  for (const name of FORWARDED_RESPONSE_HEADERS) {
    const value = upstream.headers.get(name);
    if (value !== null) headers.set(name, value);
  }
  const location = headers.get("location");
  if (location?.startsWith("/api/v1/")) headers.set("location", `${BFF_PREFIX}${location}`);
  else if (location) headers.delete("location");
  if (!headers.has("cache-control")) headers.set("cache-control", "no-store");
  // Only matters where src/proxy.ts sets no CSP of its own (OWN_CSP_PATHS); elsewhere the
  // page-wide policy set by the proxy wins, because Next.js keeps the header set first.
  const policy = upstream.headers.get("content-security-policy");
  headers.set("content-security-policy", isStrictPolicy(policy) ? policy : STRICT_FALLBACK_CSP);
  if ((headers.get("content-type") ?? "").startsWith("text/event-stream")) {
    headers.set("cache-control", "no-cache, no-transform");
    headers.set("x-accel-buffering", "no");
  }
  headers.set("x-request-id", requestId);
  return headers;
}

async function problemCode(response: Response): Promise<string | null> {
  if (!(response.headers.get("content-type") ?? "").includes("json")) return null;
  try {
    const body = (await response.clone().json()) as { code?: unknown };
    return typeof body.code === "string" ? body.code : null;
  } catch {
    return null;
  }
}

function loginUrl(kind: SessionKind): string {
  if (kind === "operator") return "/bff/auth/platform/login";
  // A support session is restarted from the admin panel (it needs the grant).
  if (kind === "support") return "/signed-out?kind=support";
  return "/bff/auth/login";
}

function sessionEnded(requestId: string, kind: SessionKind): Response {
  return problem(requestId, 401, "unauthenticated", "Sign in to continue", {
    detail: "Your session has ended. Sign in again.",
    login_url: loginUrl(kind),
  });
}

/** The path as the API will route it (percent-decoded); null when an escape is malformed. */
function decodedPath(path: string): string | null {
  try {
    return decodeURIComponent(path);
  } catch {
    return null;
  }
}

async function resolveSession(
  request: Request,
  runtime: AuthRuntime,
  kind: SessionKind,
  requestId: string,
  touch: boolean,
): Promise<Session | Response> {
  const own =
    kind === "staff"
      ? await readSchoolSession(request, runtime, { touch })
      : await readSession(request, runtime, kind, { touch });
  if (own) return own.session;
  const other =
    kind === "operator"
      ? await readSchoolSession(request, runtime, { touch: false })
      : await readSession(request, runtime, "operator", { touch: false });
  if (other) {
    return problem(requestId, 403, "wrong_session", "Not available with this sign-in", {
      detail:
        kind === "operator"
          ? "The platform admin panel needs an operator sign-in."
          : "Platform operators cannot use the school console.",
    });
  }
  return sessionEnded(requestId, kind);
}

export async function proxyToApi(request: Request, runtime: AuthRuntime): Promise<Response> {
  const requestId = requestIdFrom(request.headers);
  const method = request.method.toUpperCase();
  const url = new URL(request.url);
  const rawPath = url.pathname;
  if (!rawPath.startsWith(`${BFF_PREFIX}/api/v1/`) || SUSPICIOUS_PATH.test(rawPath)) {
    return problem(requestId, 400, "bad_request", "Bad request");
  }
  const apiPath = rawPath.slice(BFF_PREFIX.length);
  // The API routes on the percent-decoded path (/api/v1/%65dge/... is /api/v1/edge/...), so
  // the machine-only and control-plane checks look at the decoded path too.
  const routed = decodedPath(apiPath);
  if (routed === null) return problem(requestId, 400, "bad_request", "Bad request");
  if (routed === "/api/v1/edge" || routed.startsWith("/api/v1/edge/")) {
    return problem(requestId, 404, "not_found", "Not found");
  }
  const isPlatform = routed === "/api/v1/platform" || routed.startsWith("/api/v1/platform/");
  const kind: SessionKind = isPlatform ? "operator" : "staff";
  if (isPlatform && !runtime.config.platformEnabled) {
    return problem(requestId, 404, "not_found", "Not found");
  }

  const passive = method === "GET" && request.headers.get(PASSIVE_HEADER) === "1";
  const resolved = await resolveSession(request, runtime, kind, requestId, !passive);
  if (resolved instanceof Response) return resolved;
  let session = resolved;

  if (isUnsafeMethod(method) && !csrfOk(request, session, runtime)) return csrfFailed(requestId);

  let body: Uint8Array | null = null;
  if (method !== "GET" && method !== "HEAD") {
    try {
      body = await readBody(request);
    } catch (error) {
      if (error instanceof BodyTooLargeError) {
        return problem(requestId, 413, "payload_too_large", "Request too large", {
          detail: "Send less data at once (limit 1 MB).",
        });
      }
      throw error;
    }
  }

  let staleAccessToken: string | undefined;
  for (let attempt = 0; attempt < 2; attempt += 1) {
    let accessToken: string;
    try {
      session = await runtime.refresher.ensureFresh(
        session,
        attempt === 0 ? { force: false } : { force: true, staleAccessToken },
      );
      const stored = await runtime.store.tokens(session.id);
      if (!stored) return sessionEnded(requestId, session.kind);
      accessToken = stored.tokens.accessToken;
    } catch (error) {
      if (error instanceof SessionEndedError) return sessionEnded(requestId, session.kind);
      if (error instanceof RefreshBusyError) {
        return problem(
          requestId,
          503,
          "service_unavailable",
          "Try again in a moment",
          {},
          {
            "Retry-After": "1",
          },
        );
      }
      logEvent("token_refresh_error", { kind, request_id: requestId }, "error");
      return problem(requestId, 503, "service_unavailable", "Sign-in service is unavailable");
    }

    let upstream: Response;
    try {
      upstream = await callApi(runtime, {
        session,
        accessToken,
        method,
        path: apiPath,
        search: url.search,
        incomingHeaders: request.headers,
        body,
        requestId,
        signal: request.signal,
      });
    } catch {
      logEvent("api_unreachable", { method, request_id: requestId }, "error");
      return problem(requestId, 502, "bad_gateway", "The server is not responding", {
        detail: "Check your internet connection and try again in a minute.",
      });
    }

    if (
      upstream.status === 401 &&
      attempt === 0 &&
      (await problemCode(upstream)) === "token_expired"
    ) {
      await upstream.body?.cancel();
      staleAccessToken = accessToken;
      continue;
    }

    if (upstream.status === 428) {
      await upstream.body?.cancel();
      const next = nextFromReferer(
        request.headers.get("referer"),
        runtime.config.appBaseUrl.origin,
        session.kind,
      );
      const stepUpPath = {
        operator: "/bff/auth/platform/step-up",
        staff: "/bff/auth/step-up",
        support: "/bff/auth/support/step-up",
      }[session.kind];
      return problem(requestId, 428, "step_up_required", "Confirm it's you", {
        detail: "For your security, sign in again to continue.",
        step_up_url: `${stepUpPath}?next=${encodeURIComponent(next)}`,
      });
    }

    if (upstream.ok && method === "GET" && apiPath === "/api/v1/me" && session.kind === "staff") {
      // The school's idle timeout follows its settings (FR-TEN-012, FR-IAM-003), as for the
      // server-rendered pages: a changed setting applies on the next /me read, without a
      // reload. A failure keeps the session's current timeout.
      const me: unknown = await upstream
        .clone()
        .json()
        .catch(() => null);
      await applySchoolSettingsFromMe(runtime.store, session, me).catch(() => null);
    }

    return new Response(upstream.body, {
      status: upstream.status,
      statusText: upstream.statusText,
      headers: responseHeaders(upstream, requestId),
    });
  }
  return sessionEnded(requestId, session.kind);
}
