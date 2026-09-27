/**
 * Test-only environment for the BFF. Secrets are generated per run (never literals), so
 * nothing here looks like a real credential.
 */
import { randomBytes } from "node:crypto";

export const TEST_SESSION_SECRET = randomBytes(32).toString("base64url");
export const TEST_SERVICE_TOKEN_KEY = randomBytes(32).toString("base64url");

export function testEnv(overrides: Record<string, string | undefined> = {}) {
  return {
    NODE_ENV: "test",
    APP_BASE_URL: "https://office.school.example",
    SESSION_SECRET: TEST_SESSION_SECRET,
    SOS_SERVICE_TOKEN_KEY: TEST_SERVICE_TOKEN_KEY,
    REDIS_URL: "redis://valkey.test:6379/1",
    API_INTERNAL_URL: "http://api.internal:8000",
    OIDC_ISSUER: "https://idp.example/staff",
    OIDC_CLIENT_ID: "staff-client",
    OIDC_CLIENT_SECRET: randomBytes(16).toString("hex"),
    PLATFORM_OIDC_ISSUER: "https://idp.example/operators",
    PLATFORM_OIDC_CLIENT_ID: "operator-client",
    PLATFORM_OIDC_CLIENT_SECRET: randomBytes(16).toString("hex"),
    // Break-glass support client of the operator pool (ADR-0023); same issuer as operators.
    SUPPORT_OIDC_CLIENT_ID: "support-client",
    SUPPORT_OIDC_CLIENT_SECRET: randomBytes(16).toString("hex"),
    ...overrides,
  };
}
