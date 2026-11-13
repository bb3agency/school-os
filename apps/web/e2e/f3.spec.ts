import { expect, test, type Locator, type Page } from "@playwright/test";
import {
  expectFocusInsideOpenDialog,
  expectFocusRing,
  expectNoAxeViolations,
  expectVisibleFocusOnEveryStop,
  pressOn,
  signIn,
} from "./support/a11y-helpers";
import { F3_IDS, F3_OFFICE, F3_TEXT } from "./support/f3-api";
import { R2_STUDENT } from "./support/r2-api";
import { measure, problems, settle } from "./support/responsive";
import { API_PORT } from "./support/stand-in";

/**
 * Browser acceptance of the wave F3 screens (NFR-A11Y-001; CLAUDE.md §8, §10) against the
 * stand-in IdP and the canned F3 API (e2e/support/f3-api.ts; synthetic data only):
 *
 * 1. Every screen at 1366×768: its heading and data, no WCAG 2.2 AA axe violations, no sideways
 *    page scroll, nothing clipped or past its card, no target under 24px, and a visible focus
 *    indicator on every Tab stop.
 * 2. The main action of each screen, keyboard only (focus ring checked before each key), and
 *    the print pages the screen opens through the BFF in a new tab (the API's own strict CSP
 *    kept; the stand-in answers a synthetic page, the API's print layout is tested in the API).
 *
 * The closure message's parent page (/p/view) is in parent-pages.spec.ts (@parent).
 * Needs E2E_STAND_IN=1 (and Valkey at REDIS_URL), like the other signed-in journeys.
 */

const STAND_IN = process.env.E2E_STAND_IN === "1";

/** [path, h1 name, text that proves the data (not only the shell or an error) is shown] */
const SCREENS: ReadonlyArray<readonly [string, string | RegExp, string]> = [
  ["/", /./, "School certificates"],
  ["/certificates/tc-requests", "TC requests", F3_TEXT.tcStudent],
  ["/exports/enrolment-statistics", "Enrolment statistics", "Enrolment by gender"],
  ["/exports/scheme-packs", "Scheme verification pack", "Show attendance list"],
  ["/compliance/inspection", "Prepare for an inspection", "Fire safety certificate (NOC)"],
  ["/compliance/checks", "Safety drills and checks", "CCTV working check"],
  ["/compliance/policies", "School policies", F3_TEXT.policy],
  [`/compliance/policies/${F3_IDS.policy}`, F3_TEXT.policy, "Synthetica Office Admin"],
  ["/compliance/acknowledge", "Policies to read", F3_TEXT.policy],
  ["/ask/gaps", "Ask the school", F3_TEXT.gapQuestion],
  ["/findings/fix-letters", "Letters to parents and the board", "Synthetica Kiran Teja"],
  ["/fees/setup", "Fee setup", "Fee structure notice"],
  ["/fees/cheques", "Cheques", "Synthetic Co-operative Bank"],
  ["/completeness", "Attendance and marks entry", "Class 6-B"],
  ["/follow-up-counts", "Student follow-up counts", "Flags raised"],
  [`/flags/${F3_IDS.flag}`, "Follow-up flag", "Synthetica Rao"],
  ["/messages/closures", "Urgent closure message", "Reopening"],
];

async function resetStandIn(page: Page) {
  const reset = await page.request.post(`http://localhost:${API_PORT}/__e2e/reset`);
  expect(reset.status()).toBe(204);
}

/** Open a screen and wait for its data (the skeletons gone, the proof text shown). */
async function open(page: Page, path: string, heading: string | RegExp, proof: string) {
  await page.goto(path);
  await settle(page);
  await expect(page.getByRole("heading", { level: 1, name: heading }).first()).toBeVisible();
  await expect(page.getByText(proof).filter({ visible: true }).first()).toBeVisible();
}

/** The layout rules of responsive.spec.ts at 1366×768 (sideways scroll, clipping, targets). */
async function expectLayout(page: Page, label: string) {
  expect(problems(await measure(page)), `${label} layout`).toEqual([]);
}

/**
 * A print link: opens in a new tab with the keyboard, through the BFF, and the API page keeps
 * its own strict policy (proxy.ts OWN_CSP_PATHS; the BFF replaces anything looser).
 */
