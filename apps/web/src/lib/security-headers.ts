/**
 * Security headers for every page, route handler and BFF response (SEC-010, docs/07 §11).
 *
 * The Content-Security-Policy is built per request with a fresh nonce. Next.js reads the
 * nonce from the request's CSP header during server rendering and adds it to its own
 * scripts, so pages must be dynamically rendered (the locale layout calls `connection()`).
 */

export interface CspOptions {
  /** Base64 nonce, unique per request. */
  nonce: string;
  /** `next dev` needs 'unsafe-eval' (React debugging) and inline styles (dev overlay). */
  isDev: boolean;
  /**
   * Origin that serves presigned file URLs and takes presigned uploads, e.g.
   * https://files.schoolos.example (img-src and connect-src).
   */
  filesOrigin?: string | undefined;
  /** Add `upgrade-insecure-requests` (skip it only for plain-http local runs). */
  upgradeInsecureRequests: boolean;
  /**
   * Meta's Embedded Signup on `/settings/whatsapp` only (R2-1C; ADR-0043; docs/10 §11.2): its SDK
   * from connect.facebook.net, its frames and Graph API calls. Nowhere else.
   */
  embeddedSignup?: boolean;
}

/** The one staff route that may load Meta's SDK (with or without a locale prefix). */
export const EMBEDDED_SIGNUP_PATH = /^(\/(en|te))?\/settings\/whatsapp\/?$/;
const META_SCRIPT = "https://connect.facebook.net";
const META_FRAMES = "https://www.facebook.com https://web.facebook.com";
const META_CONNECT = "https://graph.facebook.com https://www.facebook.com";

const NONCE_BYTES = 16;

/** 128 bits from the platform CSPRNG, base64 encoded. */
export function generateNonce(): string {
  const bytes = new Uint8Array(NONCE_BYTES);
  crypto.getRandomValues(bytes);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}

/** Hosts a plain-http files origin may use under `next dev` (local SeaweedFS, docs/10). */
const LOOPBACK_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]"]);

/**
 * Only accept an https origin (scheme + host [+ port]); anything else is ignored. With
 * `allowLoopbackHttp` (next dev only) a plain-http origin on a loopback host is accepted too,
 * so local uploads to SeaweedFS work; production never accepts http.
 */
export function normaliseFilesOrigin(
  value: string | undefined,
  { allowLoopbackHttp = false }: { allowLoopbackHttp?: boolean } = {},
): string | undefined {
  if (!value) return undefined;
  try {
    const url = new URL(value);
    if (url.protocol === "https:") return url.origin;
    if (url.protocol === "http:" && allowLoopbackHttp && LOOPBACK_HOSTS.has(url.hostname)) {
      return url.origin;
    }
    return undefined;
  } catch {
    return undefined;
  }
}

export function buildContentSecurityPolicy(options: CspOptions): string {
  const { nonce, isDev } = options;
  const meta = options.embeddedSignup === true;
  const filesOrigin = normaliseFilesOrigin(options.filesOrigin, { allowLoopbackHttp: isDev });
  const directives: string[] = [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${meta ? ` ${META_SCRIPT}` : ""}${isDev ? " 'unsafe-eval'" : ""}`,
    // Production: stylesheets only from our origin (no inline styles, no style attributes).
    `style-src 'self'${isDev ? " 'unsafe-inline'" : ""}`,
    `img-src 'self' data: blob:${filesOrigin ? ` ${filesOrigin}` : ""}`,
    // Files origin (only when configured, https origin only, as for img-src): the browser
    // posts uploads straight to the presigned storage URL (docs/07 §10: presigned POST; the
    // BFF never carries file bodies). Lead decision 2026-09-27, recorded in docs/07 §11.
    // `next dev` uses a websocket for hot reload.
    `connect-src 'self'${filesOrigin ? ` ${filesOrigin}` : ""}${meta ? ` ${META_CONNECT}` : ""}${isDev ? " ws: wss:" : ""}`,
    "font-src 'self'",
    "object-src 'none'",
    "base-uri 'none'",
    ...(meta ? [`frame-src ${META_FRAMES}`] : []),
    "frame-ancestors 'none'",
    "form-action 'self'",
  ];
  if (options.upgradeInsecureRequests) directives.push("upgrade-insecure-requests");
  return directives.join("; ");
}

export interface SecurityHeaderOptions {
  csp: string;
  /** Send HSTS only when the app is served over https. */
  hsts: boolean;
  /**
   * `same-origin-allow-popups` on the Embedded Signup page: Meta's sign-up window must be able
   * to post its result back to the page that opened it (R2-1C). `same-origin` everywhere else.
   */
  allowPopups?: boolean;
}

export function securityHeaders(options: SecurityHeaderOptions): Record<string, string> {
  const headers: Record<string, string> = {
    "Content-Security-Policy": options.csp,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(self), microphone=(), geolocation=(), payment=()",
    "Cross-Origin-Opener-Policy": options.allowPopups ? "same-origin-allow-popups" : "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
  };
  if (options.hsts) {
    headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains; preload";
  }
  return headers;
}

export function applySecurityHeaders(target: Headers, options: SecurityHeaderOptions): void {
  for (const [name, value] of Object.entries(securityHeaders(options))) {
    target.set(name, value);
  }
}

/**
 * The app is treated as https unless APP_BASE_URL explicitly says http (local runs and
 * the Playwright smoke test). Secure by default when the variable is missing.
 */
export function isHttpsDeployment(appBaseUrl: string | undefined): boolean {
  if (!appBaseUrl) return true;
  return !appBaseUrl.trim().toLowerCase().startsWith("http://");
}
