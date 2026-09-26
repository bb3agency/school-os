import "server-only";
import type { Problem } from "@schoolos/api-client";
import { problemResponse } from "@/lib/problem";
import type { SessionKind } from "@/server/config";
import type { AuthRuntime } from "@/server/runtime";
import { constantTimeEqual } from "@/server/session/crypto";
import { parseCookies, sessionCookieName } from "@/server/session/cookies";
import type { Session } from "@/server/session/store";

/** Response helpers and request checks shared by the BFF route handlers. */

export const CSRF_HEADER = "x-csrf-token";
const ERROR_TYPE = "https://docs.schoolos.example/errors/";
const UNSAFE_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

export function isUnsafeMethod(method: string): boolean {
  return UNSAFE_METHODS.has(method.toUpperCase());
}

export function problem(
  requestId: string,
  status: number,
  code: string,
  title: string,
  extra: Partial<Problem> & Record<string, unknown> = {},
  headers: Record<string, string> = {},
): Response {
  const response = problemResponse(
    { type: `${ERROR_TYPE}${code.replace(/_/g, "-")}`, title, status, code, ...extra },
    requestId,
  );
  for (const [name, value] of Object.entries(headers)) response.headers.append(name, value);
  return response;
}

export function jsonResponse(
  body: unknown,
  init: { status?: number; cookies?: string[]; requestId?: string } = {},
): Response {
  const headers = new Headers({
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store",
  });
  if (init.requestId) headers.set("X-Request-Id", init.requestId);
  for (const cookie of init.cookies ?? []) headers.append("Set-Cookie", cookie);
  return new Response(body === null ? null : JSON.stringify(body), {
    status: init.status ?? 200,
    headers,
  });
}

/** 303 See Other. Relative locations resolve against APP_BASE_URL (never the Host header). */
export function seeOther(runtime: AuthRuntime, location: string, cookies: string[] = []): Response {
  const headers = new Headers({
    Location: new URL(location, runtime.config.appBaseUrl).href,
    "Cache-Control": "no-store",
  });
  for (const cookie of cookies) headers.append("Set-Cookie", cookie);
  return new Response(null, { status: 303, headers });
}

export interface RequestSession {
  session: Session;
  cookieValue: string;
}

export async function readSession(
  request: Request,
  runtime: AuthRuntime,
  kind: SessionKind,
  options: { touch: boolean },
): Promise<RequestSession | null> {
  const cookies = parseCookies(request.headers.get("cookie"));
  const cookieValue = cookies.get(sessionCookieName(kind, runtime.config.secureCookies));
  if (!cookieValue) return null;
  const session = await runtime.store.load(cookieValue, options);
  if (!session || session.kind !== kind) return null;
  return { session, cookieValue };
}

/**
 * CSRF defence for state-changing requests (docs/07 §5.2): synchronizer token in
 * X-CSRF-Token compared in constant time, plus SameSite=Lax cookies and, when the browser
 * sends them, same-origin Origin / Sec-Fetch-Site checks.
 */
export function csrfOk(request: Request, session: Session, runtime: AuthRuntime): boolean {
  const origin = request.headers.get("origin");
  if (origin !== null && origin !== runtime.config.appBaseUrl.origin) return false;
  const site = request.headers.get("sec-fetch-site");
  if (site !== null && site !== "same-origin") return false;
  const token = request.headers.get(CSRF_HEADER);
  if (!token) return false;
  return constantTimeEqual(token, session.csrfToken);
}

export function csrfFailed(requestId: string): Response {
  return problem(
    requestId,
    403,
    "csrf_failed",
    "Security check failed",
    { detail: "Reload the page and try again." },
  );
}

export function kindParam(request: Request): SessionKind {
  return new URL(request.url).searchParams.get("kind") === "operator" ? "operator" : "staff";
}
