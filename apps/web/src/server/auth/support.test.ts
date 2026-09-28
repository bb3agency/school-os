// @vitest-environment node
import { beforeEach, describe, expect, it } from "vitest";
import { proxyToApi } from "@/server/bff/proxy";
import { loadAuthConfig, ConfigError } from "@/server/config";
import { createHarness, defaultApi, type Harness } from "@/test/bff-harness";
import { testEnv } from "@/test/server-env";
import {
  handleCallback,
  handleLogin,
  handleLogout,
  handleSessionInfo,
  handleSupportLogin,
} from "./handlers";

/**
 * Break-glass support sign-in (ADR-0023 option C; SEC-021, SEC-005, FR-IAM-001): the support
 * app client of the operator pool, a forced fresh MFA sign-in, a separate __Host- cookie, and
 * the API's POST /breakglass/support-session before any school page. Synthetic IDs only.
 */

const REQUEST = "0192f3a4-0000-7000-8000-00000000bb01";
const TENANT = "0192f3a4-0000-7000-8000-000000000001";
const operator = { sub: "op-sub-1", name: "Synthetic Operator", mfa: true };
let h: Harness;

beforeEach(async () => {
  h = await createHarness();
});

function locationOf(response: Response): URL {
  return new URL(response.headers.get("location") ?? "about:blank");
}

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

/** Default API plus a scripted answer for the support-session call. */
function apiWithSupport(status: number, code = "breakglass_only") {
  h.setApi((request) => {
    if (new URL(request.url).pathname === "/api/v1/breakglass/support-session") {
      return status === 200
        ? json({
            grant_id: "0192f3a4-0000-7000-8000-00000000cc01",
            platform_request_id: REQUEST,
            tenant_id: TENANT,
            expires_at: "2026-09-27T12:00:00Z",
            scope: {},
          })
        : json({ code, title: "Refused", status }, status);
    }
    return defaultApi(request);
  });
}

async function supportSignIn(
  user: { sub: string; mfa?: boolean; authTime?: number } = operator,
  query = `request=${REQUEST}&tenant=${TENANT}&c=1`,
) {
  const login = h.absorb(
    await handleSupportLogin(h.request(`/bff/auth/support/login?${query}`), h.runtime),
  );
  const back = h.idp.support.authorize(login.headers.get("location") ?? "", user);
  return h.absorb(await handleCallback(h.request(back.href), h.runtime, "support"));
}

describe("support sign-in configuration (ADR-0023)", () => {
  it("is off without SUPPORT_OIDC_CLIENT_ID and on in the operator pool when set", () => {
    const off = loadAuthConfig(testEnv({ SUPPORT_OIDC_CLIENT_ID: undefined }));
    expect(off.supportEnabled).toBe(false);
    const on = loadAuthConfig(testEnv());
    expect(on.supportEnabled).toBe(true);
    expect(on.support.issuer.href).toBe(on.operator.issuer.href);
    expect(on.support.clientId).toBe("support-client");
    expect(on.support.redirectUri).toBe("https://office.school.example/bff/auth/support/callback");
  });

  it.each([
    [{ SUPPORT_OIDC_CLIENT_ID: "operator-client" }, "SUPPORT_OIDC_CLIENT_ID"],
    [{ SUPPORT_OIDC_CLIENT_ID: "staff-client" }, "SUPPORT_OIDC_CLIENT_ID"],
    [{ SUPPORT_OIDC_ISSUER: "https://idp.example/staff" }, "SUPPORT_OIDC_ISSUER"],
    [{ SUPPORT_OIDC_CLIENT_SECRET: undefined }, "SUPPORT_OIDC_CLIENT_SECRET"],
    [{ SUPPORT_OIDC_ISSUER: "http://idp.example/operators" }, "SUPPORT_OIDC_ISSUER"],
  ])("refuses a confusable or incomplete support client %#", (overrides, name) => {
    expect(() => loadAuthConfig(testEnv(overrides))).toThrow(ConfigError);
    try {
      loadAuthConfig(testEnv(overrides));
    } catch (error) {
      expect((error as ConfigError).problems.join(" ")).toContain(name);
    }
  });
});

describe("GET /bff/auth/support/login", () => {
  it("first moves to the school app's own address (host-only cookies), without a cookie", async () => {
    const response = await handleSupportLogin(
      h.request(`/bff/auth/support/login?request=${REQUEST.toUpperCase()}&tenant=${TENANT}`),
      h.runtime,
    );
    expect(response.status).toBe(303);
    expect(response.headers.get("location")).toBe(
      `https://office.school.example/bff/auth/support/login?request=${REQUEST}&tenant=${TENANT}&c=1`,
    );
    expect(response.headers.getSetCookie()).toEqual([]);
  });

  it("forces a fresh sign-in with the support client and a separate transaction cookie", async () => {
    const response = await handleSupportLogin(
      h.request(`/bff/auth/support/login?request=${REQUEST}&tenant=${TENANT}&c=1`),
      h.runtime,
    );
    expect(response.status).toBe(303);
    const url = locationOf(response);
    expect(url.origin + url.pathname).toBe("https://idp.example/operators/authorize");
    expect(url.searchParams.get("client_id")).toBe("support-client");
    expect(url.searchParams.get("prompt")).toBe("login");
    expect(url.searchParams.get("max_age")).toBe("0");
    expect(url.searchParams.get("redirect_uri")).toBe(
      "https://office.school.example/bff/auth/support/callback",
    );
    const [cookie] = response.headers.getSetCookie();
    expect(cookie).toMatch(/^__Host-sos_support_auth_tx=v1\./);
    expect(cookie).not.toContain(REQUEST);
  });

  it("answers 404 when the support client is off (fail closed)", async () => {
    const off = await createHarness({ SUPPORT_OIDC_CLIENT_ID: undefined });
    const response = await handleSupportLogin(
      off.request(`/bff/auth/support/login?request=${REQUEST}&tenant=${TENANT}`),
      off.runtime,
    );
    expect(response.status).toBe(404);
  });

  it.each(["request=nope&tenant=x", `request=${REQUEST}`, `tenant=${TENANT}`, ""])(
    "refuses a link without both IDs (%s)",
    async (query) => {
      const response = await handleSupportLogin(
        h.request(`/bff/auth/support/login?${query}`),
        h.runtime,
      );
      expect(response.headers.get("location")).toBe(
        "https://office.school.example/signed-out?kind=support&error=support_not_allowed",
      );
    },
  );

  it("never starts a support session through the generic login route", async () => {
    const response = await handleLogin(h.request("/bff/auth/login"), h.runtime, "support");
    expect(response.status).toBe(404);
  });
});

