import { expect, test } from "@playwright/test";
import {
  LOCALE_COOKIE,
  selectLanguage,
  TELUGU,
  TELUGU_OFF_REASON,
  teluguOn,
} from "./support/telugu";

/**
 * Smoke tests without an identity provider: every console page needs a session, so a
 * visitor is sent to sign-in; the public signed-out page carries the CSP and i18n checks.
 * Full sign-in is verified manually against the dev OIDC stub (apps/web/README.md).
 * No URL carries a locale (product owner 2026-09-30; ADR-0036 note).
 */
test.describe("smoke (SEC-010, NFR-I18N-001, FR-IAM-001)", () => {
  test(`console pages send visitors to sign-in, keeping the return path ${TELUGU}`, async ({
    request,
  }) => {
    // The return path is prefix-less with Telugu on and off.
    for (const [path, login] of [
      ["/settings/users", "/bff/auth/login?next=%2Fsettings%2Fusers"],
      ["/platform/schools", "/bff/auth/platform/login?next=%2Fplatform%2Fschools"],
      ["/students?page=2", "/bff/auth/login?next=%2Fstudents%3Fpage%3D2"],
    ] as const) {
      const response = await request.get(path, { maxRedirects: 0 });
      expect(response.status(), path).toBe(307);
      expect(response.headers()["location"], path).toContain(login);
    }
    // An old prefixed link first loses its prefix (308), then goes to sign-in.
    const old = await request.get("/te/platform/schools", { maxRedirects: 0 });
    expect(old.status()).toBe(308);
    expect(new URL(old.headers()["location"] ?? "", "http://x").pathname).toBe("/platform/schools");
  });

  test("signed-out page loads without CSP violations", async ({ page }) => {
    const violations: string[] = [];
    page.on("console", (message) => {
      if (/Content Security Policy|Refused to/i.test(message.text()))
        violations.push(message.text());
    });

    const response = await page.goto("/signed-out?error=signin_failed");
    expect(new URL(page.url()).pathname).toBe("/signed-out");
    expect(new URL(page.url()).search).toBe("?error=signin_failed");
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

  test(`switching to Telugu keeps the same address and renders Telugu text ${TELUGU}`, async ({
    page,
  }, testInfo) => {
    test.skip(!teluguOn(testInfo), TELUGU_OFF_REASON);
    await page.goto("/signed-out?kind=operator");
    await expect(page.locator("html")).toHaveAttribute("lang", "en");
    await page.getByRole("button", { name: "తెలుగు" }).click();
    await expect(page.locator("html")).toHaveAttribute("lang", "te");
    await expect(page).toHaveURL(
      (url) => `${url.pathname}${url.search}` === "/signed-out?kind=operator",
    );
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("మీరు సైన్ అవుట్ అయ్యారు");
    const cookies = await page.context().cookies();
    expect(cookies.find((cookie) => cookie.name === LOCALE_COOKIE)?.value).toBe("te");
    // The choice is remembered on other pages, and switching back works the same way.
    await page.goto("/welcome");
    await expect(page.locator("html")).toHaveAttribute("lang", "te");
    await page.goto("/signed-out");
    await page.getByRole("button", { name: "English" }).click();
    await expect(page.locator("html")).toHaveAttribute("lang", "en");
    await expect(page).toHaveURL((url) => url.pathname === "/signed-out");
  });

  test(`with Telugu on: the cookie first, then Accept-Language; an old /te link stores Telugu ${TELUGU}`, async ({
    browser,
    page,
    request,
  }, testInfo) => {
    test.skip(!teluguOn(testInfo), TELUGU_OFF_REASON);
    // A browser that prefers Telugu gets Telugu at the plain address.
    const teluguBrowser = await browser.newContext({ locale: "te-IN" });
    const telugu = await teluguBrowser.newPage();
    await telugu.goto("/welcome");
    await expect(telugu).toHaveURL((url) => url.pathname === "/welcome");
    await expect(telugu.locator("html")).toHaveAttribute("lang", "te");
    // A stored English choice wins over the browser's preference.
    await teluguBrowser.addCookies([
      { name: LOCALE_COOKIE, value: "en", url: test.info().project.use.baseURL ?? "" },
    ]);
    await telugu.goto("/welcome");
    await expect(telugu.locator("html")).toHaveAttribute("lang", "en");
    await teluguBrowser.close();

    // The cookie alone selects Telugu for an English browser.
    await selectLanguage(page, "te");
    await page.goto("/signed-out");
    await expect(page.locator("html")).toHaveAttribute("lang", "te");

    // An old /te link: 308 to the same path without the prefix, storing Telugu first.
    const old = await request.get("/te/welcome?from=bookmark", { maxRedirects: 0 });
    expect(old.status()).toBe(308);
    const location = new URL(old.headers()["location"] ?? "", "http://x");
    expect(`${location.pathname}${location.search}`).toBe("/welcome?from=bookmark");
    expect(old.headers()["set-cookie"] ?? "").toContain(`${LOCALE_COOKIE}=te`);
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
