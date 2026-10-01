import "server-only";
import { withoutLocalePrefix } from "@/i18n/languages";
import type { SessionKind } from "@/server/config";

/**
 * `next` validation for sign-in, step-up and error redirects (open-redirect prevention).
 *
 * Only same-origin relative paths are accepted. Anything else falls back to the kind's
 * home: "/" for school staff, "/platform" for operators. Staff cannot be sent into the
 * control plane and operators cannot be sent into the school console. No URL carries a locale
 * (ADR-0036 note, 2026-09-30): an old `/en/...` or `/te/...` return address loses its prefix.
 */

const MAX_NEXT_LENGTH = 2048;
const PROBE_ORIGIN = "https://next.invalid";
// ASCII control characters, space and backslash are never valid in our paths.
const FORBIDDEN = /[\u0000- \u007f\\]/;
const PLATFORM_PATH = /^\/platform(\/|$|\?|#)/;

export const DEFAULT_NEXT: Record<SessionKind, string> = {
  staff: "/",
  operator: "/platform",
  support: "/",
};

export function safeNext(value: string | null | undefined, kind: SessionKind): string {
  const fallback = DEFAULT_NEXT[kind];
  if (typeof value !== "string" || value.length === 0 || value.length > MAX_NEXT_LENGTH) {
    return fallback;
  }
  if (!value.startsWith("/") || value.startsWith("//") || FORBIDDEN.test(value)) return fallback;
  let url: URL;
  try {
    url = new URL(value, PROBE_ORIGIN);
  } catch {
    return fallback;
  }
  if (url.origin !== PROBE_ORIGIN) return fallback;
  // An old locale prefix is dropped ("/en/x" → "/x"; "/en//evil.example" → "/evil.example").
  const path = withoutLocalePrefix(`${url.pathname}${url.search}${url.hash}`);
  // Dot-segment normalisation can turn "/.//evil.example" into "//evil.example".
  if (!path.startsWith("/") || path.startsWith("//")) return fallback;
  // Never bounce into the BFF itself (auth routes, API proxy).
  if (path === "/bff" || path.startsWith("/bff/")) return fallback;
  const isPlatform = PLATFORM_PATH.test(path);
  if (kind === "operator" && !isPlatform) return fallback;
  // Support sessions use the school console, never the control plane (ADR-0023).
  if ((kind === "staff" || kind === "support") && isPlatform) return fallback;
  return path;
}

/** Same-origin Referer → safe path (used for the step-up return address). */
export function nextFromReferer(
  referer: string | null,
  appOrigin: string,
  kind: SessionKind,
): string {
  if (!referer) return DEFAULT_NEXT[kind];
  try {
    const url = new URL(referer);
    if (url.origin !== appOrigin) return DEFAULT_NEXT[kind];
    return safeNext(`${url.pathname}${url.search}`, kind);
  } catch {
    return DEFAULT_NEXT[kind];
  }
}