async function expectPrintPage(page: Page, link: Locator, title: string) {
  await expect(link).toHaveAttribute("target", "_blank");
  const href = (await link.getAttribute("href")) ?? "";
  expect(href, "print pages go through the BFF").toMatch(/^\/bff\/api\/v1\//);
  const path = new URL(href, "http://localhost").pathname;
  const answered = page
    .context()
    .waitForEvent("response", (response) => new URL(response.url()).pathname === path);
  const popup = page.waitForEvent("popup");
  await pressOn(link, "Enter", title);
  const [view, response] = await Promise.all([popup, answered]);
  expect(response.status(), `${title} status`).toBe(200);
  expect(response.headers()["content-type"]).toContain("text/html");
  const policy = response.headers()["content-security-policy"] ?? "";
  expect(policy, `${title} keeps a strict policy`).toContain("default-src 'none'");
  expect(policy).toContain("frame-ancestors 'none'");
  await expect(view.getByRole("heading", { level: 1, name: title })).toBeVisible();
  await view.close();
}

test.describe("wave F3 screens (stand-in IdP)", () => {
  test.skip(!STAND_IN, "set E2E_STAND_IN=1 (needs Valkey at REDIS_URL)");

  test.beforeEach(async ({ page }) => {
    await resetStandIn(page);
    await signIn(page, "/support", F3_OFFICE);
    await expect(page).toHaveURL((url) => url.pathname === "/support");
  });

  test("every F3 screen: axe clean, fits 1366×768, visible focus on every stop (NFR-A11Y-001)", async ({
    page,
  }) => {
    test.setTimeout(6 * 60_000);
    for (const [path, heading, proof] of SCREENS) {
      await open(page, path, heading, proof);
      await expectNoAxeViolations(page, path);
      await expectLayout(page, path);
      await expectVisibleFocusOnEveryStop(page, path, 120);
    }
  });

  test("TC requests: log a request with the keyboard (FR-CERT-026, FR-CERT-044)", async ({
    page,
  }) => {
    await open(page, "/certificates/tc-requests", "TC requests", F3_TEXT.tcStudent);
    await expect(page.getByText("1 request is overdue")).toBeVisible();
    await pressOn(page.getByRole("button", { name: "Log a request" }), "Enter", "log");
    await expectFocusInsideOpenDialog(page);
    const dialog = page.getByRole("dialog", { name: "Log a TC request" });
    await expectNoAxeViolations(page, "log TC request dialog");
    const search = dialog.getByRole("combobox", { name: "Find the student" });
    await search.focus();
    await page.keyboard.type("Synthetica Anjali");
    await expect(dialog.getByRole("option", { name: /Synthetica Anjali Devi/ })).toBeVisible();
    await page.keyboard.press("Enter");
    await expect(dialog.getByRole("button", { name: "Change student" })).toBeVisible();
    await pressOn(dialog.getByRole("button", { name: "Log a request" }), "Enter", "log confirm");
    await expect(dialog).toBeHidden();
    await expect(page.getByText("Synthetica Anjali Devi").first()).toBeVisible();
    await expectLayout(page, "TC requests after logging");
  });

  test("enrolment statistics: show the counts, then print through the BFF (FR-EXP-013)", async ({
    page,
  }) => {
    await open(
      page,
      "/exports/enrolment-statistics",
      "Enrolment statistics",
      "Enrolment by gender",
    );
    await pressOn(page.getByRole("button", { name: "Show counts" }), "Enter", "show counts");
    await expect(page.getByText("Enrolment by age on the reference date")).toBeVisible();
    await pressOn(page.getByRole("button", { name: "Prepare print" }), "Enter", "prepare print");
    const link = page.getByRole("link", { name: /^Open print page/ });
    await expectPrintPage(page, link, "Enrolment statistics (synthetic)");
  });

  test("scheme pack: the attendance list for a section, then print (FR-EXP-022)", async ({
    page,
  }) => {
    await open(page, "/exports/scheme-packs", "Scheme verification pack", "Show attendance list");
    const section = page.getByLabel("Class and section");
    await section.focus();
    await expectFocusRing(section, "section");
    await section.selectOption({ index: 1 });
    await pressOn(page.getByRole("button", { name: "Show attendance list" }), "Enter", "show");
    await expect(page.getByText(F3_TEXT.tcStudent, { exact: true })).toBeVisible();
    await expectNoAxeViolations(page, "scheme attendance list");
    await expectLayout(page, "scheme attendance list");
    await pressOn(page.getByRole("button", { name: "Prepare print" }), "Enter", "prepare print");
    const link = page.getByRole("link", { name: /^Open print page/ });
    await expectPrintPage(page, link, "Scheme attendance list (synthetic)");
  });

  test("inspection: make the pack and print its index; the home card counts (FR-COMP-032, FR-COMP-038)", async ({
    page,
  }) => {
    // FC-01 / FC-07: the home card shows counts only and links to the register.
    await open(page, "/", /./, "School certificates");
    await expect(
      page.getByText("1 certificate has expired, 2 expire soon and 3 are not recorded yet."),
    ).toBeVisible();

    await open(page, "/compliance/inspection", "Prepare for an inspection", "Building safety");
    await pressOn(page.getByRole("button", { name: "Make the pack" }), "Enter", "make the pack");
    const link = page.getByRole("link", { name: /^Print the index \(opens in a new tab\)/ });
    await expect(link).toBeVisible();
    // Measured from the top: scrolled down, the sticky top bar covers part of a row's link.
    await page.evaluate(() => window.scrollTo(0, 0));
    await expectNoAxeViolations(page, "inspection pack made");
    await expectLayout(page, "inspection pack made");
    await expectPrintPage(page, link, "Inspection pack index (synthetic)");
  });

  test("drills and checks: log a check with the keyboard (FC-09)", async ({ page }) => {
    await open(page, "/compliance/checks", "Safety drills and checks", "CCTV working check");
    await pressOn(page.getByRole("button", { name: "Log a check" }), "Enter", "log a check");
    await expectFocusInsideOpenDialog(page);
    const dialog = page.getByRole("dialog", { name: "Log a check" });
    await expectNoAxeViolations(page, "log a check dialog");
    const kind = dialog.getByLabel("Check", { exact: true });
    await kind.focus();
    await kind.selectOption("cctv_check");
    const observations = dialog.getByLabel(/^What was found/);
    await observations.focus();
    await page.keyboard.type("Synthetic: all cameras recording.");
    await pressOn(dialog.getByRole("button", { name: "Log check" }), "Enter", "log check");
    await expect(dialog).toBeHidden();
    await expect(page.getByText("Synthetic: all cameras recording.")).toBeVisible();
  });

  test("policies: add dialog, remind staff, then read and confirm a policy (FC-10, FR-COMP-063)", async ({
    page,
    context,
  }) => {
    await open(page, "/compliance/policies", "School policies", F3_TEXT.policy);
    const add = page.getByRole("button", { name: "Add a policy" });
    await pressOn(add, "Enter", "add a policy");
    await expectFocusInsideOpenDialog(page);
    await expectNoAxeViolations(page, "add a policy dialog");
    await page.keyboard.press("Escape");
    await expect(page.locator("dialog[open]")).toHaveCount(0);
    await expect(add).toBeFocused();

    await open(page, `/compliance/policies/${F3_IDS.policy}`, F3_TEXT.policy, "Synthetica Teacher");
    const remind = "Remind staff who have not read it";
    await pressOn(page.getByRole("button", { name: remind }), "Enter", "remind");
    const dialog = page.getByRole("dialog", { name: remind });
    await pressOn(dialog.getByRole("button", { name: remind }), "Enter", "remind confirm");
    await expect(dialog.getByText("2 people were reminded.")).toBeVisible();
    await page.keyboard.press("Escape");

    // The member reads the policy (a new tab), then confirms; the stand-in file is answered here.
    await context.route("**/e2e-files/child-protection-policy.pdf", (route) =>
      route.fulfill({
        status: 200,
        contentType: "text/html; charset=utf-8",
        body: "<!doctype html><title>Synthetic policy</title><h1>Synthetic policy</h1>",
      }),
    );
    await open(page, "/compliance/acknowledge", "Policies to read", F3_TEXT.policy);
    const popup = page.waitForEvent("popup");
    await pressOn(
      page.getByRole("button", { name: `Open ${F3_TEXT.policy}` }),
      "Enter",
      "open policy",
    );
    await (await popup).close();
    const confirm = page.getByRole("button", {
      name: `Confirm that you have read ${F3_TEXT.policy}`,
    });
    await pressOn(confirm, "Enter", "confirm read");
    await expect(page.getByText(/^Read on \d{2}\/\d{2}\/\d{4}$/)).toBeVisible();
    await expectNoAxeViolations(page, "policy read");
  });

  test("unanswered questions: dismiss one with the keyboard (FR-KB-085)", async ({ page }) => {
    await open(page, "/ask/gaps", "Ask the school", F3_TEXT.gapQuestion);
    await pressOn(page.getByRole("button", { name: "Dismiss" }), "Enter", "dismiss");
    await expectFocusInsideOpenDialog(page);
    const dialog = page.getByRole("dialog", { name: "Dismiss this question?" });
    await expectNoAxeViolations(page, "dismiss dialog");
    await pressOn(dialog.getByRole("button", { name: "Dismiss" }), "Enter", "dismiss confirm");
    await expect(dialog).toBeHidden();
    await expect(page.getByText(F3_TEXT.gapQuestion)).toHaveCount(0);
  });

  test("fix-owner letters: record letters given and print a section's letters (FR-DQ-051, FR-DQ-057)", async ({
    page,
  }) => {
    await open(
      page,
      "/findings/fix-letters",
      "Letters to parents and the board",
      F3_TEXT.tcStudent,
    );
    const record = page.getByRole("button", { name: "Record letters given" }).first();
    await pressOn(record, "Enter", "record letters given");
    await expectFocusInsideOpenDialog(page);
    const dialog = page.getByRole("dialog", { name: "Record letters given to parents" });
    await expectNoAxeViolations(page, "record letters dialog");
    await pressOn(dialog.getByRole("button", { name: "Record", exact: true }), "Enter", "record");
    await expect(dialog).toBeHidden();
    const print = page.getByRole("link", { name: /^Print letters for this section/ }).first();
    await expectPrintPage(page, print, "Letters to parents (synthetic)");
  });

  test("fee setup: the structure notice and the change against last year, print (FR-FEE-113..124)", async ({
    page,
  }) => {
    await open(page, "/fees/setup", "Fee setup", "Fee structure notice");
    await expect(page.getByText("Laboratory fee")).toBeVisible();
    const threshold = page.getByLabel(/^Flag increases above \(%\)/);
    await threshold.focus();
    await expectFocusRing(threshold, "threshold");
    await page.keyboard.type("25");
    await expectNoAxeViolations(page, "fee setup with the structure notice");
    await expectLayout(page, "fee setup with the structure notice");
    await expectPrintPage(
      page,
      page.getByRole("link", { name: "Print notice" }),
      "Fee structure notice (synthetic)",
    );
  });

  test("fee counter: the Family panel for a student with a sibling (FR-FEE-101)", async ({
    page,
  }) => {
    await page.goto("/fees/collect");
    await expect(page.getByRole("heading", { level: 1, name: "Collect fees" })).toBeVisible();
    const search = page.getByRole("combobox", { name: "Student name or admission number" });
    await expectFocusRing(search, "search on arrival");
    await page.keyboard.type("Synthetica Laksh");
    const matches = page.getByRole("listbox", { name: "Matching students" });
    await expect(matches.getByRole("option").first()).toBeVisible();
    await page.keyboard.press("Enter");
    await expect(page.getByText(R2_STUDENT.admissionNo).first()).toBeVisible();
    const family = page.getByRole("form", { name: "One payment for the family" });
    await expect(family).toBeVisible();
    await expect(
      family.getByRole("cell", { name: `${F3_TEXT.sibling} IV-B`, exact: true }),
    ).toBeVisible();
    await pressOn(family.getByRole("button", { name: "Fill in everything due" }), "Enter", "fill");
    await expect(family.getByRole("button", { name: "Save 2 receipts" })).toBeVisible();
    await expectNoAxeViolations(page, "fee counter with the Family panel");
    await expectLayout(page, "fee counter with the Family panel");
  });

  test("cheques: tick one, mark it deposited, print the deposit list (FR-FEE-131..136)", async ({
    page,
  }) => {
    await open(page, "/fees/cheques", "Cheques", "Synthetic Co-operative Bank");
    const tick = page.getByRole("checkbox", { name: "Cheque 004512" });
    await pressOn(tick, "Space", "tick cheque");
    await expect(tick).toBeChecked();
    await pressOn(
      page.getByRole("button", { name: "Mark 1 cheque deposited" }),
      "Enter",
      "mark deposited",
    );
    await expectFocusInsideOpenDialog(page);
    const dialog = page.locator("dialog[open]");
    await pressOn(
      dialog.getByRole("button", { name: "Mark 1 cheque deposited" }),
      "Enter",
      "deposit confirm",
    );
    await expect(page.getByText("1 cheque marked deposited.")).toBeVisible();
    // The dialog fades out before it closes; the page is inert until then.
    await expect(page.locator("dialog[open]")).toHaveCount(0);
    await expectPrintPage(
      page,
      page.getByRole("link", { name: /^Print deposit list/ }),
      "Cheque deposit list (synthetic)",
    );
  });

  test("completeness and follow-up counts: remind a section, the marks grid (FR-ATT-024, FR-EW-026)", async ({
    page,
  }) => {
    await open(page, "/completeness", "Attendance and marks entry", "Class 6-B");
    await pressOn(
      page.getByRole("button", { name: "Send a reminder for Class 6-B" }).first(),
      "Enter",
      "remind",
    );
    await expect(page.getByRole("status").filter({ hasText: "Reminder sent" })).toBeVisible();
    const exam = page.getByLabel("Exam", { exact: true });
    await exam.focus();
    await exam.selectOption({ index: 1 });
    await expect(page.getByText("Mathematics").first()).toBeVisible();
    await expectNoAxeViolations(page, "completeness with the marks grid");
    await expectLayout(page, "completeness with the marks grid");

    // FT-13: counts only, never a name.
    await open(page, "/follow-up-counts", "Student follow-up counts", "Flags raised");
    await expect(page.getByRole("main").getByText("Synthetica")).toHaveCount(0);
  });

  test("flag: log an action with a next review date, shown on the flag (FR-EW-019, FR-EW-020)", async ({
    page,
  }) => {
    await open(page, `/flags/${F3_IDS.flag}`, "Follow-up flag", "Synthetica Rao");
    await expect(page.getByText("Next review", { exact: true })).toHaveCount(0);
    await pressOn(page.getByRole("button", { name: "Log an action" }), "Enter", "log an action");
    await expectFocusInsideOpenDialog(page);
    const dialog = page.locator("dialog[open]");
    const kind = dialog.getByLabel("What did you do?");
    await kind.focus();
    await kind.selectOption("called_parent");
    const review = dialog.getByLabel(/^Next review \(optional\)/);
    const min = (await review.getAttribute("min")) ?? "";
    expect(min).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    await review.focus();
    await expectFocusRing(review, "next review");
    await review.fill(min);
    await expectNoAxeViolations(page, "log an action with a next review");
    await pressOn(dialog.getByRole("button", { name: "Save action" }), "Enter", "save action");
    await expect(dialog).toBeHidden();
    const [y, m, d] = min.split("-");
    await expect(page.getByText("Next review", { exact: true })).toBeVisible();
    await expect(page.getByText(`${d}/${m}/${y}`).first()).toBeVisible();
  });

  test("closures: the reopening column and a reopening message preview (FR-MSG-056..065)", async ({
    page,
  }) => {
    await open(page, "/messages/closures", "Urgent closure message", "Reopening");
    await pressOn(
      page.getByRole("button", { name: "Send a reopening message" }),
      "Enter",
      "reopening",
    );
    await expect(page.getByRole("heading", { name: "School reopens" })).toBeVisible();
    await pressOn(
      page.getByRole("button", { name: "Check the reopening message" }),
      "Enter",
      "check reopening",
    );
    await expect(page.getByText(/Sri Saraswati High School reopens on/)).toBeVisible();
    await expect(page.getByText("58 messages to 55 guardians.")).toBeVisible();
    await expectNoAxeViolations(page, "reopening preview");
    await expectLayout(page, "reopening preview");
  });
});
