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
import {
  selectLanguage,
  TELUGU,
  TELUGU_OFF_REASON,
  teluguOn,
  type UiLocale,
} from "./support/telugu";

/** ADR-0036: a [te] variant is tagged to run with Telugu on, and skipped while it is off. */
const tag = (locale: string) => (locale === "te" ? ` ${TELUGU}` : "");

/**
 * Responsive layout (NFR-A11Y-001, NFR-I18N-001; CLAUDE.md §8, §10; docs/17 §5.1): every
 * screen at 1366×768 (office PC) and 375×812 (phone), in English and (while it is switched on,
 * ADR-0036) Telugu, has no
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

// Every test signs in on its own and reads only the browser-side layout fixtures (no shared
// stand-in state), so the tests may run in parallel and CI can split them across shards.
test.describe.configure({ mode: "parallel" });

async function checkScreens(page: Page, locale: UiLocale, pages: string[]): Promise<string[]> {
  const failures: string[] = [];
  // The language comes from the NEXT_LOCALE cookie; no URL carries a locale (ADR-0036 note).
  await selectLanguage(page, locale);
  for (const path of pages) {
    const url = path || "/";
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
        test(`${name} screens [${locale}]: no sideways scroll, clipping or small targets (NFR-A11Y-001)${tag(locale)}`, async ({
          page,
        }, testInfo) => {
          test.skip(locale === "te" && !teluguOn(testInfo), TELUGU_OFF_REASON);
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

/** The wide-screen sidebar (hidden below lg); the drawer is the open <dialog>. */
const INLINE_SIDEBAR = "[data-sidebar-mode='inline']";

test.describe("app shell on a phone: the one sidebar as a keyboard-only drawer (NFR-A11Y-001)", () => {
  test.skip(!standIn, "set E2E_STAND_IN=1 (needs Valkey at REDIS_URL)");
  test.use({ viewport: { width: 375, height: 812 } });

  for (const locale of LOCALES) {
    test(`school console [${locale}]: open with Enter, focus trapped, Escape closes and returns focus${tag(locale)}`, async ({
      page,
    }, testInfo) => {
      test.skip(locale === "te" && !teluguOn(testInfo), TELUGU_OFF_REASON);
      await installFixtures(page);
      await signInAs(page, "/support", "clerk");
      await selectLanguage(page, locale);
      await page.goto("/students");
      await settle(page);
      const menuName = locale === "en" ? "Menu" : "మెనూ";
      const closeName = locale === "en" ? "Close menu" : "మెనూ మూసివేయండి";
      const mainName = locale === "en" ? "Main" : "ప్రధాన మెనూ";
      const accountName = locale === "en" ? "Your account" : "మీ ఖాతా";

      // Below lg the wide sidebar is hidden: no navigation landmark until the drawer opens.
      await expect(page.locator(INLINE_SIDEBAR)).toBeHidden();
      await expect(page.getByRole("navigation", { name: mainName })).toHaveCount(0);
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
      // The same sidebar: one Main navigation (inside the drawer) and the account area.
      await expect(page.getByRole("navigation", { name: mainName })).toHaveCount(1);
      await expect(drawer.getByRole("navigation", { name: mainName })).toBeVisible();
      await expect(drawer.getByRole("region", { name: accountName })).toBeVisible();
      // The drawer fits the phone and leaves a strip of the dimmed page to tap (after its
      // short slide-in, which reduced motion skips).
      await expect.poll(async () => (await drawer.boundingBox())?.x).toBe(0);
      const box = await drawer.boundingBox();
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
      await expect(page).toHaveURL((url) => url.pathname === "/support");
    });
  }

  test("platform console: the drawer on the dark chrome and the sidebar from lg", async ({
    page,
  }) => {
    await installFixtures(page);
    await signInAs(page, "/platform", "operator-1");
    await page.goto("/platform/schools");
    await settle(page);
    await page.getByRole("button", { name: "Menu", exact: true }).click();
    const drawer = page.getByRole("dialog", { name: "Menu" });
    await expect(drawer.getByRole("navigation", { name: "Platform" })).toBeVisible();
    await expect(drawer).toHaveClass(/bg-platform/);
    await page.keyboard.press("Escape");
    await expect(drawer).toBeHidden();

    // Growing the window to lg shows the sidebar instead, and no menu button; still exactly
    // one Platform navigation.
    await page.setViewportSize({ width: 1366, height: 768 });
    await expect(page.getByRole("button", { name: "Menu", exact: true })).toBeHidden();
    await expect(page.locator(`${INLINE_SIDEBAR} nav`)).toBeVisible();
    await expect(page.getByRole("navigation", { name: "Platform" })).toHaveCount(1);
  });
});

