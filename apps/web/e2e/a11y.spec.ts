import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

/**
 * Accessibility (WCAG 2.2 AA via axe-core) and keyboard-only paths at 1366×768 (PRD §8,
 * CLAUDE.md §8, §10). The signed-out page is always checked; signed-in school and platform
 * pages need E2E_STAND_IN=1 (scripted IdP + canned API, e2e/support/stand-in.ts) and Valkey.
 */

const WCAG = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];

async function expectNoAxeViolations(page: Page, label: string) {
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

async function signIn(page: Page, path: string, subject: string) {
  await page.goto(path);
  // The stand-in IdP's login form (plain HTML; not part of SchoolOS).
  await page.getByLabel("Subject").fill(subject);
  await page.getByRole("button", { name: "Sign in" }).click();
}

test.describe("accessibility: public pages", () => {
  for (const locale of ["en", "te"] as const) {
    test(`signed-out page has no WCAG 2.2 AA violations [${locale}]`, async ({ page }) => {
      await page.goto(`/${locale}/signed-out?error=signin_failed`);
      await expectNoAxeViolations(page, `signed-out ${locale}`);
    });
  }
});

test.describe("accessibility and keyboard: signed in (stand-in IdP)", () => {
  test.skip(process.env.E2E_STAND_IN !== "1", "set E2E_STAND_IN=1 (needs Valkey at REDIS_URL)");

  test("school pages: banner, permission-filtered menu, no axe violations", async ({ page }) => {
    await signIn(page, "/en/settings/billing", "clerk");
    await expect(page).toHaveURL(/\/en\/settings\/billing$/);
    await expect(page.getByRole("heading", { level: 1, name: "Plan and billing" })).toBeVisible();
    await expect(page.getByText("Maintenance on Sunday", { exact: true })).toBeVisible();
    const nav = page.getByRole("navigation", { name: "Main" });
    await expect(nav.getByRole("link", { name: "Support" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Switch school" })).toHaveCount(0);
    await expectNoAxeViolations(page, "billing en");

    // Keyboard: the first Tab reaches the skip link, which moves focus to <main>.
    await page.keyboard.press("Tab");
    await expect(page.getByRole("link", { name: "Skip to main content" })).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(page.locator("main#main")).toBeFocused();

    for (const path of ["/en", "/en/support", "/te/settings/billing", "/te/support"]) {
      await page.goto(path);
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      await expectNoAxeViolations(page, path);
    }
  });

  test("several schools: the picker, keyboard only, then 'Switch school'", async ({ page }) => {
    await signIn(page, "/en/support", "multi");
    await expect(page).toHaveURL(/\/en\/choose-school\?next=%2Fen%2Fsupport$/);
    await expectNoAxeViolations(page, "choose-school");
    const open = page.getByRole("button", { name: /^Open\s?: Sri Saraswati High School$/ });
    await expect(page.getByRole("button", { name: /^Open\s?: Vidya Nilayam$/ })).toBeDisabled();
    await open.focus();
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/\/en\/support$/);
    await expect(page.getByRole("link", { name: "Switch school" })).toBeVisible();
  });

  test("platform pages: no axe violations; wizard and dialogs by keyboard", async ({ page }) => {
    await signIn(page, "/en/platform", "operator-1");
    await expect(page.getByRole("heading", { level: 1, name: "Platform dashboard" })).toBeVisible();
    await expect(page.getByText("4 of 5 healthy")).toBeVisible();
    for (const path of [
      "/en/platform",
      "/en/platform/schools",
      "/en/platform/invoices",
      "/en/platform/plans",
      "/te/platform/provision",
    ]) {
      await page.goto(path);
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      await expectNoAxeViolations(page, path);
    }

    // Provision wizard, keyboard only: type, Tab, and Enter on "Next".
    await page.goto("/en/platform/provision");
    await page.getByLabel("School name").focus();
    await page.keyboard.type("Sample Public School");
    await page.keyboard.press("Tab");
    await expect(page.getByLabel("Short code")).toBeFocused();
    await page.keyboard.type("sample-public");
    const next = page.getByRole("button", { name: "Next" });
    for (
      let i = 0;
      i < 8 && !(await next.evaluate((el) => el === document.activeElement));
      i += 1
    ) {
      await page.keyboard.press("Tab");
    }
    await expect(next).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("heading", { level: 2, name: "Deployment" })).toBeFocused();

    // Dialog: opens on Enter with focus inside, Escape closes and returns focus.
    await page.goto("/en/platform/invoices");
    const trigger = page.getByRole("button", { name: "Record payment" });
    await trigger.focus();
    await page.keyboard.press("Enter");
    const dialog = page.getByRole("dialog", { name: "Record a payment" });
    await expect(dialog).toBeVisible();
    // Focus is inside the modal (on the dialog or its first control).
    await expect
      .poll(() =>
        page.evaluate(
          () => document.querySelector("dialog[open]")?.contains(document.activeElement) ?? false,
        ),
      )
      .toBe(true);
    await expectNoAxeViolations(page, "payment dialog");
    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
    await expect(trigger).toBeFocused();
  });
});
