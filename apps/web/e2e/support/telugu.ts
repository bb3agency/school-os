import { expect, test, type Page, type TestInfo } from "@playwright/test";

/**
 * ADR-0036: Telugu is hidden unless SOS_TELUGU_ENABLED is on. playwright.config.ts runs two
 * web servers from the same build: the default project against Telugu off (the product
 * default) and `chromium-telugu` against Telugu on, which runs only tests tagged @telugu.
 *
 * No URL carries a locale (product owner 2026-09-30; ADR-0036 note): every page has one
 * prefix-less address and the language comes from the NEXT_LOCALE cookie, which the
 * language switcher sets. Tests choose Telugu the same way. With Telugu off the same page
 * must stay English whatever the cookie says.
 */

export const TELUGU = "@telugu";

/** The cookie that carries the chosen UI language (src/i18n/routing.ts). */
export const LOCALE_COOKIE = "NEXT_LOCALE";

export type UiLocale = "en" | "te";

/** True in the project whose web server has Telugu switched on. */
export function teluguOn(testInfo: TestInfo): boolean {
  return testInfo.project.metadata?.telugu === true;
}

/** Why a Telugu-only test does not run in the default project. */
export const TELUGU_OFF_REASON =
  "Telugu is switched off (ADR-0036); a Telugu cookie and old /te URLs are checked in english-only.spec.ts";

const TELUGU_SCRIPT = /[ఀ-౿]/;

/** An old address without its locale prefix: `/te/x?y` → `/x?y`, `/en` → `/`. */
export function withoutPrefix(path: string): string {
  return path.replace(/^\/(en|te)(?=\/|$|\?)/, "").replace(/^(?=\?|$)/, "/");
}

/** A locale prefix at the start of a path (`/en`, `/te/...`): never in any URL now. */
export const LOCALE_PREFIX = /^\/(en|te)(\/|$|\?|#)/;

/** Choose the UI language like the switcher does: store it in the NEXT_LOCALE cookie. */
export async function selectLanguage(page: Page, locale: UiLocale): Promise<void> {
  const url = test.info().project.use.baseURL ?? "http://localhost:3000";
  await page.context().addCookies([{ name: LOCALE_COOKIE, value: locale, url }]);
}

/**
 * Nothing Telugu on the page: no Telugu script, no `lang`/`hreflang` te, no link to a /te
 * page, no language switch, no Telugu font stylesheet or face.
 */
export async function expectNoTelugu(page: Page, label: string): Promise<void> {
  const text = await page.locator("body").innerText();
  expect(text.match(/.{0,30}[ఀ-౿]+/)?.[0] ?? "", `${label}: Telugu text`).toBe("");
  expect(TELUGU_SCRIPT.test(await page.title()), `${label}: Telugu title`).toBe(false);
  expect(
    await page
      .locator("[lang='te'], [hreflang='te'], a[href^='/te'], link[href*='/fonts/telugu']")
      .count(),
    `${label}: Telugu attributes, links or font stylesheet`,
  ).toBe(0);
  expect(
    await page.getByRole("navigation", { name: "Language" }).count(),
    `${label}: language switch`,
  ).toBe(0);
  const fonts = await page.evaluate(() =>
    [...document.fonts].map((face) => face.family).filter((family) => /telugu/i.test(family)),
  );
  expect(fonts, `${label}: Telugu font faces`).toEqual([]);
}

/** No link on the page names a locale in its address (`/en/...`, `/te/...`). */
export async function expectNoLocaleLinks(page: Page, label: string): Promise<void> {
  const prefixed = await page.evaluate(
    (pattern) =>
      [...document.querySelectorAll("[href], form[action]")]
        .map((node) => node.getAttribute("href") ?? node.getAttribute("action") ?? "")
        .filter((value) => new RegExp(pattern).test(value)),
    LOCALE_PREFIX.source,
  );
  expect(prefixed, `${label}: links with a locale prefix`).toEqual([]);
}

/**
 * Open `path` in `locale` (the NEXT_LOCALE cookie; the address stays `path`). English always
 * opens (true). Telugu opens when it is switched on (true: the loaded page is Telugu, and the
 * next page opened is English again); with it off the page must be the same address in
 * English with no Telugu (false: skip the Telugu checks).
 */
export async function openIn(
  page: Page,
  locale: UiLocale,
  path: string,
  testInfo: TestInfo,
): Promise<boolean> {
  if (locale === "te") return inTelugu(page, path, testInfo);
  expect(path, "a prefix-less path").not.toMatch(LOCALE_PREFIX);
  await selectLanguage(page, "en");
  await page.goto(path);
  return true;
}

/**
 * Open a page in Telugu (the NEXT_LOCALE cookie; the address stays `path`). With Telugu on,
 * `checks` run on the Telugu page (returns true). With it off, the page must be the same
 * address in English with no Telugu (returns false; `checks` do not run). English is chosen
 * again afterwards either way, so the test's next pages are English.
 */
export async function inTelugu(
  page: Page,
  path: string,
  testInfo: TestInfo,
  checks: () => Promise<void> = async () => undefined,
): Promise<boolean> {
  expect(path, "a prefix-less path").not.toMatch(LOCALE_PREFIX);
  await selectLanguage(page, "te");
  try {
    await page.goto(path);
    const url = new URL(path, "http://localhost");
    await expect(page).toHaveURL(
      (current) => `${current.pathname}${current.search}` === `${url.pathname}${url.search}`,
    );
    if (teluguOn(testInfo)) {
      await expect(page.locator("html")).toHaveAttribute("lang", "te");
      await checks();
      return true;
    }
    await expect(page.locator("html")).toHaveAttribute("lang", "en");
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await expectNoTelugu(page, path);
    return false;
  } finally {
    await selectLanguage(page, "en");
  }
}
