// @vitest-environment node
import { randomBytes } from "node:crypto";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { SessionKind } from "@/server/config";
import { setAuthRuntimeForTesting } from "@/server/runtime";
import { createHarness, HARNESS_TENANT, type Harness } from "@/test/bff-harness";
import { sessionCookieName } from "./cookies";

/**
 * apiGetAsSession from Server Components: the school console runs as a support session when
 * there is no live staff session (ADR-0023), the same rule as the BFF's readSchoolSession.
 * Synthetic sessions only.
 */

const cookieJar = new Map<string, string>();

vi.mock("next/headers", () => ({
  cookies: async () => ({
    get: (name: string) => (cookieJar.has(name) ? { name, value: cookieJar.get(name) } : undefined),
  }),
  headers: async () => new Headers(),
}));

import { apiGetAsSession } from "./rsc";

let h: Harness;

async function signIn(kind: SessionKind, accessToken: string): Promise<string> {
  const created = await h.runtime.store.create({
    kind,
    subject: `${kind}-${randomBytes(4).toString("hex")}`,
    issuer: h.runtime.config[kind].issuer.href,
    displayName: "Synthetic User",
    authTime: Math.floor(Date.now() / 1000),
    mfa: true,
    tokens: { accessToken, refreshToken: null, idToken: null },
    accessExpiresAt: Date.now() + 600_000,
    activeTenantId: HARNESS_TENANT,
  });
  cookieJar.set(sessionCookieName(kind, h.runtime.config.secureCookies), created.cookieValue);
  return created.cookieValue;
}

beforeEach(async () => {
  h = await createHarness();
  setAuthRuntimeForTesting(h.runtime);
  cookieJar.clear();
  vi.stubEnv("APP_BASE_URL", h.runtime.config.appBaseUrl.origin);
});

afterEach(() => {
  setAuthRuntimeForTesting(null);
  vi.unstubAllEnvs();
});

describe("apiGetAsSession falls back to the support session (ADR-0023, FR-IAM-013)", () => {
  it("uses the support session when the staff cookie is present but its session is gone", async () => {
    await signIn("support", "access-token-support");
    // An expired or signed-out staff session: the cookie is still sent, the store has nothing.
    cookieJar.set(sessionCookieName("staff", h.runtime.config.secureCookies), "stale-cookie");
    const result = await apiGetAsSession("staff", "/api/v1/me");
    expect(result?.status).toBe(200);
    const call = h.apiCalls.find((r) => new URL(r.url).pathname === "/api/v1/me");
    expect(call?.headers.get("authorization")).toBe("Bearer access-token-support");
  });

  it("prefers a live staff session", async () => {
    await signIn("support", "access-token-support");
    await signIn("staff", "access-token-staff");
    expect((await apiGetAsSession("staff", "/api/v1/me"))?.status).toBe(200);
    const call = h.apiCalls.find((r) => new URL(r.url).pathname === "/api/v1/me");
    expect(call?.headers.get("authorization")).toBe("Bearer access-token-staff");
  });

  it("never falls back for an operator", async () => {
    await signIn("support", "access-token-support");
    expect(await apiGetAsSession("operator", "/api/v1/platform/me")).toBeNull();
  });
});
