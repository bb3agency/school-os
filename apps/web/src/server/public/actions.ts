import "server-only";
import type { components } from "@schoolos/api-client";
import { isPayUrl, PUBLIC_EXPIRED_PATH, PUBLIC_VIEW_PATH } from "@/lib/public-surface";
import { logEvent } from "@/server/log";
import { callPublicApi, type PublicRuntime } from "./client";
import { csrfMatches, sameOrigin } from "./csrf";
import { UNAVAILABLE_TEXT } from "./exchange";
import { parentToken } from "./view";

/**
 * The parent pages' requests that are not a page view (wave R2-3H; ADR-0046 decision 9;
 * FR-PUB-009, FR-PAY-005, FR-LANG-003). Plain HTML forms and links, no client JavaScript:
 *
 * - `GET /p/document`: the notice or receipt PDF. The API gives a presigned link (at most
 *   5 minutes) and this answers 303 to it; without one, back to the page saying so.
 * - `POST /p/pay`: "Pay online". The ticked instalments go to the API, which makes or reuses
 *   the payment link on the school's own Razorpay account; this answers 303 to Razorpay's page
 *   (https on rzp.io / razorpay.com only). Any refusal goes back to the page with a reason code.
 * - `POST /p/optin`: the parent's message language.
 *
 * Posts need the parent cookie, the CSRF token bound to it and the public origin (csrf.ts).
 * Nothing here logs a token, an amount or a URL (invariant 5).
 */

const DOCUMENT_PATH = "/api/v1/public/links/current/document";
const PAY_PATH = "/api/v1/public/links/current/pay";
const PAYMENT_PATH = "/api/v1/public/links/current/payment";
const OPTIN_PATH = "/api/v1/public/links/current/optin";
const MAX_INSTALMENTS = 24;

const HEADERS = {
  "Cache-Control": "private, no-store",
  "Referrer-Policy": "no-referrer",
  "X-Robots-Tag": "noindex, nofollow",
} as const;

export type PayError =
  "choose" | "not_connected" | "nothing_due" | "too_many" | "limit" | "failed" | "expired";
export const PAY_ERRORS: readonly PayError[] = [
  "choose",
  "not_connected",
  "nothing_due",
  "too_many",
  "limit",
  "failed",
  "expired",
];

function see(location: string): Response {
  return new Response(null, { status: 303, headers: { ...HEADERS, Location: location } });
}

function refused(): Response {
  return new Response("Forbidden", {
    status: 403,
    headers: { ...HEADERS, "Content-Type": "text/plain; charset=utf-8" },
  });
}

function unavailable(): Response {
  return new Response(UNAVAILABLE_TEXT, {
    status: 503,
    headers: { ...HEADERS, "Content-Type": "text/plain; charset=utf-8", "Retry-After": "60" },
  });
}

function isDev(): boolean {
  return process.env.NODE_ENV === "development";
}

/** A presigned file URL we may send a parent to: https, or http on a loopback host (local). */
function isFileUrl(value: unknown): value is string {
  if (typeof value !== "string") return false;
  try {
    const url = new URL(value);
    if (url.protocol === "https:") return true;
    return url.protocol === "http:" && ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname);
  } catch {
    return false;
  }
}

export async function documentRedirect(
  runtime: PublicRuntime,
  request: Request,
): Promise<Response> {
  const token = parentToken(request.headers);
  if (!token) return see(PUBLIC_EXPIRED_PATH);
  let upstream: Response;
  try {
    upstream = await callPublicApi(runtime, {
      method: "GET",
      path: DOCUMENT_PATH,
      token,
      incomingHeaders: request.headers,
    });
  } catch {
    logEvent("public_api_unreachable_document", {}, "error");
    return see(`${PUBLIC_VIEW_PATH}?pdf=0`);
  }
  if (upstream.status !== 200) {
    await upstream.body?.cancel();
    return see(`${PUBLIC_VIEW_PATH}?pdf=0`);
  }
  const body = (await upstream.json().catch(() => null)) as { url?: unknown } | null;
  const url = body?.url;
  return isFileUrl(url) ? see(url) : see(`${PUBLIC_VIEW_PATH}?pdf=0`);
}

interface CheckedPost {
  token: string;
  form: FormData;
}

async function checkedPost(runtime: PublicRuntime, request: Request): Promise<CheckedPost | null> {
  const token = parentToken(request.headers);
  if (!token || !sameOrigin(request.headers)) return null;
  const type = request.headers.get("content-type") ?? "";
  if (!type.startsWith("application/x-www-form-urlencoded")) return null;
  const form = await request.formData().catch(() => null);
  if (!form || !csrfMatches(runtime.config.serviceTokenKey, token, form.get("csrf"))) return null;
  return { token, form };
}

