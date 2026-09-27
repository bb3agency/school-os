// @vitest-environment node
import { randomBytes } from "node:crypto";
import { beforeEach, describe, expect, it } from "vitest";
import { MemoryKeyValue } from "./kv";
import {
  ABSOLUTE_TIMEOUT_MS,
  IDLE_TIMEOUT_MINUTES,
  IDLE_TIMEOUT_MS,
  idleTimeoutMsFromMinutes,
  SESSION_KEY_PREFIX,
  SessionStore,
  type NewSession,
} from "./store";

const MINUTE = 60_000;
let clock: number;
let kv: MemoryKeyValue;
let store: SessionStore;

const ACCESS = `access-${randomBytes(12).toString("hex")}`;
const REFRESH = `refresh-${randomBytes(12).toString("hex")}`;

function staff(overrides: Partial<NewSession> = {}): NewSession {
  return {
    kind: "staff",
    subject: "sub-1",
    issuer: "https://idp.example/staff",
    displayName: "Office Clerk",
    authTime: Math.floor(clock / 1000),
    mfa: false,
    tokens: { accessToken: ACCESS, refreshToken: REFRESH, idToken: null },
    accessExpiresAt: clock + 10 * MINUTE,
    ...overrides,
  };
}

beforeEach(() => {
  clock = Date.UTC(2026, 8, 26, 4, 30);
  kv = new MemoryKeyValue(() => clock);
  store = new SessionStore(kv, randomBytes(32), { now: () => clock });
});

describe("SessionStore (SEC-004, SEC-006, FR-IAM-003)", () => {
  it("creates an opaque 256-bit id and keeps only its hash as the key prefix sos:web:sess:", async () => {
    const { cookieValue, session } = await store.create(staff());
    expect(cookieValue).toMatch(/^[A-Za-z0-9_-]{43}$/);
    const keys = Object.keys(kv.dump());
    expect(keys.every((k) => k.startsWith(SESSION_KEY_PREFIX))).toBe(true);
    expect(keys.some((k) => k.includes(cookieValue))).toBe(false);
    expect(keys).toContain(`${SESSION_KEY_PREFIX}${session.id}`);
  });

  it("stores tokens encrypted at rest", async () => {
    const { session } = await store.create(staff());
    const raw = JSON.stringify(kv.dump());
    expect(raw).not.toContain(ACCESS);
    expect(raw).not.toContain(REFRESH);
    expect((await store.tokens(session.id))?.tokens).toEqual({
      accessToken: ACCESS,
      refreshToken: REFRESH,
      idToken: null,
    });
  });

  it("rejects malformed cookie values without touching the store", async () => {
    expect(await store.load("not-a-session", { touch: true })).toBeNull();
    expect(await store.load(undefined, { touch: true })).toBeNull();
  });

  it("expires after 15 minutes idle and slides on each authenticated request", async () => {
    const { cookieValue } = await store.create(staff());
    clock += 14 * MINUTE;
    const touched = await store.load(cookieValue, { touch: true });
    expect(touched?.idleExpiresAt).toBe(clock + IDLE_TIMEOUT_MS);
    clock += 14 * MINUTE;
    expect(await store.load(cookieValue, { touch: false })).not.toBeNull();
    // A read without touch (session info polling) does not slide the timeout.
    clock += 1 * MINUTE + 1;
    expect(await store.load(cookieValue, { touch: true })).toBeNull();
    // Session keys are gone; index sets are cleaned lazily and expire at the absolute limit.
    expect(Object.keys(kv.dump()).filter((k) => !/:(idx|fam|rt):/.test(k))).toEqual([]);
  });

  it("expires after 12 hours absolute even when active", async () => {
    const { cookieValue } = await store.create(staff());
    for (
      let elapsed = 0;
      elapsed < ABSOLUTE_TIMEOUT_MS.staff - 10 * MINUTE;
      elapsed += 10 * MINUTE
    ) {
      clock += 10 * MINUTE;
      expect(await store.load(cookieValue, { touch: true })).not.toBeNull();
    }
    clock += 10 * MINUTE;
    expect(await store.load(cookieValue, { touch: true })).toBeNull();
  });

  it("gives operator sessions an 8 hour absolute lifetime", async () => {
    const { session } = await store.create(staff({ kind: "operator" }));
    expect(session.absoluteExpiresAt - session.createdAt).toBe(8 * 60 * MINUTE);
  });

  it("lists and revokes a person's own sessions by public handle", async () => {
    const a = await store.create(staff());
    const b = await store.create(staff());
    await store.create(staff({ subject: "someone-else" }));
    const mine = await store.listFor(a.session);
    expect(mine.map((s) => s.handle).sort()).toEqual([a.session.handle, b.session.handle].sort());

    expect(await store.revokeByHandle(a.session, "unknown")).toBeNull();
    const revoked = await store.revokeByHandle(a.session, b.session.handle);
    expect(revoked?.id).toBe(b.session.id);
    expect(await store.load(b.cookieValue, { touch: false })).toBeNull();
    expect(await store.load(a.cookieValue, { touch: false })).not.toBeNull();
  });

  it("revokes every session in a refresh-token family", async () => {
    const first = await store.create(staff());
    const second = await store.create(staff({ familyId: first.session.familyId }));
    const other = await store.create(staff());
    expect(await store.revokeFamily(first.session.familyId)).toBe(2);
    expect(await store.load(first.cookieValue, { touch: false })).toBeNull();
    expect(await store.load(second.cookieValue, { touch: false })).toBeNull();
    expect(await store.load(other.cookieValue, { touch: false })).not.toBeNull();
  });

  it("detects a refresh token spent twice", async () => {
    const { session } = await store.create(staff());
    expect(await store.spendRefreshToken(session.familyId, REFRESH)).toBe(true);
    expect(await store.spendRefreshToken(session.familyId, REFRESH)).toBe(false);
    await store.unspendRefreshToken(session.familyId, REFRESH);
    expect(await store.spendRefreshToken(session.familyId, REFRESH)).toBe(true);
  });

  it("changes the active tenant without touching tokens", async () => {
    const { cookieValue, session } = await store.create(staff());
    await store.setActiveTenant(session, "0192f3a4-0000-7000-8000-000000000001");
    const reloaded = await store.load(cookieValue, { touch: false });
    expect(reloaded?.activeTenantId).toBe("0192f3a4-0000-7000-8000-000000000001");
    expect((await store.tokens(session.id))?.tokens.accessToken).toBe(ACCESS);
  });
});

