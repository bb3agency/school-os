import { expect, test, type Page } from "@playwright/test";
import { installFixtures } from "./support/layout-fixtures";
import {
  LOCALES,
  REQUIRED_VIEWPORTS,
  SCREEN_GROUPS,
  measure,
  problems,
  settle,
  signInAs,
} from "./support/responsive";

/**
 * Responsive layout (NFR-A11Y-001, NFR-I18N-001; CLAUDE.md §8, §10; docs/17 §5.1): every
 * screen at 1366×768 (office PC) and 375×812 (phone), in English and Telugu, has no
 * horizontal page scroll, nothing sticking out of the screen or its card, no clipped text
 * and no touch target under 24px (WCAG 2.5.8). The shell's menu drawer works keyboard-only,
 * dialogs fit a phone and print stays inside an A4 page.
 *
 * Signed-in screens need E2E_STAND_IN=1 (scripted IdP + canned API, Valkey at REDIS_URL);
 * e2e/support/layout-fixtures.ts answers the browser's GET /bff/api/v1/* calls with long,
 * mixed-script SYNTHETIC content so every list and detail has rows to lay out.
 * The wider sweep (ten viewports, screenshots, JSON report) is e2e/audit/responsive.audit.ts.
 */

const standIn = process.env.E2E_STAND_IN === "1";

async function checkScreens(page: Page, locale: string, pages: string[]): Promise<string[]> {
  const failures: string[] = [];
  for (const path of pages) {
    const url = `/${locale}${path}`;
    await page.goto(url);
    await settle(page);
    await expect(page.locator("main#main, main").first(), url).toBeVisible();
    for (const problem of problems(await measure(page))) failures.push(`${url}: ${problem}`);
  }
  return failures;
}

for (const [width, height] of REQUIRED_VIEWPORTS) {
  test.describe(`responsive layout at ${width}×${height}`, () => {
    test.use({ viewport: { width, height } });

    for (const [name, group] of Object.entries(SCREEN_GROUPS)) {
      for (const locale of LOCALES) {
        test(`${name} screens [${locale}]: no sideways scroll, clipping or small targets (NFR-A11Y-001)`, async ({
          page,
        }) => {
          test.skip(group.subject !== null && !standIn, "set E2E_STAND_IN=1 (needs Valkey)");
          test.setTimeout(10 * 60_000);
          if (group.subject) {
            await installFixtures(page);
            await signInAs(page, group.signInPath, group.subject);
          }
          expect(await checkScreens(page, locale, group.pages)).toEqual([]);
        });
      }
    }
  });
}

