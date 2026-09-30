import { expect, test } from "@playwright/test";
import {
  expectNoAxeViolations,
  expectNoHorizontalOverflow,
  expectVisibleFocusOnEveryStop,
  signIn,
} from "./support/a11y-helpers";
import { openTelugu, TELUGU, TELUGU_OFF_REASON, teluguOn } from "./support/telugu";

/**
 * Accessibility (WCAG 2.2 AA via axe-core) and keyboard-only paths at 1366×768 (PRD §8,
 * CLAUDE.md §8, §10). The signed-out page is always checked; signed-in school and platform
 * pages need E2E_STAND_IN=1 (scripted IdP + canned API, e2e/support/stand-in.ts) and Valkey.
 */

/** Promotion screen of the stand-in's current year (e2e/support/stand-in.ts YEAR_ID). */
const PROMOTIONS = "/en/settings/structure/years/0192f3a4-0000-7000-8000-0000000000a1/promotions";

test.describe("accessibility: public pages", () => {
  for (const locale of ["en", "te"] as const) {
    test(`signed-out page has no WCAG 2.2 AA violations [${locale}]${locale === "te" ? ` ${TELUGU}` : ""}`, async ({
      page,
    }, testInfo) => {
      test.skip(locale === "te" && !teluguOn(testInfo), TELUGU_OFF_REASON);
      await page.goto(`/${locale}/signed-out?error=signin_failed`);
      await expectNoAxeViolations(page, `signed-out ${locale}`);
    });
  }
});