function payError(status: number, code: unknown): PayError {
  if (status === 404) return "expired";
  if (status === 429) return "limit";
  if (status === 422) return "choose";
  if (status === 409) {
    if (code === "payments_not_connected") return "not_connected";
    if (code === "nothing_due") return "nothing_due";
    if (code === "too_many_links") return "too_many";
  }
  return "failed";
}

function payBack(error: PayError): Response {
  return error === "expired"
    ? see(PUBLIC_EXPIRED_PATH)
    : see(`${PUBLIC_VIEW_PATH}?pay=${error}#pay`);
}

export async function payFromForm(runtime: PublicRuntime, request: Request): Promise<Response> {
  const post = await checkedPost(runtime, request);
  if (!post) return refused();
  const instalments = [
    ...new Set(
      post.form
        .getAll("instalment")
        .map((v) => (typeof v === "string" && /^\d{1,3}$/.test(v) ? Number(v) : Number.NaN))
        .filter((n) => Number.isInteger(n) && n >= 1),
    ),
  ].sort((a, b) => a - b);
  if (instalments.length === 0 || instalments.length > MAX_INSTALMENTS) return payBack("choose");
  let upstream: Response;
  try {
    upstream = await callPublicApi(runtime, {
      method: "POST",
      path: PAY_PATH,
      token: post.token,
      body: { instalments },
      incomingHeaders: request.headers,
    });
  } catch {
    logEvent("public_api_unreachable_pay", {}, "error");
    return payBack("failed");
  }
  const body = (await upstream.json().catch(() => null)) as
    (Partial<components["schemas"]["ParentPayOut"]> & { code?: unknown }) | null;
  if (upstream.status !== 200) {
    if (upstream.status >= 500) logEvent("public_pay_failed", { status: upstream.status }, "warn");
    return payBack(payError(upstream.status, body?.code));
  }
  const target = body?.redirect_url;
  if (typeof target !== "string" || !isPayUrl(target, isDev())) {
    logEvent("public_pay_refused_redirect", {}, "error");
    return payBack("failed");
  }
  return see(target);
}

export type PaymentStatus = components["schemas"]["ParentPaymentStatusOut"];

export type PaymentView =
  | { state: "ok"; payment: PaymentStatus }
  | { state: "unknown" }
  | { state: "expired" }
  | { state: "busy" };

/** The return page's status of the payment link Razorpay names (`ref`: 32 hex) or the newest. */
export async function loadPayment(
  runtime: PublicRuntime,
  headers: Headers,
  ref: string | null,
): Promise<PaymentView> {
  const token = parentToken(headers);
  if (!token) return { state: "expired" };
  const query = ref && /^[0-9a-f]{32}$/.test(ref) ? { intent: ref } : undefined;
  let upstream: Response;
  try {
    upstream = await callPublicApi(runtime, {
      method: "GET",
      path: PAYMENT_PATH,
      query,
      token,
      incomingHeaders: headers,
    });
  } catch {
    logEvent("public_api_unreachable_payment", {}, "error");
    return { state: "busy" };
  }
  if (upstream.status === 429 || upstream.status >= 500) {
    await upstream.body?.cancel();
    return { state: "busy" };
  }
  if (upstream.status !== 200) {
    await upstream.body?.cancel();
    return { state: "unknown" };
  }
  const body = (await upstream.json().catch(() => null)) as PaymentStatus | null;
  if (!body || typeof body.status !== "string" || typeof body.amount !== "string") {
    return { state: "unknown" };
  }
  return { state: "ok", payment: body };
}

export async function optinFromForm(runtime: PublicRuntime, request: Request): Promise<Response> {
  const post = await checkedPost(runtime, request);
  if (!post) return refused();
  const language = post.form.get("language");
  if (language !== "en" && language !== "te" && language !== "school") {
    return see(`${PUBLIC_VIEW_PATH}?saved=0`);
  }
  let upstream: Response;
  try {
    upstream = await callPublicApi(runtime, {
      method: "POST",
      path: OPTIN_PATH,
      token: post.token,
      body: { language },
      incomingHeaders: request.headers,
    });
  } catch {
    logEvent("public_api_unreachable_optin", {}, "error");
    return unavailable();
  }
  await upstream.body?.cancel();
  if (upstream.status === 404) return see(PUBLIC_EXPIRED_PATH);
  return see(`${PUBLIC_VIEW_PATH}?saved=${upstream.status === 200 ? "1" : "0"}`);
}