describe("GET /bff/auth/support/callback", () => {
  it("starts the support session with the API, pinned to the grant's school", async () => {
    apiWithSupport(200);
    const response = await supportSignIn();
    expect(response.status).toBe(303);
    expect(response.headers.get("location")).toBe("https://office.school.example/");
    expect(h.jar.has("__Host-sos_support_session")).toBe(true);
    expect(h.jar.has("__Host-sos_session")).toBe(false);

    const start = h.apiCalls.find((r) => r.url.endsWith("/api/v1/breakglass/support-session"));
    expect(start?.method).toBe("POST");
    expect(start?.headers.get("x-active-tenant")).toBe(TENANT);
    expect(start?.headers.get("authorization")).toMatch(/^Bearer ey/);
    await expect(start?.json()).resolves.toEqual({ platform_request_id: REQUEST });
    const login = h.apiCalls.find((r) => r.url.endsWith("/api/v1/me/login-event"));
    expect(login?.headers.get("x-active-tenant")).toBe(TENANT);
    // Staff-only sign-in steps never run for support.
    expect(h.apiCalls.some((r) => r.url.endsWith("/api/v1/me/accept-invitations"))).toBe(false);

    const session = await h.runtime.store.load(h.jar.get("__Host-sos_support_session"), {
      touch: false,
    });
    expect(session?.kind).toBe("support");
    expect(session?.activeTenantId).toBe(TENANT);
  });

  it.each([
    [403, "support_not_allowed"],
    [404, "support_not_allowed"],
    [409, "support_ended"],
    [428, "step_up_failed"],
    [503, "signin_unavailable"],
  ])("API %i: no session, error %s", async (status, error) => {
    apiWithSupport(status);
    const response = await supportSignIn();
    expect(response.headers.get("location")).toBe(
      `https://office.school.example/signed-out?kind=support&error=${error}`,
    );
    expect(h.jar.has("__Host-sos_support_session")).toBe(false);
    expect(h.apiCalls.some((r) => r.url.endsWith("/api/v1/me/login-event"))).toBe(false);
  });

  it("refuses a sign-in without MFA", async () => {
    apiWithSupport(200);
    const response = await supportSignIn({ sub: "op-sub-1", mfa: false });
    expect(response.headers.get("location")).toContain("error=mfa_required");
    expect(h.jar.has("__Host-sos_support_session")).toBe(false);
    expect(h.apiCalls).toHaveLength(0);
  });

  it("refuses a sign-in older than 5 minutes (step-up at session start)", async () => {
    apiWithSupport(200);
    const response = await supportSignIn({
      ...operator,
      authTime: Math.floor(Date.now() / 1000) - 600,
    });
    expect(response.headers.get("location")).toContain("error=step_up_failed");
    expect(h.apiCalls).toHaveLength(0);
  });
});

describe("support session in the school console", () => {
  it("is the school session when there is no staff session; logout ends it", async () => {
    apiWithSupport(200);
    await supportSignIn();
    const info = await handleSessionInfo(h.request("/bff/auth/session?kind=staff"), h.runtime);
    await expect(info.json()).resolves.toMatchObject({
      authenticated: true,
      kind: "support",
      active_tenant_id: TENANT,
    });

    h.apiCalls.length = 0;
    const proxied = await proxyToApi(h.request("/bff/api/v1/sections"), h.runtime);
    expect(proxied.status).toBe(200);
    expect(h.apiCalls[0]?.headers.get("x-active-tenant")).toBe(TENANT);

    // The control plane stays closed to a support session.
    const platform = await proxyToApi(h.request("/bff/api/v1/platform/tenants"), h.runtime);
    expect(platform.status).toBe(403);
    await expect(platform.json()).resolves.toMatchObject({ code: "wrong_session" });

    const csrf = await h.csrf("support");
    const out = h.absorb(
      await handleLogout(
        h.request("/bff/auth/logout?kind=support", {
          method: "POST",
          headers: { "x-csrf-token": csrf },
        }),
        h.runtime,
      ),
    );
    expect(out.status).toBe(200);
    expect(h.jar.has("__Host-sos_support_session")).toBe(false);
    const after = await proxyToApi(h.request("/bff/api/v1/sections"), h.runtime);
    expect(after.status).toBe(401);
  });

  it("a 428 on a support session points at the support step-up route", async () => {
    apiWithSupport(200);
    await supportSignIn();
    h.setApi(() => json({ code: "step_up_required" }, 428));
    const response = await proxyToApi(h.request("/bff/api/v1/me"), h.runtime);
    expect(response.status).toBe(428);
    const body = (await response.json()) as { step_up_url: string };
    expect(body.step_up_url.startsWith("/bff/auth/support/step-up?next=")).toBe(true);
  });
});
