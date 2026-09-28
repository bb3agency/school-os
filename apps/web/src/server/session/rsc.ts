import "server-only";
import { cookies, headers } from "next/headers";
import { redirect } from "next/navigation";
import { routing, type Locale } from "@/i18n/routing";
import { isHttpsDeployment } from "@/lib/security-headers";
import { callApi } from "@/server/bff/upstream";
import type { SessionKind } from "@/server/config";
import { getAuthRuntime } from "@/server/runtime";
import { sessionCookieName } from "./cookies";
import { applySchoolSettingsFromMe } from "./school-settings";
import type { Session } from "./store";

/**
 * Session helpers for React Server Components (layouts and pages).
 *
 * They return a `SessionView`: only what the UI needs. Never tokens, never the CSRF token
 * or session id, so nothing secret can end up in the RSC payload (SEC-004).
 */

export interface SessionView {
  kind: SessionKind;
  displayName: string | null;
  activeTenantId: string | null;
  mfa: boolean;
  idleExpiresAt: number;
  absoluteExpiresAt: number;
}

export function toSessionView(session: Session): SessionView {
  return {
    kind: session.kind,
    displayName: session.displayName,
    activeTenantId: session.activeTenantId,
    mfa: session.mfa,
    idleExpiresAt: session.idleExpiresAt,
    absoluteExpiresAt: session.absoluteExpiresAt,
  };
}

/** Request header set by src/proxy.ts with the current path (for the sign-in return). */
export const PATH_HEADER = "x-sos-path";

/** The signed-in session of this kind, or null. Counts as activity (slides idle timeout). */
export async function getSession(kind: SessionKind): Promise<SessionView | null> {
  const secure = isHttpsDeployment(process.env.APP_BASE_URL);
  const value = (await cookies()).get(sessionCookieName(kind, secure))?.value;
  // No cookie: no need to reach Valkey (or even have it configured).
  if (!value) return null;
  const runtime = await getAuthRuntime();
  const session = await runtime.store.load(value, { touch: true });
  return session && session.kind === kind ? toSessionView(session) : null;
}

/** The bare school home: "/", "/en", "/te" (optional trailing slash; the query is ignored). */
const SCHOOL_HOME = /^\/(?:(en|te)\/?)?$/;

/**
 * The locale when `path` (pathname plus optional query, as in PATH_HEADER) is exactly the
 * school home, otherwise null. "/" has no locale yet: the default one.
 */
export function isSchoolHomePath(path: string): Locale | null {
  const match = SCHOOL_HOME.exec(path.split("?", 1)[0] ?? "");
  if (!match) return null;
  return (match[1] as Locale | undefined) ?? routing.defaultLocale;
}

async function requireSession(kind: SessionKind): Promise<SessionView> {
  // The school console also runs as a SchoolOS support session (break-glass, ADR-0023).
  const session =
    (await getSession(kind)) ?? (kind === "staff" ? await getSession("support") : null);
  if (session) return session;
  const path = (await headers()).get(PATH_HEADER) ?? "/";
  // A signed-out visitor to the school home sees the public product page first; deep
  // links still go straight to sign-in and come back (FR-IAM-001).
  const home = kind === "staff" ? isSchoolHomePath(path) : null;
  if (home) redirect(`/${home}/welcome`);
  const login = kind === "operator" ? "/bff/auth/platform/login" : "/bff/auth/login";
  // The login route validates `next` again (same-origin paths of the right kind only).
  redirect(`${login}?next=${encodeURIComponent(path)}`);
}

/** School console pages: a staff session (or a SchoolOS support session), or off to staff
 * sign-in. */
export function requireStaff(): Promise<SessionView> {
  return requireSession("staff");
}

/** Platform admin panel: an operator session, or off to operator sign-in. */
export function requireOperator(): Promise<SessionView> {
  return requireSession("operator");
}

/** SOS_DEPLOYMENT_MODE=dedicated switches the control plane off (contract §1). */
export function platformEnabled(): boolean {
  return (process.env.SOS_DEPLOYMENT_MODE ?? "shared").trim() !== "dedicated";
}

export interface RscApiResult<T> {
  status: number;
  data: T | null;
  code: string | null;
}

/**
 * GET an API route as the signed-in user from a Server Component (layouts use it for the
 * shell: effective permissions, number of schools). Same path as the BFF proxy (fresh access
 * token + service token + X-Active-Tenant); the result is data only, never tokens.
 * Returns null when there is no session or the API cannot be reached.
 */
export async function apiGetAsSession<T>(
  kind: SessionKind,
  path: `/api/v1/${string}`,
): Promise<RscApiResult<T> | null> {
  const secure = isHttpsDeployment(process.env.APP_BASE_URL);
  const jar = await cookies();
  let effective = kind;
  let value = jar.get(sessionCookieName(kind, secure))?.value;
  if (!value && kind === "staff") {
    // No staff session: the school console may run as a support session (ADR-0023).
    effective = "support";
    value = jar.get(sessionCookieName("support", secure))?.value;
  }
  if (!value) return null;
  try {
    const runtime = await getAuthRuntime();
    const current = await runtime.store.load(value, { touch: false });
    if (!current || current.kind !== effective) return null;
    const session = await runtime.refresher.ensureFresh(current);
    const stored = await runtime.store.tokens(session.id);
    if (!stored) return null;
    const response = await callApi(runtime, {
      session,
      accessToken: stored.tokens.accessToken,
      method: "GET",
      path,
      requestId: `req_${crypto.randomUUID()}`,
      headersTimeoutMs: 5_000,
    });
    let body: unknown = null;
    if ((response.headers.get("content-type") ?? "").includes("json")) {
      body = await response.json().catch(() => null);
    } else {
      await response.body?.cancel();
    }
    if (response.ok && path === "/api/v1/me") {
      // The school's idle timeout follows its settings (FR-TEN-012, FR-IAM-003).
      // A failure keeps the session's current timeout (the default after a school switch).
      await applySchoolSettingsFromMe(runtime.store, session, body).catch(() => null);
    }
    const code =
      body && typeof body === "object" && typeof (body as { code?: unknown }).code === "string"
        ? (body as { code: string }).code
        : null;
    return { status: response.status, data: response.ok ? (body as T) : null, code };
  } catch {
    return null;
  }
}
