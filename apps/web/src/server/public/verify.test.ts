// @vitest-environment node
import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { POST as check } from "@/app/(public)/v/check/route";
import { routeSurface, verifyOrigin } from "@/lib/public-surface";
import { groupCode, normaliseCode } from "@/lib/verification-code";
import { proxy } from "@/proxy";
import { loadAuthConfig } from "@/server/config";
import { testEnv } from "@/test/server-env";
import type { PublicRuntime } from "./client";
import { loadVerification } from "./verify";

/**
 * Certificate verification on its own host (ADR-0045 decisions 6, 7, 9; FR-CERT-021,
 * FR-CERT-022; FR-PUB-001; D29): code normalisation, which host serves /v, the form's redirect
 * and the BFF loader. Synthetic codes only.
 */

const CODE = "7K4MQ2XP9HD3RT6W";
const STAFF = "office.school.example";
const PARENT = "my.school.example";
const VERIFY = "verify.school.example";
const ENV = {
  APP_BASE_URL: `https://${STAFF}`,
  PUBLIC_BASE_URL: `https://${PARENT}`,
  VERIFY_BASE_URL: `https://${VERIFY}`,
};
let calls: Request[];
let answer: (request: Request) => Response;

function runtime(): PublicRuntime {
  return {
    config: loadAuthConfig(testEnv({ TRUSTED_PROXY_HOPS: "1" })),
    now: () => Date.parse("2026-10-17T10:00:00Z"),
    apiFetch: (async (input: RequestInfo | URL, init?: RequestInit) => {
      const request = new Request(input, init);
      calls.push(request.clone());
      return answer(request);
    }) as typeof fetch,
  };
}

const VALID = {
  status: "valid",
  certificate_type: "bonafide",
  title_en: "Bonafide certificate",
  school_name: "Synthetic Model School",
  school_place: "Vijayawada",
  serial: "BC/2026-27/0001",
  issued_on: "2026-10-17",
  duplicate: null,
  cancelled_on: null,
  content: null,
};

beforeEach(() => {
  calls = [];
  answer = () =>
    new Response(JSON.stringify(VALID), { headers: { "content-type": "application/json" } });
});

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("verification codes (FR-CERT-022)", () => {
  it("ignores case, spaces and dashes and reads look-alikes the Crockford way", () => {
    expect(normaliseCode("7k4m-q2xp 9hd3-rt6w")).toBe(CODE);
    expect(normaliseCode("7K4M–Q2XP– 9HD3 RT6W")).toBe(CODE);
    expect(normaliseCode("OK4M-Q2XP-9HD3-RTLI")).toBe("0K4MQ2XP9HD3RT11");
    for (const bad of [
      "7K4M-Q2XP-9HD3-RT6U",
      "7K4M",
      "BC/2026-27/0001",
      "",
      null,
      "7".repeat(65),
    ]) {
      expect(normaliseCode(bad), String(bad)).toBeNull();
    }
    expect(groupCode(CODE)).toBe("7K4M-Q2XP-9HD3-RT6W");
  });
});

