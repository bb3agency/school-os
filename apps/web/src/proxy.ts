import createIntlMiddleware from "next-intl/middleware";
import { NextResponse, type NextRequest } from "next/server";
import { routing } from "@/i18n/routing";
import {
  applySecurityHeaders,
  buildContentSecurityPolicy,
  generateNonce,
  isHttpsDeployment,
} from "@/lib/security-headers";

const handleI18nRouting = createIntlMiddleware(routing);

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
const OWN_CSP_PATHS: readonly RegExp[] = [/^\/bff\/api\/v1\/change-requests\/[0-9a-f-]{36}\/memo$/i];

function isNonLocalised(pathname: string): boolean {
  return NON_LOCALISED_PREFIXES.some(
    (prefix) => pathname === prefix.replace(/\/$/, "") || pathname.startsWith(prefix),
  );
}

/**
 * Next.js 16 proxy (formerly middleware): per-request CSP nonce + security headers
 * (SEC-010) and locale negotiation/prefix routing (NFR-I18N-001).
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
    : handleI18nRouting(request);

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
