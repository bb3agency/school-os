import "server-only";
import type { Session, SessionStore } from "./store";

/**
 * The school settings the BFF applies to a staff session, read from the API's `MeOut`
 * (GET /me and POST /me/active-tenant; FR-TEN-012, FR-IAM-003). Only the idle timeout is
 * enforced here; date format and languages are display settings the pages read from /me.
 */

function objectOf(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" ? (value as Record<string, unknown>) : null;
}

/**
 * `settings.idle_timeout_minutes` from a MeOut body, but only when the body is about
 * `tenantId` (a stale or mismatched answer never sets another school's timeout). Returns null
 * when unknown: the store then uses the default. The store clamps the value to 5–30 minutes.
 */
export function schoolIdleTimeoutFromMe(body: unknown, tenantId: string): number | null {
  const me = objectOf(body);
  if (!me || typeof me.tenant_id !== "string") return null;
  if (me.tenant_id.toLowerCase() !== tenantId.toLowerCase()) return null;
  const minutes = objectOf(me.settings)?.idle_timeout_minutes;
  return typeof minutes === "number" && Number.isFinite(minutes) ? minutes : null;
}

/**
 * After a successful GET /me for a staff session: apply the school's idle timeout when the
 * answer is about the session's active school. Returns the updated session, or null when
 * nothing was applied (or the session ended because it was idle longer than the new value).
 */
export async function applySchoolSettingsFromMe(
  store: SessionStore,
  session: Pick<Session, "id" | "kind" | "activeTenantId">,
  body: unknown,
): Promise<Session | null> {
  if (session.kind !== "staff" || !session.activeTenantId) return null;
  const minutes = schoolIdleTimeoutFromMe(body, session.activeTenantId);
  if (minutes === null) return null;
  return store.applySchoolIdleTimeout(session, session.activeTenantId, minutes);
}