test.describe("accessibility and keyboard: signed in (stand-in IdP)", () => {
  test.skip(process.env.E2E_STAND_IN !== "1", "set E2E_STAND_IN=1 (needs Valkey at REDIS_URL)");

  test(`school pages: banner, permission-filtered menu, no axe violations ${TELUGU}`, async ({
    page,
  }, testInfo) => {
    await signIn(page, "/en/settings/billing", "clerk");
    await expect(page).toHaveURL(/\/en\/settings\/billing$/);
    await expect(page.getByRole("heading", { level: 1, name: "Plan and billing" })).toBeVisible();
    await expect(page.getByText("Maintenance on Sunday", { exact: true })).toBeVisible();
    const nav = page.getByRole("navigation", { name: "Main" });
    await expect(nav.getByRole("link", { name: "Support" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Switch school" })).toHaveCount(0);
    // One sidebar (docs/17 §5.2): one Main navigation, the account area at its foot.
    await expect(page.getByRole("navigation", { name: "Main" })).toHaveCount(1);
    await expect(page.getByRole("navigation", { name: "Sections" })).toHaveCount(0);
    await expect(
      page.getByRole("region", { name: "Your account" }).getByRole("button", { name: "Lock now" }),
    ).toBeVisible();
    await expectNoAxeViolations(page, "billing en");
    await expectNoHorizontalOverflow(page, "billing en");

    // Compact sidebar: still no violations (icon-only links keep their names), then back.
    await page.getByRole("button", { name: "Collapse menu" }).click();
    await expect(page.getByRole("button", { name: "Expand menu" })).toBeVisible();
    await expect(nav.getByRole("link", { name: "Support" })).toBeVisible();
    await expectNoAxeViolations(page, "billing en, compact sidebar");
    await expectNoHorizontalOverflow(page, "billing en, compact sidebar");
    await page.getByRole("button", { name: "Expand menu" }).click();
    // A fresh load starts expanded again (remembered) with focus at the top of the page.
    await page.reload();
    await expect(page.getByRole("button", { name: "Collapse menu" })).toBeVisible();

    // Keyboard: the first Tab reaches the skip link, which moves focus to <main>.
    await page.keyboard.press("Tab");
    await expect(page.getByRole("link", { name: "Skip to main content" })).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(page.locator("main#main")).toBeFocused();

    for (const path of ["/en", "/en/support", "/te/settings/billing", "/te/support"]) {
      // ADR-0036: /te pages only while Telugu is on; otherwise they must land on English.
      if (!path.startsWith("/te")) await page.goto(path);
      else if (!(await openTelugu(page, path, testInfo))) continue;
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

  test(`school settings, structure, users, documents, audit check: axe and keyboard (NFR-A11Y-001) ${TELUGU}`, async ({
    page,
  }, testInfo) => {
    // Eighteen pages, each with axe and a Tab-through: about 45s against a production build.
    test.setTimeout(120_000);
    await signIn(page, "/en/settings/structure", "clerk");
    await expect(page).toHaveURL(/\/en\/settings\/structure$/);
    // [page, text that proves the data (not only the shell or an error) is shown]
    const pages: Array<[string, string]> = [
      ["/en/settings/structure", "Class 6"],
      ["/te/settings/structure", "6వ తరగతి"],
      [PROMOTIONS, "Plan the promotion"],
      [PROMOTIONS.replace("/en/", "/te/"), "ప్రమోషన్ ప్రణాళిక"],
      ["/en/settings/school", "STATE_AP, CBSE"],
      ["/te/settings/school", "STATE_AP, CBSE"],
      ["/en/settings/users", "Synthetic Teacher"],
      ["/en/settings/users/new", "Invite"],
      ["/en/settings/users/0192f3a4-0000-7000-8000-0000000000d1", "teacher@school.example"],
      ["/te/settings/users", "Synthetic Teacher"],
      ["/en/documents", "Dasara holidays circular 2026"],
      ["/en/documents/new", "Upload"],
      ["/en/documents/0192f3a4-0000-7000-8000-00000000d001", "Dasara holidays circular 2026"],
      ["/te/documents", "Dasara holidays circular 2026"],
      ["/en/audit/verify", "Check integrity"],
      // Ask chat (FR-KB-012): all chats and memory, en and te.
      ["/en/ask/history", "Only you can see them"],
      ["/en/ask/memory", "I work in the school office"],
      ["/te/ask/memory", "I work in the school office"],
    ];
    for (const [path, proof] of pages) {
      // ADR-0036: /te pages only while Telugu is on; otherwise they must land on English.
      if (!path.startsWith("/te")) await page.goto(path);
      else if (!(await openTelugu(page, path, testInfo))) continue;
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      // Wait for the data, not only the shell, before checking.
      await expect(page.getByText(proof).first()).toBeVisible();
      await expect(page.getByText("Loading…")).toHaveCount(0);
      await expectNoAxeViolations(page, path);
      // 1366×768: no horizontal page scroll.
      await expectNoHorizontalOverflow(page, path);
      if (path.startsWith("/en/")) await expectVisibleFocusOnEveryStop(page, path);
    }

    // Structure, keyboard only: open "Add academic year" with Enter, fields reachable by Tab,
    // Escape closes and returns focus to the trigger.
    await page.goto("/en/settings/structure");
    const add = page.getByRole("button", { name: "Add academic year" });
    await add.focus();
    await page.keyboard.press("Enter");
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await expect
      .poll(() =>
        page.evaluate(
          () => document.querySelector("dialog[open]")?.contains(document.activeElement) ?? false,
        ),
      )
      .toBe(true);
    await expectNoAxeViolations(page, "add year dialog");
    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
    await expect(add).toBeFocused();

    // Show archived, then the class teacher picker in "Edit" of a section, keyboard only.
    await page.getByLabel("Show archived years, classes and sections").focus();
    await page.keyboard.press("Space");
    await expect(page.getByLabel("Show archived years, classes and sections")).toBeChecked();
    const editSection = page
      .getByRole("region", { name: "Sections" })
      .getByRole("button", { name: "Edit" })
      .first();
    await editSection.focus();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("dialog").getByLabel("Class teacher")).toBeVisible();
    await expectNoAxeViolations(page, "edit section dialog");
    await page.keyboard.press("Escape");

    // Promotion (FR-TEN-011): preview by keyboard; the result and the section choice pass axe.
    await page.goto(PROMOTIONS);
    const preview = page.getByRole("button", { name: "Preview promotion" });
    await preview.focus();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("region", { name: "Preview: 2026-27 to 2027-28" })).toBeVisible();
    await expect(page.getByLabel("Students of 6-A going to Class 6")).toBeVisible();
    await expectNoAxeViolations(page, "promotion preview");
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow, "promotion preview horizontal overflow").toBeLessThanOrEqual(0);
    await expectVisibleFocusOnEveryStop(page, "promotion preview", 80);

    // Audit chain check by keyboard: the button runs it and the result is announced.
    await page.goto("/en/audit/verify");
    await page.getByRole("button", { name: "Check integrity" }).focus();
    await page.keyboard.press("Enter");
    await expect(page.getByText("The audit log is intact")).toBeVisible();
    await expectNoAxeViolations(page, "audit verify result");
  });

  test(`platform school detail: provisioning state and 'Resume provisioning' by keyboard (FR-PLT-002) ${TELUGU}`, async ({
    page,
  }, testInfo) => {
    const detail = "/en/platform/schools/0192f3a4-0000-7000-8000-000000000003";
    await signIn(page, detail, "operator-1");
    await expect(
      page.getByRole("heading", { level: 1, name: "Sample Model School" }),
    ).toBeVisible();
    await expect(
      page.getByRole("region", { name: "Setup stopped before it finished" }),
    ).toBeVisible();
    await expectNoAxeViolations(page, "school detail provisioning en");
    await expectNoHorizontalOverflow(page, "school detail");
    await expectVisibleFocusOnEveryStop(page, "school detail");

    const resume = page.getByRole("button", { name: "Resume provisioning" });
    await resume.focus();
    await page.keyboard.press("Enter");
    const dialog = page.getByRole("dialog", { name: "Resume setting up this school?" });
    await expect(dialog).toBeVisible();
    await expectNoAxeViolations(page, "resume dialog");
    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
    await expect(resume).toBeFocused();

    if (await openTelugu(page, detail.replace("/en/", "/te/"), testInfo)) {
      await expect(page.getByRole("button", { name: "సెటప్‌ను కొనసాగించండి" })).toBeVisible();
      await expectNoAxeViolations(page, "school detail provisioning te");
    }
  });

  test(`platform pages: no axe violations; wizard and dialogs by keyboard ${TELUGU}`, async ({
    page,
  }, testInfo) => {
    await signIn(page, "/en/platform", "operator-1");
    await expect(page.getByRole("heading", { level: 1, name: "Platform dashboard" })).toBeVisible();
    await expect(page.getByText("4 of 5 healthy")).toBeVisible();
    for (const path of [
      "/en/platform",
      "/en/platform/schools",
      "/en/platform/invoices",
      "/en/platform/plans",
      "/en/platform/provision",
      "/te/platform/provision",
    ]) {
      // ADR-0036: /te pages only while Telugu is on; otherwise they must land on English.
      if (!path.startsWith("/te")) await page.goto(path);
      else if (!(await openTelugu(page, path, testInfo))) continue;
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      // The dark violet sidebar: one Platform navigation, yellow focus ring (platform-chrome).
      await expect(page.getByRole("navigation", { name: /^(Platform|ప్లాట్‌ఫామ్)$/ })).toHaveCount(
        1,
      );
      await expectNoAxeViolations(page, path);
      await expectNoHorizontalOverflow(page, path);
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
