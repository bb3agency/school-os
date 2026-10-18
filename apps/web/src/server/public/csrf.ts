import "server-only";
import { createHmac, timingSafeEqual } from "node:crypto";
import { publicOrigins } from "@/lib/public-surface";

/**
 * CSRF for the parent pages' two forms (`/p/pay`, `/p/optin`; R2-3H, docs/09 §4 "Public": "CSRF
 * token bound to the cookie"). The token is an HMAC of the parent cookie's token under a key
 * derived from the BFF's service-token key, so it needs no storage and is worthless without the
 * cookie. A post is accepted only with the parent cookie (SameSite=Lax: not sent on cross-site
 * posts), a matching token, and no sign of another site (`sameOrigin`).
 */

const LABEL = "sos-parent-csrf";

export function parentCsrf(serviceTokenKey: Uint8Array, cookieToken: string): string {
  const key = createHmac("sha256", serviceTokenKey).update(LABEL).digest();
  return createHmac("sha256", key).update(cookieToken).digest("base64url");
}

export function csrfMatches(
  serviceTokenKey: Uint8Array,
  cookieToken: string,
  given: unknown,
): boolean {
  if (typeof given !== "string" || given.length === 0 || given.length > 64) return false;
  const expected = Buffer.from(parentCsrf(serviceTokenKey, cookieToken));
  const actual = Buffer.from(given);
  return actual.length === expected.length && timingSafeEqual(actual, expected);
}

/**
 * The request comes from a page of the public origin (not another site). The parent pages send
 * `Referrer-Policy: no-referrer`, so browsers post their forms with `Origin: null`; then
 * `Sec-Fetch-Site` decides, and old WebViews that send neither rely on the SameSite cookie and
 * the CSRF token alone. A foreign Origin or a cross-site fetch is always refused.
 */
export function sameOrigin(headers: Headers): boolean {
  const origins = publicOrigins();
  if (!origins) return false;
  const site = headers.get("sec-fetch-site");
  if (site !== null && site !== "same-origin") return false;
  const origin = headers.get("origin");
  if (origin === null || origin === "null") return true;
  try {
    return new URL(origin).host.toLowerCase() === origins.publicHost;
  } catch {
    return false;
  }
}