test.describe("app shell on the office PC: one sidebar, compact mode (NFR-A11Y-001)", () => {
  test.skip(!standIn, "set E2E_STAND_IN=1 (needs Valkey at REDIS_URL)");
  test.use({ viewport: { width: 1366, height: 768 } });

  for (const locale of LOCALES) {
    test(`school console [${locale}]: sticky sidebar, own scroll, collapse remembered without a shift${tag(locale)}`, async ({
      page,
    }, testInfo) => {
      test.skip(locale === "te" && !teluguOn(testInfo), TELUGU_OFF_REASON);
      await installFixtures(page);
      await signInAs(page, "/support", "clerk");
      await selectLanguage(page, locale);
      await page.goto("/students");
      await settle(page);
      const mainName = locale === "en" ? "Main" : "ప్రధాన మెనూ";
      const collapseName = locale === "en" ? "Collapse menu" : "మెనూను కుదించండి";
      const expandName = locale === "en" ? "Expand menu" : "మెనూను విస్తరించండి";
      const sidebar = page.locator(INLINE_SIDEBAR);

      // One sidebar, one Main navigation, no menu button; the sidebar fills the height and
      // stays put while the page scrolls.
      await expect(sidebar).toBeVisible();
      await expect(page.getByRole("navigation", { name: mainName })).toHaveCount(1);
      await expect(page.getByRole("button", { name: /^(Menu|మెనూ)$/ })).toBeHidden();
      const box = await sidebar.boundingBox();
      expect(box?.x).toBe(0);
      expect(box?.height).toBe(768);
      await page.mouse.wheel(0, 600);
      expect((await sidebar.boundingBox())?.y).toBe(0);
      // The menu scrolls inside the sidebar, never the page sideways.
      const nav = sidebar.getByRole("navigation", { name: mainName });
      expect(await nav.evaluate((el) => getComputedStyle(el).overflowY)).toBe("auto");
      await expect(nav.locator("a[aria-current='page']")).toHaveCount(1);
      expect(problems(await measure(page))).toEqual([]);

      // Collapse with the keyboard: compact width, labels gone visually, names kept.
      const toggle = sidebar.getByRole("button", { name: collapseName });
      await toggle.focus();
      await page.keyboard.press("Enter");
      await expect(sidebar.getByRole("button", { name: expandName })).toBeFocused();
      expect((await sidebar.boundingBox())?.width).toBe(72);
      const current = nav.locator("a[aria-current='page']");
      await expect(current).toHaveAccessibleName(locale === "en" ? "Students" : /.+/);
      // Its label shows beside it on focus (visual only), and Escape dismisses it.
      await current.focus();
      const tip = page.locator(".sidebar-tip");
      await expect(tip).toBeVisible();
      expect(await tip.getAttribute("aria-hidden")).toBe("true");
      const tipBox = await tip.locator(".sidebar-tip-bubble").boundingBox();
      expect(tipBox?.x ?? 0).toBeGreaterThanOrEqual(72);
      await page.keyboard.press("Escape");
      await expect(tip).toBeHidden();
      expect(problems(await measure(page))).toEqual([]);

      // Remembered on this computer and applied before the first paint: compact already
      // when the HTML has been parsed, before the app's scripts run.
      await page.goto("/findings", { waitUntil: "domcontentloaded" });
      expect(await page.evaluate(() => document.documentElement.getAttribute("data-sidebar"))).toBe(
        "collapsed",
      );
      expect((await sidebar.boundingBox())?.width).toBe(72);
      await settle(page);
      await sidebar.getByRole("button", { name: expandName }).click();
      expect((await sidebar.boundingBox())?.width).toBe(272);
    });
  }
});

test.describe("dialogs on a phone (NFR-A11Y-001)", () => {
  test.skip(!standIn, "set E2E_STAND_IN=1 (needs Valkey at REDIS_URL)");
  test.use({ viewport: { width: 375, height: 812 } });

  for (const locale of LOCALES) {
    test(`a form dialog fits the screen and scrolls inside [${locale}]${tag(locale)}`, async ({
      page,
    }, testInfo) => {
      test.skip(locale === "te" && !teluguOn(testInfo), TELUGU_OFF_REASON);
      await installFixtures(page);
      await signInAs(page, "/platform", "operator-1");
      await selectLanguage(page, locale);
      await page.goto("/platform/plans");
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

  test(`student profile and lists print inside the page, Telugu unclipped ${TELUGU}`, async ({
    page,
  }, testInfo) => {
    test.setTimeout(3 * 60_000);
    await installFixtures(page);
    await signInAs(page, "/support", "clerk");
    await page.emulateMedia({ media: "print", reducedMotion: "reduce" });
    const failures: string[] = [];
    // ADR-0036: the Telugu pages while Telugu is on, their English pages while it is off.
    const telugu: UiLocale = teluguOn(testInfo) ? "te" : "en";
    const pages: [UiLocale, string][] = [
      [telugu, "/students/0192f3a4-0000-7000-8000-00000000e501"],
      ["en", "/students"],
      [telugu, "/findings"],
      [telugu, "/settings/structure"],
    ];
    for (const [locale, path] of pages) {
      await selectLanguage(page, locale);
      await page.goto(path);
      await settle(page);
      // Navigation chrome is not printed.
      await expect(page.getByRole("button", { name: /^(Menu|మెనూ)$/ })).toBeHidden();
      await expect(page.locator(INLINE_SIDEBAR)).toBeHidden();
      await expect(page.getByRole("banner")).toBeHidden();
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
