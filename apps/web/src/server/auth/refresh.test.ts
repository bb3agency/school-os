// @vitest-environment node
import { randomBytes } from "node:crypto";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryKeyValue } from "@/server/session/kv";
import { SessionStore, type Session } from "@/server/session/store";
import { OidcGrantError, type OidcClient, type OidcTokens } from "./oidc";
import { RefreshBusyError, SessionEndedError, TokenRefresher } from "./refresh";

const MINUTE = 60_000;
let kv: MemoryKeyValue;
let store: SessionStore;
let secret: Buffer;
let issued: number;

/** Fake IdP token endpoint: rotates refresh tokens and records every grant. */
function fakeIdp(options: { rotate?: boolean; delayMs?: number } = {}) {
  const calls: string[] = [];
  const refresh = vi.fn(async (refreshToken: string): Promise<OidcTokens> => {
    calls.push(refreshToken);
    if (options.delayMs) await new Promise((resolve) => setTimeout(resolve, options.delayMs));
    issued += 1;
    return {
      accessToken: `access-${issued}`,
      refreshToken: options.rotate === false ? null : `refresh-${issued}`,
      idToken: null,
      accessExpiresAt: Date.now() + 10 * MINUTE,
      idClaims: null,
    };
  });
  const client: OidcClient = {
    kind: "staff",
    authorizationUrl: vi.fn(),
    exchangeCode: vi.fn(),
    refresh,
    endSessionUrl: vi.fn(),
    revoke: vi.fn(),
  };
  return { client, calls, refresh };
}

function refresherFor(client: OidcClient, sharedStore = store) {
  return new TokenRefresher({
    store: sharedStore,
    oidc: { staff: client, operator: client },
    pollMs: 2,
  });
}

async function newSession(
  accessExpiresInMs: number,
): Promise<{ cookie: string; session: Session }> {
  const { cookieValue, session } = await store.create({
    kind: "staff",
    subject: "sub-1",
    issuer: "https://idp.example/staff",
    displayName: null,
    authTime: null,
    mfa: false,
    tokens: { accessToken: "access-0", refreshToken: "refresh-0", idToken: "id-0" },
    accessExpiresAt: Date.now() + accessExpiresInMs,
  });
  return { cookie: cookieValue, session };
}

beforeEach(() => {
  issued = 0;
  kv = new MemoryKeyValue();
  secret = randomBytes(32);
  store = new SessionStore(kv, secret);
});

