import "server-only";
import { parentCookieName, PARENT_TOKEN, publicOrigins } from "@/lib/public-surface";
import type { LinkEnvelope } from "@/features/public/PublicPages";
import { logEvent } from "@/server/log";
import { parseCookies } from "@/server/session/cookies";
import { callPublicApi, type PublicRuntime } from "./client";
import { parentLanguage } from "./messages";

/**
 * `/p/view`: the cookie's link, re-resolved by the API on every request (FR-PUB-010). Only the
 * parent cookie is read (never a staff session cookie, even where both share a local origin).
 * Every failure is the uniform expired page; a 429 or an unreachable API is "busy" so the page
 * can say so without naming anything.
 */

const CURRENT_PATH = "/api/v1/public/links/current";

export type LinkView =
  { state: "ok"; envelope: LinkEnvelope } | { state: "expired" } | { state: "busy" };

const KINDS = new Set(["parent_notice", "parent_receipt", "parent_dues", "parent_optin"]);

function isEnvelope(value: unknown): value is LinkEnvelope {
  const v = value as Partial<LinkEnvelope> | null;
  return (
    typeof v?.kind === "string" &&
    KINDS.has(v.kind) &&
    typeof v.expires_at === "string" &&
    typeof v.school_name === "string"
  );
}

export async function loadLinkView(runtime: PublicRuntime, headers: Headers): Promise<LinkView> {
  const secure = publicOrigins()?.secure ?? true;
  const token = parseCookies(headers.get("cookie")).get(parentCookieName(secure));
  if (!token || !PARENT_TOKEN.test(token)) return { state: "expired" };
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
  return { state: "ok", envelope: { ...body, language: parentLanguage(body.language) } };
}
