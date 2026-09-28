import "server-only";
import type { SessionKind } from "@/server/config";

/**
 * Session and sign-in cookies (docs/07 §5.1–5.2, docs/16 §2).
 *
 * https deployments: `__Host-` prefix, `Secure; HttpOnly; SameSite=Lax; Path=/`, no Domain.
 * Plain-http loopback runs (local dev only; loadAuthConfig refuses http anywhere else):
 * the same names without the `__Host-` prefix and without `Secure`, because browsers drop
 * Secure cookies on http. Session cookies have no Max-Age: closing the browser on a shared
 * office PC ends them, and the server enforces the idle and absolute timeouts.
 */

const SESSION_COOKIE: Record<SessionKind, string> = {
  staff: "sos_session",
  operator: "sos_platform_session",
  support: "sos_support_session",
};
const TRANSACTION_COOKIE: Record<SessionKind, string> = {
  staff: "sos_auth_tx",
  operator: "sos_platform_auth_tx",
  support: "sos_support_auth_tx",
};

export function sessionCookieName(kind: SessionKind, secure: boolean): string {
  return secure ? `__Host-${SESSION_COOKIE[kind]}` : SESSION_COOKIE[kind];
}

export function transactionCookieName(kind: SessionKind, secure: boolean): string {
  return secure ? `__Host-${TRANSACTION_COOKIE[kind]}` : TRANSACTION_COOKIE[kind];
}

export interface CookieOptions {
  secure: boolean;
  /** Seconds; omitted for browser-session cookies. 0 deletes the cookie. */
  maxAge?: number;
}

const COOKIE_VALUE = /^[A-Za-z0-9._~-]*$/;

export function serializeCookie(name: string, value: string, options: CookieOptions): string {
  if (!COOKIE_VALUE.test(value)) throw new Error("cookie value has unsupported characters");
  const parts = [`${name}=${value}`, "Path=/", "HttpOnly", "SameSite=Lax"];
  if (options.secure) parts.push("Secure");
  if (options.maxAge !== undefined) {
    parts.push(`Max-Age=${Math.max(0, Math.floor(options.maxAge))}`);
    if (options.maxAge <= 0) parts.push("Expires=Thu, 01 Jan 1970 00:00:00 GMT");
  }
  return parts.join("; ");
}

export function clearCookie(name: string, secure: boolean): string {
  return serializeCookie(name, "", { secure, maxAge: 0 });
}

/** Parse a Cookie request header. First occurrence wins (as in browsers' ordering). */
export function parseCookies(header: string | null): Map<string, string> {
  const cookies = new Map<string, string>();
  if (!header) return cookies;
  for (const part of header.split(";")) {
    const index = part.indexOf("=");
    if (index <= 0) continue;
    const name = part.slice(0, index).trim();
    const value = part.slice(index + 1).trim();
    if (name && !cookies.has(name)) cookies.set(name, value);
  }
  return cookies;
}
