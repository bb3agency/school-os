import { expect, test, type Page } from "@playwright/test";
import {
  expectFocusRing,
  expectNoAxeViolations,
  expectNoHorizontalOverflow,
  expectVisibleFocusOnEveryStop,
} from "./support/a11y-helpers";
import {
  cspViolations,
  dedicatedOn,
  expectEverythingRevealedAfterScrolling,
  expectPublicUrl,
  HEADER_PAGES,
  hiddenRevealContent,
  MK,
  openPublicPage,
  PUBLIC_PAGES,
  publicPathPattern,
  redirectChain,
  settleAnimations,
} from "./support/marketing";
import { E2E_PUBLIC_SETTINGS, E2E_WHATSAPP_DIGITS } from "./support/public-settings";
import {
  expectNoLocaleLinks,
  expectNoTelugu,
  selectLanguage,
  TELUGU,
  TELUGU_OFF_REASON,
  teluguOn,
} from "./support/telugu";
import en from "../messages/en.json" with { type: "json" };

/**
 * The public site (docs/17 §5.6): /welcome, /features, /security, /pricing and /about.
 * FR-IAM-001 (signed-out entry point, sign-in), NFR-A11Y-001 (WCAG 2.2 AA via axe at 375, 1366
 * and 1920 px, keyboard only, reduced motion), SEC-010 (no CSP violations), NFR-I18N-001 /
 * ADR-0036 (English; the Telugu checks are tagged @telugu).
 *
 * URLs: no URL carries a locale (ADR-0036 note, 2026-09-30). Tests open the plain paths
 * (`/features`), follow the site's own links and accept exactly those paths
 * (support/marketing.ts `publicPathPattern`); Telugu is chosen by the NEXT_LOCALE cookie, and
 * old `/en` and `/te` URLs are checked to answer 308 to the plain path.
 *
 * Server variants (playwright.config.ts): the default server has no SOS_PUBLIC_* settings (every
 * contact element hidden); @contact runs against synthetic values for all four
 * (support/public-settings.ts); @dedicated runs against SOS_DEPLOYMENT_MODE=dedicated.
 * More per-page checks (400% zoom, focus not obscured, links, print) are in marketing.spec.ts.
 */

const WIDTHS = [
  { width: 375, height: 812 },
  { width: 1366, height: 768 },
  { width: 1920, height: 1080 },
] as const;

const HOME = PUBLIC_PAGES[0];

/** Collects uncaught page errors and console errors (a hydration error shows up here). */
function pageErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  return errors;
}

/** The mailto, WhatsApp and company elements that exist only when their setting is set. */
async function contactElements(page: Page) {
  return {
    mailto: await page.locator('a[href^="mailto:"]').count(),
    whatsapp: await page.locator('a[href^="https://wa.me/"]').count(),
    talk: await page.getByRole("link", { name: MK.cta.talk }).count(),
    copyright: await page.getByRole("contentinfo").getByText("©").count(),
  };
}

