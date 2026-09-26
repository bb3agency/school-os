// @vitest-environment node
import { beforeEach, describe, expect, it } from "vitest";
import { createHarness, type Harness } from "@/test/bff-harness";
import {
  handleActiveTenant,
  handleCallback,
  handleLogin,
  handleLogout,
  handleSessionInfo,
  handleSessions,
  handleStepUp,
} from "./handlers";

const TENANT = "0192f3a4-0000-7000-8000-000000000001";
const clerk = { sub: "staff-sub-1", name: "Office Clerk" };
let h: Harness;

beforeEach(async () => {
  h = await createHarness();
});

function locationOf(response: Response): URL {
  return new URL(response.headers.get("location") ?? "about:blank");
}

describe("GET /bff/auth/login (FR-IAM-001)", () => {
  it("redirects to the IdP with PKCE S256, state and nonce, and a sealed transaction cookie", async () => {
    const response = h.absorb(
      await handleLogin(h.request("/bff/auth/login?next=/en/settings/users"), h.runtime, "staff"),
    );
    expect(response.status).toBe(303);
    const url = locationOf(response);
    expect(url.origin + url.pathname).toBe("https://idp.example/staff/authorize");
    expect(url.searchParams.get("client_id")).toBe("staff-client");
    expect(url.searchParams.get("response_type")).toBe("code");
    expect(url.searchParams.get("code_challenge_method")).toBe("S256");
    expect(url.searchParams.get("code_challenge")).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(url.searchParams.get("state")).toBeTruthy();
    expect(url.searchParams.get("nonce")).toBeTruthy();
    expect(url.searchParams.get("redirect_uri")).toBe(
      "https://office.school.example/bff/auth/callback",
    );
    expect(url.searchParams.has("prompt")).toBe(false);

    const [cookie] = response.headers.getSetCookie();
    expect(cookie).toMatch(/^__Host-sos_auth_tx=v1\./);
    expect(cookie).toContain("HttpOnly");
    expect(cookie).toContain("Secure");
    expect(cookie).toContain("SameSite=Lax");
    expect(cookie).toContain("Path=/");
    expect(cookie).toContain("Max-Age=600");
    expect(cookie).not.toContain("Domain");
    // The verifier and nonce are sealed, not readable in the cookie.
    expect(cookie).not.toContain(url.searchParams.get("nonce") ?? "-");
  });

  it("uses the separate operator client for the platform login", async () => {
    const response = await handleLogin(
      h.request("/bff/auth/platform/login?next=/en/platform/schools"),
      h.runtime,
      "operator",
    );
    const url = locationOf(response);
    expect(url.origin + url.pathname).toBe("https://idp.example/operators/authorize");
    expect(url.searchParams.get("client_id")).toBe("operator-client");
    expect(response.headers.getSetCookie()[0]).toMatch(/^__Host-sos_platform_auth_tx=/);
  });

  it("returns 404 for the platform login on a dedicated host", async () => {
    const dedicated = await createHarness({ SOS_DEPLOYMENT_MODE: "dedicated" });
    const response = await handleLogin(
      dedicated.request("/bff/auth/platform/login"),
      dedicated.runtime,
      "operator",
    );
    expect(response.status).toBe(404);
  });
});

