import path from "node:path";
import { defineConfig } from "@playwright/test";
import base from "../../playwright.config";
import { chromiumLaunchOptions } from "../support/browser";

/**
 * Responsive layout sweep (e2e/audit/responsive.audit.ts): many viewports, screenshots and
 * a JSON report. Not part of `make e2e` (e2e/responsive.spec.ts gates the two required
 * viewports there); the nightly workflow runs it with `make e2e-audit`.
 *
 * Without E2E_BASE_URL it starts `next start` (after `next build`) with the stand-in IdP and API
 * settings of playwright.config.ts, exactly like `make e2e`; set E2E_STAND_IN=1 and Valkey at
 * REDIS_URL. With E2E_BASE_URL it audits an app you started yourself with those settings
 * (E2E_IDP_PORT, E2E_API_PORT):
 *
 *   E2E_BASE_URL=http://localhost:3407 npx playwright test -c e2e/audit/audit.config.ts
 *
 * The stand-ins are started here (globalSetup). Locally, PW_CHROMIUM_PATH (or a preinstalled
 * headless shell, e2e/support/browser.ts) stands in for a browser download that is blocked.
 */
const external = process.env.E2E_BASE_URL;
const server = base.webServer && !Array.isArray(base.webServer) ? base.webServer : undefined;

export default defineConfig({
  testDir: ".",
  testMatch: /.*\.audit\.ts/,
  fullyParallel: false,
  workers: 1,
  timeout: 90 * 60_000,
  reporter: [["list"]],
  globalSetup: "../support/global-setup.ts",
  use: {
    baseURL: external ?? base.use?.baseURL,
    locale: "en-IN",
    timezoneId: "Asia/Kolkata",
    launchOptions: chromiumLaunchOptions(),
  },
  // playwright.config.ts's `next start`, run from apps/web (not from this directory).
  ...(!external && server
    ? { webServer: { ...server, cwd: path.resolve(import.meta.dirname, "../..") } }
    : {}),
});
