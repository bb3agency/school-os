import { expect, test } from "@playwright/test";
import {
  expectNoAxeViolations,
  expectNoHorizontalOverflow,
  expectVisibleFocusOnEveryStop,
} from "./support/a11y-helpers";
import {
  cspViolations,
  expectEverythingRevealedAfterScrolling,
  hiddenRevealContent,
  settleAnimations,
} from "./support/marketing";
import { expectNoTelugu, teluguOn } from "./support/telugu";

/**
 * Public marketing pages (docs/17 §5.6; FR-IAM-001, NFR-A11Y-001, SEC-010): home, Features,
 * Security, Pricing and About at 1366×768 and 375 px, with and without reduced motion: no
 * CSP violations or console errors, no axe violations, no sideways scroll, every link resolves,
 * the scroll reveal never leaves content hidden (also at 320 px × 256 px, i.e. 400% zoom),
 * keyboard focus is never obscured by the sticky header, and the phone menu is a keyboard
 * disclosure. The e2e server runs without SOS_PUBLIC_* settings (no "Talk to us").
 * The dedicated-host variant (SOS_DEPLOYMENT_MODE=dedicated) is covered in app/marketing.test.tsx.
 */

const PAGES = [
  { path: "/welcome", h1: "Enter student details once." },
  { path: "/features", h1: "Everything the office needs to keep student records right" },
  { path: "/security", h1: "Built for children's data from the first line of code" },
  { path: "/pricing", h1: "Two ways to run SchoolOS" },
  { path: "/about", h1: "Software for the school office, built with the office" },
] as const;

const VIEWPORTS = [
  { width: 1366, height: 768 },
  { width: 375, height: 812 },
] as const;