describe("GET /bff/auth/callback", () => {
  it("creates a server-side session with a __Host- cookie and redirects to next", async () => {
    const response = await h.signIn("staff", clerk, "/en/settings/users");
    expect(response.status).toBe(303);
    expect(response.headers.get("location")).toBe(
      "https://office.school.example/en/settings/users",
    );
    const cookies = response.headers.getSetCookie();
    const session = cookies.find((c) => c.startsWith("__Host-sos_session="));
    expect(session).toMatch(
      /^__Host-sos_session=[A-Za-z0-9_-]{43}; Path=\/; HttpOnly; SameSite=Lax; Secure$/,
    );
    expect(
      cookies.some((c) => c.startsWith("__Host-sos_auth_tx=;") && c.includes("Max-Age=0")),
    ).toBe(true);
    expect(h.jar.has("__Host-sos_auth_tx")).toBe(false);
  });

  it("records the login with the API (best effort) with both tokens", async () => {
    h.setApi(() => new Response(null, { status: 404 }));
    const response = await h.signIn("staff", clerk);
    expect(response.status).toBe(303);
    const call = h.apiCalls.find((r) => r.url.endsWith("/api/v1/me/login-event"));
    expect(call?.method).toBe("POST");
    expect(call?.headers.get("authorization")).toMatch(/^Bearer ey/);
    expect(call?.headers.get("x-service-token")).toMatch(/^ey/);
  });

  it("still signs in when the login-event call fails", async () => {
    h.setApi(() => {
      throw new TypeError("fetch failed");
    });
    const response = await h.signIn("staff", clerk);
    expect(response.headers.get("location")).toBe("https://office.school.example/");
    expect(h.jar.has("__Host-sos_session")).toBe(true);
  });

  it("rejects a state mismatch", async () => {
    const login = h.absorb(await handleLogin(h.request("/bff/auth/login"), h.runtime, "staff"));
    const back = h.idp.staff.authorize(login.headers.get("location") ?? "", clerk, {
      state: "forged-state",
    });
    const response = h.absorb(await handleCallback(h.request(back.href), h.runtime, "staff"));
    expect(response.status).toBe(303);
    expect(response.headers.get("location")).toBe(
      "https://office.school.example/signed-out?error=signin_failed",
    );
    expect(h.jar.has("__Host-sos_session")).toBe(false);
  });

  it("rejects a nonce mismatch in the ID token", async () => {
    const login = h.absorb(await handleLogin(h.request("/bff/auth/login"), h.runtime, "staff"));
    const back = h.idp.staff.authorize(login.headers.get("location") ?? "", clerk, {
      nonce: "replayed-nonce",
    });
    const response = h.absorb(await handleCallback(h.request(back.href), h.runtime, "staff"));
    expect(response.headers.get("location")).toContain("error=signin_failed");
    expect(h.jar.has("__Host-sos_session")).toBe(false);
  });

  it("rejects a PKCE verifier mismatch (code injected from another sign-in)", async () => {
    // Attacker starts their own sign-in and injects the resulting code into the victim's callback.
    const attacker = await createHarness();
    const attackerLogin = await handleLogin(
      attacker.request("/bff/auth/login"),
      attacker.runtime,
      "staff",
    );
    const victimLogin = h.absorb(
      await handleLogin(h.request("/bff/auth/login"), h.runtime, "staff"),
    );
    const victimState = locationOf(victimLogin).searchParams.get("state") ?? "";
    // Same IdP: register the attacker's code at the victim harness's IdP.
    const attackerAuthorize = new URL(attackerLogin.headers.get("location") ?? "");
    attackerAuthorize.searchParams.set(
      "nonce",
      locationOf(victimLogin).searchParams.get("nonce") ?? "",
    );
    const back = h.idp.staff.authorize(attackerAuthorize, clerk, { state: victimState });
    const response = h.absorb(await handleCallback(h.request(back.href), h.runtime, "staff"));
    expect(response.headers.get("location")).toContain("error=signin_failed");
    expect(h.jar.has("__Host-sos_session")).toBe(false);
  });

  it("rejects a missing or tampered transaction cookie", async () => {
    const login = h.absorb(await handleLogin(h.request("/bff/auth/login"), h.runtime, "staff"));
    const back = h.idp.staff.authorize(login.headers.get("location") ?? "", clerk);
    const tx = h.jar.get("__Host-sos_auth_tx") ?? "";
    h.jar.set("__Host-sos_auth_tx", `${tx.slice(0, -4)}AAAA`);
    const tampered = await handleCallback(h.request(back.href), h.runtime, "staff");
    expect(tampered.headers.get("location")).toContain("error=signin_expired");

    h.jar.clear();
    const missing = await handleCallback(h.request(back.href), h.runtime, "staff");
    expect(missing.headers.get("location")).toContain("error=signin_expired");
  });

  it("refuses an operator sign-in without MFA", async () => {
    const response = await h.signIn("operator", { sub: "op-1", mfa: false });
    expect(response.headers.get("location")).toContain("error=mfa_required");
    expect(h.jar.has("__Host-sos_platform_session")).toBe(false);
  });

  it("signs an operator in with a separate cookie", async () => {
    const response = await h.signIn("operator", { sub: "op-1", mfa: true }, "/te/platform/schools");
    expect(response.headers.get("location")).toBe(
      "https://office.school.example/te/platform/schools",
    );
    expect(h.jar.has("__Host-sos_platform_session")).toBe(true);
    expect(h.jar.has("__Host-sos_session")).toBe(false);
  });

  it.each([
    ["https://evil.example/phish", "https://office.school.example/"],
    ["//evil.example", "https://office.school.example/"],
    ["/\\evil.example", "https://office.school.example/"],
    ["/en/platform", "https://office.school.example/"],
  ])("never redirects to %s after sign-in", async (next, expected) => {
    const response = await h.signIn("staff", clerk, next);
    expect(response.headers.get("location")).toBe(expected);
  });
});

