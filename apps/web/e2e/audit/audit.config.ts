import { defineConfig } from "@playwright/test";

/** Throwaway responsive audit (not committed). Server started by hand on E2E_BASE_URL. */
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
  },
});