test.describe("public marketing pages (docs/17 §5.6)", () => {
  test.beforeEach(({}, testInfo) => {
    test.skip(teluguOn(testInfo), "the Telugu-on project runs only @telugu tests");
  });

  for (const viewport of VIEWPORTS) {
    for (const reducedMotion of ["no-preference", "reduce"] as const) {
      test(`${viewport.width}px, ${reducedMotion} motion: no CSP or console errors, axe, no sideways scroll, nothing left hidden`, async ({
        page,
      }) => {
        test.setTimeout(120_000);
        const violations = cspViolations(page);
        const errors: string[] = [];
        page.on("pageerror", (error) => errors.push(error.message));
        page.on("console", (message) => {
          if (message.type() === "error") errors.push(message.text());
        });
        await page.setViewportSize(viewport);
        await page.emulateMedia({ reducedMotion });
        for (const { path, h1 } of PAGES) {
          const label = `${path} ${viewport.width} ${reducedMotion}`;
          await page.goto(path);
          await expect(page.getByRole("heading", { level: 1 }), label).toContainText(h1);
          if (reducedMotion === "reduce") {
            // Nothing is held hidden and nothing moves.
            expect(await hiddenRevealContent(page), label).toEqual([]);
          }
          await settleAnimations(page);
          await expectNoAxeViolations(page, label);
          await expectNoHorizontalOverflow(page, label);
          await expectNoTelugu(page, label);
          await expectEverythingRevealedAfterScrolling(page, label);
        }
        expect(violations).toEqual([]);
        expect(errors).toEqual([]);
      });
    }
  }

  test("400% zoom (320 × 256 CSS px): tall revealed blocks still appear (WCAG 1.4.10)", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 320, height: 256 });
    for (const { path } of PAGES) {
      await page.goto(path);
      await expectNoHorizontalOverflow(page, `${path} 320`);
      await expectEverythingRevealedAfterScrolling(page, `${path} 320×256`);
    }
  });

  test("keyboard focus inside a block that waits for its reveal shows it at once", async ({
    page,
  }) => {
    await page.goto("/welcome");
    const tile = page
      .getByRole("region", { name: "Built around the work the office already does" })
      .getByRole("link")
      .first();
    const item = tile.locator("xpath=..");
    // Below the fold at 1366×768: held hidden until it scrolls in.
    expect(Number(await item.evaluate((el) => getComputedStyle(el).opacity))).toBeLessThan(1);
    await tile.focus();
    await expect(tile).toBeInViewport();
    expect(Number(await item.evaluate((el) => getComputedStyle(el).opacity))).toBe(1);
    expect(await item.evaluate((el) => el.getAnimations().length)).toBe(0);
  });

  test("Shift+Tab back up the page: the focused control is never under the sticky header (WCAG 2.4.11)", async ({
    page,
  }) => {
    for (const path of ["/welcome", "/security"]) {
      await page.goto(path);
      await page.getByRole("contentinfo").getByRole("link").last().focus();
      const obscured: string[] = [];
      for (let i = 0; i < 40; i += 1) {
        await page.keyboard.press("Shift+Tab");
        const stop = await page.evaluate(() => {
          const el = document.activeElement as HTMLElement | null;
          const header = document.querySelector("header");
          // The skip link sits above the header on purpose (it is the first stop).
          const skip = el?.getAttribute("href") === "#main";
          if (!el || !header || header.contains(el) || el === document.body || skip) return null;
          const top = el.getBoundingClientRect().top;
          return {
            top,
            header: header.getBoundingClientRect().bottom,
            name: el.innerText.slice(0, 30),
          };
        });
        if (stop && stop.top < stop.header - 1) obscured.push(`${stop.name} (${stop.top})`);
      }
      expect(obscured, path).toEqual([]);
    }
  });

  test("every header and footer link resolves; current page is marked", async ({ page }) => {
    for (const { path } of PAGES) {
      await page.goto(path);
      const current = page.getByRole("banner").locator('[aria-current="page"]');
      await expect(current).toHaveCount(1);
      expect(await current.getAttribute("href")).toBe(path);
      const hrefs = await page
        .locator("header a[href], footer a[href]")
        .evaluateAll((links) => links.map((link) => link.getAttribute("href") ?? ""));
      for (const href of hrefs) {
        // No URL carries a locale (ADR-0036 note): page links are plain paths, never /en or /te.
        expect(href, path).toMatch(/^(#|\/[a-z][a-z-]*(#[a-z-]+)?|\/bff\/auth\/login)$/);
        expect(href, path).not.toMatch(/^\/(en|te)(\/|$|#)/);
      }
      for (const href of new Set(
        hrefs.filter((href) => /^\/[a-z]/.test(href) && !href.startsWith("/bff/")),
      )) {
        expect((await page.request.get(href)).status(), href).toBe(200);
      }
    }
  });

  test("features: the jump list moves to each section below the sticky header", async ({
    page,
  }) => {
    await page.goto("/features");
    const jump = page.getByRole("navigation", { name: "Jump to a feature" });
    await jump.getByRole("link").nth(3).click();
    await expect(page).toHaveURL(/#ask$/);
    const header = await page
      .getByRole("banner")
      .evaluate((el) => el.getBoundingClientRect().bottom);
    const top = await page
      .locator("section#ask h2")
      .evaluate((el) => el.getBoundingClientRect().top);
    expect(top).toBeGreaterThanOrEqual(header);
    // A deep link lands with its illustration visible.
    await page.goto("/features#certificates");
    await expect(page.locator("section#certificates h2")).toBeInViewport();
    await settleAnimations(page);
    const illustration = page.locator("section#certificates [data-reveal]");
    expect(Number(await illustration.evaluate((el) => getComputedStyle(el).opacity))).toBe(1);
    // Blocks above it reveal as the page scrolls back through them.
    await expectEverythingRevealedAfterScrolling(page, "/features#certificates");
  });

  test("keyboard only on every page: visible focus on each stop", async ({ page }) => {
    for (const { path } of PAGES.slice(1)) {
      await page.goto(path);
      await expectVisibleFocusOnEveryStop(page, path, 40);
    }
  });

  test("phone menu (375 px): a keyboard disclosure that closes on Escape, Tab out and outside clicks", async ({
    page,
  }) => {
    const violations = cspViolations(page);
    await page.setViewportSize({ width: 375, height: 812 });
    await page.goto("/pricing");
    const menu = page.getByRole("button", { name: "Menu" });
    await expect(menu).toHaveAttribute("aria-expanded", "false");
    await menu.click();
    const close = page.getByRole("button", { name: "Close menu" });
    await expect(close).toHaveAttribute("aria-expanded", "true");
    const panel = page.locator(`[id="${await close.getAttribute("aria-controls")}"]`);
    await expect(panel.getByRole("link", { name: "Features" })).toBeFocused();
    await expect(panel.getByRole("link", { name: "Pricing" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    await expect(panel.getByRole("link", { name: "Sign in" })).toHaveAttribute(
      "href",
      "/bff/auth/login",
    );
    await settleAnimations(page);
    await expectNoAxeViolations(page, "phone menu open");
    await expectNoHorizontalOverflow(page, "phone menu open");

    // Escape closes and returns focus to the button.
    await page.keyboard.press("Escape");
    await expect(menu).toBeFocused();
    await expect(menu).toHaveAttribute("aria-expanded", "false");
    await expect(panel).toHaveCount(0);

    // Tab past the last item leaves the header: the panel closes rather than cover the page.
    await menu.press("Enter");
    await expect(panel.getByRole("link", { name: "Features" })).toBeFocused();
    await panel.getByRole("link", { name: "Sign in" }).focus();
    await page.keyboard.press("Tab");
    await expect(page.getByRole("button", { name: "Menu" })).toHaveAttribute(
      "aria-expanded",
      "false",
    );

    // A click (tap) outside closes it too.
    await page.getByRole("button", { name: "Menu" }).click();
    await expect(page.getByRole("button", { name: "Close menu" })).toBeVisible();
    await page.mouse.click(8, 780);
    await expect(page.getByRole("button", { name: "Menu" })).toHaveAttribute(
      "aria-expanded",
      "false",
    );

    // Following a link closes it and opens the page.
    await page.getByRole("button", { name: "Menu" }).click();
    await page.getByRole("button", { name: "Close menu" }).waitFor();
    await page.locator("header").getByRole("link", { name: "About" }).click();
    await expect(page).toHaveURL((url) => url.pathname === "/about");
    await expect(page.getByRole("button", { name: "Menu" })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
    expect(violations).toEqual([]);
  });

  test("print: revealed content and dark bands print plainly", async ({ page }) => {
    await page.goto("/security");
    await page.emulateMedia({ media: "print" });
    expect(await hiddenRevealContent(page)).toEqual([]);
    await expect(page.locator("header.mk-header")).toBeHidden();
  });
});