describe("local http mode (loopback only)", () => {
  it("drops Secure and the __Host- prefix only for http://localhost", async () => {
    const local = await createHarness({
      APP_BASE_URL: "http://localhost:3000",
      OIDC_ISSUER: "http://localhost:8080/schoolos",
      PLATFORM_OIDC_ISSUER: "http://localhost:8080/platform",
    });
    const response = await local.signIn("staff", clerk);
    const session = response.headers.getSetCookie().find((c) => c.startsWith("sos_session="));
    expect(session).toBeDefined();
    expect(session).not.toContain("Secure");
    expect(session).toContain("HttpOnly");
  });
});

describe("GET /bff/auth/session", () => {
  it("returns session facts but never tokens", async () => {
    await h.signIn("staff", clerk);
    const response = await handleSessionInfo(h.request("/bff/auth/session"), h.runtime);
    expect(response.headers.get("cache-control")).toBe("no-store");
    const text = await response.text();
    const body = JSON.parse(text) as Record<string, unknown>;
    expect(body).toMatchObject({
      authenticated: true,
      kind: "staff",
      display_name: "Office Clerk",
      active_tenant_id: null,
      idle_timeout_ms: 15 * 60_000,
    });
    expect(body.csrf_token).toMatch(/^[A-Za-z0-9_-]{43}$/);
    // No JWTs, no refresh tokens, no session id.
    expect(text).not.toMatch(/eyJ[A-Za-z0-9_-]+\./);
    expect(text).not.toMatch(/rt-[0-9a-f]{32}/);
    expect(text).not.toContain(h.jar.get("__Host-sos_session") ?? "-");
    expect(Object.keys(body).sort()).toEqual(
      [
        "absolute_expires_at",
        "active_tenant_id",
        "authenticated",
        "csrf_token",
        "display_name",
        "expires_in_ms",
        "idle_expires_at",
        "idle_timeout_ms",
        "kind",
        "mfa",
      ].sort(),
    );
  });

  it("says signed out without a session", async () => {
    const response = await handleSessionInfo(h.request("/bff/auth/session"), h.runtime);
    await expect(response.json()).resolves.toEqual({ authenticated: false, kind: "staff" });
  });

  it("stay-signed-in (POST) needs the CSRF token", async () => {
    await h.signIn("staff", clerk);
    const denied = await handleSessionInfo(
      h.request("/bff/auth/session", { method: "POST" }),
      h.runtime,
    );
    expect(denied.status).toBe(403);
    const ok = await handleSessionInfo(
      h.request("/bff/auth/session", {
        method: "POST",
        headers: { "x-csrf-token": await h.csrf() },
      }),
      h.runtime,
    );
    expect(ok.status).toBe(200);
  });
});

