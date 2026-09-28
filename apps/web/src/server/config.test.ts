// @vitest-environment node
import { describe, expect, it } from "vitest";
import { testEnv } from "@/test/server-env";
import { ConfigError, loadAuthConfig } from "./config";

function problemsOf(env: Record<string, string | undefined>): string[] {
  try {
    loadAuthConfig(env);
    return [];
  } catch (error) {
    if (error instanceof ConfigError) return [...error.problems];
    throw error;
  }
}

describe("loadAuthConfig (SEC-004, SEC-006)", () => {
  it("accepts a complete https configuration with secure cookies", () => {
    const config = loadAuthConfig(testEnv());
    expect(config.secureCookies).toBe(true);
    expect(config.staff.redirectUri).toBe("https://office.school.example/bff/auth/callback");
    expect(config.operator.redirectUri).toBe(
      "https://office.school.example/bff/auth/platform/callback",
    );
    expect(config.platformEnabled).toBe(true);
  });

  it("requires SESSION_SECRET of at least 32 bytes", () => {
    expect(problemsOf(testEnv({ SESSION_SECRET: "short" }))).toContain(
      "SESSION_SECRET must be at least 32 bytes",
    );
    expect(problemsOf(testEnv({ SESSION_SECRET: undefined }))).toContain(
      "SESSION_SECRET is required",
    );
  });

  it("never echoes secret values in errors", () => {
    const value = "x".repeat(10);
    const error = (() => {
      try {
        loadAuthConfig(testEnv({ SESSION_SECRET: value, SOS_SERVICE_TOKEN_KEY: value }));
      } catch (caught) {
        return caught as Error;
      }
      return null;
    })();
    expect(error?.message).not.toContain(value);
  });

  it("refuses dev-only placeholder secrets on https deployments", () => {
    const placeholder = `dev-only-${"a".repeat(40)}`;
    expect(problemsOf(testEnv({ SESSION_SECRET: placeholder }))).toContain(
      "SESSION_SECRET still has a dev-only placeholder value",
    );
  });

  it("allows plain http only for loopback hosts, and then turns off Secure cookies", () => {
    const local = loadAuthConfig(
      testEnv({
        APP_BASE_URL: "http://localhost:3000",
        OIDC_ISSUER: "http://localhost:8080/schoolos",
        PLATFORM_OIDC_ISSUER: "http://localhost:8080/platform",
      }),
    );
    expect(local.secureCookies).toBe(false);
    expect(local.staff.allowInsecureIssuer).toBe(true);

    expect(problemsOf(testEnv({ APP_BASE_URL: "http://office.school.example" }))).toContain(
      "APP_BASE_URL must use https (plain http is allowed only for localhost)",
    );
  });

  it("accepts the compose dev issuer on *.localhost (browser and containers resolve it), only locally", () => {
    const local = loadAuthConfig(
      testEnv({
        APP_BASE_URL: "http://localhost:3000",
        OIDC_ISSUER: "http://oidc.localhost:8080/schoolos",
        PLATFORM_OIDC_ISSUER: "http://oidc.localhost:8080/platform",
      }),
    );
    expect(local.staff.issuer.href).toBe("http://oidc.localhost:8080/schoolos");
    expect(local.operator.allowInsecureIssuer).toBe(true);
    expect(problemsOf(testEnv({ OIDC_ISSUER: "http://oidc.localhost:8080/schoolos" }))).toContain(
      "OIDC_ISSUER must use https",
    );
    expect(
      problemsOf(
        testEnv({
          APP_BASE_URL: "http://localhost:3000",
          OIDC_ISSUER: "http://oidc.localhost.evil.example/schoolos",
        }),
      ),
    ).toContain("OIDC_ISSUER must use https");
    expect(problemsOf(testEnv({ APP_BASE_URL: "http://app.localhost:3000" }))).toContain(
      "APP_BASE_URL must use https (plain http is allowed only for localhost)",
    );
  });

  it("refuses an http issuer when the app itself is served over https", () => {
    expect(problemsOf(testEnv({ OIDC_ISSUER: "http://localhost:8080/schoolos" }))).toContain(
      "OIDC_ISSUER must use https",
    );
  });

  it("switches the control plane off on dedicated hosts", () => {
    expect(loadAuthConfig(testEnv({ SOS_DEPLOYMENT_MODE: "dedicated" })).platformEnabled).toBe(
      false,
    );
  });

  it("starts a dedicated host without the operator client (the control plane is off there)", () => {
    const env = testEnv({
      SOS_DEPLOYMENT_MODE: "dedicated",
      PLATFORM_OIDC_ISSUER: undefined,
      PLATFORM_OIDC_CLIENT_ID: undefined,
      PLATFORM_OIDC_CLIENT_SECRET: undefined,
      SUPPORT_OIDC_CLIENT_ID: undefined,
      SUPPORT_OIDC_CLIENT_SECRET: undefined,
    });
    const config = loadAuthConfig(env);
    expect(config.platformEnabled).toBe(false);
    expect(config.operator.clientId).toBe("");
    expect(config.supportEnabled).toBe(false);
  });

  it("still requires the operator client on the shared tier", () => {
    expect(problemsOf(testEnv({ PLATFORM_OIDC_CLIENT_ID: undefined }))).toContain(
      "PLATFORM_OIDC_CLIENT_ID is required",
    );
  });

  it("needs an explicit support issuer for break-glass on a dedicated host (ADR-0023)", () => {
    const dedicated = {
      SOS_DEPLOYMENT_MODE: "dedicated",
      PLATFORM_OIDC_ISSUER: undefined,
      PLATFORM_OIDC_CLIENT_ID: undefined,
      PLATFORM_OIDC_CLIENT_SECRET: undefined,
      SUPPORT_OIDC_CLIENT_ID: "support-client",
      SUPPORT_OIDC_CLIENT_SECRET: "support-secret",
    };
    expect(problemsOf(testEnv({ ...dedicated, SUPPORT_OIDC_ISSUER: undefined }))).toContain(
      "SUPPORT_OIDC_ISSUER must be an absolute URL",
    );
    const config = loadAuthConfig(
      testEnv({
        ...dedicated,
        SUPPORT_OIDC_ISSUER: "https://cognito-idp.ap-south-1.amazonaws.com/ap-south-1_Operators",
      }),
    );
    expect(config.supportEnabled).toBe(true);
    expect(config.support.redirectUri).toBe(
      "https://office.school.example/bff/auth/support/callback",
    );
  });
});
