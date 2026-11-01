import { expect, test, type Browser, type Page, type TestInfo } from "@playwright/test";
import {
  expectNoAxeViolations,
  expectNoHorizontalOverflow,
  expectFocusInsideOpenDialog,
  pressOn,
} from "./support/a11y-helpers";
import {
  CALENDAR_EVENT,
  DISCLOSURE_KEY,
  DISCLOSURE_SCHOOL,
  DPO_OFFICE,
  DPO_PRINCIPAL,
  PURPOSE_LABEL,
  R3_IDS,
  R3_STUDENT,
  SECURITY_OPERATOR,
} from "./support/r3-api";
import { API_PORT } from "./support/stand-in";

/**
 * R3 journeys (roadmap R3-2F, "E2E (Playwright)" in the R3 test plan) against the stand-in IdP
 * and the canned R3 API (e2e/support/r3-api.ts; synthetic data only):
 *
 * 1. Access report, keyboard only at 1366×768: log → verify → build → release by a second
 *    person (maker-checker) → download (FR-DPDP-005..010, NFR-PRV-007).
 * 2. Erasure plan: erasable and refused kinds of record with their reasons (FR-DPDP-012).
 * 3. Breach register: record a breach, the 24 h and 72 h duty clocks, then overdue with the
 *    browser clock moved on (FR-DPDP-030..032, ADR-0050).
 * 4. CBSE disclosure: publish from /settings/disclosure (FR-DISC-008); the public /d/{key} page
 *    at 375 px is tagged @parent (it is served only on the public origin, like the parent pages).
 * 5. Portal calendar: confirm the dates, then the readiness "Next window" line (FR-RDY-004, -009).
 * 6. Consents: record a family's answer for a student, then see it in the consents view
 *    (FR-DPDP-021, US-2604).
 * 7. axe (WCAG 2.2 AA) on every new R3 screen (NFR-A11Y-001).
 *
 * Needs E2E_STAND_IN=1 (and Valkey at REDIS_URL), like the other signed-in journeys.
 */

const STAND_IN = process.env.E2E_STAND_IN === "1";
const STAND_IN_REASON = "set E2E_STAND_IN=1 (needs Valkey at REDIS_URL)";

/** Sign in on the stand-in IdP form with the keyboard only (type the subject, Enter submits). */
async function signInByKeyboard(page: Page, path: string, subject: string) {
  await page.goto(path);
  const field = page.getByLabel("Subject");
  await field.focus();
  await page.keyboard.press("ControlOrMeta+A");
  await page.keyboard.type(subject);
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL((url) => url.pathname === path);
}

/** A second person on another browser context (a separate session: the checker). */
async function secondPerson(browser: Browser, testInfo: TestInfo): Promise<Page> {
  const context = await browser.newContext({
    ...(testInfo.project.use.baseURL ? { baseURL: testInfo.project.use.baseURL } : {}),
    viewport: { width: 1366, height: 768 },
    locale: "en-IN",
    timezoneId: "Asia/Kolkata",
  });
  return context.newPage();
}

async function resetStandIn(page: Page) {
  const reset = await page.request.post(`http://localhost:${API_PORT}/__e2e/reset`);
  expect(reset.status()).toBe(204);
}

async function setStandInClock(page: Page, now: Date | null) {
  const answer = await page.request.post(`http://localhost:${API_PORT}/__e2e/r3/clock`, {
    data: { now: now ? now.toISOString() : null },
  });
  expect(answer.status()).toBe(204);
}

