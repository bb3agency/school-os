/**
 * Helpers for the public marketing pages (docs/17 §5.6): CSP violations, waiting for the
 * one-time entrance motion before axe (a fade caught half-way would read as low contrast), and
 * the scroll reveal.
 */
import { expect, type Page } from "@playwright/test";

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
