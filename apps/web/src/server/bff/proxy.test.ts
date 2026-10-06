// @vitest-environment node
import { decodeProtectedHeader, jwtVerify } from "jose";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createHarness, type Harness } from "@/test/bff-harness";
import { TEST_SERVICE_TOKEN_KEY } from "@/test/server-env";
import { isStrictPolicy, MAX_REQUEST_BODY_BYTES, proxyToApi } from "./proxy";

const TENANT = "0192f3a4-0000-7000-8000-000000000001";
const clerk = { sub: "staff-sub-1", name: "Office Clerk" };
const operator = { sub: "op-1", mfa: true };
let h: Harness;

beforeEach(async () => {
  h = await createHarness();
});

afterEach(() => {
  vi.unstubAllEnvs();
});

const json = (body: unknown, status = 200, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", ...headers },
  });

async function call(path: string, init: RequestInit = {}) {
  return proxyToApi(h.request(path, init), h.runtime);
}

describe("BFF proxy /bff/api/v1/* (SEC-004)", () => {
  it("passes the API's 429, Retry-After and RateLimit headers through unchanged (P2-07)", async () => {
    await h.signIn("staff", clerk);
    h.setApi(() =>
      json({ code: "rate_limited", status: 429, retry_after: 12 }, 429, {
        "retry-after": "12",
        "ratelimit-policy": '"user";q=600;w=60, "search";q=60;w=60',
        ratelimit: '"user";r=500;t=10, "search";r=0;t=12',
      }),
    );
    const response = await call("/bff/api/v1/students/search", { method: "GET" });
    expect(response.status).toBe(429);
    expect(response.headers.get("retry-after")).toBe("12");
    expect(response.headers.get("ratelimit-policy")).toBe('"user";q=600;w=60, "search";q=60;w=60');
    expect(response.headers.get("ratelimit")).toBe('"user";r=500;t=10, "search";r=0;t=12');
    await expect(response.json()).resolves.toMatchObject({ retry_after: 12 });
  });

  it("answers 401 problem+json without a session and never calls the API", async () => {
    const response = await call("/bff/api/v1/users");
    expect(response.status).toBe(401);
    expect(response.headers.get("content-type")).toContain("application/problem+json");
    await expect(response.json()).resolves.toMatchObject({
      code: "unauthenticated",
      login_url: "/bff/auth/login",
    });
    expect(h.apiCalls).toHaveLength(0);
  });

  it("forwards with the access token and a service token that matches the API contract", async () => {
    // Telugu switched on explicitly (ADR-0036): the browser's Telugu preference is forwarded.
    vi.stubEnv("SOS_TELUGU_ENABLED", "true");
    await h.signIn("staff", clerk);
    h.apiCalls.length = 0;
    h.setApi(() => json({ data: [{ id: "u1" }], next_cursor: null }));
    const response = await call("/bff/api/v1/users?limit=50", {
      headers: { "accept-language": "te-IN,te;q=0.9,en;q=0.5", "x-request-id": "req_abc-1" },
    });
    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toMatchObject({ data: [{ id: "u1" }] });

    const [upstream] = h.apiCalls;
    expect(upstream?.url).toBe("http://api.internal:8000/api/v1/users?limit=50");
    expect(upstream?.headers.get("authorization")).toMatch(/^Bearer eyJ/);
    expect(upstream?.headers.get("accept-language")).toBe("te");
    expect(upstream?.headers.get("x-request-id")).toBe("req_abc-1");
    expect(response.headers.get("x-request-id")).toBe("req_abc-1");

    const token = upstream?.headers.get("x-service-token") ?? "";
    expect(decodeProtectedHeader(token).alg).toBe("HS256");
    const { payload } = await jwtVerify(token, new TextEncoder().encode(TEST_SERVICE_TOKEN_KEY), {
      issuer: "sos-web",
      audience: "sos-api",
      algorithms: ["HS256"],
    });
    expect(typeof payload.aud).toBe("string");
    expect(payload.exp! - payload.iat!).toBeLessThanOrEqual(60);
    expect(payload.exp! - payload.iat!).toBeGreaterThan(0);
    expect(payload.jti).toMatch(/^[A-Za-z0-9_-]{16,128}$/);
  });

  it("never asks the API for Telugu while Telugu is switched off (ADR-0036)", async () => {
    await h.signIn("staff", clerk);
    h.apiCalls.length = 0;
    h.setApi(() => json({ data: [], next_cursor: null }));
    await call("/bff/api/v1/users", { headers: { "accept-language": "te-IN,te;q=0.9,en;q=0.5" } });
    await call("/bff/api/v1/users", { headers: { "accept-language": "te" } });
    expect(h.apiCalls.map((upstream) => upstream.headers.get("accept-language"))).toEqual([
      "en",
      "en",
    ]);
  });

  it("mints a fresh service token (new jti) for every call", async () => {
    await h.signIn("staff", clerk);
    h.apiCalls.length = 0;
    await call("/bff/api/v1/classes");
    await call("/bff/api/v1/classes");
    const jtis = await Promise.all(
      h.apiCalls.map(async (r) => {
        const { payload } = await jwtVerify(
          r.headers.get("x-service-token") ?? "",
          new TextEncoder().encode(TEST_SERVICE_TOKEN_KEY),
        );
        return payload.jti;
      }),
    );
    expect(new Set(jtis).size).toBe(2);
  });

  it("strips cookies, hop-by-hop and browser auth headers, and never forwards Set-Cookie", async () => {
    await h.signIn("staff", clerk);
    await h.runtime.store.setActiveTenant(
      (await h.runtime.store.load(h.jar.get("__Host-sos_session"), { touch: false }))!,
      TENANT,
    );
    h.apiCalls.length = 0;
    h.setApi(() =>
      json({ ok: true }, 200, {
        "set-cookie": "api_cookie=1; Path=/",
        connection: "keep-alive",
        "x-powered-by": "uvicorn",
        etag: '"v1"',
      }),
    );
    const response = await call("/bff/api/v1/classes", {
      headers: {
        authorization: "Bearer attacker-token",
        "x-service-token": "forged",
        "x-active-tenant": "0192f3a4-0000-7000-8000-00000000dead",
        connection: "keep-alive, x-secret",
        "proxy-authorization": "Basic abc",
        "x-forwarded-for": "10.0.0.1, 6.6.6.6, 198.51.100.40",
        "if-none-match": '"v0"',
      },
    });
    const upstream = h.apiCalls[0];
    expect(upstream?.headers.get("cookie")).toBeNull();
    expect(upstream?.headers.get("proxy-authorization")).toBeNull();
    // Never the browser's chain as sent: only the address the trusted proxy appended (P2-07).
    expect(upstream?.headers.get("x-forwarded-for")).toBe("198.51.100.40");
    expect(upstream?.headers.get("authorization")).not.toBe("Bearer attacker-token");
    expect(upstream?.headers.get("x-service-token")).not.toBe("forged");
    expect(upstream?.headers.get("x-active-tenant")).toBe(TENANT);
    expect(upstream?.headers.get("if-none-match")).toBe('"v0"');

    expect(response.headers.get("set-cookie")).toBeNull();
    expect(response.headers.get("x-powered-by")).toBeNull();
    expect(response.headers.get("etag")).toBe('"v1"');
  });

  describe("CSRF (docs/07 §5.2)", () => {
    it.each(["POST", "PUT", "PATCH", "DELETE"])(
      "%s without the token is refused",
      async (method) => {
        await h.signIn("staff", clerk);
        h.apiCalls.length = 0;
        const response = await call("/bff/api/v1/classes", { method, body: "{}" });
        expect(response.status).toBe(403);
        await expect(response.json()).resolves.toMatchObject({ code: "csrf_failed" });
        expect(h.apiCalls).toHaveLength(0);
      },
    );

    it("a wrong token is refused", async () => {
      await h.signIn("staff", clerk);
      const response = await call("/bff/api/v1/classes", {
        method: "POST",
        body: "{}",
        headers: { "x-csrf-token": "A".repeat(43) },
      });
      expect(response.status).toBe(403);
    });

    it("the right token passes and the body is forwarded", async () => {
      await h.signIn("staff", clerk);
      h.apiCalls.length = 0;
      h.setApi(async (request) => json({ echoed: await request.json() }, 201));
      const response = await call("/bff/api/v1/classes", {
        method: "POST",
        body: JSON.stringify({ name: "Class 6" }),
        headers: {
          "x-csrf-token": await h.csrf(),
          "content-type": "application/json",
          "idempotency-key": "k-1",
        },
      });
      expect(response.status).toBe(201);
      await expect(response.json()).resolves.toEqual({ echoed: { name: "Class 6" } });
      expect(h.apiCalls[0]?.headers.get("idempotency-key")).toBe("k-1");
    });
  });

  it("caps request bodies at 1 MiB", async () => {
    await h.signIn("staff", clerk);
    const response = await call("/bff/api/v1/classes", {
      method: "POST",
      body: "x".repeat(MAX_REQUEST_BODY_BYTES + 1),
      headers: { "x-csrf-token": await h.csrf() },
    });
    expect(response.status).toBe(413);
  });

  describe("session kinds", () => {
    it("staff sessions cannot call /platform/*", async () => {
      await h.signIn("staff", clerk);
      h.apiCalls.length = 0;
      const response = await call("/bff/api/v1/platform/tenants");
      expect(response.status).toBe(403);
      await expect(response.json()).resolves.toMatchObject({ code: "wrong_session" });
      expect(h.apiCalls).toHaveLength(0);
    });

    it("operator sessions can call only /platform/*", async () => {
      await h.signIn("operator", operator);
      h.apiCalls.length = 0;
      expect((await call("/bff/api/v1/platform/dashboard")).status).toBe(200);
      expect(h.apiCalls[0]?.headers.get("x-active-tenant")).toBeNull();
      const denied = await call("/bff/api/v1/users");
      expect(denied.status).toBe(403);
      expect(h.apiCalls).toHaveLength(1);
    });

    it("edge-agent routes are machine-only: 404 and never proxied (ADR-0032)", async () => {
      await h.signIn("staff", clerk);
      h.apiCalls.length = 0;
      for (const path of ["/bff/api/v1/edge/tally/config", "/bff/api/v1/edge/tally/syncs"]) {
        const response = await call(path, { method: "GET" });
        expect(response.status).toBe(404);
      }
      expect(h.apiCalls).toHaveLength(0);
    });

    it("edge-agent routes stay unproxied when the path is percent-encoded (ADR-0032)", async () => {
      // The API decodes the path before routing, so /api/v1/%65dge/... IS /api/v1/edge/...
      await h.signIn("staff", clerk);
      h.apiCalls.length = 0;
      for (const path of [
        "/bff/api/v1/%65dge/tally/config",
        "/bff/api/v1/%65%64%67%65/tally/syncs",
        "/bff/api/v1/edg%65",
      ]) {
        const response = await call(path, { method: "GET" });
        expect(response.status).toBe(404);
      }
      const malformed = await call("/bff/api/v1/%e0%a4/tally", { method: "GET" });
      expect(malformed.status).toBe(400);
      expect(h.apiCalls).toHaveLength(0);
    });

    it("platform routes are 404 on dedicated hosts", async () => {
      const dedicated = await createHarness({ SOS_DEPLOYMENT_MODE: "dedicated" });
      const response = await proxyToApi(
        dedicated.request("/bff/api/v1/platform/dashboard"),
        dedicated.runtime,
      );
      expect(response.status).toBe(404);
    });
  });

  it.each([
    "/bff/api/v1/users/%2e%2e/platform/tenants",
    "/bff/api/v1/users/..%2Fplatform",
    "/bff/api/v1/users%5c..%5cplatform",
  ])("never lets encoded traversal reach another API path: %s", async (path) => {
    await h.signIn("staff", clerk);
    h.apiCalls.length = 0;
    const response = await call(path);
    // Either refused outright (400) or normalised first and then refused as a platform
    // path for a staff session (403); the API is never called.
    expect([400, 403]).toContain(response.status);
    expect(h.apiCalls).toHaveLength(0);
  });

  it("maps 428 step_up_required to a problem with step_up_url", async () => {
    await h.signIn("staff", clerk);
    h.setApi(() =>
      json({ type: "about:blank", title: "Step-up", status: 428, code: "step_up_required" }, 428),
    );
    const response = await call("/bff/api/v1/users", {
      headers: { referer: "https://office.school.example/settings/users" },
    });
    expect(response.status).toBe(428);
    await expect(response.json()).resolves.toMatchObject({
      code: "step_up_required",
      step_up_url: "/bff/auth/step-up?next=%2Fsettings%2Fusers",
    });
  });

  it("uses the operator step-up route for platform calls", async () => {
    await h.signIn("operator", operator);
    h.setApi(() => json({ code: "step_up_required" }, 428));
    const response = await call("/bff/api/v1/platform/tenants", {
      method: "POST",
      body: "{}",
      headers: {
        "x-csrf-token": await h.csrf("operator"),
        referer: "https://evil.example/platform/schools",
      },
    });
    await expect(response.json()).resolves.toMatchObject({
      step_up_url: "/bff/auth/platform/step-up?next=%2Fplatform",
    });
  });

  it("refreshes once and retries when the API says token_expired", async () => {
    await h.signIn("staff", clerk);
    h.apiCalls.length = 0;
    let first = true;
    h.setApi(() => {
      if (first) {
        first = false;
        return json({ status: 401, code: "token_expired" }, 401);
      }
      return json({ data: [], next_cursor: null });
    });
    const tokenCallsBefore = h.idp.staff.tokenRequests.length;
    const response = await call("/bff/api/v1/users");
    expect(response.status).toBe(200);
    expect(h.apiCalls).toHaveLength(2);
    expect(h.idp.staff.tokenRequests.length - tokenCallsBefore).toBe(1);
    expect(h.idp.staff.tokenRequests.at(-1)?.get("grant_type")).toBe("refresh_token");
    expect(h.apiCalls[1]?.headers.get("authorization")).not.toBe(
      h.apiCalls[0]?.headers.get("authorization"),
    );
  });

  it("does not loop when the API keeps saying token_expired", async () => {
    await h.signIn("staff", clerk);
    h.apiCalls.length = 0;
    h.setApi(() => json({ status: 401, code: "token_expired" }, 401));
    const response = await call("/bff/api/v1/users");
    expect(response.status).toBe(401);
    expect(h.apiCalls).toHaveLength(2);
  });

  it("returns 401 when the refresh token was reused and the family is revoked", async () => {
    await h.signIn("staff", clerk);
    const session = (await h.runtime.store.load(h.jar.get("__Host-sos_session"), {
      touch: false,
    }))!;
    const stored = (await h.runtime.store.tokens(session.id))!;
    await h.runtime.store.spendRefreshToken(session.familyId, stored.tokens.refreshToken ?? "");
    await h.runtime.store.saveTokens(session, stored.tokens, Date.now() + 1_000);
    const response = await call("/bff/api/v1/users");
    expect(response.status).toBe(401);
    expect(await h.runtime.store.get(session.id)).toBeNull();
  });

  it("streams text/event-stream without buffering", async () => {
    await h.signIn("staff", clerk);
    let push!: (chunk: string) => void;
    let close!: () => void;
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        push = (chunk) => controller.enqueue(new TextEncoder().encode(chunk));
        close = () => controller.close();
      },
    });
    h.setApi(() => new Response(stream, { headers: { "content-type": "text/event-stream" } }));
    const response = await call("/bff/api/v1/knowledge/ask");
    expect(response.headers.get("content-type")).toBe("text/event-stream");
    expect(response.headers.get("cache-control")).toBe("no-cache, no-transform");
    const reader = response.body!.getReader();
    push("data: first\n\n");
    const first = await reader.read();
    expect(new TextDecoder().decode(first.value)).toBe("data: first\n\n");
    close();
    expect((await reader.read()).done).toBe(true);
  });

  describe("Ask the school over SSE (FR-KB-008, SEC-004)", () => {
    function openStream() {
      let push!: (chunk: string) => void;
      const stream = new ReadableStream<Uint8Array>({
        start(controller) {
          push = (chunk) => controller.enqueue(new TextEncoder().encode(chunk));
        },
      });
      return { stream, push: (chunk: string) => push(chunk) };
    }

    it("POST /knowledge/ask needs the CSRF token and never reaches the API without it", async () => {
      await h.signIn("staff", clerk);
      h.apiCalls.length = 0;
      const response = await call("/bff/api/v1/knowledge/ask", {
        method: "POST",
        headers: { "content-type": "application/json", accept: "text/event-stream" },
        body: JSON.stringify({ question: "q", session_id: TENANT }),
      });
      expect(response.status).toBe(403);
      expect(h.apiCalls).toHaveLength(0);
    });

    it("forwards the question in the body with the tokens and streams events as they arrive", async () => {
      await h.signIn("staff", clerk);
      h.apiCalls.length = 0;
      const upstream = openStream();
      h.setApi(
        () =>
          new Response(upstream.stream, {
            headers: {
              "content-type": "text/event-stream; charset=utf-8",
              "set-cookie": "leak=1",
            },
          }),
      );
      const response = await call("/bff/api/v1/knowledge/ask", {
        method: "POST",
        headers: {
          "content-type": "application/json",
          accept: "text/event-stream",
          "x-csrf-token": await h.csrf(),
        },
        body: JSON.stringify({ question: "When do exams begin?", session_id: TENANT }),
      });
      const [sent] = h.apiCalls;
      expect(sent?.url).toBe("http://api.internal:8000/api/v1/knowledge/ask");
      expect(sent?.headers.get("accept")).toBe("text/event-stream");
      expect(sent?.headers.get("authorization")).toMatch(/^Bearer /);
      await expect(sent?.json()).resolves.toMatchObject({ question: "When do exams begin?" });

      expect(response.status).toBe(200);
      expect(response.headers.get("content-type")).toBe("text/event-stream; charset=utf-8");
      expect(response.headers.get("cache-control")).toBe("no-cache, no-transform");
      expect(response.headers.get("x-accel-buffering")).toBe("no");
      expect(response.headers.get("set-cookie")).toBeNull();
      const reader = response.body!.getReader();
      upstream.push('event: meta\ndata: {"query_id":"q1","language":"en","mode":"full"}\n\n');
      const first = await reader.read();
      expect(new TextDecoder().decode(first.value)).toContain("event: meta");
      upstream.push('event: token\ndata: {"text":"Soon."}\n\n');
      const second = await reader.read();
      expect(new TextDecoder().decode(second.value)).toContain("Soon.");
      await reader.cancel();
    });

    it("aborts the API call when the browser stops the answer", async () => {
      await h.signIn("staff", clerk);
      let upstreamSignal: AbortSignal | undefined;
      h.setApi((request) => {
        upstreamSignal = request.signal;
        return new Response(openStream().stream, {
          headers: { "content-type": "text/event-stream" },
        });
      });
      const browser = new AbortController();
      const response = await call("/bff/api/v1/knowledge/ask", {
        method: "POST",
        headers: { "content-type": "application/json", "x-csrf-token": await h.csrf() },
        body: JSON.stringify({ question: "q", session_id: TENANT }),
        signal: browser.signal,
      });
      expect(response.status).toBe(200);
      expect(upstreamSignal?.aborted).toBe(false);
      browser.abort();
      expect(upstreamSignal?.aborted).toBe(true);
    });

    it("passes a 429 ai_rate_limited problem through unchanged", async () => {
      await h.signIn("staff", clerk);
      h.setApi(() =>
        json({ code: "ai_rate_limited", status: 429, title: "Too many questions" }, 429, {
          "content-type": "application/problem+json",
          "retry-after": "30",
        }),
      );
      const response = await call("/bff/api/v1/knowledge/ask", {
        method: "POST",
        headers: { "content-type": "application/json", "x-csrf-token": await h.csrf() },
        body: JSON.stringify({ question: "q", session_id: TENANT }),
      });
      expect(response.status).toBe(429);
      expect(response.headers.get("retry-after")).toBe("30");
      await expect(response.json()).resolves.toMatchObject({ code: "ai_rate_limited" });
    });
  });

  it("rewrites API Location headers to the BFF and drops foreign ones", async () => {
    await h.signIn("staff", clerk);
    h.setApi(() => json({}, 202, { location: "/api/v1/jobs/j1" }));
    const response = await call("/bff/api/v1/jobs", {
      method: "POST",
      headers: { "x-csrf-token": await h.csrf() },
    });
    expect(response.headers.get("location")).toBe("/bff/api/v1/jobs/j1");
    h.setApi(() => json({}, 302, { location: "https://evil.example/" }));
    expect((await call("/bff/api/v1/jobs")).headers.get("location")).toBeNull();
  });

  it("answers 502 when the API is unreachable", async () => {
    await h.signIn("staff", clerk);
    h.setApi(() => {
      throw new TypeError("fetch failed");
    });
    const response = await call("/bff/api/v1/users");
    expect(response.status).toBe(502);
    expect(await response.text()).not.toContain("fetch failed");
  });
});