describe("POST /bff/auth/logout", () => {
  it("requires CSRF, revokes server-side and ends the IdP session without an ID token", async () => {
    await h.signIn("staff", clerk);
    const csrf = await h.csrf();
    const forged = await handleLogout(h.request("/bff/auth/logout", { method: "POST" }), h.runtime);
    expect(forged.status).toBe(403);
    await expect(forged.json()).resolves.toMatchObject({ code: "csrf_failed" });

    const wrong = await handleLogout(
      h.request("/bff/auth/logout", { method: "POST", headers: { "x-csrf-token": `${csrf}x` } }),
      h.runtime,
    );
    expect(wrong.status).toBe(403);

    const cookieValue = h.jar.get("__Host-sos_session");
    const response = h.absorb(
      await handleLogout(
        h.request("/bff/auth/logout", { method: "POST", headers: { "x-csrf-token": csrf } }),
        h.runtime,
      ),
    );
    expect(response.status).toBe(200);
    const body = (await response.json()) as { redirect_to: string };
    const endSession = new URL(body.redirect_to);
    expect(endSession.origin + endSession.pathname).toBe("https://idp.example/staff/endsession");
    expect(endSession.searchParams.get("client_id")).toBe("staff-client");
    expect(endSession.searchParams.get("post_logout_redirect_uri")).toBe(
      "https://office.school.example/signed-out",
    );
    expect(endSession.searchParams.has("id_token_hint")).toBe(false);
    expect(h.jar.has("__Host-sos_session")).toBe(false);
    expect(await h.runtime.store.load(cookieValue, { touch: false })).toBeNull();
  });

  it("rejects a cross-origin logout even with the token", async () => {
    await h.signIn("staff", clerk);
    const response = await handleLogout(
      h.request("/bff/auth/logout", {
        method: "POST",
        headers: { "x-csrf-token": await h.csrf(), origin: "https://evil.example" },
      }),
      h.runtime,
    );
    expect(response.status).toBe(403);
  });
});

describe("GET /bff/auth/step-up (SEC-005)", () => {
  it("re-authenticates with prompt=login and max_age=0 and keeps the session family", async () => {
    await h.signIn("staff", clerk);
    const before = await h.runtime.store.load(h.jar.get("__Host-sos_session"), { touch: false });
    const response = h.absorb(
      await handleStepUp(
        h.request("/bff/auth/step-up?next=/en/settings/users"),
        h.runtime,
        "staff",
      ),
    );
    const url = locationOf(response);
    expect(url.searchParams.get("prompt")).toBe("login");
    expect(url.searchParams.get("max_age")).toBe("0");

    const back = h.idp.staff.authorize(url, clerk);
    const done = h.absorb(await handleCallback(h.request(back.href), h.runtime, "staff"));
    expect(done.headers.get("location")).toBe("https://office.school.example/en/settings/users");
    const after = await h.runtime.store.load(h.jar.get("__Host-sos_session"), { touch: false });
    expect(after?.id).not.toBe(before?.id);
    expect(after?.familyId).toBe(before?.familyId);
    expect(after?.csrfToken).toBe(before?.csrfToken);
    expect(await h.runtime.store.get(before?.id ?? "")).toBeNull();
  });

  it("refuses a stale auth_time on step-up", async () => {
    await h.signIn("staff", clerk);
    const response = h.absorb(
      await handleStepUp(h.request("/bff/auth/step-up"), h.runtime, "staff"),
    );
    const back = h.idp.staff.authorize(locationOf(response), {
      ...clerk,
      authTime: Math.floor(Date.now() / 1000) - 3600,
    });
    const done = await handleCallback(h.request(back.href), h.runtime, "staff");
    expect(done.headers.get("location")).toContain("error=step_up_failed");
  });
});

