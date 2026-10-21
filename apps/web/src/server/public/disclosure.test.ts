// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { routeSurface } from "@/lib/public-surface";
import { loadAuthConfig } from "@/server/config";
import { testEnv } from "@/test/server-env";
import type { PublicRuntime } from "./client";
import { disclosureDocumentRedirect, loadDisclosure } from "./disclosure";

/**
 * The CBSE disclosure page through the BFF (R3-1C; ADR-0051; FR-DISC-009, FR-DISC-010;
 * FR-PUB-001): which host serves /d, the key's shape check, the loader's uniform "not found"
 * and the document redirect. Synthetic data only.
 */

const KEY = "AbCdEfGhIjKlMnOpQrSt_-";
const STAFF = "office.school.example";
const PARENT = "my.school.example";
const ENV = { APP_BASE_URL: `https://${STAFF}`, PUBLIC_BASE_URL: `https://${PARENT}` };
let calls: Request[];
let answer: (request: Request) => Response;

function runtime(): PublicRuntime {
  return {
    config: loadAuthConfig(testEnv({ TRUSTED_PROXY_HOPS: "1" })),
    now: () => Date.parse("2026-10-21T10:00:00Z"),
    apiFetch: (async (input: RequestInfo | URL, init?: RequestInit) => {
      const request = new Request(input, init);
      calls.push(request.clone());
      return answer(request);
    }) as typeof fetch,
  };
}

const PAGE = {
  school_name: "Synthetic Model School",
  title: "Mandatory public disclosure (CBSE Appendix IX)",
  published_at: "2026-10-21T05:00:00Z",
  format_version: 1,
  sections: [],
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

beforeEach(() => {
  calls = [];
  answer = () => json(PAGE);
});

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("which host serves /d (ADR-0051 decision 4)", () => {
  it("serves the disclosure pages on the public host only", () => {
    expect(routeSurface(PARENT, `/d/${KEY}`, ENV)).toEqual({ kind: "public", served: true });
    expect(routeSurface(PARENT, `/d/${KEY}/documents/3`, ENV)).toEqual({
      kind: "public",
      served: true,
    });
    expect(routeSurface(STAFF, `/d/${KEY}`, ENV)).toEqual({ kind: "not_found" });
    expect(routeSurface(PARENT, "/d/a/b/c", ENV)).toEqual({ kind: "public", served: false });
    // Staff paths that merely start with "d" are untouched.
    expect(routeSurface(STAFF, "/documents", ENV)).toEqual({ kind: "staff" });
  });
});

describe("loadDisclosure (FR-DISC-009)", () => {
  it("sends the key in the path with the service token and no user token", async () => {
    const view = await loadDisclosure(runtime(), new Headers(), KEY);
    expect(view).toEqual({ state: "ok", disclosure: PAGE, key: KEY });
    expect(calls).toHaveLength(1);
    const call = calls[0]!;
    expect(new URL(call.url).pathname).toBe(`/api/v1/public/disclosure/${KEY}`);
    expect(call.headers.get("x-service-token")).toBeTruthy();
    expect(call.headers.get("authorization")).toBeNull();
  });

  it("checks the key's shape before calling the API", async () => {
    for (const bad of ["short", `${KEY}x`, "AbCdEfGhIjKlMnOpQrSt/.", "../../admin12345678901"]) {
      expect(await loadDisclosure(runtime(), new Headers(), bad)).toEqual({ state: "not_found" });
    }
    expect(calls).toHaveLength(0);
  });

  it("answers the same not found for 404, odd bodies and other statuses; busy for 429 and 5xx", async () => {
    answer = () => json({ code: "not_found" }, 404);
    expect(await loadDisclosure(runtime(), new Headers(), KEY)).toEqual({ state: "not_found" });
    answer = () => json({ unexpected: true });
    expect(await loadDisclosure(runtime(), new Headers(), KEY)).toEqual({ state: "not_found" });
    answer = () => json({ code: "rate_limited" }, 429);
    expect(await loadDisclosure(runtime(), new Headers(), KEY)).toEqual({ state: "busy" });
    answer = () => json({}, 503);
    expect(await loadDisclosure(runtime(), new Headers(), KEY)).toEqual({ state: "busy" });
  });
});

describe("document redirect (FR-DISC-009)", () => {
  const request = new Request(`https://${PARENT}/d/${KEY}/documents/1`);

  it("answers 303 to the presigned file", async () => {
    answer = () => json({ url: "https://files.example/doc.pdf?sig=x", expires_at: "x" });
    const res = await disclosureDocumentRedirect(runtime(), request, KEY, "1");
    expect(res.status).toBe(303);
    expect(res.headers.get("location")).toBe("https://files.example/doc.pdf?sig=x");
    expect(res.headers.get("cache-control")).toContain("no-store");
  });

  it("goes back to the page for anything else", async () => {
    answer = () => json({ code: "not_found" }, 404);
    const res = await disclosureDocumentRedirect(runtime(), request, KEY, "1");
    expect(res.headers.get("location")).toBe(`https://${PARENT}/d/${KEY}`);
    answer = () => json({ url: "javascript:alert(1)" });
    const odd = await disclosureDocumentRedirect(runtime(), request, KEY, "1");
    expect(odd.headers.get("location")).toBe(`https://${PARENT}/d/${KEY}`);
    calls = [];
    const bad = await disclosureDocumentRedirect(runtime(), request, KEY, "0");
    expect(bad.status).toBe(303);
    expect(calls).toHaveLength(0);
  });
});
