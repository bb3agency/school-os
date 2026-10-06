// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createHarness, type Harness } from "@/test/bff-harness";
import { callApi } from "@/server/bff/upstream";
import { MemoryKeyValue, type KeyValue } from "@/server/session/kv";
import { handleCallback, handleLogin, handleStepUp } from "./handlers";
import { AUTH_RATE_LIMITS, AuthLimiter, clientIp, ipHash } from "./rate-limit";

/** Sign-in rate limits and failure backoff in the BFF (P2-07; ASVS 2.2.1). Synthetic only. */

describe("clientIp", () => {
  const headers = (xff?: string) => new Headers(xff ? { "x-forwarded-for": xff } : {});

  it("takes the entry the trusted proxy appended, never the client's own", () => {
    expect(clientIp(headers("6.6.6.6, 198.51.100.7"), 1)).toBe("198.51.100.7");
    expect(clientIp(headers("198.51.100.7, 10.0.0.9"), 2)).toBe("198.51.100.7");
  });

  it("is unknown without a proxy, a header or a valid address", () => {
    expect(clientIp(headers("198.51.100.7"), 0)).toBe("unknown");
    expect(clientIp(headers(), 1)).toBe("unknown");
    expect(clientIp(headers("not-an-ip"), 1)).toBe("unknown");
  });
});

describe("ipHash", () => {
  it("matches the API's keyed hash (app/core/ratelimit.py) for the same key", () => {
    const key = new TextEncoder().encode("synthetic-service-token-key-for-tests-0123");
    expect(ipHash(key, "198.51.100.7")).toBe("90ea39d18681baac");
  });
});

describe("AuthLimiter", () => {
  it("limits starts per IP within the window, then lets them through again", async () => {
    let now = 1_000_000;
    const limiter = new AuthLimiter(new MemoryKeyValue(() => now), () => now);
    const { limit, windowS } = AUTH_RATE_LIMITS.perIp;
    for (let i = 0; i < limit; i += 1) expect(await limiter.check("aaaa")).toBe(0);
    expect(await limiter.check("aaaa")).toBe(windowS);
    expect(await limiter.check("bbbb")).toBe(0);
    now += windowS * 1000;
    expect(await limiter.check("aaaa")).toBe(0);
  });

  it("backs off exponentially after the free failures and clears on success", async () => {
    let now = 1_000_000;
    const limiter = new AuthLimiter(new MemoryKeyValue(() => now), () => now);
    const { free, baseDelayS } = AUTH_RATE_LIMITS.failures;
    for (let i = 0; i < free; i += 1) expect(await limiter.fail("aaaa")).toBe(0);
    expect(await limiter.fail("aaaa")).toBe(baseDelayS);
    expect(await limiter.fail("aaaa")).toBe(baseDelayS * 2);
    expect(await limiter.check("aaaa")).toBe(baseDelayS * 2);
    now += baseDelayS * 2 * 1000;
    expect(await limiter.check("aaaa")).toBe(0);
    await limiter.clear("aaaa");
    expect(await limiter.fail("aaaa")).toBe(0);
  });

  it("fails closed to a per-process limiter while Valkey is down", async () => {
    const down = new Proxy({} as KeyValue, {
      get: () => () => Promise.reject(new Error("valkey down")),
    });
    const limiter = new AuthLimiter(down, () => 1_000_000);
    const { limit } = AUTH_RATE_LIMITS.perIp;
    for (let i = 0; i < limit; i += 1) await limiter.check("aaaa");
    expect(await limiter.check("aaaa")).toBeGreaterThan(0);
  });
});

describe("sign-in routes", () => {
  let h: Harness;
  let warn: { mock: { calls: unknown[][] }; mockRestore: () => void };

  beforeEach(async () => {
    vi.stubEnv("SOS_WEB_TEST_LOGS", "1");
    h = await createHarness();
    warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
  });

  afterEach(() => {
    warn.mockRestore();
    vi.unstubAllEnvs();
  });

  const events = (name: string) =>
    warn.mock.calls
      .map((args: unknown[]) => JSON.parse(String(args[0])) as Record<string, unknown>)
      .filter((line: Record<string, unknown>) => line.event === name);

  const from = (ip: string) => ({ headers: { "x-forwarded-for": ip } });

  it("refused callbacks log signin_failed with an ip hash and then back off", async () => {
    const { free } = AUTH_RATE_LIMITS.failures;
    for (let i = 0; i <= free; i += 1) {
      const response = await handleCallback(
        h.request("/bff/auth/callback?code=x&state=y", from("198.51.100.7")),
        h.runtime,
        "staff",
      );
      expect(response.headers.get("location")).toContain("error=signin_expired");
    }
    const failed = events("signin_failed");
    expect(failed).toHaveLength(free + 1);
    expect(failed[0]?.ip_hash).toMatch(/^[0-9a-f]{16}$/);
    expect(JSON.stringify(failed)).not.toContain("198.51.100.7");
    expect(failed.at(-1)?.retry_after_s).toBe(AUTH_RATE_LIMITS.failures.baseDelayS);

    const waiting = await handleLogin(
      h.request("/bff/auth/login", from("198.51.100.7")),
      h.runtime,
      "staff",
    );
    expect(waiting.headers.get("location")).toContain("error=too_many_attempts");
    expect(events("auth_rate_limited")).toHaveLength(1);
    // Another address is not affected (no lockout of other people).
    const other = await handleLogin(
      h.request("/bff/auth/login", from("198.51.100.8")),
      h.runtime,
      "staff",
    );
    expect(other.headers.get("location")).toContain("idp.example");
  });

  it("limits step-up starts per IP too", async () => {
    const { limit } = AUTH_RATE_LIMITS.perIp;
    for (let i = 0; i < limit; i += 1) {
      await h.runtime.authLimiter.check(ipHash(h.runtime.config.serviceTokenKey, "198.51.100.9"));
    }
    const response = await handleStepUp(
      h.request("/bff/auth/step-up", from("198.51.100.9")),
      h.runtime,
      "staff",
    );
    expect(response.headers.get("location")).toContain("error=too_many_attempts");
  });
});

describe("BFF to API", () => {
  it("forwards only the client address its own proxy vouches for", async () => {
    const h = await createHarness();
    const call = (incoming?: Headers) =>
      callApi(h.runtime, {
        session: { kind: "staff", activeTenantId: null } as never,
        accessToken: "synthetic-access-token",
        method: "GET",
        path: "/api/v1/me",
        requestId: "req_synthetic_xff",
        ...(incoming ? { incomingHeaders: incoming } : {}),
      });
    await call(new Headers({ "x-forwarded-for": "6.6.6.6, 198.51.100.7" }));
    await call();
    const [withBrowser, serverSide] = h.apiCalls.slice(-2);
    expect(withBrowser?.headers.get("x-forwarded-for")).toBe("198.51.100.7");
    expect(serverSide?.headers.has("x-forwarded-for")).toBe(false);
  });
});
