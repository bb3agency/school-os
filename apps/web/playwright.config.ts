import { defineConfig, devices } from "@playwright/test";
import { chromiumLaunchOptions } from "./e2e/support/browser";
import { E2E_PUBLIC_SETTINGS } from "./e2e/support/public-settings";

const PORT = Number(process.env.E2E_PORT ?? 3000);
const baseURL = process.env.E2E_BASE_URL ?? `http://localhost:${PORT}`;
/**
 * ADR-0036: a second server from the same build with SOS_TELUGU_ENABLED on, for the tests
 * tagged @telugu (project chromium-telugu). Against a running stack, set
 * E2E_TELUGU_BASE_URL to one started with the switch on, or the project is left out.
 */
const TELUGU_PORT = Number(process.env.E2E_TELUGU_PORT ?? PORT + 1);
const teluguBaseURL =
  process.env.E2E_TELUGU_BASE_URL ??
  (process.env.E2E_BASE_URL ? undefined : `http://localhost:${TELUGU_PORT}`);
/**
 * Public-site variants (docs/17 §5.6), one more server each from the same build: tests tagged
 * @contact run against every SOS_PUBLIC_* setting set to synthetic values
 * (e2e/support/public-settings.ts), tests tagged @dedicated against
 * SOS_DEPLOYMENT_MODE=dedicated (the marketing pages answer 404; /welcome is a sign-in card).
 * Against a running stack, set E2E_CONTACT_BASE_URL / E2E_DEDICATED_BASE_URL to servers started
 * that way, or the project is left out.
 */
const CONTACT_PORT = Number(process.env.E2E_CONTACT_PORT ?? PORT + 2);
const DEDICATED_PORT = Number(process.env.E2E_DEDICATED_PORT ?? PORT + 3);
const localVariant = (port: number) =>
  process.env.E2E_BASE_URL ? undefined : `http://localhost:${port}`;
const contactBaseURL = process.env.E2E_CONTACT_BASE_URL ?? localVariant(CONTACT_PORT);
const dedicatedBaseURL = process.env.E2E_DEDICATED_BASE_URL ?? localVariant(DEDICATED_PORT);
/** Sign-in e2e with the scripted stand-in IdP and canned API (needs Valkey at REDIS_URL). */
const standIn = process.env.E2E_STAND_IN === "1";
const IDP_PORT = Number(process.env.E2E_IDP_PORT ?? 8089);
const API_PORT = Number(process.env.E2E_API_PORT ?? 8099);

/** Obvious placeholder (>= 32 bytes); accepted by the BFF only for http://localhost. */
const devOnly = (name: string) => `dev-only-e2e-${name}-${"x".repeat(32)}`;

const desktop = { ...devices["Desktop Chrome"], viewport: { width: 1366, height: 768 } };

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
  // The stand-in API keeps state (journeys reset it): one test at a time across projects.
  ...(standIn ? { workers: 1 } : {}),
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
    // The locked Chromium; locally a preinstalled one when the download is blocked (support/browser.ts).
    launchOptions: chromiumLaunchOptions(),
  },
  projects: [
    {
      // The product default: Telugu switched off (ADR-0036), shared tier, no SOS_PUBLIC_*.
      name: "chromium",
      metadata: { telugu: false },
      // The public-site variants run only in their own projects below.
      grepInvert: /@contact|@dedicated/,
      use: desktop,
    },
    ...(teluguBaseURL
      ? [
          {
            // Telugu switched on: the dormant Telugu paths keep their checks.
            name: "chromium-telugu",
            metadata: { telugu: true },
            grep: /@telugu/,
            use: { ...desktop, baseURL: teluguBaseURL },
          },
        ]
      : []),
    ...(contactBaseURL
      ? [
          {
            name: "chromium-contact",
            metadata: { telugu: false, contact: true },
            grep: /@contact/,
            use: { ...desktop, baseURL: contactBaseURL },
          },
        ]
      : []),
    ...(dedicatedBaseURL
      ? [
          {
            name: "chromium-dedicated",
            metadata: { telugu: false, dedicated: true },
            grep: /@dedicated/,
            use: { ...desktop, baseURL: dedicatedBaseURL },
          },
        ]
      : []),
  ],
  ...(process.env.E2E_BASE_URL
    ? {}
    : {
        webServer: [
          { port: PORT, base: baseURL, telugu: false, extra: {} },
          { port: TELUGU_PORT, base: `http://localhost:${TELUGU_PORT}`, telugu: true, extra: {} },
          {
            port: CONTACT_PORT,
            base: `http://localhost:${CONTACT_PORT}`,
            telugu: false,
            extra: E2E_PUBLIC_SETTINGS,
          },
          {
            port: DEDICATED_PORT,
            base: `http://localhost:${DEDICATED_PORT}`,
            telugu: false,
            extra: { SOS_DEPLOYMENT_MODE: "dedicated" },
          },
        ].map(({ port, base, telugu, extra }) => ({
          command: `npx next start --port ${port}`,
          url: `${base}/healthz`,
          reuseExistingServer: !process.env.CI,
          timeout: 60_000,
          // Local http run: dev-only placeholder values are accepted only for localhost.
          env: {
            APP_BASE_URL: base,
            SOS_TELUGU_ENABLED: telugu ? "true" : "false",
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
            ...extra,
          },
        })),
      }),
});
