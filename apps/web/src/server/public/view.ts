import "server-only";
import type { components } from "@schoolos/api-client";
import { parentCookieName, PARENT_TOKEN, publicOrigins } from "@/lib/public-surface";
import { logEvent } from "@/server/log";
import { parseCookies } from "@/server/session/cookies";
import { callPublicApi, type PublicRuntime } from "./client";
import { parentCsrf } from "./csrf";
import { parentLanguage, type ParentLanguage } from "./messages";

/**
 * `/p/view`: the cookie's link, re-resolved by the API on every request (FR-PUB-010), with the
 * content of its kind (R2-3H: notice, receipt, dues or opt-in; ADR-0046 decision 2; FP-02
 * follow-up: the closure message's read-only page). Only the
 * parent cookie is read (never a staff session cookie, even where both share a local origin).
 * Every failure is the uniform expired page; a 429 or an unreachable API is "busy" so the page
 * can say so without naming anything. The forms on the page carry a CSRF token bound to the
 * cookie (`csrf`).
 */

const CURRENT_PATH = "/api/v1/public/links/current";

/**
 * FP-02 follow-up (FR-PUB-013; D86): the closure message's read-only page. Hand-written until
 * the lead regenerates the client; then it is `components["schemas"]["ParentClosureOut"]` and
 * `LinkEnvelopeOut` carries `closure` and the `parent_closure` kind itself.
 */
export interface ParentClosure {
  closed_from: string;
  closed_to: string;
  reason: string;
  notice_text: string | null;
  reopens_on: string | null;
}

type Envelope = components["schemas"]["LinkEnvelopeOut"];

export type LinkPage = Omit<Envelope, "language" | "kind"> & {
  kind: Envelope["kind"] | "parent_closure";
  language: ParentLanguage;
  closure?: ParentClosure | null;
};

export type LinkView =
  { state: "ok"; envelope: LinkPage; csrf: string } | { state: "expired" } | { state: "busy" };

const KINDS = new Set([
  "parent_notice",
  "parent_receipt",
  "parent_dues",
  "parent_optin",
  "parent_closure",
]);
const CONTENT: Record<string, keyof LinkPage> = {
  parent_notice: "notice",
  parent_receipt: "receipt",
  parent_dues: "dues",
  parent_optin: "optin",
  parent_closure: "closure",
};

function isEnvelope(value: unknown): value is Omit<LinkPage, "language"> & { language: string } {
  const v = value as Partial<LinkPage> | null;
  if (
    typeof v?.kind !== "string" ||
    !KINDS.has(v.kind) ||
    typeof v.expires_at !== "string" ||
    typeof v.school_name !== "string"
  ) {
    return false;
  }
  const block = v[CONTENT[v.kind] as keyof typeof v];
  return typeof block === "object" && block !== null;
}

/** The parent cookie's token, when it has the shape of one. */
export function parentToken(headers: Headers): string | null {
  const secure = publicOrigins()?.secure ?? true;
  const token = parseCookies(headers.get("cookie")).get(parentCookieName(secure));
  return token && PARENT_TOKEN.test(token) ? token : null;
}

export async function loadLinkView(runtime: PublicRuntime, headers: Headers): Promise<LinkView> {
  const token = parentToken(headers);
  if (!token) return { state: "expired" };
  let upstream: Response;
  try {
    upstream = await callPublicApi(runtime, {
      method: "GET",
      path: CURRENT_PATH,
      token,
      incomingHeaders: headers,
    });
  } catch {
    logEvent("public_api_unreachable_view", {}, "error");
    return { state: "busy" };
  }
  if (upstream.status === 429 || upstream.status >= 500) {
    await upstream.body?.cancel();
    return { state: "busy" };
  }
  if (upstream.status !== 200) {
    await upstream.body?.cancel();
    return { state: "expired" };
  }
  const body: unknown = await upstream.json().catch(() => null);
  if (!isEnvelope(body)) return { state: "expired" };
  return {
    state: "ok",
    envelope: { ...body, language: parentLanguage(body.language) },
    csrf: parentCsrf(runtime.config.serviceTokenKey, token),
  };
}