describe("/bff/auth/sessions", () => {
  it("lists and revokes only the caller's own sessions", async () => {
    await h.signIn("staff", clerk);
    const firstCookie = h.jar.get("__Host-sos_session") ?? "";
    h.jar.clear();
    await h.signIn("staff", clerk);
    const csrf = await h.csrf();

    const list = await handleSessions(h.request("/bff/auth/sessions"), h.runtime);
    const { data } = (await list.json()) as { data: Array<{ id: string; current: boolean }> };
    expect(data).toHaveLength(2);
    expect(data.filter((s) => s.current)).toHaveLength(1);
    const other = data.find((s) => !s.current);

    const denied = await handleSessions(
      h.request(`/bff/auth/sessions?id=${other?.id}`, { method: "DELETE" }),
      h.runtime,
    );
    expect(denied.status).toBe(403);
    const revoked = await handleSessions(
      h.request(`/bff/auth/sessions?id=${other?.id}`, {
        method: "DELETE",
        headers: { "x-csrf-token": csrf },
      }),
      h.runtime,
    );
    expect(revoked.status).toBe(204);
    expect(await h.runtime.store.load(firstCookie, { touch: false })).toBeNull();

    const missing = await handleSessions(
      h.request("/bff/auth/sessions?id=AAAAAAAAAAAAAAAAAAAAAA", {
        method: "DELETE",
        headers: { "x-csrf-token": csrf },
      }),
      h.runtime,
    );
    expect(missing.status).toBe(404);
  });

  it("needs a session", async () => {
    const response = await handleSessions(h.request("/bff/auth/sessions"), h.runtime);
    expect(response.status).toBe(401);
    expect(response.headers.get("content-type")).toContain("application/problem+json");
  });
});

describe("POST /bff/auth/active-tenant", () => {
  it("switches the tenant after the API accepts it, and requires CSRF", async () => {
    await h.signIn("staff", clerk);
    const body = JSON.stringify({ tenant_id: TENANT });
    const denied = await handleActiveTenant(
      h.request("/bff/auth/active-tenant", { method: "POST", body }),
      h.runtime,
    );
    expect(denied.status).toBe(403);

    h.setApi(() => new Response(null, { status: 204 }));
    const response = await handleActiveTenant(
      h.request("/bff/auth/active-tenant", {
        method: "POST",
        body,
        headers: { "x-csrf-token": await h.csrf(), "content-type": "application/json" },
      }),
      h.runtime,
    );
    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toMatchObject({ active_tenant_id: TENANT });
    const call = h.apiCalls.find((r) => r.url.endsWith("/api/v1/me/active-tenant"));
    expect(call?.headers.get("x-active-tenant")).toBe(TENANT);
  });

  it("refuses a school the API says the user cannot open", async () => {
    await h.signIn("staff", clerk);
    h.setApi(() => new Response(null, { status: 403 }));
    const response = await handleActiveTenant(
      h.request("/bff/auth/active-tenant", {
        method: "POST",
        body: JSON.stringify({ tenant_id: TENANT }),
        headers: { "x-csrf-token": await h.csrf() },
      }),
      h.runtime,
    );
    expect(response.status).toBe(403);
    const session = await h.runtime.store.load(h.jar.get("__Host-sos_session"), { touch: false });
    expect(session?.activeTenantId).toBeNull();
  });

  it("validates the tenant id", async () => {
    await h.signIn("staff", clerk);
    const response = await handleActiveTenant(
      h.request("/bff/auth/active-tenant", {
        method: "POST",
        body: JSON.stringify({ tenant_id: "../../etc" }),
        headers: { "x-csrf-token": await h.csrf() },
      }),
      h.runtime,
    );
    expect(response.status).toBe(422);
  });
});
