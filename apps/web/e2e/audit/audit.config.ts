import { defineConfig } from "@playwright/test";

/**
 * Responsive layout sweep (e2e/audit/responsive.audit.ts): many viewports, screenshots and
 * a JSON report. Not part of `make e2e` (e2e/responsive.spec.ts gates the two required
 * viewports there). Start the app yourself with the stand-in IdP and API settings of
 * playwright.config.ts (E2E_IDP_PORT, E2E_API_PORT, Valkey at REDIS_URL), then:
 *
 *   E2E_BASE_URL=http://localhost:3407 npx playwright test -c e2e/audit/audit.config.ts
 *
 * The stand-ins are started here (globalSetup). PW_CHROMIUM_PATH points Playwright at a
 * preinstalled Chromium where the browser download is blocked.
 */
export default defineConfig({
  testDir: ".",
  testMatch: /.*\.audit\.ts/,
  fullyParallel: false,
  workers: 1,
  timeout: 90 * 60_000,
  reporter: [["list"]],
  globalSetup: "../support/global-setup.ts",
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3407",
    locale: "en-IN",
    timezoneId: "Asia/Kolkata",
    ...(process.env.PW_CHROMIUM_PATH
      ? { launchOptions: { executablePath: process.env.PW_CHROMIUM_PATH } }
      : {}),
  },
});