test.describe.serial("R3 journeys (stand-in IdP)", () => {
  test.skip(!STAND_IN, STAND_IN_REASON);

  test.beforeEach(async ({ page }) => {
    await resetStandIn(page);
  });

  test("access report: log, verify, build, release by a second person, keyboard only (FR-DPDP-010, NFR-PRV-007)", async ({
    page,
    browser,
  }, testInfo) => {
    test.setTimeout(120_000);
    // The maker: the office admin logs the request.
    await signInByKeyboard(page, "/privacy/requests/new", DPO_OFFICE);
    await expect(page.getByRole("heading", { level: 1, name: "Log a request" })).toBeVisible();

    await pressOn(page.getByRole("radio", { name: /^What the school holds/ }), "Space", "kind");
    await expect(page.getByRole("radio", { name: /^What the school holds/ })).toBeChecked();

    const search = page.getByRole("combobox", { name: "Find the student" });
    await search.focus();
    await page.keyboard.type("Synthetica Ravi");
    await expect(page.getByRole("option", { name: new RegExp(R3_STUDENT.name) })).toBeVisible();
    await page.keyboard.press("Enter");
    await expect(page.getByText(`${R3_STUDENT.name} · ${R3_STUDENT.admissionNo}`)).toBeVisible();

    const relationship = page.getByLabel("Who asked");
    await relationship.focus();
    await relationship.selectOption("mother");
    await page.getByLabel("What they asked").focus();
    await page.keyboard.type("Synthetic: what does the school hold about my child?");

    await pressOn(page.getByRole("button", { name: "Log the request" }), "Enter", "log");
    await expect(page.getByText(/^Request PR-2026-\d{4} logged$/)).toBeVisible();
    await pressOn(page.getByRole("link", { name: "Open the request" }), "Enter", "open request");
    await expect(page).toHaveURL(/\/privacy\/requests\/[0-9a-f-]{36}$/);
    const requestUrl = new URL(page.url()).pathname;
    await expect(page.getByText("The report has not been made yet.")).toBeVisible();

    // How the office checked who asked (saved with the keyboard).
    const verification = page.getByLabel("How you checked who asked");
    await verification.focus();
    await verification.selectOption("in_person_document");
    await pressOn(page.getByRole("button", { name: "Save", exact: true }), "Enter", "save");
    await expect(page.getByText("Identity checked")).toBeVisible();

    // Build the report; the page polls until it is ready.
    await pressOn(page.getByRole("button", { name: "Build the report" }), "Enter", "build");
    await expect(page.getByText("The report is ready. Check it, then release it.")).toBeVisible({
      timeout: 20_000,
    });
    // The maker cannot release what they logged (the API refuses it too).
    await expect(
      page.getByText("Someone else must release it. You logged this request."),
    ).toBeVisible();
    await expect(page.getByRole("button", { name: "Release the report" })).toBeDisabled();
    await expectNoAxeViolations(page, "access report ready (maker)");
    await expectNoHorizontalOverflow(page, "access report ready (maker)");

    // The checker: the principal releases it from a separate session.
    const checker = await secondPerson(browser, testInfo);
    try {
      await signInByKeyboard(checker, requestUrl, DPO_PRINCIPAL);
      const release = checker.getByRole("button", { name: "Release the report" });
      await expect(release).toBeEnabled();
      await pressOn(release, "Enter", "release trigger");
      const dialog = checker.getByRole("dialog", { name: "Release the access report?" });
      await expect(dialog).toBeVisible();
      await expectFocusInsideOpenDialog(checker);
      await expectNoAxeViolations(checker, "release dialog");
      await pressOn(
        dialog.getByRole("button", { name: "Release the report" }),
        "Enter",
        "release confirm",
      );
      await expect(dialog).toBeHidden();
      await expect(checker.getByText(/^Released on /)).toBeVisible();
      await expect(checker.getByText("Report released")).toBeVisible();

      // The download link is shown and answers (a short-lived presigned URL).
      const download = checker.getByRole("button", { name: "Download the report" });
      await expect(download).toBeVisible();
      const link = checker.waitForResponse(
        (response) =>
          response.url().includes("/report/download-url") && response.request().method() === "GET",
      );
      await pressOn(download, "Enter", "download");
      expect((await link).status()).toBe(200);
      await expectNoAxeViolations(checker, "access report released");
    } finally {
      await checker.context().close();
    }
  });

  test("erasure plan: erasable and refused kinds of record with reasons (FR-DPDP-012)", async ({
    page,
  }) => {
    await signInByKeyboard(page, `/privacy/requests/${R3_IDS.erasureRequest}`, DPO_PRINCIPAL);
    await expect(page.getByRole("heading", { name: "Erasure plan" })).toBeVisible();
    const plan = page.getByRole("table", { name: "Erasure plan" });
    for (const [label, outcome, reason] of [
      ["Optional consents", "Can be erased", "nothing requires the school to keep it"],
      ["Admission register entry", "Kept: school record", "never erased"],
      ["Fee receipts", "Kept: required by law", "eight years under tax law"],
      ["Daily attendance", /Kept/, "end of the retention period"],
    ] as const) {
      const row = plan.getByRole("row").filter({ hasText: label });
      await expect(row).toBeVisible();
      await expect(row).toContainText(outcome);
      await expect(row).toContainText(reason);
    }
    // Only erasable kinds can be ticked; the refused ones offer no box.
    await expect(page.getByRole("checkbox", { name: "Erase Optional consents" })).toBeVisible();
    await expect(page.getByRole("checkbox", { name: "Erase Fee receipts" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Erase the ticked records" })).toBeDisabled();
    await pressOn(
      page.getByRole("checkbox", { name: "Erase Optional consents" }),
      "Space",
      "tick consents",
    );
    await expect(page.getByRole("button", { name: "Erase the ticked records" })).toBeEnabled();
    await expectNoAxeViolations(page, "erasure plan");
    await expectNoHorizontalOverflow(page, "erasure plan");
  });

  test("breach register: record a breach, the 24 h and 72 h clocks, then overdue (FR-DPDP-030, ADR-0050)", async ({
    page,
  }) => {
    test.setTimeout(90_000);
    // Freeze the browser clock on a whole minute so the hours read exactly.
    const t0 = new Date(Math.floor(Date.now() / 60_000) * 60_000);
    await page.clock.setFixedTime(t0);
    await setStandInClock(page, t0);
    try {
      await signInByKeyboard(page, "/privacy/breaches/new", DPO_PRINCIPAL);
      await expect(page.getByRole("heading", { level: 1, name: "Record a breach" })).toBeVisible();
      await page.getByRole("textbox", { name: "What happened" }).focus();
      await page.keyboard.type("Synthetic: an attendance sheet was e-mailed to the wrong parent.");
      await pressOn(page.getByRole("checkbox", { name: "Attendance" }), "Space", "attendance");
      await page.getByLabel("Children").fill("1");
      await pressOn(page.getByRole("button", { name: "Record the breach" }), "Enter", "record");

      await expect(page).toHaveURL(/\/privacy\/breaches\/[0-9a-f-]{36}$/);
      await expect(page.getByText("Board notice due in 24 h")).toBeVisible();
      await expect(page.getByText("Board report due in 72 h")).toBeVisible();
      await expect(page.getByText("Families and staff told: as soon as possible")).toBeVisible();
      await expectNoAxeViolations(page, "breach detail, clocks running");

      // A breach noticed 30 hours ago: the board notice is overdue, the report has 42 h left.
      // (The clock stays frozen at t0: moving the browser clock 30 h on signs the user out
      // for being idle, as it should.)
      await page.goto("/privacy/breaches/new");
      const noticed = new Date(t0.getTime() - 30 * 3_600_000 + 330 * 60_000);
      await page.getByLabel("When the school noticed it").fill(noticed.toISOString().slice(0, 16));
      await page.getByRole("textbox", { name: "What happened" }).focus();
      await page.keyboard.type("Synthetic: a fee receipt book was missing for a day and found.");
      await pressOn(page.getByRole("checkbox", { name: "Fees and payments" }), "Space", "fees");
      await pressOn(page.getByRole("button", { name: "Record the breach" }), "Enter", "record");
      await expect(page).toHaveURL(/\/privacy\/breaches\/[0-9a-f-]{36}$/);
      await expect(page.getByText("Board notice overdue by 6 h")).toBeVisible();
      await expect(page.getByText("Board report due in 42 h")).toBeVisible();
      await expectNoAxeViolations(page, "breach detail, overdue");

      // The register shows each breach's next duty from the API's whole hours.
      await page.goto("/privacy/breaches");
      await expect(page.getByRole("heading", { level: 1, name: "Breach register" })).toBeVisible();
      await expect(page.getByRole("row").filter({ hasText: "Attendance" })).toContainText(
        "Board notice due in 24 h",
      );
      await expect(page.getByRole("row").filter({ hasText: "Fees and payments" })).toContainText(
        "Board notice overdue by 6 h",
      );
      await expectNoAxeViolations(page, "breach register, overdue");
      await expectNoHorizontalOverflow(page, "breach register");
    } finally {
      await setStandInClock(page, null);
    }
  });

  test("disclosure: publish the CBSE disclosure and see its public link (FR-DISC-008)", async ({
    page,
  }) => {
    // Regression: the Publish dialog's form once sat inside the draft editor's form, so publishing
    // did a native GET of /settings/disclosure instead of POST /disclosure/publish (2026-11-01).
    await signInByKeyboard(page, "/settings/disclosure", DPO_PRINCIPAL);
    await expect(
      page.getByRole("heading", { level: 1, name: "CBSE disclosure (Appendix IX)" }),
    ).toBeVisible();
    await expect(page.getByText(DISCLOSURE_SCHOOL).first()).toBeVisible();
    const trigger = page.getByRole("button", { name: "Publish", exact: true });
    await pressOn(trigger, "Enter", "publish trigger");
    const dialog = page.getByRole("dialog", { name: "Publish the disclosure" });
    await expect(dialog).toBeVisible();
    // The confirmation is required: publishing without it says so.
    await pressOn(dialog.getByRole("button", { name: "Publish" }), "Enter", "publish unticked");
    await expect(dialog.getByText("Tick the box to confirm before publishing.")).toBeVisible();
    await pressOn(
      dialog.getByRole("checkbox", { name: "These documents contain no student's personal data." }),
      "Space",
      "no student data",
    );
    await expectNoAxeViolations(page, "publish dialog");
    await pressOn(dialog.getByRole("button", { name: "Publish" }), "Enter", "publish");
    await expect(dialog).toBeHidden();
    await expect(page.getByRole("heading", { name: "Public page" })).toBeVisible();
    await expect(page.getByText(new RegExp(`/d/${DISCLOSURE_KEY}$`))).toBeVisible();
    await expect(page.getByRole("button", { name: "Publish again" })).toBeVisible();
    await expectNoAxeViolations(page, "disclosure published");
  });

  test("portal calendar: confirm the dates, then the readiness 'Next window' line (FR-RDY-004, FR-RDY-009)", async ({
    page,
  }) => {
    await signInByKeyboard(page, "/findings/calendar", DPO_OFFICE);
    await expect(page.getByRole("heading", { level: 1, name: "Portal calendar" })).toBeVisible();
    const row = page.getByRole("row").filter({ hasText: CALENDAR_EVENT.label });
    await expect(row).toContainText("Expected");
    await pressOn(row.getByRole("button", { name: "Confirm dates" }), "Enter", "confirm dates");
    const dialog = page.getByRole("dialog", { name: "Confirm the dates" });
    await expect(dialog).toBeVisible();
    await dialog.getByLabel("Opens on").fill("01/12/2026");
    await dialog.getByLabel("Closes on").fill("31/01/2027");
    await dialog.getByLabel("Where the date came from").selectOption("circular");
    await dialog.getByLabel("Who owns the work").selectOption({ label: "Synthetica Office Admin" });
    await expectNoAxeViolations(page, "confirm dates dialog");
    await pressOn(dialog.getByRole("button", { name: "Save dates" }), "Enter", "save dates");
    await expect(dialog).toBeHidden();
    await expect(row).toContainText(/Confirmed: 01\/12\/2026 – 31\/01\/2027/);
    await expect(row).toContainText("Synthetica Office Admin");

    await page.goto("/findings/readiness");
    const next = page.getByTestId("next-window");
    await expect(next).toContainText(`Next window: ${CALENDAR_EVENT.label}.`);
    await expect(next).toContainText(/Closes in \d+ days/);
    await expect(next).toContainText("Confirmed");
    await expect(next.getByRole("link", { name: "Portal calendar" })).toBeVisible();
  });

  test("consents: record a family's answer and see it in the consents view (FR-DPDP-021)", async ({
    page,
  }) => {
    await signInByKeyboard(page, `/students/${R3_IDS.student}`, DPO_OFFICE);
    await pressOn(page.getByRole("tab", { name: "Consents" }), "Enter", "consents tab");
    await expect(page.getByRole("heading", { name: "Record a decision" })).toBeVisible();
    await expect(page.getByLabel("Purpose", { exact: true })).toHaveValue(R3_IDS.purpose);
    await pressOn(page.getByRole("radio", { name: "Did not agree" }), "Space", "refused");
    await page.getByLabel("Relationship to the student").selectOption("mother");
    await page.getByLabel("Date on the form").fill("28/10/2026");
    await pressOn(page.getByRole("button", { name: "Save the decision" }), "Enter", "save");
    await expect(page.getByText("Decision saved")).toBeVisible();
    const history = page.locator("section, div").filter({
      has: page.getByRole("heading", { name: "Decisions recorded" }),
    });
    await expect(history.getByRole("row").filter({ hasText: PURPOSE_LABEL }).first()).toContainText(
      "Did not agree",
    );
    await expectNoAxeViolations(page, "student consents");

    await page.goto("/consents");
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    const card = page.locator("section, div").filter({
      has: page.getByRole("heading", { name: PURPOSE_LABEL }),
    });
    const refused = card.locator("dl > div").filter({ hasText: "Did not agree" }).first();
    await expect(refused.locator("dd")).toHaveText("1");
  });
});

test.describe("R3 public disclosure page at 375 px @parent", () => {
  test.skip(!STAND_IN, STAND_IN_REASON);

  test("/d/{key}: renders, no horizontal scroll, noindex (FR-DISC-009, NFR-A11Y-001)", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 375, height: 812 });
    const response = await page.goto(`/d/${DISCLOSURE_KEY}`);
    expect(response?.status()).toBe(200);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await expect(page.getByText(DISCLOSURE_SCHOOL).first()).toBeVisible();
    await expect(page.getByText("Affiliation number")).toBeVisible();
    await expect(page.getByText("SYN0000001")).toBeVisible();
    // Not listed by search engines: a robots meta and (or) the X-Robots-Tag header.
    const robotsMeta = await page
      .locator('meta[name="robots"]')
      .evaluateAll((nodes) => nodes.map((node) => node.getAttribute("content") ?? ""));
    const robotsHeader = response?.headers()["x-robots-tag"] ?? "";
    expect(robotsMeta.some((content) => /noindex/.test(content))).toBe(true);
    expect(`${robotsMeta.join(",")} ${robotsHeader}`).toMatch(/noindex/);
    await expectNoHorizontalOverflow(page, "/d/{key} at 375 px");
    await expectNoAxeViolations(page, "/d/{key} at 375 px");
  });

  test("/d/{unknown}: not found", async ({ page }) => {
    const response = await page.goto(`/d/${"z".repeat(22)}`);
    expect(response?.status()).toBe(404);
  });
});

