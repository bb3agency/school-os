import { expect, test } from "@playwright/test";
import { TELUGU, TELUGU_OFF_REASON, teluguOn } from "./support/telugu";

/**
 * Smoke tests without an identity provider: every console page needs a session, so a
 * visitor is sent to sign-in; the public signed-out page carries the CSP and i18n checks.
 * Full sign-in is verified manually against the dev OIDC stub (apps/web/README.md).
 */
test.describe("smoke (SEC-010, NFR-I18N-001, FR-IAM-001)", () => {
  test(`console pages send visitors to sign-in, keeping the return path ${TELUGU}`, async ({
    request,
  }, testInfo) => {
    // ADR-0036: with Telugu off, /te first goes to the same English page.
    const telugu = teluguOn(testInfo);
    for (const [path, login] of [
      ["/en/settings/users", "/bff/auth/login?next=%2Fen%2Fsettings%2Fusers"],
      ["/en/platform/schools", "/bff/auth/platform/login?next=%2Fen%2Fplatform%2Fschools"],
      [
        "/te/platform/schools",
        telugu
          ? "/bff/auth/platform/login?next=%2Fte%2Fplatform%2Fschools"
          : "/en/platform/schools",
      ],
    ] as const) {
      const response = await request.get(path, { maxRedirects: 0 });
      expect(response.status(), path).toBe(307);
      expect(response.headers()["location"], path).toContain(login);
    }
  });

  test("signed-out page loads without CSP violations", async ({ page }) => {
    const violations: string[] = [];
    page.on("console", (message) => {
      if (/Content Security Policy|Refused to/i.test(message.text()))
        violations.push(message.text());
    });

    const response = await page.goto("/signed-out?error=signin_failed");
    expect(page.url()).toMatch(/\/en\/signed-out\?error=signin_failed$/);
    const csp = response?.headers()["content-security-policy"] ?? "";
    expect(csp).toContain("'strict-dynamic'");
    expect(csp).toMatch(/'nonce-[A-Za-z0-9+/=]+'/);
    expect(csp).not.toMatch(/script-src[^;]*'unsafe-inline'/);

    await expect(page.getByRole("heading", { level: 1, name: "You are signed out" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Sign in to the school office" })).toHaveAttribute(
      "href",
      "/bff/auth/login",
    );
    expect(violations).toEqual([]);
  });

  test(`switching to Telugu keeps the page and renders Telugu text ${TELUGU}`, async ({
    page,
  }, testInfo) => {
    test.skip(!teluguOn(testInfo), TELUGU_OFF_REASON);
    await page.goto("/en/signed-out?kind=operator");
    await page.getByRole("link", { name: "తెలుగు" }).click();
    await expect(page).toHaveURL(/\/te\/signed-out/);
    await expect(page.locator("html")).toHaveAttribute("lang", "te");
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("మీరు సైన్ అవుట్ అయ్యారు");
  });

  test("health check, and the BFF never answers without a session", async ({ request }) => {
    const health = await request.get("/healthz");
    expect(await health.json()).toEqual({ status: "ok" });
    const bff = await request.get("/bff/api/v1/me");
    // 401 with Valkey running, 503 when the smoke run has no Valkey.
    expect([401, 503]).toContain(bff.status());
    expect(bff.headers()["content-type"]).toContain("application/problem+json");
  });
});