describe("token refresh (FR-IAM-004)", () => {
  it("does nothing while the access token has more than 60 s left", async () => {
    const idp = fakeIdp();
    const { session } = await newSession(5 * MINUTE);
    expect(await refresherFor(idp.client).ensureFresh(session)).toBe(session);
    expect(idp.refresh).not.toHaveBeenCalled();
  });

  it("rotates the refresh token and stores the new tokens encrypted", async () => {
    const idp = fakeIdp();
    const { session } = await newSession(30_000);
    const fresh = await refresherFor(idp.client).ensureFresh(session);
    expect(idp.calls).toEqual(["refresh-0"]);
    expect(fresh.accessExpiresAt).toBeGreaterThan(Date.now() + 9 * MINUTE);
    const stored = await store.tokens(session.id);
    expect(stored?.tokens).toEqual({
      accessToken: "access-1",
      refreshToken: "refresh-1",
      idToken: "id-0",
    });
    expect(JSON.stringify(kv.dump())).not.toContain("refresh-1");
  });

  it("revokes the whole family when a spent refresh token is presented again", async () => {
    const idp = fakeIdp();
    const { cookie, session } = await newSession(30_000);
    // A second session in the same family (e.g. after step-up re-authentication).
    const sibling = await store.create({
      kind: "staff",
      subject: "sub-1",
      issuer: "https://idp.example/staff",
      displayName: null,
      authTime: null,
      mfa: false,
      tokens: { accessToken: "a", refreshToken: "r", idToken: null },
      accessExpiresAt: Date.now() + 10 * MINUTE,
      familyId: session.familyId,
    });
    const refresher = refresherFor(idp.client);
    await refresher.ensureFresh(session);

    // Replay: an old copy of the session's tokens (refresh-0) comes back into use.
    await store.saveTokens(
      session,
      { accessToken: "access-0", refreshToken: "refresh-0", idToken: null },
      Date.now() + 1_000,
    );
    const replayed = await store.load(cookie, { touch: false });
    await expect(refresher.ensureFresh(replayed as Session)).rejects.toMatchObject({
      reason: "refresh_reuse",
    });
    expect(idp.calls).toEqual(["refresh-0"]);
    expect(await store.load(cookie, { touch: false })).toBeNull();
    expect(await store.load(sibling.cookieValue, { touch: false })).toBeNull();
  });

  it("makes one upstream call for concurrent requests in one process", async () => {
    const idp = fakeIdp({ delayMs: 20 });
    const { session } = await newSession(10_000);
    const refresher = refresherFor(idp.client);
    const results = await Promise.all(
      Array.from({ length: 5 }, () => refresher.ensureFresh(session)),
    );
    expect(idp.refresh).toHaveBeenCalledTimes(1);
    expect(new Set(results.map((s) => s.accessExpiresAt)).size).toBe(1);
  });

  it("makes one upstream call across web tasks sharing Valkey (SET NX PX lock)", async () => {
    const idp = fakeIdp({ delayMs: 30 });
    const { session } = await newSession(10_000);
    // Two tasks: separate refreshers and store objects over the same key-value store.
    const otherStore = new SessionStore(kv, secret);
    const [a, b] = await Promise.all([
      refresherFor(idp.client).ensureFresh(session),
      refresherFor(idp.client, otherStore).ensureFresh(session),
    ]);
    expect(idp.refresh).toHaveBeenCalledTimes(1);
    expect(a.accessExpiresAt).toBe(b.accessExpiresAt);
    expect((await store.tokens(session.id))?.tokens.refreshToken).toBe("refresh-1");
    expect(Object.keys(kv.dump()).some((k) => k.includes(":lock:"))).toBe(false);
  });

  it("gives up waiting for a stuck lock with a retryable error", async () => {
    const idp = fakeIdp();
    const { session } = await newSession(10_000);
    await kv.set(`sos:web:sess:lock:${session.id}`, "someone-else", { pxMs: 60_000 });
    const refresher = new TokenRefresher({
      store,
      oidc: { staff: idp.client, operator: idp.client },
      pollMs: 2,
      lockWaitMs: 20,
    });
    await expect(refresher.ensureFresh(session)).rejects.toBeInstanceOf(RefreshBusyError);
    expect(idp.refresh).not.toHaveBeenCalled();
  });

  it("keeps a non-rotated refresh token usable (IdP without rotation)", async () => {
    const idp = fakeIdp({ rotate: false });
    const { session } = await newSession(10_000);
    const refresher = refresherFor(idp.client);
    const once = await refresher.ensureFresh(session);
    await store.saveTokens(
      once,
      { accessToken: "access-1", refreshToken: "refresh-0", idToken: null },
      Date.now() + 1_000,
    );
    await refresher.ensureFresh({ ...once, accessExpiresAt: Date.now() + 1_000 });
    expect(idp.calls).toEqual(["refresh-0", "refresh-0"]);
  });

  it("ends the session when the IdP refuses the refresh token", async () => {
    const idp = fakeIdp();
    idp.refresh.mockRejectedValueOnce(new OidcGrantError("invalid_grant"));
    const { cookie, session } = await newSession(10_000);
    await expect(refresherFor(idp.client).ensureFresh(session)).rejects.toBeInstanceOf(
      SessionEndedError,
    );
    expect(await store.load(cookie, { touch: false })).toBeNull();
  });

  it("forces a refresh after token_expired unless another request already did", async () => {
    const idp = fakeIdp();
    const { session } = await newSession(5 * MINUTE);
    const refresher = refresherFor(idp.client);
    await refresher.ensureFresh(session, { force: true, staleAccessToken: "access-0" });
    expect(idp.refresh).toHaveBeenCalledTimes(1);
    const current = (await store.get(session.id)) as Session;
    await refresher.ensureFresh(current, { force: true, staleAccessToken: "access-0" });
    expect(idp.refresh).toHaveBeenCalledTimes(1);
  });
});