const SCHOOL = "0192f3a4-0000-7000-8000-000000000001";
const OTHER_SCHOOL = "0192f3a4-0000-7000-8000-000000000002";

describe("per-school idle timeout (FR-IAM-003, FR-TEN-012, SEC-006)", () => {
  async function staffIn(tenant: string, minutes: unknown) {
    const created = await store.create(staff());
    await store.setActiveTenant(created.session, tenant, { idleTimeoutMinutes: minutes });
    return created;
  }

  it("documents the range 5-30 minutes with a 15 minute default", () => {
    expect(IDLE_TIMEOUT_MINUTES).toEqual({ min: 5, max: 30, fallback: 15 });
    expect(IDLE_TIMEOUT_MS).toBe(15 * MINUTE);
  });

  it("clamps out-of-range values into 5-30 minutes and ignores what is not a number", () => {
    expect(idleTimeoutMsFromMinutes(5)).toBe(5 * MINUTE);
    expect(idleTimeoutMsFromMinutes(30)).toBe(30 * MINUTE);
    expect(idleTimeoutMsFromMinutes(4)).toBe(5 * MINUTE);
    expect(idleTimeoutMsFromMinutes(0)).toBe(5 * MINUTE);
    expect(idleTimeoutMsFromMinutes(-10)).toBe(5 * MINUTE);
    expect(idleTimeoutMsFromMinutes(31)).toBe(30 * MINUTE);
    expect(idleTimeoutMsFromMinutes(24 * 60)).toBe(30 * MINUTE);
    expect(idleTimeoutMsFromMinutes(12.7)).toBe(12 * MINUTE);
    for (const value of [null, undefined, "20", Number.NaN, Infinity, -Infinity, {}, true]) {
      expect(idleTimeoutMsFromMinutes(value)).toBeNull();
    }
  });

  it("uses the 15 minute default until the school's setting is known", async () => {
    const { session } = await store.create(staff());
    expect(session.idleTimeoutMs).toBe(15 * MINUTE);
    expect(session.idleExpiresAt).toBe(clock + 15 * MINUTE);
  });

  it("expires at the lower boundary of 5 minutes", async () => {
    const { cookieValue } = await staffIn(SCHOOL, 5);
    clock += 5 * MINUTE - 1;
    const alive = await store.load(cookieValue, { touch: false });
    expect(alive?.idleTimeoutMs).toBe(5 * MINUTE);
    clock += 1;
    expect(await store.load(cookieValue, { touch: false })).toBeNull();
  });

  it("stays signed in up to the upper boundary of 30 minutes, and slides by 30", async () => {
    const { cookieValue } = await staffIn(SCHOOL, 30);
    clock += 29 * MINUTE;
    const touched = await store.load(cookieValue, { touch: true });
    expect(touched?.idleExpiresAt).toBe(clock + 30 * MINUTE);
    clock += 30 * MINUTE - 1;
    expect(await store.load(cookieValue, { touch: false })).not.toBeNull();
    clock += 1;
    expect(await store.load(cookieValue, { touch: false })).toBeNull();
    expect(Object.keys(kv.dump()).filter((k) => !/:(idx|fam|rt):/.test(k))).toEqual([]);
  });

  it("clamps a value above 30 minutes to 30", async () => {
    const { cookieValue } = await staffIn(SCHOOL, 600);
    clock += 30 * MINUTE;
    expect(await store.load(cookieValue, { touch: false })).toBeNull();
  });

  it("falls back to 15 minutes when the setting is missing or malformed", async () => {
    const { cookieValue } = await staffIn(SCHOOL, "thirty");
    clock += 15 * MINUTE - 1;
    expect((await store.load(cookieValue, { touch: false }))?.idleTimeoutMs).toBe(15 * MINUTE);
    clock += 1;
    expect(await store.load(cookieValue, { touch: false })).toBeNull();
  });

  it("switching school resets the timeout to the new school's value, or the default", async () => {
    const { cookieValue, session } = await staffIn(SCHOOL, 30);
    await store.setActiveTenant(session, OTHER_SCHOOL, { idleTimeoutMinutes: 5 });
    expect((await store.load(cookieValue, { touch: false }))?.idleTimeoutMs).toBe(5 * MINUTE);
    // A school whose settings are not known yet never inherits the previous school's value.
    await store.setActiveTenant(session, SCHOOL);
    expect((await store.load(cookieValue, { touch: false }))?.idleTimeoutMs).toBe(15 * MINUTE);
  });

  it("applies /me settings only to the school the session is still in", async () => {
    const { cookieValue, session } = await staffIn(SCHOOL, undefined);
    expect(await store.applySchoolIdleTimeout(session, OTHER_SCHOOL, 30)).toBeNull();
    expect((await store.load(cookieValue, { touch: false }))?.idleTimeoutMs).toBe(15 * MINUTE);

    const applied = await store.applySchoolIdleTimeout(session, SCHOOL, 30);
    expect(applied?.idleTimeoutMs).toBe(30 * MINUTE);
    // Lengthening keeps every session key alive for the new window.
    clock += 29 * MINUTE;
    expect(await store.load(cookieValue, { touch: false })).not.toBeNull();
    clock += 1 * MINUTE;
    expect(await store.load(cookieValue, { touch: false })).toBeNull();
  });

  it("shortening applies at once, counted from the last activity", async () => {
    const { cookieValue, session } = await staffIn(SCHOOL, 30);
    clock += 10 * MINUTE;
    expect(await store.applySchoolIdleTimeout(session, SCHOOL, 5)).toBeNull();
    expect(await store.load(cookieValue, { touch: false })).toBeNull();
  });

  it("never lets the idle window pass the absolute limit", async () => {
    const { cookieValue } = await staffIn(SCHOOL, 30);
    clock += ABSOLUTE_TIMEOUT_MS.staff - 10 * MINUTE;
    const late = await store.load(cookieValue, { touch: true });
    expect(late?.idleExpiresAt).toBe(late?.absoluteExpiresAt);
    clock += 10 * MINUTE;
    expect(await store.load(cookieValue, { touch: false })).toBeNull();
  });

  it("keeps operators on the fixed 15 minutes (docs/07 §5.1)", async () => {
    const { cookieValue, session } = await store.create(staff({ kind: "operator" }));
    await store.setActiveTenant(session, SCHOOL, { idleTimeoutMinutes: 30 });
    expect((await store.load(cookieValue, { touch: false }))?.idleTimeoutMs).toBe(15 * MINUTE);
  });

  it("carries the idle timeout into a new session (step-up), clamped", async () => {
    const { session } = await store.create(staff({ idleTimeoutMs: 30 * MINUTE }));
    expect(session.idleTimeoutMs).toBe(30 * MINUTE);
    const tooLong = await store.create(staff({ idleTimeoutMs: 8 * 60 * MINUTE }));
    expect(tooLong.session.idleTimeoutMs).toBe(30 * MINUTE);
  });

  it("refreshing tokens keeps them for the session's own idle window", async () => {
    const { cookieValue, session } = await staffIn(SCHOOL, 30);
    const current = await store.load(cookieValue, { touch: false });
    await store.saveTokens(
      current ?? session,
      { accessToken: "a2", refreshToken: "r2", idToken: null },
      clock + 10 * MINUTE,
    );
    clock += 29 * MINUTE;
    expect((await store.tokens(session.id))?.tokens.accessToken).toBe("a2");
    expect(await store.load(cookieValue, { touch: false })).not.toBeNull();
  });
});
