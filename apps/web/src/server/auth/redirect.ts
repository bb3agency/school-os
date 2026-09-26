import "server-only";
import type { SessionKind } from "@/server/config";

/**
 * `next` validation for sign-in, step-up and error redirects (open-redirect prevention).
 *
 * Only same-origin relative paths are accepted. Anything else falls back to the kind's
 * home: "/" for school staff, "/en/platform" for operators. Staff cannot be sent into the
 * control plane and operators cannot be sent into the school console.
 */

const MAX_NEXT_LENGTH = 2048;
const PROBE_ORIGIN = "https://next.invalid";
// ASCII control characters, space and backslash are never valid in our paths.
const FORBIDDEN = /[\u0000- \u007f\\]/;
const PLATFORM_PATH = /^\/(en|te)\/platform(\/|$|\?|#)/;

export const DEFAULT_NEXT: Record<SessionKind, string> = {
  staff: "/",
  operator: "/en/platform",
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
  const path = `${url.pathname}${url.search}${url.hash}`;
  // Dot-segment normalisation can turn "/.//evil.example" into "//evil.example".
  if (!path.startsWith("/") || path.startsWith("//")) return fallback;
  // Never bounce into the BFF itself (auth routes, API proxy).
  if (path === "/bff" || path.startsWith("/bff/")) return fallback;
  const isPlatform = PLATFORM_PATH.test(path);
  if (kind === "operator" && !isPlatform) return fallback;
  if (kind === "staff" && isPlatform) return fallback;
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
