import "server-only";
import {
  PARENT_TOKEN,
  parentCookieName,
  PUBLIC_EXPIRED_PATH,
  PUBLIC_VIEW_PATH,
  publicOrigins,
} from "@/lib/public-surface";
import { logEvent } from "@/server/log";
import { clearCookie, serializeCookie } from "@/server/session/cookies";
import { callPublicApi, type PublicRuntime } from "./client";

/**
 * `GET /p/{token}`: exchange-and-redirect (ADR-0046 decision 6, FR-PUB-010).
 *
 * The token is checked by the API (`POST /api/v1/public/links/exchange`, service token only).
 * A live link sets the parent cookie (`__Host-sos_parent`: Secure, HttpOnly, SameSite=Lax,
 * Path=/, Max-Age = the link's remaining lifetime) and answers 303 to `/p/view`, so the token
 * leaves the address bar and the history at once. A dead link (unknown, malformed, expired,
 * revoked, suspended school) clears the cookie and answers 303 to `/p/expired`, which shows the
 * same expired page with 404 for all of them. Link preview fetchers that do not follow the
 * redirect see only the 303 and never count as a view (the API counts views on `/p/view`).
 */

const EXCHANGE_PATH = "/api/v1/public/links/exchange";

const PUBLIC_HEADERS = {
  "Cache-Control": "private, no-store",
  "Referrer-Policy": "no-referrer",
  "X-Robots-Tag": "noindex, nofollow",
} as const;

function redirect(location: string, cookie: string): Response {
  return new Response(null, {
    status: 303,
    headers: { ...PUBLIC_HEADERS, Location: location, "Set-Cookie": cookie },
  });
}

function plain(status: number, text: string, extra: Record<string, string> = {}): Response {
  return new Response(text, {
    status,
    headers: { ...PUBLIC_HEADERS, "Content-Type": "text/plain; charset=utf-8", ...extra },
  });
}

/** Too many tries (the API's 429, ADR-0046 decision 7); English and Telugu, no school named. */
export const BUSY_TEXT =
  "Too many tries from this connection. Wait a few minutes and try again.\n" +
  "ఈ కనెక్షన్ నుండి చాలా ప్రయత్నాలు జరిగాయి. కొన్ని నిమిషాలు ఆగి మళ్ళీ ప్రయత్నించండి.";
export const UNAVAILABLE_TEXT =
  "This page is not available right now. Try again in a few minutes.\n" +
  "ఈ పేజీ ఇప్పుడు అందుబాటులో లేదు. కొన్ని నిమిషాల తర్వాత మళ్ళీ ప్రయత్నించండి.";

interface ExchangeAnswer {
  kind: string;
  expires_at: string;
}

function isAnswer(value: unknown): value is ExchangeAnswer {
  const v = value as Partial<ExchangeAnswer> | null;
  return typeof v?.kind === "string" && typeof v.expires_at === "string";
}

export async function exchangeParentLink(
  request: Request,
  runtime: PublicRuntime,
  token: string,
): Promise<Response> {
  const origins = publicOrigins();
  const secure = origins?.secure ?? true;
  const name = parentCookieName(secure);
  const dead = () => redirect(PUBLIC_EXPIRED_PATH, clearCookie(name, secure));
  if (!PARENT_TOKEN.test(token)) return dead();

  let upstream: Response;
  try {
    upstream = await callPublicApi(runtime, {
      method: "POST",
      path: EXCHANGE_PATH,
      body: { token },
      incomingHeaders: request.headers,
    });
  } catch {
    logEvent("public_api_unreachable_exchange", {}, "error");
    return plain(503, UNAVAILABLE_TEXT, { "Retry-After": "60" });
  }
  if (upstream.status === 404) {
    await upstream.body?.cancel();
    return dead();
  }
  if (upstream.status === 429) {
    await upstream.body?.cancel();
    const retry = upstream.headers.get("retry-after");
    return plain(429, BUSY_TEXT, retry && /^\d{1,6}$/.test(retry) ? { "Retry-After": retry } : {});
  }
  if (upstream.status !== 200) {
    await upstream.body?.cancel();
    logEvent("public_exchange_failed", { status: upstream.status }, "error");
    return plain(503, UNAVAILABLE_TEXT, { "Retry-After": "60" });
  }
  const answer: unknown = await upstream.json().catch(() => null);
  const expires = isAnswer(answer) ? Date.parse(answer.expires_at) : Number.NaN;
  const maxAge = Math.floor((expires - runtime.now()) / 1000);
  if (!Number.isFinite(maxAge) || maxAge <= 0) return dead();
  return redirect(PUBLIC_VIEW_PATH, serializeCookie(name, token, { secure, maxAge }));
}
