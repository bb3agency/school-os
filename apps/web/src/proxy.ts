import createIntlMiddleware from "next-intl/middleware";
import { NextResponse, type NextRequest } from "next/server";
import { legacyLocalePath, teluguEnabled, uiLocale } from "@/i18n/languages";
import { englishRouting, LOCALE_COOKIE, routing } from "@/i18n/routing";
import {
  applySecurityHeaders,
  buildContentSecurityPolicy,
  generateNonce,
  isHttpsDeployment,
} from "@/lib/security-headers";

// ADR-0036: English and Telugu when SOS_TELUGU_ENABLED is on, English only otherwise.
const handleBilingualRouting = createIntlMiddleware(routing);
const handleEnglishRouting = createIntlMiddleware(englishRouting);

/** Same name as PATH_HEADER in src/server/session/rsc.ts (kept here: no server-only import). */
const PATH_HEADER = "x-sos-path";

/** Paths that are not localised: health check and the BFF (docs/09 §1). */
const NON_LOCALISED_PREFIXES = ["/bff/", "/healthz"] as const;

/**
 * BFF paths that serve an API page with its own strict Content-Security-Policy (FR-CR-005:
 * the correction memo allows exactly one hashed style block). The page-wide policy would
 * block that style, and Next.js keeps the header set here over the route's, so these paths
 * get every other security header here and their CSP from the BFF handler, which keeps the
 * API's policy only when it denies everything by default (server/bff/proxy.ts).
 */
const OWN_CSP_PATHS: readonly RegExp[] = [
  /^\/bff\/api\/v1\/change-requests\/[0-9a-f-]{36}\/memo$/i,
  // FR-CERT-011, FR-REG-004: certificate and register print views (hashed style blocks only).
  /^\/bff\/api\/v1\/certificates\/[0-9a-f-]{36}\/print$/i,
  /^\/bff\/api\/v1\/registers\/(transfer-certificates|certificates|admission-withdrawal)$/i,
  // FR-DQ-045 (ADR-0040): parent verification slips of board and portal readiness.
  /^\/bff\/api\/v1\/dq\/readiness\/[a-z0-9][a-z0-9-]{0,63}\/slips$/i,
];

function isNonLocalised(pathname: string): boolean {
  return NON_LOCALISED_PREFIXES.some(
    (prefix) => pathname === prefix.replace(/\/$/, "") || pathname.startsWith(prefix),
  );
}

/**
 * An old URL with a locale prefix (`/en/x`, `/te/x`): 308 to the same path without it, query
 * kept (product owner 2026-09-30: no URL carries a locale; ADR-0036 note). While Telugu is on
 * the old prefix still names a language, so it is stored in the language cookie first; while
 * it is off the redirect sets nothing (English whatever the old link said). The target is
 * built on this request's own URL with a single leading slash (`legacyLocalePath`), so
 * `/en//evil.example` stays on this site.
 */
function redirectLegacyPrefix(request: NextRequest): NextResponse | null {
  const legacy = legacyLocalePath(request.nextUrl.pathname);
  if (!legacy) return null;
  const target = request.nextUrl.clone();
  target.pathname = legacy.pathname;
  const response = NextResponse.redirect(target, 308);
  if (teluguEnabled()) {
    const { name, ...options } = LOCALE_COOKIE;
    response.cookies.set(name, uiLocale(legacy.locale), { ...options, path: "/" });
  }
  return response;
}

/**
 * Locale routing (NFR-I18N-001) with no prefix in any URL: next-intl rewrites `/x` to the
 * internal `/<locale>/x`, the language taken from the NEXT_LOCALE cookie, then
 * Accept-Language. With Telugu switched off (ADR-0036) only English is negotiated.
 */
function localise(request: NextRequest): NextResponse {
  const legacy = redirectLegacyPrefix(request);
  if (legacy) return legacy;
  return teluguEnabled() ? handleBilingualRouting(request) : handleEnglishRouting(request);
}

/**
 * Next.js 16 proxy (formerly middleware): per-request CSP nonce + security headers
 * (SEC-010) and locale negotiation without URL prefixes (NFR-I18N-001).
 */
export function proxy(request: NextRequest): NextResponse {
  const nonce = generateNonce();
  const https = isHttpsDeployment(process.env.APP_BASE_URL);
  const csp = buildContentSecurityPolicy({
    nonce,
    isDev: process.env.NODE_ENV === "development",
    filesOrigin: process.env.FILES_ORIGIN,
    upgradeInsecureRequests: https,
  });

  // Next.js reads the nonce from the request's CSP header while rendering. next-intl
  // forwards request.headers on its rewrite/next responses, so set them here first.
  request.headers.set("x-nonce", nonce);
  request.headers.set("Content-Security-Policy", csp);
  // Current path for the sign-in return address (layouts cannot see the URL). Always
  // overwritten here, never taken from the client; the login route re-validates it.
  request.headers.set(PATH_HEADER, `${request.nextUrl.pathname}${request.nextUrl.search}`);

  const response = isNonLocalised(request.nextUrl.pathname)
    ? NextResponse.next({ request: { headers: request.headers } })
    : localise(request);

  applySecurityHeaders(response.headers, { csp, hsts: https });
  if (OWN_CSP_PATHS.some((pattern) => pattern.test(request.nextUrl.pathname))) {
    response.headers.delete("Content-Security-Policy");
  }
  return response;
}

export const config = {
  matcher: [
    // Everything except Next.js internals and files with an extension (static assets).
    "/((?!_next/static|_next/image|_vercel|.*\\..*).*)",
    // BFF paths may contain dots (e.g. file names) and still need headers.
    "/bff/:path*",
  ],
};
