// @vitest-environment node
import { randomBytes } from "node:crypto";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { connectRedis, type RedisKeyValue } from "./redis";
import { SessionStore } from "./store";

/**
 * Integration test against a real Valkey. Runs only when SOS_WEB_TEST_REDIS_URL is set,
 * e.g. `docker run --rm -p 6390:6379 valkey/valkey:8.1-alpine` and
 * `SOS_WEB_TEST_REDIS_URL=redis://localhost:6390/15 npm test -w @schoolos/web`.
 */
const url = process.env.SOS_WEB_TEST_REDIS_URL;

describe.skipIf(!url)("RedisKeyValue against Valkey (ADR-0014)", () => {
  let kv: RedisKeyValue;
  const prefix = `sos:web:sess:test:${randomBytes(6).toString("hex")}:`;

  beforeAll(async () => {
    kv = await connectRedis(url as string);
  });

  afterAll(async () => {
    await kv.del(`${prefix}a`, `${prefix}lock`, `${prefix}set`);
  });

  it("SET NX PX, GET, compare-and-delete", async () => {
    expect(await kv.set(`${prefix}lock`, "me", { nx: true, pxMs: 5_000 })).toBe(true);
    expect(await kv.set(`${prefix}lock`, "other", { nx: true, pxMs: 5_000 })).toBe(false);
    expect(await kv.get(`${prefix}lock`)).toBe("me");
    expect(await kv.delIfEquals(`${prefix}lock`, "other")).toBe(false);
    expect(await kv.delIfEquals(`${prefix}lock`, "me")).toBe(true);
    expect(await kv.get(`${prefix}lock`)).toBeNull();
  });

  it("expires keys and handles sets", async () => {
    await kv.set(`${prefix}a`, "1", { pxMs: 50 });
    await new Promise((resolve) => setTimeout(resolve, 120));
    expect(await kv.get(`${prefix}a`)).toBeNull();
    expect(await kv.sadd(`${prefix}set`, "x")).toBe(1);
    expect(await kv.sadd(`${prefix}set`, "x")).toBe(0);
    expect(await kv.smembers(`${prefix}set`)).toEqual(["x"]);
    await kv.srem(`${prefix}set`, "x");
    expect(await kv.smembers(`${prefix}set`)).toEqual([]);
  });

  it("runs the session store end to end", async () => {
    const store = new SessionStore(kv, randomBytes(32));
    const { cookieValue, session } = await store.create({
      kind: "staff",
      subject: `sub-${randomBytes(4).toString("hex")}`,
      issuer: "https://idp.example/staff",
      displayName: null,
      authTime: null,
      mfa: false,
      tokens: { accessToken: "a", refreshToken: "r", idToken: null },
      accessExpiresAt: Date.now() + 600_000,
    });
    expect((await store.load(cookieValue, { touch: true }))?.id).toBe(session.id);
    await store.revokeFamily(session.familyId);
    expect(await store.load(cookieValue, { touch: false })).toBeNull();
  });
});

describe("connectRedis", () => {
  it("gives up quickly when Valkey is unreachable", async () => {
    const started = Date.now();
    await expect(connectRedis("redis://127.0.0.1:1/0")).rejects.toThrow();
    expect(Date.now() - started).toBeLessThan(5_000);
  }, 10_000);
});