test.describe("R3 screens: axe (WCAG 2.2 AA) and no sideways scroll at 1366×768", () => {
  test.skip(!STAND_IN, STAND_IN_REASON);

  test.beforeEach(async ({ page }) => {
    await resetStandIn(page);
  });

  test("school screens (NFR-A11Y-001)", async ({ page }) => {
    test.setTimeout(150_000);
    await signInByKeyboard(page, "/privacy/requests", DPO_PRINCIPAL);
    // [page, text that proves the data (not only the shell or an error) is shown]
    const pages: Array<[string, string]> = [
      ["/privacy/requests", "PR-2026-0007"],
      ["/privacy/requests/new", "What they asked for"],
      [`/privacy/requests/${R3_IDS.erasureRequest}`, "Optional consents"],
      ["/privacy/notices", "Notice"],
      ["/privacy/breaches", "Names and identity details"],
      ["/privacy/breaches/new", "When the school noticed it"],
      [`/privacy/breaches/${R3_IDS.breach}`, "Board report"],
      ["/consents", PURPOSE_LABEL],
      ["/settings/consent-purposes", PURPOSE_LABEL],
      ["/settings/disclosure", DISCLOSURE_SCHOOL],
      ["/findings/calendar", CALENDAR_EVENT.label],
    ];
    const failures: string[] = [];
    for (const [path, proof] of pages) {
      await page.goto(path);
      await expect(page.getByRole("heading", { level: 1 }), path).toBeVisible();
      await expect(page.getByText(proof).filter({ visible: true }).first(), path).toBeVisible();
      // Collect every page's result, so one run reports them all.
      await expectNoAxeViolations(page, path).catch((error: unknown) => {
        failures.push(String(error));
      });
      await expectNoHorizontalOverflow(page, path).catch((error: unknown) => {
        failures.push(String(error));
      });
    }
    expect(failures, "axe and overflow on the R3 school screens").toEqual([]);
  });

  test("platform incidents (NFR-A11Y-001)", async ({ page }) => {
    await signInByKeyboard(page, "/platform/incidents", SECURITY_OPERATOR);
    const failures: string[] = [];
    for (const [path, proof] of [
      ["/platform/incidents", "misconfigured backup bucket"],
      [`/platform/incidents/${R3_IDS.incident}`, "Sri Saraswati High School"],
    ] as const) {
      await page.goto(path);
      await expect(page.getByRole("heading", { level: 1 }), path).toBeVisible();
      await expect(page.getByText(proof).filter({ visible: true }).first(), path).toBeVisible();
      await expectNoAxeViolations(page, path).catch((error: unknown) => {
        failures.push(String(error));
      });
      await expectNoHorizontalOverflow(page, path).catch((error: unknown) => {
        failures.push(String(error));
      });
    }
    expect(failures, "axe and overflow on the incident screens").toEqual([]);
  });
});
