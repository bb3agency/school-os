import { defineConfig, devices } from "@playwright/test";

const PORT = Number(process.env.E2E_PORT ?? 3000);
const baseURL = process.env.E2E_BASE_URL ?? `http://localhost:${PORT}`;

/**
 * End-to-end smoke tests (`make e2e`). Not part of the CI unit stage.
 * By default builds nothing: it starts `next start` against an existing `next build`.
 * Set E2E_BASE_URL to test an already running stack (e.g. docker compose).
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"], ["html", { open: "never" }]],
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
          env: { APP_BASE_URL: baseURL },
        },
      }),
});
