/**
 * Shared accessibility and keyboard helpers for the e2e specs (WCAG 2.2 AA via axe-core,
 * visible focus, 1366×768 without horizontal scroll; PRD §8, CLAUDE.md §8, §10).
 */
import AxeBuilder from "@axe-core/playwright";
import { expect, type Locator, type Page } from "@playwright/test";

export const WCAG = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];

export async function expectNoAxeViolations(page: Page, label: string) {
  const results = await new AxeBuilder({ page }).withTags(WCAG).analyze();
  const summary = results.violations.map(
    (violation) =>
      `${violation.id} (${violation.impact ?? "?"}): ${violation.nodes
        .slice(0, 3)
        .map((node) => node.target.join(" "))
        .join(" | ")}`,
  );
  expect(summary, label).toEqual([]);
}

/**
 * Keyboard only (WCAG 2.4.7): Tab through the page from the top and require a visible focus
 * indicator (outline or box shadow) on every stop, including the parts of native controls
 * such as the date picker button.
 */
export async function expectVisibleFocusOnEveryStop(page: Page, label: string, maxStops = 60) {
  await page.locator("body").focus();
  const missing: string[] = [];
  let first: string | null = null;
  for (let i = 0; i < maxStops; i += 1) {
    await page.keyboard.press("Tab");
    const stop = await page.evaluate((index) => {
      const el = document.activeElement as HTMLElement | null;
      if (!el || el === document.body) return null;
      const style = getComputedStyle(el);
      const ring =
        (style.outlineStyle !== "none" && parseFloat(style.outlineWidth) > 0) ||
        style.boxShadow !== "none";
      const text = (el.getAttribute("aria-label") ?? el.textContent ?? "").trim().slice(0, 30);
      return { id: `${index}:${el.tagName.toLowerCase()}[${text}]`, ring };
    }, i);
    if (!stop) break;
    const key = stop.id.replace(/^\d+:/, "");
    if (first === key) break;
    first ??= key;
    if (!stop.ring) missing.push(stop.id);
  }
  expect(missing, `${label}: focus stops without a visible indicator`).toEqual([]);
}

/** 1366×768: the page never scrolls sideways (wide tables scroll inside their own region). */
export async function expectNoHorizontalOverflow(page: Page, label: string) {
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow, `${label} horizontal overflow`).toBeLessThanOrEqual(0);
}

/** The focused element shows a visible focus indicator. */
export async function expectFocusRing(locator: Locator, label: string) {
  await expect(locator, label).toBeFocused();
  const ring = await locator.evaluate((el) => {
    const style = getComputedStyle(el);
    return (
      (style.outlineStyle !== "none" && parseFloat(style.outlineWidth) > 0) ||
      style.boxShadow !== "none"
    );
  });
  expect(ring, `${label}: visible focus indicator`).toBe(true);
}

/** Keyboard only: move focus to the control, check its focus ring, then press the key. */
export async function pressOn(locator: Locator, key: "Enter" | "Space", label: string) {
  await locator.focus();
  await expectFocusRing(locator, label);
  await locator.page().keyboard.press(key);
}

/** Focus is inside the open modal <dialog> (on the dialog or one of its controls). */
export async function expectFocusInsideOpenDialog(page: Page) {
  await expect
    .poll(() =>
      page.evaluate(
        () => document.querySelector("dialog[open]")?.contains(document.activeElement) ?? false,
      ),
    )
    .toBe(true);
}

/** Sign in through the stand-in IdP's login form (plain HTML; not part of SchoolOS). */
export async function signIn(page: Page, path: string, subject: string) {
  await page.goto(path);
  await page.getByLabel("Subject").fill(subject);
  await page.getByRole("button", { name: "Sign in" }).click();
}
