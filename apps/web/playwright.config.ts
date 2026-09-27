import { defineConfig, devices } from "@playwright/test";

const PORT = Number(process.env.E2E_PORT ?? 3000);
const baseURL = process.env.E2E_BASE_URL ?? `http://localhost:${PORT}`;
/** Sign-in e2e with the scripted stand-in IdP and canned API (needs Valkey at REDIS_URL). */
const standIn = process.env.E2E_STAND_IN === "1";
const IDP_PORT = Number(process.env.E2E_IDP_PORT ?? 8089);
const API_PORT = Number(process.env.E2E_API_PORT ?? 8099);

/** Obvious placeholder (>= 32 bytes); accepted by the BFF only for http://localhost. */
const devOnly = (name: string) => `dev-only-e2e-${name}-${"x".repeat(32)}`;

/**
 * End-to-end tests (`make e2e`). Not part of the CI unit stage.
 * By default builds nothing: it starts `next start` against an existing `next build`.
 * Set E2E_BASE_URL to test an already running stack (e.g. docker compose).
 * Set E2E_STAND_IN=1 to sign in through e2e/support/stand-in.ts (IdP + canned API) and
 * run the accessibility checks on signed-in school and platform pages.
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: !standIn,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"], ["html", { open: "never" }]],
  ...(standIn ? { globalSetup: "./e2e/support/global-setup.ts" } : {}),
  use: {
    baseURL,
    trace: "retain-on-failure",
    // Office PCs: 1366×768 is the design baseline (PRD §8).
    viewport: { width: 1366, height: 768 },
    locale: "en-IN",
    timezoneId: "Asia/Kolkata",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1366, height: 768 } },
    },
  ],
  ...(process.env.E2E_BASE_URL
    ? {}
    : {
        webServer: {
          command: `npx next start --port ${PORT}`,
          url: `${baseURL}/healthz`,
          reuseExistingServer: !process.env.CI,
          timeout: 60_000,
          // Local http run: dev-only placeholder values are accepted only for localhost.
          env: {
            APP_BASE_URL: baseURL,
            SESSION_SECRET: process.env.SESSION_SECRET ?? devOnly("session"),
            SOS_SERVICE_TOKEN_KEY: process.env.SOS_SERVICE_TOKEN_KEY ?? devOnly("service"),
            REDIS_URL: process.env.REDIS_URL ?? "redis://localhost:6379/1",
            API_INTERNAL_URL: standIn
              ? `http://localhost:${API_PORT}`
              : (process.env.API_INTERNAL_URL ?? "http://localhost:8000"),
            OIDC_ISSUER: standIn
              ? `http://localhost:${IDP_PORT}/schoolos`
              : (process.env.OIDC_ISSUER ?? "http://localhost:8080/schoolos"),
            OIDC_CLIENT_ID: "schoolos-web",
            OIDC_CLIENT_SECRET: devOnly("staff-client"),
            PLATFORM_OIDC_ISSUER: standIn
              ? `http://localhost:${IDP_PORT}/platform`
              : (process.env.PLATFORM_OIDC_ISSUER ?? "http://localhost:8080/platform"),
            PLATFORM_OIDC_CLIENT_ID: "schoolos-platform",
            PLATFORM_OIDC_CLIENT_SECRET: devOnly("operator-client"),
          },
        },
      }),
});
