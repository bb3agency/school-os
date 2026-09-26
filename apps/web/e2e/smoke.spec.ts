import { expect, test } from "@playwright/test";

test.describe("smoke (SEC-010, NFR-I18N-001)", () => {
  test("root redirects to English and pages load without CSP violations", async ({ page }) => {
    const violations: string[] = [];
    page.on("console", (message) => {
      if (/Content Security Policy|Refused to/i.test(message.text()))
        violations.push(message.text());
    });

    const response = await page.goto("/");
    expect(page.url()).toMatch(/\/en$/);
    const csp = response?.headers()["content-security-policy"] ?? "";
    expect(csp).toContain("'strict-dynamic'");
    expect(csp).toMatch(/'nonce-[A-Za-z0-9+/=]+'/);
    expect(csp).not.toMatch(/script-src[^;]*'unsafe-inline'/);

    await expect(page.getByRole("heading", { level: 1, name: "Home" })).toBeVisible();

    await page.goto("/en/platform/provision");
    await expect(page.getByText("Platform admin")).toBeVisible();
    await page.getByRole("button", { name: "Next" }).click();
    await expect(page.getByRole("alert").filter({ hasText: "need attention" })).toBeVisible();

    expect(violations).toEqual([]);
  });

  test("switching to Telugu keeps the page and renders Telugu text", async ({ page }) => {
    await page.goto("/en/platform/support");
    await page.getByRole("link", { name: "తెలుగు" }).click();
    await expect(page).toHaveURL(/\/te\/platform\/support$/);
    await expect(page.locator("html")).toHaveAttribute("lang", "te");
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("సపోర్ట్ టికెట్లు");
    await expect(page.getByText("విద్యార్థుల వ్యక్తిగత సమాచారం చేర్చవద్దు")).toBeVisible();
  });

  test("keyboard: skip link is the first tab stop and moves focus to main", async ({ page }) => {
    await page.goto("/en/settings/structure");
    await page.keyboard.press("Tab");
    const skip = page.getByRole("link", { name: "Skip to main content" });
    await expect(skip).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(page.locator("main#main")).toBeFocused();
  });

  test("health check and BFF stub", async ({ request }) => {
    const health = await request.get("/healthz");
    expect(await health.json()).toEqual({ status: "ok" });
    const bff = await request.get("/bff/api/v1/me");
    expect(bff.status()).toBe(501);
    expect(bff.headers()["content-type"]).toContain("application/problem+json");
  });
});
