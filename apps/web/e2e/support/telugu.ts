import { expect, type Page, type TestInfo } from "@playwright/test";

/**
 * ADR-0036: Telugu is hidden unless SOS_TELUGU_ENABLED is on. playwright.config.ts runs two
 * web servers from the same build: the default project against Telugu off (the product
 * default) and `chromium-telugu` against Telugu on, which runs only tests tagged @telugu.
 * Telugu checks keep running with the switch on; with it off, the same /te URLs must land on
 * the English page and show no Telugu.
 */

export const TELUGU = "@telugu";

/** True in the project whose web server has Telugu switched on. */
export function teluguOn(testInfo: TestInfo): boolean {
  return testInfo.project.metadata?.telugu === true;
}

/** Why a Telugu-only test does not run in the default project. */
export const TELUGU_OFF_REASON =
  "Telugu is switched off (ADR-0036); /te → /en and no Telugu are checked in english-only.spec.ts";

const TELUGU_SCRIPT = /[ఀ-౿]/;

/** The English page a /te URL redirects to while Telugu is off. */
export function englishPath(path: string): string {
  return path.replace(/^\/te(?=\/|$|\?)/, "/en");
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

/**
 * Open a Telugu page. With Telugu on it simply opens (returns true: run the Telugu checks).
 * With it off, the URL must redirect to the same English page with no Telugu (returns false).
 */
export async function openTelugu(page: Page, path: string, testInfo: TestInfo): Promise<boolean> {
  await page.goto(path);
  if (teluguOn(testInfo)) return true;
  const url = new URL(path, "http://localhost");
  const english = englishPath(`${url.pathname}${url.search}`);
  await expect(page).toHaveURL((url) => `${url.pathname}${url.search}` === english);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await expectNoTelugu(page, path);
  return false;
}
