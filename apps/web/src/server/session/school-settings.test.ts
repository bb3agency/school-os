// @vitest-environment node
import { randomBytes } from "node:crypto";
import { beforeEach, describe, expect, it } from "vitest";
import { MemoryKeyValue } from "./kv";
import { applySchoolSettingsFromMe, schoolIdleTimeoutFromMe } from "./school-settings";
import { SessionStore, type NewSession } from "./store";

const MINUTE = 60_000;
const SCHOOL = "0192f3a4-0000-7000-8000-000000000001";
const OTHER_SCHOOL = "0192f3a4-0000-7000-8000-000000000002";
let clock: number;
let store: SessionStore;

function me(tenantId: string, idle: unknown) {
  return {
    tenant_id: tenantId,
    settings: { idle_timeout_minutes: idle, date_format: "YYYY-MM-DD", languages: ["te", "en"] },
  };
}

const newStaff: NewSession = {
  kind: "staff",
  subject: "sub-1",
  issuer: "https://idp.example/staff",
  displayName: null,
  authTime: null,
  mfa: false,
  tokens: { accessToken: "a", refreshToken: "r", idToken: null },
  accessExpiresAt: 0,
};

beforeEach(() => {
  clock = Date.UTC(2026, 8, 27, 4, 30);
  store = new SessionStore(new MemoryKeyValue(() => clock), randomBytes(32), {
    now: () => clock,
  });
});

describe("school settings from GET /me (FR-TEN-012, FR-IAM-003)", () => {
  it("reads idle_timeout_minutes only for the matching school", () => {
    expect(schoolIdleTimeoutFromMe(me(SCHOOL, 20), SCHOOL)).toBe(20);
    expect(schoolIdleTimeoutFromMe(me(SCHOOL.toUpperCase(), 20), SCHOOL)).toBe(20);
    expect(schoolIdleTimeoutFromMe(me(OTHER_SCHOOL, 20), SCHOOL)).toBeNull();
    expect(schoolIdleTimeoutFromMe(me(SCHOOL, "20"), SCHOOL)).toBeNull();
    expect(schoolIdleTimeoutFromMe({ tenant_id: SCHOOL }, SCHOOL)).toBeNull();
    expect(schoolIdleTimeoutFromMe(null, SCHOOL)).toBeNull();
    expect(schoolIdleTimeoutFromMe("x", SCHOOL)).toBeNull();
  });

  it("applies the value to the session's active school, clamped", async () => {
    const { cookieValue, session } = await store.create({ ...newStaff, activeTenantId: SCHOOL });
    const applied = await applySchoolSettingsFromMe(store, session, me(SCHOOL, 45));
    expect(applied?.idleTimeoutMs).toBe(30 * MINUTE);
    clock += 30 * MINUTE;
    expect(await store.load(cookieValue, { touch: false })).toBeNull();
  });

  it("ignores operators, sessions without a school and answers for another school", async () => {
    const operator = await store.create({ ...newStaff, kind: "operator", activeTenantId: SCHOOL });
    expect(await applySchoolSettingsFromMe(store, operator.session, me(SCHOOL, 30))).toBeNull();
    const none = await store.create(newStaff);
    expect(await applySchoolSettingsFromMe(store, none.session, me(SCHOOL, 30))).toBeNull();
    const other = await store.create({ ...newStaff, activeTenantId: SCHOOL });
    expect(await applySchoolSettingsFromMe(store, other.session, me(OTHER_SCHOOL, 30))).toBeNull();
    expect((await store.get(other.session.id))?.idleTimeoutMs).toBe(15 * MINUTE);
  });
});
