// @vitest-environment node
import { jwtVerify } from "jose";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { loadAuthConfig } from "@/server/config";
import { TEST_SERVICE_TOKEN_KEY, testEnv } from "@/test/server-env";
import { callPublicApi, PUBLIC_TOKEN_HEADER, PublicPathError, type PublicRuntime } from "./client";
import { BUSY_TEXT, exchangeParentLink } from "./exchange";
import { loadLinkView } from "./view";

/**
 * The public BFF client, the /p/{token} exchange-and-redirect and the /p/view loader
 * (ADR-0046 decision 6; FR-PUB-001, FR-PUB-010; SEC-036, SEC-037). Synthetic tokens only.
 */

const TOKEN = "Abcdefghijklmnopqrstuvwxyz0123456789_-ABCDE"; // 43 base64url characters
const NOW = Date.parse("2026-10-17T10:00:00Z");
const EXPIRES = "2026-10-24T10:00:00+00:00";
let calls: Request[];
let answer: (request: Request) => Response | Promise<Response>;

function runtime(env: Record<string, string> = {}): PublicRuntime {
  const config = loadAuthConfig(testEnv({ TRUSTED_PROXY_HOPS: "1", ...env }));
  return {
    config,
    now: () => NOW,
    apiFetch: (async (input: RequestInfo | URL, init?: RequestInit) => {
      const request = new Request(input, init);
      calls.push(request.clone());
      return answer(request);
    }) as typeof fetch,
  };
}

function json(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", ...headers },
  });
}

function browser(headers: Record<string, string> = {}): Request {
  return new Request(`https://my.school.example/p/${TOKEN}`, {
    headers: { "x-forwarded-for": "198.51.100.7", ...headers },
  });
}

beforeEach(() => {
  calls = [];
  answer = () => json({ kind: "parent_notice", expires_at: EXPIRES });
  vi.stubEnv("PUBLIC_BASE_URL", "https://my.school.example");
  vi.stubEnv("APP_BASE_URL", "https://office.school.example");
});

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("public BFF client (FR-PUB-001)", () => {
  it("sends a service token and the parent's address, never a user token or cookies", async () => {
    await callPublicApi(runtime(), {
      method: "GET",
      path: "/api/v1/public/links/current",
      token: TOKEN,
      incomingHeaders: new Headers({
        cookie: "__Host-sos_session=staff-session; __Host-sos_parent=x",
        authorization: "Bearer staff-access-token",
        "x-forwarded-for": "198.51.100.7",
      }),
    });
    const [sent] = calls;
    expect(sent?.url).toBe("http://api.internal:8000/api/v1/public/links/current");
    expect(sent?.headers.get("authorization")).toBeNull();
    expect(sent?.headers.get("cookie")).toBeNull();
    expect(sent?.headers.get(PUBLIC_TOKEN_HEADER)).toBe(TOKEN);
    expect(sent?.headers.get("x-forwarded-for")).toBe("198.51.100.7");
    const service = sent?.headers.get("x-service-token") ?? "";
    const verified = await jwtVerify(service, new TextEncoder().encode(TEST_SERVICE_TOKEN_KEY), {
      issuer: "sos-web",
      audience: "sos-api",
      currentDate: new Date(NOW),
    });
    expect(verified.payload.jti).toBeTruthy();
  });

  it("refuses every path outside /api/v1/public/", async () => {
    for (const path of [
      "/api/v1/students",
      "/api/v1/public/../students",
      "/api/v1/public/links?x=1",
      "/api/v1/public/%2e%2e/me",
      "/api/v1/webhooks/meta",
    ]) {
      await expect(
        callPublicApi(runtime(), { method: "GET", path, incomingHeaders: new Headers() }),
      ).rejects.toBeInstanceOf(PublicPathError);
    }
    expect(calls).toHaveLength(0);
  });
});