describe("BFF proxy: the school's idle timeout follows GET /me (FR-TEN-012, FR-IAM-003)", () => {
  async function idleMs(): Promise<number> {
    const { handleSessionInfo } = await import("@/server/auth/handlers");
    const response = await handleSessionInfo(h.request("/bff/auth/session?kind=staff"), h.runtime);
    return ((await response.json()) as { idle_timeout_ms: number }).idle_timeout_ms;
  }
  const me = (tenant: string, minutes: number) => ({
    user_id: "0192f3a4-0000-7000-8000-0000000000d1",
    tenant_id: tenant,
    settings: { idle_timeout_minutes: minutes, date_format: "DD/MM/YYYY", languages: ["en"] },
  });

  it("applies the idle timeout from a GET /me answer about the active school", async () => {
    await h.signIn("staff", clerk);
    const before = await idleMs();
    h.setApi(() => json(me(TENANT, 7)));
    const response = await call("/bff/api/v1/me");
    expect(response.status).toBe(200);
    // The browser still gets the whole answer.
    await expect(response.json()).resolves.toMatchObject({ tenant_id: TENANT });
    expect(await idleMs()).toBe(7 * 60_000);
    expect(before).not.toBe(7 * 60_000);
  });

  it("ignores answers about another school, failures and other paths", async () => {
    await h.signIn("staff", clerk);
    const before = await idleMs();
    h.setApi(() => json(me("0192f3a4-0000-7000-8000-0000000000ff", 7)));
    await call("/bff/api/v1/me");
    h.setApi(() => json({ code: "forbidden", ...me(TENANT, 7) }, 403));
    await call("/bff/api/v1/me");
    h.setApi(() => json(me(TENANT, 7)));
    await call("/bff/api/v1/users");
    expect(await idleMs()).toBe(before);
  });
});