describe("the verification host (D29)", () => {
  it("serves /v only on VERIFY_BASE_URL and nothing else there", () => {
    expect(verifyOrigin(ENV)).toEqual({ verifyHost: VERIFY, sharedWithApp: false, secure: true });
    expect(routeSurface(VERIFY, `/v/${CODE}`, ENV)).toEqual({ kind: "verify", served: true });
    expect(routeSurface(VERIFY, "/v", ENV)).toEqual({ kind: "verify", served: true });
    expect(routeSurface(VERIFY, "/v/check", ENV)).toEqual({ kind: "verify", served: true });
    expect(routeSurface(VERIFY, "/v/a/b", ENV)).toEqual({ kind: "verify", served: false });
    expect(routeSurface(VERIFY, "/students", ENV)).toEqual({ kind: "not_found" });
    expect(routeSurface(VERIFY, "/p/view", ENV)).toEqual({ kind: "not_found" });
    expect(routeSurface(STAFF, `/v/${CODE}`, ENV)).toEqual({ kind: "not_found" });
    expect(routeSurface(PARENT, `/v/${CODE}`, ENV)).toEqual({ kind: "not_found" });
    // Parent pages and staff pages are unchanged.
    expect(routeSurface(PARENT, "/p/view", ENV)).toEqual({ kind: "public", served: true });
    expect(routeSurface(STAFF, "/students", ENV)).toEqual({ kind: "staff" });
    // Verification off: /v is nowhere.
    const off = { APP_BASE_URL: ENV.APP_BASE_URL, PUBLIC_BASE_URL: ENV.PUBLIC_BASE_URL };
    expect(routeSurface(STAFF, "/v", off)).toEqual({ kind: "not_found" });
  });

  it("may share the parent host (dedicated school) but not the staff host except locally", () => {
    const dedicated = { ...ENV, VERIFY_BASE_URL: ENV.PUBLIC_BASE_URL };
    expect(routeSurface(PARENT, `/v/${CODE}`, dedicated)).toEqual({ kind: "verify", served: true });
    expect(routeSurface(PARENT, "/p/view", dedicated)).toEqual({ kind: "public", served: true });
    expect(verifyOrigin({ ...ENV, VERIFY_BASE_URL: ENV.APP_BASE_URL })).toBeNull();
    expect(verifyOrigin({ ...ENV, VERIFY_BASE_URL: "http://verify.school.example" })).toBeNull();
    const local = {
      APP_BASE_URL: "http://localhost:3000",
      VERIFY_BASE_URL: "http://localhost:3000",
    };
    expect(routeSurface("localhost:3000", "/v", local)).toEqual({ kind: "verify", served: true });
    expect(routeSurface("localhost:3000", "/students", local)).toEqual({ kind: "staff" });
  });

  it("gives the verification pages the public headers through the proxy", () => {
    for (const [name, value] of Object.entries(ENV)) vi.stubEnv(name, value);
    const request = new NextRequest(new URL(`/v/${CODE}`, `https://${VERIFY}`), {
      headers: { host: VERIFY },
    });
    const response = proxy(request);
    expect(response.headers.get("referrer-policy")).toBe("no-referrer");
    expect(response.headers.get("cache-control")).toBe("private, no-store");
    expect(response.headers.get("x-robots-tag")).toBe("noindex, nofollow");
    expect(response.headers.get("content-security-policy")).toContain("frame-ancestors 'none'");
    const odd = proxy(
      new NextRequest(new URL("/v/a/b", `https://${VERIFY}`), { headers: { host: VERIFY } }),
    );
    expect(new URL(odd.headers.get("x-middleware-rewrite") ?? "").pathname).toBe("/v/unknown");
    const staff = proxy(
      new NextRequest(new URL("/students", `https://${VERIFY}`), { headers: { host: VERIFY } }),
    );
    expect(staff.status).toBe(404);
  });
});

describe("the code box (no JavaScript)", () => {
  function post(code: string): Request {
    const body = new URLSearchParams({ code });
    return new Request(`https://${VERIFY}/v/check`, { method: "POST", body });
  }

  it("redirects a typed code to its canonical page and anything else back with an error", async () => {
    const ok = await check(post("7k4m-q2xp-9hd3-rt6w"));
    expect(ok.status).toBe(303);
    expect(ok.headers.get("location")).toBe(`/v/${CODE}`);
    expect(ok.headers.get("referrer-policy")).toBe("no-referrer");
    const bad = await check(post("Synthetica Student"));
    expect(bad.headers.get("location")).toBe("/v?error=invalid");
  });
});

describe("the BFF loader (FR-CERT-021, FR-PUB-001)", () => {
  it("asks the API once with the normalised code in the path and no user token", async () => {
    const view = await loadVerification(
      runtime(),
      new Headers({ cookie: "__Host-sos_session=staff", authorization: "Bearer staff" }),
      "7k4m-q2xp-9hd3-rt6w",
    );
    expect(view).toMatchObject({ state: "ok", code: CODE });
    expect(calls).toHaveLength(1);
    const [sent] = calls;
    expect(new URL(sent?.url ?? "").pathname).toBe(`/api/v1/public/certificates/${CODE}`);
    expect(sent?.headers.get("authorization")).toBeNull();
    expect(sent?.headers.get("cookie")).toBeNull();
    expect(sent?.headers.get("x-service-token")).toBeTruthy();
  });

  it("never calls the API for something that cannot be a code", async () => {
    expect(await loadVerification(runtime(), new Headers(), "BC/2026-27/0001")).toEqual({
      state: "not_found",
    });
    expect(calls).toHaveLength(0);
  });

  it("maps 404 to not found and 429 or 5xx to busy", async () => {
    answer = () => new Response("{}", { status: 404 });
    expect((await loadVerification(runtime(), new Headers(), CODE)).state).toBe("not_found");
    answer = () => new Response("{}", { status: 429 });
    expect((await loadVerification(runtime(), new Headers(), CODE)).state).toBe("busy");
    answer = () => new Response("{}", { status: 503 });
    expect((await loadVerification(runtime(), new Headers(), CODE)).state).toBe("busy");
  });
});