test.describe("public site, shared tier (FR-IAM-001, NFR-A11Y-001, SEC-010)", () => {
  test.beforeEach(({}, testInfo) => {
    test.skip(teluguOn(testInfo), "the Telugu-on project runs only @telugu tests");
  });

  test("the school home sends signed-out visitors to /welcome; deep links go to sign-in", async ({
    request,
  }) => {
    const home = await redirectChain(request, "/");
    expect(home, "/ goes straight to /welcome").toEqual(["/welcome"]);
    // Deep links keep going straight to sign-in, with the return path.
    const deep = await redirectChain(request, "/students");
    const login = new URL(deep.at(-1) ?? "", "http://x");
    expect(login.pathname).toBe("/bff/auth/login");
    expect(login.searchParams.get("next") ?? "").toMatch(publicPathPattern("/students"));
    // Every public page answers without a session.
    for (const { path } of PUBLIC_PAGES) {
      const response = await request.get(path);
      expect(response.status(), path).toBe(200);
      expect(response.headers()["content-security-policy"], path).toContain("default-src 'self'");
    }
  });

  for (const viewport of WIDTHS) {
    test(`${viewport.width} px: WCAG 2.2 AA, no sideways scroll, no CSP violations or errors on every page`, async ({
      page,
    }) => {
      test.setTimeout(120_000);
      const violations = cspViolations(page);
      const errors = pageErrors(page);
      await page.setViewportSize(viewport);
      for (const target of PUBLIC_PAGES) {
        const label = `${target.path} ${viewport.width}`;
        await openPublicPage(page, target);
        await settleAnimations(page);
        await expectNoAxeViolations(page, label);
        await expectNoHorizontalOverflow(page, label);
        await expectNoTelugu(page, label);
      }
      // The welcome FAQ with every answer open.
      await openPublicPage(page, HOME);
      await page
        .locator("#faq summary")
        .evaluateAll((items) => items.forEach((summary) => (summary as HTMLElement).click()));
      await settleAnimations(page);
      await expectNoAxeViolations(page, `welcome FAQ open ${viewport.width}`);
      await expectNoHorizontalOverflow(page, `welcome FAQ open ${viewport.width}`);
      expect(violations).toEqual([]);
      expect(errors).toEqual([]);
    });
  }

  test("1366×768: the hero, Sign in and the secondary call fit above the fold", async ({
    page,
  }) => {
    await openPublicPage(page, HOME);
    await expect(page).toHaveTitle(new RegExp(MK.home.title));
    const cta = page
      .getByRole("main")
      .getByRole("link", { name: MK.cta.signIn, exact: true })
      .first();
    await expect(cta).toHaveAttribute("href", "/bff/auth/login");
    const bottom = await page
      .getByRole("link", { name: MK.home.secondary })
      .evaluate((el) => el.getBoundingClientRect().bottom);
    expect(bottom).toBeLessThanOrEqual(768);
  });

  test("settings unset: no Talk to us, WhatsApp, company line or contact block, and no placeholder", async ({
    page,
  }) => {
    for (const target of PUBLIC_PAGES) {
      await openPublicPage(page, target);
      expect(await contactElements(page), target.path).toEqual({
        mailto: 0,
        whatsapp: 0,
        talk: 0,
        copyright: 0,
      });
    }
    await openPublicPage(page, PUBLIC_PAGES[4]);
    await expect(page.locator("section#contact")).toHaveCount(0);
    await expect(page.getByRole("heading", { name: MK.about.contact.title })).toHaveCount(0);
  });

  test("prefers-reduced-motion: every block is shown at once, before any scrolling, and nothing animates", async ({
    page,
  }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    for (const width of [375, 1366]) {
      await page.setViewportSize({ width, height: width === 375 ? 812 : 768 });
      for (const target of PUBLIC_PAGES) {
        const label = `${target.path} ${width} reduce`;
        await openPublicPage(page, target);
        expect(await hiddenRevealContent(page), label).toEqual([]);
        const running = await page.evaluate(
          () =>
            document
              .getAnimations()
              .filter(
                (animation) =>
                  animation.playState === "running" &&
                  isFinite(Number(animation.effect?.getTiming().duration)) &&
                  Number(animation.effect?.getTiming().duration) > 0,
              ).length,
        );
        expect(running, `${label}: running animations`).toBe(0);
        // Every heading of the page is visible without scrolling through it first.
        const headings = page.getByRole("main").getByRole("heading");
        for (const heading of await headings.all()) {
          await expect(heading, label).toBeVisible();
          expect(Number(await heading.evaluate((el) => getComputedStyle(el).opacity)), label).toBe(
            1,
          );
        }
      }
    }
  });

  test("keyboard only (1366 px): skip link, header in order, pages open with Enter, visible focus", async ({
    page,
  }) => {
    await openPublicPage(page, HOME);
    await page.keyboard.press("Tab");
    await expect(page.getByRole("link", { name: en.common.skipToContent })).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(page.locator("main#main")).toBeFocused();

    // From the top: skip link, wordmark, the four pages, then Sign in.
    await openPublicPage(page, HOME);
    await page.keyboard.press("Tab");
    await page.keyboard.press("Tab");
    const banner = page.getByRole("banner");
    await expectFocusRing(banner.getByRole("link", { name: en.common.appName }), "wordmark");
    const nav = banner.getByRole("navigation", { name: MK.nav.label });
    for (const name of HEADER_PAGES) {
      await page.keyboard.press("Tab");
      await expectFocusRing(nav.getByRole("link", { name: MK.nav[name], exact: true }), name);
    }
    await page.keyboard.press("Tab");
    const signIn = banner.getByRole("link", { name: MK.cta.signIn });
    await expectFocusRing(signIn, "Sign in");
    await expect(signIn).toHaveAttribute("href", "/bff/auth/login");

    // Enter on each header link opens that page, marked as the current one.
    for (const target of PUBLIC_PAGES.slice(1)) {
      const link = page
        .getByRole("banner")
        .getByRole("navigation", { name: MK.nav.label })
        .getByRole("link", { name: MK.nav[target.name as (typeof HEADER_PAGES)[number]] });
      await link.focus();
      await page.keyboard.press("Enter");
      await expectPublicUrl(page, target.path);
      await expect(page.getByRole("heading", { level: 1 })).toContainText(target.h1);
      await expect(link).toHaveAttribute("aria-current", "page");
    }

    // "See how it works" moves to the steps below the sticky header.
    await openPublicPage(page, HOME);
    await page.getByRole("link", { name: MK.home.secondary }).focus();
    await page.keyboard.press("Enter");
    await expectPublicUrl(page, "/welcome", "#how");
    const heading = page.getByRole("heading", { level: 2, name: MK.home.how.title });
    await expect(heading).toBeInViewport();
    const headerBottom = await page
      .getByRole("banner")
      .evaluate((el) => el.getBoundingClientRect().bottom);
    expect(await heading.evaluate((el) => el.getBoundingClientRect().top)).toBeGreaterThanOrEqual(
      headerBottom,
    );

    // FAQ: a summary opens its answer with Enter and closes with Space.
    const summary = page.locator("#faq summary").first();
    await summary.focus();
    await page.keyboard.press("Enter");
    await expect(page.locator("#faq details").first()).toHaveAttribute("open", "");
    await page.keyboard.press("Space");
    await expect(page.locator("#faq details").first()).not.toHaveAttribute("open", "");

    await openPublicPage(page, HOME);
    await expectVisibleFocusOnEveryStop(page, "welcome", 40);
  });

  test("keyboard only (375 px): the phone menu opens with Enter, Escape closes it and returns focus", async ({
    page,
  }) => {
    const violations = cspViolations(page);
    await page.setViewportSize({ width: 375, height: 812 });
    await openPublicPage(page, PUBLIC_PAGES[2]);
    const banner = page.getByRole("banner");
    // Skip link, wordmark, then the menu button (the page links and Sign in live in the menu).
    await page.keyboard.press("Tab");
    await page.keyboard.press("Tab");
    await expect(banner.getByRole("link", { name: en.common.appName })).toBeFocused();
    await page.keyboard.press("Tab");
    const menu = banner.getByRole("button", { name: MK.nav.menu });
    await expectFocusRing(menu, "menu button");
    await expect(menu).toHaveAttribute("aria-expanded", "false");

    await page.keyboard.press("Enter");
    const close = banner.getByRole("button", { name: MK.nav.closeMenu });
    await expect(close).toHaveAttribute("aria-expanded", "true");
    const panel = page.locator(`[id="${await close.getAttribute("aria-controls")}"]`);
    // Focus moves to the first page link; Tab walks the pages in order, then Sign in.
    for (const name of HEADER_PAGES) {
      await expectFocusRing(panel.getByRole("link", { name: MK.nav[name], exact: true }), name);
      await page.keyboard.press("Tab");
    }
    await expectFocusRing(panel.getByRole("link", { name: MK.cta.signIn }), "menu Sign in");
    await expect(panel.getByRole("link", { name: MK.nav.security })).toHaveAttribute(
      "aria-current",
      "page",
    );
    await settleAnimations(page);
    await expectNoAxeViolations(page, "phone menu open");
    await expectNoHorizontalOverflow(page, "phone menu open");

    // Escape closes the menu and puts focus back on its button.
    await page.keyboard.press("Escape");
    await expect(menu).toBeFocused();
    await expect(menu).toHaveAttribute("aria-expanded", "false");
    await expect(panel).toHaveCount(0);

    // Enter on a page link in the menu opens that page with the menu closed.
    await page.keyboard.press("Enter");
    await expect(panel.getByRole("link", { name: MK.nav.pricing })).toBeVisible();
    await panel.getByRole("link", { name: MK.nav.pricing }).focus();
    await page.keyboard.press("Enter");
    await expectPublicUrl(page, "/pricing");
    await expect(banner.getByRole("button", { name: MK.nav.menu })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
    expect(violations).toEqual([]);
  });

  test("scrolling each page at 1366 px reveals every block", async ({ page }) => {
    test.setTimeout(90_000);
    for (const target of PUBLIC_PAGES) {
      await openPublicPage(page, target);
      await expectEverythingRevealedAfterScrolling(page, target.path);
    }
  });

  test("the signed-out page links back to the welcome page", async ({ page }) => {
    await page.goto("/signed-out");
    await expectPublicUrl(page, "/signed-out");
    await page.getByRole("link", { name: en.auth.signedOut.homeLink, exact: true }).click();
    await expectPublicUrl(page, "/welcome");
  });
});

test.describe("public site with every contact setting set @contact", () => {
  test("Talk to us, WhatsApp, the company line and the About contact block appear", async ({
    page,
  }) => {
    const violations = cspViolations(page);
    const mailto = `mailto:${E2E_PUBLIC_SETTINGS.SOS_PUBLIC_CONTACT_EMAIL}`;
    for (const target of PUBLIC_PAGES) {
      await openPublicPage(page, target);
      const banner = page.getByRole("banner");
      await expect(banner.getByRole("link", { name: MK.cta.talk }), target.path).toHaveAttribute(
        "href",
        mailto,
      );
      // Every mailto link is the configured address, nothing else.
      const hrefs = await page
        .locator('a[href^="mailto:"]')
        .evaluateAll((links) => links.map((link) => link.getAttribute("href")));
      expect(new Set(hrefs), target.path).toEqual(new Set([mailto]));
      await expect(
        page.getByRole("contentinfo").getByRole("link", { name: MK.footer.contact }),
      ).toHaveAttribute("href", mailto);
      await expect(
        page.getByRole("contentinfo").getByText(`© ${E2E_PUBLIC_SETTINGS.SOS_PUBLIC_COMPANY_NAME}`),
      ).toBeVisible();
    }

    const about = PUBLIC_PAGES[4];
    await openPublicPage(page, about);
    const contact = page.locator("section#contact");
    await expect(contact.getByRole("heading", { name: MK.about.contact.title })).toBeVisible();
    await expect(contact.getByText(E2E_PUBLIC_SETTINGS.SOS_PUBLIC_COMPANY_NAME)).toBeVisible();
    // The address is split into its lines on "|".
    const lines = await contact
      .locator("address span")
      .evaluateAll((spans) => spans.map((span) => span.textContent));
    expect(lines).toEqual(E2E_PUBLIC_SETTINGS.SOS_PUBLIC_COMPANY_ADDRESS.split("|"));
    await expect(
      contact.getByRole("link", { name: E2E_PUBLIC_SETTINGS.SOS_PUBLIC_CONTACT_EMAIL }),
    ).toHaveAttribute("href", mailto);
    expect(violations).toEqual([]);
  });

  test("Ask on WhatsApp links to wa.me with the configured number only", async ({ page }) => {
    await openPublicPage(page, HOME);
    const links = page.locator('a[href^="https://wa.me/"]');
    await expect(links.first()).toBeVisible();
    const hrefs = await links.evaluateAll((all) => all.map((link) => link.getAttribute("href")));
    for (const href of hrefs) {
      const url = new URL(href ?? "");
      expect(url.pathname, href ?? "").toBe(`/${E2E_WHATSAPP_DIGITS}`);
    }
    await expect(
      page
        .getByRole("main")
        .getByRole("link", { name: new RegExp(MK.cta.talk) })
        .first(),
    ).toBeVisible();
  });

  for (const viewport of WIDTHS) {
    test(`${viewport.width} px with the contact calls: WCAG 2.2 AA and no sideways scroll`, async ({
      page,
    }) => {
      test.setTimeout(120_000);
      await page.setViewportSize(viewport);
      for (const target of PUBLIC_PAGES) {
        const label = `${target.path} ${viewport.width} contact`;
        await openPublicPage(page, target);
        await settleAnimations(page);
        await expectNoAxeViolations(page, label);
        await expectNoHorizontalOverflow(page, label);
      }
    });
  }

  test("header with Sign in, WhatsApp and Talk to us fits from 320 to 1920 px (one row, no clipping)", async ({
    page,
  }) => {
    test.setTimeout(120_000);
    // 1024–1279: the bar shows the page links, so "Ask on WhatsApp" is an icon button there.
    const widths = [320, 375, 768, 1023, 1024, 1152, 1279, 1280, 1366, 1920];
    for (const width of widths) {
      const label = `header at ${width}px`;
      await page.setViewportSize({ width, height: 800 });
      await openPublicPage(page, HOME);
      await expectNoHorizontalOverflow(page, label);
      const banner = page.getByRole("banner");
      const fit = await banner.evaluate((header) => {
        const row = header.firstElementChild as HTMLElement;
        const controls = [...row.querySelectorAll<HTMLElement>("a, button")].filter(
          (el) => el.getBoundingClientRect().width > 0,
        );
        const tops = new Set(
          controls.map((el) => {
            const rect = el.getBoundingClientRect();
            return Math.round(rect.top + rect.height / 2);
          }),
        );
        return {
          rowOverflow: row.scrollWidth - row.clientWidth,
          headerHeight: Math.round(header.getBoundingClientRect().height),
          centres: tops.size,
          outside: controls
            .filter((el) => {
              const rect = el.getBoundingClientRect();
              return rect.left < 0 || rect.right > document.documentElement.clientWidth;
            })
            .map((el) => el.textContent),
          wrapped: controls
            .filter(
              (el) => el.scrollWidth > el.clientWidth || el.getBoundingClientRect().height > 48,
            )
            .map((el) => el.textContent),
        };
      });
      expect(fit, label).toEqual({
        rowOverflow: 0,
        headerHeight: 64,
        centres: 1,
        outside: [],
        wrapped: [],
      });
      const whatsapp = banner.getByRole("link", { name: new RegExp(`^${MK.cta.whatsapp}`) });
      if (width >= 1024) {
        await expect(whatsapp, label).toBeVisible();
        await expect(whatsapp, label).toHaveAttribute("href", /^https:\/\/wa\.me\//);
      } else {
        await expect(whatsapp, label).toBeHidden(); // in the Menu panel below lg
      }
    }
  });

  test("375 px: Talk to us is in the phone menu and the menu still passes axe", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 375, height: 812 });
    await openPublicPage(page, HOME);
    await page.getByRole("button", { name: MK.nav.menu }).click();
    const close = page.getByRole("button", { name: MK.nav.closeMenu });
    const panel = page.locator(`[id="${await close.getAttribute("aria-controls")}"]`);
    await expect(panel.getByRole("link", { name: MK.cta.talk })).toHaveAttribute(
      "href",
      `mailto:${E2E_PUBLIC_SETTINGS.SOS_PUBLIC_CONTACT_EMAIL}`,
    );
    await settleAnimations(page);
    await expectNoAxeViolations(page, "phone menu with contact");
    await expectNoHorizontalOverflow(page, "phone menu with contact");
  });
});

