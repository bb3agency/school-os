import { expect, test } from "@playwright/test";
import {
  expectNoAxeViolations,
  expectNoHorizontalOverflow,
  expectVisibleFocusOnEveryStop,
} from "./support/a11y-helpers";
import { cspViolations, settleAnimations } from "./support/marketing";
import { expectNoTelugu, TELUGU, TELUGU_OFF_REASON, teluguOn } from "./support/telugu";

/**
 * Public home page (/welcome; no session, no IdP needed): signed-out visitors to the school
 * home land here, deep links still go to sign-in (FR-IAM-001); WCAG 2.2 AA via axe and
 * keyboard-only use at 1366×768 and phone width (NFR-A11Y-001, CLAUDE.md §8, §10); no CSP
 * violations (SEC-010). The other public pages are in marketing.spec.ts (docs/17 §5.6).
 * The e2e server has no SOS_PUBLIC_* settings: no "Talk to us", "Sign in" is the primary call.
 */
test.describe("welcome page (FR-IAM-001, NFR-A11Y-001, SEC-010)", () => {
  test(`the school home sends signed-out visitors to the welcome page ${TELUGU}`, async ({
    request,
  }, testInfo) => {
    // ADR-0036: with Telugu off, /te goes to /en first.
    for (const [path, target] of [
      ["/en", "/en/welcome"],
      ["/te", teluguOn(testInfo) ? "/te/welcome" : "/en"],
    ] as const) {
      const response = await request.get(path, { maxRedirects: 0 });
      expect(response.status(), path).toBe(307);
      expect(new URL(response.headers()["location"] ?? "", "http://x").pathname, path).toBe(target);
    }
    // Deep links keep going straight to sign-in, with the return path.
    const deep = await request.get("/en/students", { maxRedirects: 0 });
    expect(deep.headers()["location"]).toContain("/bff/auth/login?next=%2Fen%2Fstudents");
  });

  test("loads without CSP violations; hero and sign-in fit above the fold", async ({ page }) => {
    const violations = cspViolations(page);
    await page.goto("/");
    await expect(page).toHaveURL(/\/en\/welcome$/);
    const h1 = page.getByRole("heading", { level: 1 });
    await expect(h1).toHaveText(
      "Enter student details once. Catch mismatches before the portal does.",
    );
    await expect(page).toHaveTitle(/School office records, checked/);
    const cta = page.getByRole("main").getByRole("link", { name: "Sign in", exact: true }).first();
    await expect(cta).toHaveAttribute("href", "/bff/auth/login");
    // No contact address configured: no "Talk to us" anywhere, no placeholder.
    await expect(page.getByRole("link", { name: /Talk to us/ })).toHaveCount(0);
    await expect(page.locator('a[href^="mailto:"]')).toHaveCount(0);
    // 1366×768 office PC: headline and both calls to action are visible without scrolling.
    const bottom = await page
      .getByRole("link", { name: "See how it works" })
      .evaluate((el) => el.getBoundingClientRect().bottom);
    expect(bottom).toBeLessThanOrEqual(768);
    await settleAnimations(page);
    expect(violations).toEqual([]);
  });

  for (const locale of ["en", "te"] as const) {
    test(`no WCAG 2.2 AA violations, no sideways scroll [${locale}]${locale === "te" ? ` ${TELUGU}` : ""}`, async ({
      page,
    }, testInfo) => {
      test.skip(locale === "te" && !teluguOn(testInfo), TELUGU_OFF_REASON);
      await page.goto(`/${locale}/welcome`);
      await expect(page.locator("html")).toHaveAttribute("lang", locale);
      await settleAnimations(page);
      await expectNoAxeViolations(page, `welcome ${locale}`);
      await expectNoHorizontalOverflow(page, `welcome ${locale}`);
      // With every FAQ answer open too.
      await page
        .locator("#faq summary")
        .evaluateAll((items) => items.forEach((summary) => (summary as HTMLElement).click()));
      await expectNoAxeViolations(page, `welcome ${locale} FAQ open`);
    });
  }

  test(`phone width (375 px): no axe violations, no sideways scroll ${TELUGU}`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width: 375, height: 812 });
    for (const locale of teluguOn(testInfo) ? (["en", "te"] as const) : (["en"] as const)) {
      await page.goto(`/${locale}/welcome`);
      await settleAnimations(page);
      await expectNoAxeViolations(page, `welcome ${locale} 375`);
      await expectNoHorizontalOverflow(page, `welcome ${locale} 375`);
    }
  });

  test("keyboard only: skip link, header links in order, how it works, FAQ, visible focus", async ({
    page,
  }) => {
    await page.goto("/en/welcome");

    await page.keyboard.press("Tab");
    await expect(page.getByRole("link", { name: "Skip to main content" })).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(page.locator("main#main")).toBeFocused();

    // From the top: wordmark, the four pages, then Sign in.
    await page.goto("/en/welcome");
    await page.keyboard.press("Tab");
    await page.keyboard.press("Tab");
    const banner = page.getByRole("banner");
    await expect(banner.getByRole("link", { name: "SchoolOS" })).toBeFocused();
    const nav = banner.getByRole("navigation", { name: "Main" });
    for (const name of ["Features", "Security", "Pricing", "About"]) {
      await page.keyboard.press("Tab");
      await expect(nav.getByRole("link", { name, exact: true })).toBeFocused();
    }
    await page.keyboard.press("Tab");
    await expect(banner.getByRole("link", { name: "Sign in" })).toBeFocused();
    await expect(banner.getByRole("link", { name: "Sign in" })).toHaveAttribute(
      "href",
      "/bff/auth/login",
    );

    // "See how it works" moves to the steps; the heading is not hidden under the sticky header.
    await page.getByRole("link", { name: "See how it works" }).focus();
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/#how$/);
    const heading = page.getByRole("heading", {
      level: 2,
      name: "Three steps, using the records you already keep",
    });
    await expect(heading).toBeInViewport();
    const headerBottom = await banner.evaluate((el) => el.getBoundingClientRect().bottom);
    const headingTop = await heading.evaluate((el) => el.getBoundingClientRect().top);
    expect(headingTop).toBeGreaterThanOrEqual(headerBottom);

    // FAQ: a summary opens its answer with Enter and closes with Space.
    const summary = page.locator("#faq summary").first();
    await summary.focus();
    await page.keyboard.press("Enter");
    await expect(page.locator("#faq details").first()).toHaveAttribute("open", "");
    await page.keyboard.press("Space");
    await expect(page.locator("#faq details").first()).not.toHaveAttribute("open", "");

    await page.goto("/en/welcome");
    await expectVisibleFocusOnEveryStop(page, "welcome", 40);
  });

  test(`the Telugu welcome page keeps its language ${TELUGU}`, async ({ page }, testInfo) => {
    // The language switch of the old welcome page is gone (docs/17 §5.6: no new switches);
    // with Telugu on, /te/welcome still renders as the Telugu locale.
    test.skip(!teluguOn(testInfo), TELUGU_OFF_REASON);
    await page.goto("/te/welcome");
    await expect(page).toHaveURL(/\/te\/welcome$/);
    await expect(page.locator("html")).toHaveAttribute("lang", "te");
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  });

  test("the signed-out page links back to the welcome page", async ({ page }) => {
    await page.goto("/en/signed-out");
    await page.getByRole("link", { name: "SchoolOS home", exact: true }).click();
    await expect(page).toHaveURL(/\/en\/welcome$/);
  });

  test("with Telugu off: English only, no language switch, no Telugu font (ADR-0036)", async ({
    page,
  }, testInfo) => {
    test.skip(teluguOn(testInfo), "checks the Telugu-off default");
    await page.goto("/en/welcome");
    await expectNoTelugu(page, "welcome en");
    await page.goto("/te/welcome");
    await expect(page).toHaveURL(/\/en\/welcome$/);
    await expect(page.locator("html")).toHaveAttribute("lang", "en");
    await expectNoTelugu(page, "/te/welcome");
    const font = await page.request.get("/fonts/telugu/noto-sans-telugu.css");
    expect(font.status()).toBe(404);
  });

  test(`the Telugu signed-out page links back to the welcome page ${TELUGU}`, async ({
    page,
  }, testInfo) => {
    test.skip(!teluguOn(testInfo), TELUGU_OFF_REASON);
    await page.goto("/te/signed-out");
    await page.getByRole("link", { name: "SchoolOS హోమ్ పేజీ" }).click();
    await expect(page).toHaveURL(/\/te\/welcome$/);
  });
});
