import { expect, test, type Page } from "@playwright/test";
import {
  expectNoAxeViolations,
  expectNoHorizontalOverflow,
  expectVisibleFocusOnEveryStop,
  pressOn,
  signIn,
} from "./support/a11y-helpers";
import { OFFICE, PEN_ON_ROLL, R1_IDS, SSC } from "./support/r1-api";
import { API_PORT } from "./support/stand-in";
import { inTelugu, TELUGU } from "./support/telugu";

/**
 * Signed-in R1 journeys through the stand-in IdP and canned API (E2E_STAND_IN=1; synthetic data
 * only; e2e/support/r1-api.ts): board readiness and a student's differences (US-504..US-506,
 * ADR-0040) with the parent-slip PDF (FR-DQ-047); the APAAR consent register and a student's
 * consent (US-1901..US-1903, ADR-0039) with the consent-form PDF (FR-APC-007); the transfer-in
 * PEN check in the new-student form (FR-STU-018); the school's boards (FR-TEN-020); the
 * import template library (FR-IMP-030..032).
 *
 * Every screen: the data is shown (not only the shell), no WCAG 2.2 AA axe violations, no
 * horizontal scroll at 1366×768 and a visible focus indicator on every Tab stop. Key actions
 * are driven by the keyboard. With Telugu switched off (ADR-0036) the Telugu step checks that
 * the page stays English.
 */

async function checkScreen(
  page: Page,
  label: string,
  proofs: ReadonlyArray<string | RegExp>,
  { focusStops = true }: { focusStops?: boolean } = {},
) {
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  for (const proof of proofs)
    await expect(page.getByText(proof).filter({ visible: true }).first()).toBeVisible();
  await expect(page.getByText("Loading…")).toHaveCount(0);
  await expectNoAxeViolations(page, label);
  await expectNoHorizontalOverflow(page, label);
  if (focusStops) await expectVisibleFocusOnEveryStop(page, label, 80);
}

