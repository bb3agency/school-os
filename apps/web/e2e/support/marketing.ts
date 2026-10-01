/**
 * Helpers for the public marketing pages (docs/17 §5.6): CSP violations, waiting for the
 * one-time entrance motion before axe (a fade caught half-way would read as low contrast), and
 * the scroll reveal.
 */
import { expect, type APIRequestContext, type Page, type TestInfo } from "@playwright/test";
import en from "../../messages/en.json" with { type: "json" };

/** The English marketing strings, so headings follow the copy instead of repeating it. */
export const MK = en.marketing;

/**
 * The public pages by their paths (src/features/marketing/links.ts). No URL carries a locale
 * (ADR-0036 note, 2026-09-30): each path is served directly, in every language.
 */
export const PUBLIC_PAGES = [
  { name: "home", path: "/welcome", h1: MK.home.headlineLead },
  { name: "features", path: "/features", h1: MK.features.headline },
  { name: "security", path: "/security", h1: MK.security.headline },
  { name: "pricing", path: "/pricing", h1: MK.pricing.headline },
  { name: "about", path: "/about", h1: MK.about.headline },
] as const;

export type PublicPage = (typeof PUBLIC_PAGES)[number];

/** The header links, in order (MARKETING_PAGES). */
export const HEADER_PAGES = ["features", "security", "pricing", "about"] as const;

function escape(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** Exactly the pathname `path`: no locale prefix (`/en`, `/te`) is ever accepted. */
export function publicPathPattern(path: string): RegExp {
  return new RegExp(`^${escape(path)}$`);
}

/** The current URL is the public page `path` (optionally with `hash`), with no locale prefix. */
export async function expectPublicUrl(page: Page, path: string, hash = ""): Promise<void> {
  await expect(page).toHaveURL(
    (url) => publicPathPattern(path).test(url.pathname) && url.hash === hash,
  );
}

/** Opens a public page by its locale-free path and waits for its main heading. */
export async function openPublicPage(page: Page, target: PublicPage): Promise<void> {
  await page.goto(target.path);
  await expectPublicUrl(page, target.path);
  await expect(page.getByRole("heading", { level: 1 })).toContainText(target.h1);
}

/**
 * Follows the redirects of `path` without a browser, up to the BFF (whose login route would go
 * on to the IdP), and returns the path and query of every Location seen (at most 5 hops).
 */
export async function redirectChain(request: APIRequestContext, path: string): Promise<string[]> {
  const seen: string[] = [];
  let current = path;
  for (let hop = 0; hop < 5; hop += 1) {
    const response = await request.get(current, { maxRedirects: 0 });
    const location = response.headers()["location"];
    if (!location || response.status() < 300 || response.status() >= 400) break;
    const next = new URL(location, "http://x");
    current = `${next.pathname}${next.search}`;
    seen.push(current);
    if (next.pathname.startsWith("/bff/")) break;
  }
  return seen;
}

/** The project whose server runs as a dedicated host (playwright.config.ts). */
export function dedicatedOn(testInfo: TestInfo): boolean {
  return testInfo.project.metadata?.dedicated === true;
}

/** Collects CSP violations reported on the console while the page is open (SEC-010). */
export function cspViolations(page: Page): string[] {
  const violations: string[] = [];
  page.on("console", (message) => {
    if (/Content Security Policy|Refused to/i.test(message.text())) violations.push(message.text());
  });
  return violations;
}

/** Waits until every running CSS or Web Animations API animation on the page has finished. */
export async function settleAnimations(page: Page): Promise<void> {
  await page.evaluate(() =>
    Promise.all(document.getAnimations().map((animation) => animation.finished.catch(() => null))),
  );
}

/** Elements of scroll reveals that are not fully visible (opacity below 1). */
export async function hiddenRevealContent(page: Page): Promise<string[]> {
  return page.evaluate(() =>
    [...document.querySelectorAll<HTMLElement>("[data-reveal], [data-reveal] > *")]
      .filter((element) => Number(getComputedStyle(element).opacity) < 1)
      .map((element) => `${element.tagName.toLowerCase()}: ${element.innerText.slice(0, 40)}`),
  );
}

/**
 * Scrolls from the top to the bottom in half-screen steps, then requires every revealed block
 * to be fully visible once the reveals have played.
 */
export async function expectEverythingRevealedAfterScrolling(page: Page, label: string) {
  const height = await page.evaluate(() => document.documentElement.scrollHeight);
  const step = await page.evaluate(() => Math.max(64, Math.floor(window.innerHeight / 2)));
  for (let top = 0; top <= height; top += step) {
    await page.evaluate((y) => window.scrollTo(0, y), top);
    // Give the IntersectionObserver a frame or two.
    await page.evaluate(
      () => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))),
    );
  }
  await settleAnimations(page);
  expect(await hiddenRevealContent(page), `${label}: content left hidden`).toEqual([]);
}