test.describe("dedicated host: no marketing, a sign-in card @dedicated", () => {
  test.beforeEach(({}, testInfo) => {
    test.skip(!dedicatedOn(testInfo), "runs against the SOS_DEPLOYMENT_MODE=dedicated server");
  });

  test("the marketing pages answer 404; /welcome is only a sign-in card", async ({
    page,
    request,
  }) => {
    for (const { path } of PUBLIC_PAGES.slice(1)) {
      // Served at its own path (no locale hop), as a 404.
      expect(await redirectChain(request, path), path).toEqual([]);
      expect((await request.get(path, { maxRedirects: 0 })).status(), path).toBe(404);
    }
    const violations = cspViolations(page);
    for (const viewport of WIDTHS) {
      await page.setViewportSize(viewport);
      await page.goto("/welcome");
      await expectPublicUrl(page, "/welcome");
      await expect(page.getByRole("heading", { level: 1 })).toHaveText(MK.dedicated.headline);
      await expect(page).toHaveTitle(new RegExp(MK.dedicated.title));
      const signIn = page.getByRole("link", { name: MK.cta.signIn });
      await expect(signIn).toHaveAttribute("href", "/bff/auth/login");
      // No marketing header, page links or contact calls on a school's own host.
      await expect(page.getByRole("navigation", { name: MK.nav.label })).toHaveCount(0);
      for (const name of HEADER_PAGES) {
        await expect(page.getByRole("link", { name: MK.nav[name], exact: true })).toHaveCount(0);
      }
      expect(await contactElements(page)).toEqual({
        mailto: 0,
        whatsapp: 0,
        talk: 0,
        copyright: 0,
      });
      await settleAnimations(page);
      await expectNoAxeViolations(page, `dedicated welcome ${viewport.width}`);
      await expectNoHorizontalOverflow(page, `dedicated welcome ${viewport.width}`);
      await expectNoTelugu(page, `dedicated welcome ${viewport.width}`);
    }
    // Keyboard: the sign-in button is reachable with a visible focus ring.
    await page.setViewportSize({ width: 1366, height: 768 });
    await page.goto("/welcome");
    await expectVisibleFocusOnEveryStop(page, "dedicated welcome", 10);
    expect(violations).toEqual([]);
  });
});

