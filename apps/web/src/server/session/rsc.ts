import "server-only";
import { cookies, headers } from "next/headers";
import { redirect } from "next/navigation";
import { isHttpsDeployment } from "@/lib/security-headers";
import type { SessionKind } from "@/server/config";
import { getAuthRuntime } from "@/server/runtime";
import { sessionCookieName } from "./cookies";
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

async function requireSession(kind: SessionKind): Promise<SessionView> {
  const session = await getSession(kind);
  if (session) return session;
  const path = (await headers()).get(PATH_HEADER) ?? "/";
  const login = kind === "operator" ? "/bff/auth/platform/login" : "/bff/auth/login";
  // The login route validates `next` again (same-origin paths of the right kind only).
  redirect(`${login}?next=${encodeURIComponent(path)}`);
}

/** School console pages: a staff session, or off to staff sign-in. */
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