describe("GET /p/{token}: exchange and redirect (FR-PUB-010)", () => {
  it("sets the __Host- parent cookie until the link expires and redirects to /p/view", async () => {
    const response = await exchangeParentLink(browser(), runtime(), TOKEN);
    expect(response.status).toBe(303);
    expect(response.headers.get("location")).toBe("/p/view");
    const cookie = response.headers.get("set-cookie") ?? "";
    expect(cookie).toContain(`__Host-sos_parent=${TOKEN}`);
    for (const part of ["Path=/", "HttpOnly", "SameSite=Lax", "Secure", "Max-Age=604800"]) {
      expect(cookie).toContain(part);
    }
    expect(cookie).not.toContain("Domain");
    expect(response.headers.get("cache-control")).toBe("private, no-store");
    expect(response.headers.get("referrer-policy")).toBe("no-referrer");
    const [sent] = calls;
    expect(sent?.method).toBe("POST");
    expect(new URL(sent?.url ?? "").pathname).toBe("/api/v1/public/links/exchange");
    await expect(sent?.json()).resolves.toEqual({ token: TOKEN });
    // The token travels in the body, never in a URL.
    expect(sent?.url).not.toContain(TOKEN);
  });

  it("sends every dead link to the same expired page and clears the cookie", async () => {
    answer = () => json({ code: "not_found", status: 404 }, 404);
    const dead = await exchangeParentLink(browser(), runtime(), TOKEN);
    const malformed = await exchangeParentLink(browser(), runtime(), "short-token");
    for (const response of [dead, malformed]) {
      expect(response.status).toBe(303);
      expect(response.headers.get("location")).toBe("/p/expired");
      expect(response.headers.get("set-cookie")).toContain("__Host-sos_parent=;");
      expect(response.headers.get("set-cookie")).toContain("Max-Age=0");
    }
    expect(calls).toHaveLength(1); // the malformed token never reached the API
  });

  it("treats a link that expires now as dead", async () => {
    answer = () => json({ kind: "parent_dues", expires_at: "2026-10-17T10:00:00+00:00" });
    const response = await exchangeParentLink(browser(), runtime(), TOKEN);
    expect(response.headers.get("location")).toBe("/p/expired");
  });

  it("passes a 429 on as a plain busy page, and an API failure as 503", async () => {
    answer = () => json({ code: "rate_limited" }, 429, { "retry-after": "120" });
    const busy = await exchangeParentLink(browser(), runtime(), TOKEN);
    expect(busy.status).toBe(429);
    expect(busy.headers.get("retry-after")).toBe("120");
    await expect(busy.text()).resolves.toBe(BUSY_TEXT);
    answer = () => json({ code: "internal_error" }, 500);
    expect((await exchangeParentLink(browser(), runtime(), TOKEN)).status).toBe(503);
    answer = () => {
      throw new TypeError("network down");
    };
    expect((await exchangeParentLink(browser(), runtime(), TOKEN)).status).toBe(503);
  });

  it("uses the plain cookie name on a local http run", async () => {
    vi.stubEnv("PUBLIC_BASE_URL", "http://localhost:3000");
    vi.stubEnv("APP_BASE_URL", "http://localhost:3000");
    const response = await exchangeParentLink(
      browser(),
      runtime({ APP_BASE_URL: "http://localhost:3000" }),
      TOKEN,
    );
    const cookie = response.headers.get("set-cookie") ?? "";
    expect(cookie).toMatch(/^sos_parent=/);
    expect(cookie).not.toContain("Secure");
  });
});

describe("/p/view loader (FR-PUB-008, FR-PUB-010)", () => {
  const envelope = {
    kind: "parent_receipt",
    expires_at: EXPIRES,
    language: "te",
    school_name: "Synthetic Model School",
  };

  it("re-resolves the cookie's token on every request and reads only the parent cookie", async () => {
    answer = () => json(envelope);
    const view = await loadLinkView(
      runtime(),
      new Headers({ cookie: `__Host-sos_session=staff; __Host-sos_parent=${TOKEN}` }),
    );
    expect(view).toEqual({ state: "ok", envelope });
    const [sent] = calls;
    expect(sent?.headers.get(PUBLIC_TOKEN_HEADER)).toBe(TOKEN);
    expect(sent?.headers.get("cookie")).toBeNull();
  });

  it("is the expired page without a cookie or for a dead link, busy on 429 or outage", async () => {
    expect(await loadLinkView(runtime(), new Headers())).toEqual({ state: "expired" });
    expect(
      await loadLinkView(runtime(), new Headers({ cookie: "__Host-sos_parent=not-a-token" })),
    ).toEqual({ state: "expired" });
    expect(calls).toHaveLength(0);
    const cookie = new Headers({ cookie: `__Host-sos_parent=${TOKEN}` });
    answer = () => json({ code: "not_found" }, 404);
    expect(await loadLinkView(runtime(), cookie)).toEqual({ state: "expired" });
    answer = () => json({ code: "rate_limited" }, 429);
    expect(await loadLinkView(runtime(), cookie)).toEqual({ state: "busy" });
    answer = () => json({ ...envelope, kind: "staff_page" });
    expect(await loadLinkView(runtime(), cookie)).toEqual({ state: "expired" });
  });
});
