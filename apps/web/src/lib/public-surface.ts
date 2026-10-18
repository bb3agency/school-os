/**
 * The public parent-page surface (R2; ADR-0046, ADR-0047; FR-PUB-001, FR-PUB-010; SEC-036;
 * docs/07 §11.2, docs/10 §11.2).
 *
 * Parent link pages live in the `(public)` route group under `/p/*` and are served only on
 * `PUBLIC_BASE_URL` (for example https://my.schoolos.in), a different origin from the staff app:
 * no staff cookie is ever sent there and the staff routes never answer there. On the staff host
 * `/p/*` is a 404. Locally (`next dev`, plain http on a loopback host) `PUBLIC_BASE_URL` may be
 * the staff origin itself, and both surfaces share it (docs/10 §11.2 "Local development").
 *
 * Pure module (no `server-only`): the Next.js proxy imports it.
 */

/**
 * The public pages: `/p`, `/p/view`, `/p/expired`, `/p/<token>`, `/p/fonts/telugu/<file>`, and
 * (R2-3H) `/p/document` (the PDF redirect), `/p/pay` and `/p/optin` (form posts), `/p/paid`
 * (Razorpay's return page).
 */
export const PUBLIC_PREFIX = "/p";
const PUBLIC_PATH = /^\/p(?:\/|$)/;
/** Paths the `(public)` route group really serves; anything else under /p is the expired page. */
const SERVED = [
  /^\/p\/view$/,
  /^\/p\/expired$/,
  /^\/p\/(?:document|pay|paid|optin)$/,
  /^\/p\/[A-Za-z0-9_-]{1,128}$/,
  /^\/p\/fonts\/telugu\/[A-Za-z0-9._-]{1,80}$/,
];
/** A parent link token: 32 random bytes, base64url without padding (ADR-0046 decision 3). */
export const PARENT_TOKEN = /^[A-Za-z0-9_-]{43}$/;
/** The cookie that carries the token after the exchange (FR-PUB-010). */
export const PARENT_COOKIE = "sos_parent";
export const PUBLIC_EXPIRED_PATH = "/p/expired";
export const PUBLIC_VIEW_PATH = "/p/view";
export const PUBLIC_PAID_PATH = "/p/paid";

/**
 * Where "Pay online" may send a parent (R2-3H; FR-PUB-010): Razorpay's hosted payment pages
 * (the short link on rzp.io, the page on razorpay.com). The same list as `pay_redirect_hosts`
 * in apps/api/app/public/config.yaml; `rzp.example.test` (the fake provider) only in
 * development. They are the only third-party origins in the public CSP (`form-action`, because
 * the pay form's answer is a 303 to them).
 */
export const PAY_HOSTS = ["rzp.io", "razorpay.com"] as const;
const DEV_PAY_HOSTS = ["rzp.example.test"] as const;

export function payHosts(isDev: boolean): readonly string[] {
  return isDev ? [...PAY_HOSTS, ...DEV_PAY_HOSTS] : PAY_HOSTS;
}

/** An https URL on one of the pay hosts (or a subdomain of one). */
export function isPayUrl(value: string, isDev: boolean): boolean {
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    return false;
  }
  if (url.protocol !== "https:" || url.username || url.password || url.port) return false;
  const host = url.hostname.toLowerCase();
  return payHosts(isDev).some((h) => host === h || host.endsWith(`.${h}`));
}

const LOOPBACK_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]"]);

type Env = Readonly<Record<string, string | undefined>>;

export interface PublicOrigins {
  /** The public origin's host (host[:port]), lower case. */
  publicHost: string;
  /** The staff app's host, lower case (null when APP_BASE_URL is unusable). */
  appHost: string | null;
  /** Local only: both surfaces on one loopback http origin. */
  shared: boolean;
  /** https: `__Host-` cookie with Secure; http (loopback only): plain name, no Secure. */
  secure: boolean;
}

function parseOrigin(value: string | undefined): URL | null {
  if (!value) return null;
  try {
    const url = new URL(value.trim());
    if (url.pathname !== "/" || url.search || url.hash || url.username || url.password) {
      return null;
    }
    return url;
  } catch {
    return null;
  }
}

/**
 * `PUBLIC_BASE_URL` checked: an https origin, or plain http on a loopback host (local runs).
 * Anything else (a path, another scheme, http on a real host) switches the public pages off.
 */
export function publicOrigins(env: Env = process.env): PublicOrigins | null {
  const pub = parseOrigin(env.PUBLIC_BASE_URL);
  if (!pub) return null;
  const loopback = LOOPBACK_HOSTS.has(pub.hostname.toLowerCase());
  if (pub.protocol !== "https:" && !(pub.protocol === "http:" && loopback)) return null;
  const app = parseOrigin(env.APP_BASE_URL);
  const publicHost = pub.host.toLowerCase();
  const appHost = app ? app.host.toLowerCase() : null;
  const shared = appHost === publicHost;
  // One origin for both surfaces only on a local loopback http run.
  if (shared && !(pub.protocol === "http:" && loopback)) return null;
  return { publicHost, appHost, shared, secure: pub.protocol === "https:" };
}

export function isPublicPath(pathname: string): boolean {
  return PUBLIC_PATH.test(pathname);
}

// --- R2-1A: certificate verification on VERIFY_BASE_URL (ADR-0045 decision 9; D29) -----------

