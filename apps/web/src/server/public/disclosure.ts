import "server-only";
import type { components } from "@schoolos/api-client";
import { logEvent } from "@/server/log";
import { callPublicApi, type PublicRuntime } from "./client";

/**
 * `/d/{key}`: the school's CBSE Appendix IX disclosure through the BFF (ADR-0051; FR-DISC-009,
 * FR-DISC-010; FR-PUB-001). The key is shape-checked here and sent in the path of
 * `GET /api/v1/public/disclosure/{key}` with the service token and no user token; it is never
 * logged. Every answer that is not a page is the same "not found"; a 429 or an unreachable API is
 * "busy". Documents open through `/d/{key}/documents/{n}`, which answers 303 to the presigned
 * file (at most 5 minutes).
 */

export type PublicDisclosure = components["schemas"]["PublicDisclosureOut"];
type PublicDocumentLink = components["schemas"]["PublicDocumentLinkOut"];

/**
 * Document numbers the API accepts: `Path(ge=1, le=200)` on
 * `GET /api/v1/public/disclosure/{key}/documents/{n}` (apps/api/openapi.json; a test pins it).
 * Anything else goes back to the page without calling the API.
 */
export const DISCLOSURE_DOCUMENT_MAX = 200;

function documentNumber(n: string): number | null {
  if (!/^[1-9]\d{0,2}$/.test(n)) return null;
  const value = Number(n);
  return value <= DISCLOSURE_DOCUMENT_MAX ? value : null;
}

/** A disclosure link key: 16 random bytes, base64url without padding (22 characters). */
export const DISCLOSURE_KEY = /^[A-Za-z0-9_-]{22}$/;

export type DisclosureView =
  | { state: "ok"; disclosure: PublicDisclosure; key: string }
  | { state: "not_found" }
  | { state: "busy" };

function isDisclosure(value: unknown): value is PublicDisclosure {
  const v = value as Partial<PublicDisclosure> | null;
  return typeof v?.school_name === "string" && Array.isArray(v.sections);
}

export async function loadDisclosure(
  runtime: PublicRuntime,
  headers: Headers,
  key: string,
): Promise<DisclosureView> {
  if (!DISCLOSURE_KEY.test(key)) return { state: "not_found" };
  let upstream: Response;
  try {
    upstream = await callPublicApi(runtime, {
      method: "GET",
      path: `/api/v1/public/disclosure/${key}`,
      incomingHeaders: headers,
    });
  } catch {
    logEvent("public_api_unreachable_disclosure", {}, "error");
    return { state: "busy" };
  }
  if (upstream.status === 429 || upstream.status >= 500) {
    await upstream.body?.cancel();
    return { state: "busy" };
  }
  if (upstream.status !== 200) {
    await upstream.body?.cancel();
    return { state: "not_found" };
  }
  const body: unknown = await upstream.json().catch(() => null);
  if (!isDisclosure(body)) return { state: "not_found" };
  return { state: "ok", disclosure: body, key };
}

/**
 * `/d/{key}/documents/{n}`: 303 to the presigned PDF of a published document, or back to the
 * page (which then shows itself or "not found") for anything else.
 */
export async function disclosureDocumentRedirect(
  runtime: PublicRuntime,
  request: Request,
  key: string,
  n: string,
): Promise<Response> {
  const back = new URL(`/d/${DISCLOSURE_KEY.test(key) ? key : "unknown"}`, request.url);
  const see = (location: string | URL) =>
    new Response(null, {
      status: 303,
      headers: { Location: location.toString(), "Cache-Control": "private, no-store" },
    });
  const number = documentNumber(n);
  if (!DISCLOSURE_KEY.test(key) || number === null) return see(back);
  let upstream: Response;
  try {
    upstream = await callPublicApi(runtime, {
      method: "GET",
      path: `/api/v1/public/disclosure/${key}/documents/${number}`,
      incomingHeaders: request.headers,
    });
  } catch {
    logEvent("public_api_unreachable_disclosure", {}, "error");
    return see(back);
  }
  if (upstream.status !== 200) {
    await upstream.body?.cancel();
    return see(back);
  }
  const body = (await upstream.json().catch(() => null)) as Partial<PublicDocumentLink> | null;
  const url: unknown = body?.url;
  return isFileUrl(url) ? see(url) : see(back);
}

/** A presigned file URL: https, or http on a loopback host (local runs). */
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