test.describe("app shell on a phone: keyboard-only menu drawer (NFR-A11Y-001)", () => {
  test.skip(!standIn, "set E2E_STAND_IN=1 (needs Valkey at REDIS_URL)");
  test.use({ viewport: { width: 375, height: 812 } });

  for (const locale of LOCALES) {
    test(`school console [${locale}]: open with Enter, focus trapped, Escape closes and returns focus`, async ({
      page,
    }) => {
      await installFixtures(page);
      await signInAs(page, "/en/support", "clerk");
      await page.goto(`/${locale}/students`);
      await settle(page);
      const menuName = locale === "en" ? "Menu" : "మెనూ";
      const closeName = locale === "en" ? "Close menu" : "మెనూ మూసివేయండి";

      // Below lg there is no list panel; the icon rail is hidden below md.
      await expect(page.locator("aside nav")).toBeHidden();
      const menu = page.getByRole("button", { name: menuName, exact: true });
      await expect(menu).toHaveAttribute("aria-expanded", "false");

      // The skip link comes first, then the menu button.
      await page.locator("body").focus();
      await page.keyboard.press("Tab");
      await expect(page.locator("a[href='#main']")).toBeFocused();
      await page.keyboard.press("Tab");
      await expect(menu).toBeFocused();
      await page.keyboard.press("Enter");

      const drawer = page.getByRole("dialog", { name: menuName });
      await expect(drawer).toBeVisible();
      await expect(menu).toHaveAttribute("aria-expanded", "true");
      await expect(drawer.getByRole("button", { name: closeName })).toBeFocused();
      await expect(drawer.locator("a[aria-current='page']")).toBeVisible();
      // The drawer fits the phone and leaves a strip of the dimmed page to tap.
      const box = await drawer.boundingBox();
      expect(box?.x).toBe(0);
      expect((box?.width ?? 0) <= 375 - 48).toBe(true);
      // The page behind does not scroll while the drawer is open.
      expect(await page.evaluate(() => getComputedStyle(document.documentElement).overflow)).toBe(
        "hidden",
      );

      // Focus stays in the drawer: Tab through every stop and one round more.
      const stops = await drawer.locator("a[href], button").count();
      for (let i = 0; i < stops + 2; i += 1) {
        await page.keyboard.press("Tab");
        const inside = await page.evaluate(() => {
          const active = document.activeElement;
          // Leaving the page for the browser's own UI (body) is allowed; the page behind is not.
          return active === document.body || !!active?.closest("dialog[open]");
        });
        expect(inside, `Tab stop ${i + 1} stays in the drawer`).toBe(true);
      }
      const layout = await measure(page);
      expect(problems(layout)).toEqual([]);

      await page.keyboard.press("Escape");
      await expect(drawer).toBeHidden();
      await expect(menu).toBeFocused();
      await expect(menu).toHaveAttribute("aria-expanded", "false");

      // A link in the drawer navigates and closes it.
      await menu.press("Enter");
      await drawer.locator("a[href$='/support']").click();
      await expect(drawer).toBeHidden();
      await expect(page).toHaveURL(new RegExp(`/${locale}/support$`));
    });
  }

  test("platform console: the drawer on the dark chrome and the list panel from lg", async ({
    page,
  }) => {
    await installFixtures(page);
    await signInAs(page, "/en/platform", "operator-1");
    await page.goto("/en/platform/schools");
    await settle(page);
    await page.getByRole("button", { name: "Menu", exact: true }).click();
    const drawer = page.getByRole("dialog", { name: "Menu" });
    await expect(drawer.getByRole("navigation")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(drawer).toBeHidden();

    // Growing the window to lg shows the list panel instead, and no menu button.
    await page.setViewportSize({ width: 1366, height: 768 });
    await expect(page.getByRole("button", { name: "Menu", exact: true })).toBeHidden();
    await expect(page.locator("aside nav")).toBeVisible();
  });
});

test.describe("dialogs on a phone (NFR-A11Y-001)", () => {
  test.skip(!standIn, "set E2E_STAND_IN=1 (needs Valkey at REDIS_URL)");
  test.use({ viewport: { width: 375, height: 812 } });

  for (const locale of LOCALES) {
    test(`a form dialog fits the screen and scrolls inside [${locale}]`, async ({ page }) => {
      await installFixtures(page);
      await signInAs(page, "/en/platform", "operator-1");
      await page.goto(`/${locale}/platform/plans`);
      await settle(page);
      await page
        .getByRole("button", { name: locale === "en" ? "New plan" : "కొత్త ప్లాన్", exact: true })
        .click();
      const dialog = page.locator("dialog[open]");
      await expect(dialog).toBeVisible();
      const box = await dialog.boundingBox();
      expect(box).not.toBeNull();
      expect(box!.x).toBeGreaterThanOrEqual(15);
      expect(box!.x + box!.width).toBeLessThanOrEqual(375 - 15);
      expect(box!.y).toBeGreaterThanOrEqual(0);
      expect(box!.y + box!.height).toBeLessThanOrEqual(812);
      expect(problems(await measure(page))).toEqual([]);
      await page.keyboard.press("Escape");
      await expect(dialog).toBeHidden();
    });
  }
});

test.describe("print: A4 pages (CLAUDE.md §10)", () => {
  test.skip(!standIn, "set E2E_STAND_IN=1 (needs Valkey at REDIS_URL)");
  // The A4 content box with the 12mm side margins of globals.css: 186mm ≈ 703 CSS px.
  test.use({ viewport: { width: 703, height: 1000 } });

  test("student profile and lists print inside the page, Telugu unclipped", async ({ page }) => {
    test.setTimeout(3 * 60_000);
    await installFixtures(page);
    await signInAs(page, "/en/support", "clerk");
    await page.emulateMedia({ media: "print", reducedMotion: "reduce" });
    const failures: string[] = [];
    for (const path of [
      "/te/students/0192f3a4-0000-7000-8000-00000000e501",
      "/en/students",
      "/te/findings",
      "/te/settings/structure",
    ]) {
      await page.goto(path);
      await settle(page);
      // Navigation chrome is not printed.
      await expect(page.getByRole("button", { name: /^(Menu|మెనూ)$/ })).toBeHidden();
      const report = await measure(page);
      // Target sizes and phone gutters are screen concerns (the @page margins frame the
      // paper); overflow, bleeding and clipping apply on paper too.
      for (const problem of problems({ ...report, smallTargets: [], edge: [] }))
        failures.push(`${path}: ${problem}`);
    }
    expect(failures).toEqual([]);
    const pdf = await page.pdf({ format: "A4", preferCSSPageSize: true });
    expect(pdf.byteLength).toBeGreaterThan(1000);
  });
});