/** The verification pages: `/v` (the code box), `/v/check` (its form), `/v/<code>`. */
export const VERIFY_PREFIX = "/v";
const VERIFY_PATH = /^\/v(?:\/|$)/;
const VERIFY_SERVED = [/^\/v$/, /^\/v\/check$/, /^\/v\/[A-Za-z0-9-]{1,64}$/];
/** Where an unknown /v path is sent: the uniform "not found" answer of a code page. */
export const VERIFY_UNKNOWN_PATH = "/v/unknown";

export interface VerifyOrigin {
  /** The verification host (host[:port]), lower case. */
  verifyHost: string;
  /** Local only: the verification pages on the staff app's own loopback http origin. */
  sharedWithApp: boolean;
  secure: boolean;
}

/**
 * `VERIFY_BASE_URL` checked like `PUBLIC_BASE_URL`: an https origin, or plain http on a
 * loopback host. It may equal `PUBLIC_BASE_URL` (a dedicated school's own host); it may equal
 * the staff origin only on a local http run. Anything else switches verification off.
 */
export function verifyOrigin(env: Env = process.env): VerifyOrigin | null {
  const url = parseOrigin(env.VERIFY_BASE_URL);
  if (!url) return null;
  const loopback = LOOPBACK_HOSTS.has(url.hostname.toLowerCase());
  if (url.protocol !== "https:" && !(url.protocol === "http:" && loopback)) return null;
  const app = parseOrigin(env.APP_BASE_URL);
  const verifyHost = url.host.toLowerCase();
  const sharedWithApp = app !== null && app.host.toLowerCase() === verifyHost;
  if (sharedWithApp && !(url.protocol === "http:" && loopback)) return null;
  return { verifyHost, sharedWithApp, secure: url.protocol === "https:" };
}

export function isVerifyPath(pathname: string): boolean {
  return VERIFY_PATH.test(pathname);
}

// --- end R2-1A ----------------------------------------------------------------------------------

export type SurfaceRoute =
  | { kind: "staff" }
  | { kind: "public"; served: boolean }
  /** R2-1A: the certificate verification pages on VERIFY_BASE_URL. */
  | { kind: "verify"; served: boolean }
  /** Nothing here: a public page on the staff host, or a staff page on the public host. */
  | { kind: "not_found" };

/** Which surface answers this request (host from the Host header, lower case). */
export function routeSurface(
  host: string | null,
  pathname: string,
  env: Env = process.env,
): SurfaceRoute {
  const origins = publicOrigins(env);
  const verify = verifyOrigin(env);
  const lowerHost = (host ?? "").toLowerCase();
  const onVerifyHost = verify !== null && lowerHost === verify.verifyHost;
  if (isVerifyPath(pathname)) {
    return onVerifyHost
      ? { kind: "verify", served: VERIFY_SERVED.some((p) => p.test(pathname)) }
      : { kind: "not_found" };
  }
  // A verification host of its own serves nothing else (D29).
  if (onVerifyHost && !verify.sharedWithApp && lowerHost !== origins?.publicHost) {
    return { kind: "not_found" };
  }
  const publicPath = isPublicPath(pathname);
  const onPublicHost = origins !== null && lowerHost === origins.publicHost;
  if (onPublicHost && origins.shared) {
    return publicPath
      ? { kind: "public", served: SERVED.some((p) => p.test(pathname)) }
      : { kind: "staff" };
  }
  if (onPublicHost) {
    return publicPath
      ? { kind: "public", served: SERVED.some((p) => p.test(pathname)) }
      : { kind: "not_found" };
  }
  return publicPath ? { kind: "not_found" } : { kind: "staff" };
}

/** The parent cookie's name: `__Host-sos_parent` on https, `sos_parent` on a loopback http run. */
export function parentCookieName(secure: boolean): string {
  return secure ? `__Host-${PARENT_COOKIE}` : PARENT_COOKIE;
}

/**
 * The public pages' Content-Security-Policy: our own origin only, no third-party scripts,
 * fonts or analytics, no framing (docs/07 §11.2). Razorpay's hosted page is reached by a plain
 * navigation (the pay form's 303, R2-3H), never embedded or scripted: its hosts are allowed in
 * `form-action` only.
 */
export function buildPublicCsp(options: {
  nonce: string;
  isDev: boolean;
  https: boolean;
  /** Allow the pay hosts in form-action (the parent pages; not the verification pages). */
  pay?: boolean;
}): string {
  const formAction =
    (options.pay ?? true)
      ? `form-action 'self' ${payHosts(options.isDev)
          .flatMap((h) => [`https://${h}`, `https://*.${h}`])
          .join(" ")}`
      : "form-action 'self'";
  const directives = [
    "default-src 'self'",
    `script-src 'self' 'nonce-${options.nonce}' 'strict-dynamic'${options.isDev ? " 'unsafe-eval'" : ""}`,
    `style-src 'self'${options.isDev ? " 'unsafe-inline'" : ""}`,
    "img-src 'self' data:",
    "font-src 'self'",
    `connect-src 'self'${options.isDev ? " ws: wss:" : ""}`,
    "object-src 'none'",
    "base-uri 'none'",
    "frame-ancestors 'none'",
    formAction,
  ];
  if (options.https) directives.push("upgrade-insecure-requests");
  return directives.join("; ");
}

/**
 * Headers on every public response (FR-PUB-010, SEC-036): no referrer (the token must not leak
 * through a Referer), nothing cached anywhere, never indexed.
 */
export function publicSecurityHeaders(csp: string, hsts: boolean): Record<string, string> {
  const headers: Record<string, string> = {
    "Content-Security-Policy": csp,
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "private, no-store",
    "X-Robots-Tag": "noindex, nofollow",
    "X-Content-Type-Options": "nosniff",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
  };
  if (hsts) headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains";
  return headers;
}
