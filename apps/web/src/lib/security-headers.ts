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
  /** Origin that serves presigned file URLs, e.g. https://files.schoolos.example. */
  filesOrigin?: string | undefined;
  /** Add `upgrade-insecure-requests` (skip it only for plain-http local runs). */
  upgradeInsecureRequests: boolean;
}

const NONCE_BYTES = 16;

/** 128 bits from the platform CSPRNG, base64 encoded. */
export function generateNonce(): string {
  const bytes = new Uint8Array(NONCE_BYTES);
  crypto.getRandomValues(bytes);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}

/** Only accept an https origin (scheme + host [+ port]); anything else is ignored. */
export function normaliseFilesOrigin(value: string | undefined): string | undefined {
  if (!value) return undefined;
  try {
    const url = new URL(value);
    if (url.protocol !== "https:") return undefined;
    return url.origin;
  } catch {
    return undefined;
  }
}

export function buildContentSecurityPolicy(options: CspOptions): string {
  const { nonce, isDev } = options;
  const filesOrigin = normaliseFilesOrigin(options.filesOrigin);
  const directives: string[] = [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${isDev ? " 'unsafe-eval'" : ""}`,
    // Production: stylesheets only from our origin (no inline styles, no style attributes).
    `style-src 'self'${isDev ? " 'unsafe-inline'" : ""}`,
    `img-src 'self' data: blob:${filesOrigin ? ` ${filesOrigin}` : ""}`,
    // `next dev` uses a websocket for hot reload. The files origin is deliberately NOT added
    // here (docs/07 §11 says connect-src 'self'); browser uploads to presigned URLs need a
    // documented decision first (open question in the web-findings handoff).
    `connect-src 'self'${isDev ? " ws: wss:" : ""}`,
    "font-src 'self'",
    "object-src 'none'",
    "base-uri 'none'",
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
}

export function securityHeaders(options: SecurityHeaderOptions): Record<string, string> {
  const headers: Record<string, string> = {
    "Content-Security-Policy": options.csp,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(self), microphone=(), geolocation=(), payment=()",
    "Cross-Origin-Opener-Policy": "same-origin",
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