/**
 * Telugu (ADR-0036). No URL carries a locale: Telugu is chosen by the NEXT_LOCALE cookie (as the
 * switcher does) and old `/en` and `/te` URLs answer 308 to the same path without the prefix.
 */
test.describe("public site and Telugu (ADR-0036)", () => {
  test(`the school home sends signed-out visitors to the welcome page ${TELUGU}`, async ({
    request,
  }) => {
    expect(await redirectChain(request, "/")).toEqual(["/welcome"]);
    // Old /en and /te homes lose their prefix first (308), then land on /welcome.
    for (const path of ["/en", "/te"]) {
      const response = await request.get(path, { maxRedirects: 0 });
      expect(response.status(), path).toBe(308);
      expect(new URL(response.headers()["location"] ?? "", "http://x").pathname, path).toBe("/");
      expect(await redirectChain(request, path), path).toEqual(["/", "/welcome"]);
    }
  });

  test(`Telugu welcome page: axe at 1366 and 375 px, no sideways scroll ${TELUGU}`, async ({
    page,
  }, testInfo) => {
    test.skip(!teluguOn(testInfo), TELUGU_OFF_REASON);
    for (const viewport of WIDTHS.slice(0, 2)) {
      await page.setViewportSize(viewport);
      await selectLanguage(page, "te");
      await page.goto("/welcome");
      await expectPublicUrl(page, "/welcome");
      await expect(page.locator("html")).toHaveAttribute("lang", "te");
      await expectNoLocaleLinks(page, `welcome te ${viewport.width}`);
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      await settleAnimations(page);
      await expectNoAxeViolations(page, `welcome te ${viewport.width}`);
      await expectNoHorizontalOverflow(page, `welcome te ${viewport.width}`);
    }
  });

  test("with Telugu off: a Telugu cookie or an old /te/welcome gives the English page, no Telugu font", async ({
    page,
  }, testInfo) => {
    test.skip(teluguOn(testInfo), "checks the Telugu-off default");
    await selectLanguage(page, "te");
    await page.goto("/welcome");
    await expect(page.locator("html")).toHaveAttribute("lang", "en");
    await expectNoTelugu(page, "welcome with a te cookie");
    await page.goto("/te/welcome");
    await expectPublicUrl(page, "/welcome");
    await expect(page.locator("html")).toHaveAttribute("lang", "en");
    await expectNoTelugu(page, "/te/welcome");
    const font = await page.request.get("/fonts/telugu/noto-sans-telugu.css");
    expect(font.status()).toBe(404);
  });

  test(`the Telugu signed-out page links back to the welcome page ${TELUGU}`, async ({
    page,
  }, testInfo) => {
    test.skip(!teluguOn(testInfo), TELUGU_OFF_REASON);
    await selectLanguage(page, "te");
    await page.goto("/signed-out");
    await page.getByRole("link", { name: "SchoolOS హోమ్ పేజీ" }).click();
    await expectPublicUrl(page, "/welcome");
    await expect(page.locator("html")).toHaveAttribute("lang", "te");
  });
});