test.describe
  .serial(`R1 screens: readiness, APAAR consent, PEN, boards, templates ${TELUGU}`, () => {
  test.skip(process.env.E2E_STAND_IN !== "1", "set E2E_STAND_IN=1 (needs Valkey at REDIS_URL)");

  test.beforeAll(async ({ request }) => {
    const reset = await request.post(`http://localhost:${API_PORT}/__e2e/reset`);
    expect(reset.status()).toBe(204);
  });

  test("board readiness: sections, a section's students, the slips as a PDF (US-504, US-505, FR-DQ-047)", async ({
    page,
  }, testInfo) => {
    await signIn(page, "/findings/readiness", OFFICE);
    await expect(page).toHaveURL((url) => url.pathname === "/findings/readiness");
    await checkScreen(page, "readiness", ["AP SSC 2027 (BSEAP)", "2 of 5 ready"]);

    await pressOn(
      page.getByRole("link", { name: /^Open section/ }).first(),
      "Enter",
      "open section A",
    );
    await expect(page).toHaveURL((url) => url.searchParams.get("section") === R1_IDS.sectionA);
    await checkScreen(
      page,
      "readiness section",
      ["Synthetica Ravi Sai Kumar", "Synthetica Kiran Kumar"],
      {
        focusStops: false,
      },
    );
    // The print view keeps its link; the PDF is made by the server and downloaded when ready.
    await expect(page.getByRole("link", { name: "Print slips for this section" })).toBeVisible();
    await pressOn(
      page.getByRole("button", { name: "Make PDF of this section's slips" }),
      "Enter",
      "make slips PDF",
    );
    const download = page.getByRole("button", { name: "Download PDF" });
    await expect(download).toBeVisible({ timeout: 20_000 });
    await expectNoAxeViolations(page, "readiness slips PDF ready");
    const file = page.waitForEvent("download");
    await pressOn(download, "Enter", "download slips PDF");
    expect((await file).suggestedFilename()).toBe(`verification-slips-${SSC}.pdf`);

    await inTelugu(page, "/findings/readiness", testInfo, async () => {
      await checkScreen(page, "readiness te", ["AP SSC 2027 (BSEAP)"], { focusStops: false });
    });
  });

  test("readiness of one student: the exact difference and who fixes it (US-505)", async ({
    page,
  }) => {
    await signIn(page, `/findings/readiness/${R1_IDS.student}?profile=${SSC}`, OFFICE);
    await checkScreen(page, "readiness student", [
      "Synthetica Ravi Sai Kumar",
      "Space missing after “Sai”.",
      "The parent corrects Aadhaar at an Aadhaar centre.",
    ]);
    await expect(page.getByRole("link", { name: "Print parent slip" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Make PDF" })).toBeVisible();
  });

  test("APAAR consent: counts by section, refusal as a decision, forms as a PDF (US-1901..US-1903, FR-APC-007)", async ({
    page,
  }, testInfo) => {
    await signIn(page, "/apaar", OFFICE);
    await expect(page).toHaveURL((url) => url.pathname === "/apaar");
    await checkScreen(page, "apaar", ["Decisions by section", "Synthetica Anjali Devi", "Refused"]);
    await pressOn(
      page.getByRole("button", { name: /^PDF of pending forms/ }).first(),
      "Enter",
      "make pending forms PDF",
    );
    const download = page.getByRole("button", { name: "Download PDF" });
    await expect(download).toBeVisible({ timeout: 20_000 });
    const file = page.waitForEvent("download");
    await pressOn(download, "Enter", "download forms PDF");
    expect((await file).suggestedFilename()).toBe("apaar-consent-forms.pdf");

    await page.goto(`/apaar/students/${R1_IDS.udise}`);
    await checkScreen(page, "apaar student", ["Current decision", "Refused"]);
    await expect(page.getByRole("link", { name: "Print the consent form" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Make PDF of the form" })).toBeVisible();

    await inTelugu(page, "/apaar", testInfo, async () => {
      await checkScreen(page, "apaar te", ["Synthetica Anjali Devi"], { focusStops: false });
    });
  });

  test("new student: the PEN check finds a child already on the rolls (FR-STU-018)", async ({
    page,
  }) => {
    await signIn(page, "/students/new", OFFICE);
    await expect(page).toHaveURL((url) => url.pathname === "/students/new");
    await checkScreen(page, "new student", ["UDISE+ PEN"], { focusStops: false });
    await page.getByLabel("UDISE+ PEN", { exact: true }).fill(PEN_ON_ROLL);
    await pressOn(page.getByRole("button", { name: "Check this PEN" }), "Enter", "check PEN");
    await expect(page.getByText("This child is already on our rolls")).toBeVisible();
    await expect(page.getByRole("link", { name: /Synthetica Ravi Sai Kumar/ })).toBeVisible();
    await expectNoAxeViolations(page, "new student, PEN found");
  });

  test("school boards: declare the boards and the class boards (FR-TEN-020, FR-TEN-021)", async ({
    page,
  }) => {
    await signIn(page, "/settings/school", OFFICE);
    await expect(page).toHaveURL((url) => url.pathname === "/settings/school");
    await checkScreen(page, "school settings", ["Boards and how you use SchoolOS"], {
      focusStops: false,
    });
    const bseap = page.getByRole("checkbox", { name: "AP State Board (SSC)" });
    await expect(bseap).toBeChecked();
    const cbse = page.getByRole("checkbox", { name: "CBSE" });
    await cbse.focus();
    await page.keyboard.press("Space");
    await expect(cbse).toBeChecked();
    // Two boards: say which board each registered class follows.
    await expect(page.getByText("Board for each class")).toBeVisible();
    await page.getByLabel("Class X", { exact: true }).selectOption("CBSE");
    await expectNoAxeViolations(page, "boards with class boards");
    await pressOn(
      page.getByRole("button", { name: "Save boards and mode" }),
      "Enter",
      "save boards",
    );
    // The card reloads the saved school and keeps saying it was saved.
    await expect(page.getByLabel("Class X", { exact: true })).toHaveValue("CBSE");
    await expect(page.getByText("Boards and mode saved.")).toBeVisible();
  });

  test("imports: the template library marks formats that are not yet confirmed (FR-IMP-030)", async ({
    page,
  }) => {
    await signIn(page, "/imports", OFFICE);
    await expect(page).toHaveURL((url) => url.pathname === "/imports");
    await checkScreen(page, "imports with template library", [
      "Template library",
      "UDISE+ student list",
      "Format not yet confirmed",
    ]);
    await expect(
      page.getByRole("button", { name: "Download Excel template: UDISE+ student list" }),
    ).toBeVisible();
  });
});