describe("BFF proxy: background polls and API pages (FR-NOT-001, FR-CR-005)", () => {
  it("a passive GET reads the session without sliding the idle timeout", async () => {
    await h.signIn("staff", clerk);
    const load = vi.spyOn(h.runtime.store, "load");
    await call("/bff/api/v1/notifications/unread-count", { headers: { "x-sos-passive": "1" } });
    expect(load).toHaveBeenLastCalledWith(expect.any(String), { touch: false });
    await call("/bff/api/v1/notifications/unread-count");
    expect(load).toHaveBeenLastCalledWith(expect.any(String), { touch: true });
    // Writes always count as activity, whatever the header says.
    await call("/bff/api/v1/notifications/read-all", {
      method: "POST",
      headers: { "x-sos-passive": "1", "x-csrf-token": await h.csrf() },
    });
    expect(load).toHaveBeenLastCalledWith(expect.any(String), { touch: true });
    // The marker never reaches the API.
    expect(h.apiCalls.every((request) => request.headers.get("x-sos-passive") === null)).toBe(true);
  });

  it("keeps an API page's own strict CSP and replaces anything looser", async () => {
    await h.signIn("staff", clerk);
    const memoPolicy =
      "default-src 'none'; style-src 'sha256-abc='; img-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'";
    h.setApi(
      () =>
        new Response("<!doctype html><p>memo</p>", {
          headers: {
            "content-type": "text/html; charset=utf-8",
            "content-security-policy": memoPolicy,
            "content-disposition": 'inline; filename="correction-memo-0192f3a4.html"',
          },
        }),
    );
    const memo = await call(
      "/bff/api/v1/change-requests/0192f3a4-0000-7000-8000-00000000c001/memo",
    );
    expect(memo.headers.get("content-security-policy")).toBe(memoPolicy);
    expect(memo.headers.get("content-disposition")).toContain("inline");

    h.setApi(
      () =>
        new Response("<p>x</p>", {
          headers: { "content-type": "text/html", "content-security-policy": "default-src *" },
        }),
    );
    const loose = await call(
      "/bff/api/v1/change-requests/0192f3a4-0000-7000-8000-00000000c001/memo",
    );
    expect(loose.headers.get("content-security-policy")).toContain("default-src 'none'");
    expect(loose.headers.get("content-security-policy")).not.toContain("default-src *");
  });

  it("isStrictPolicy needs default-src 'none' and frame-ancestors 'none'", () => {
    expect(isStrictPolicy("default-src 'none'; frame-ancestors 'none'")).toBe(true);
    expect(isStrictPolicy("default-src 'none'")).toBe(false);
    expect(isStrictPolicy("default-src 'self'; frame-ancestors 'none'")).toBe(false);
    expect(isStrictPolicy(null)).toBe(false);
  });

  it("isStrictPolicy allows only 'none' and hash-pinned blocks in every directive", () => {
    // The API's memo policy (apps/api/app/changes/memo.py STYLE_CSP).
    expect(
      isStrictPolicy(
        "default-src 'none'; style-src 'sha256-AbC+/12='; img-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
      ),
    ).toBe(true);
    for (const loose of [
      "default-src 'none'; script-src 'unsafe-inline'; frame-ancestors 'none'",
      "default-src 'none'; script-src *; frame-ancestors 'none'",
      "default-src 'none'; img-src https://evil.example; frame-ancestors 'none'",
      "default-src 'none'; style-src 'self'; frame-ancestors 'none'",
      "default-src 'none'; script-src 'nonce-abc'; frame-ancestors 'none'",
      "default-src 'none'; connect-src data:; frame-ancestors 'none'",
      "default-src 'none' *; frame-ancestors 'none'",
      "default-src 'none'; frame-ancestors 'none'; default-src *",
      "default-src 'none'; sandbox; frame-ancestors 'none'",
    ]) {
      expect(isStrictPolicy(loose), loose).toBe(false);
    }
  });

  it("every response on an API page path carries a CSP, also when the BFF answers itself", async () => {
    // Signed out: the BFF answers 401 itself; src/proxy.ts sets no CSP on this path.
    const signedOut = await call(
      "/bff/api/v1/change-requests/0192f3a4-0000-7000-8000-00000000c001/memo",
    );
    expect(signedOut.status).toBe(401);
    expect(signedOut.headers.get("content-security-policy")).toContain("default-src 'none'");
  });
});
