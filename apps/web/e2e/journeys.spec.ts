import { expect, test, type Locator, type Page } from "@playwright/test";
import { openTelugu, TELUGU } from "./support/telugu";
import {
  expectFocusInsideOpenDialog,
  expectFocusRing,
  expectNoAxeViolations,
  expectNoHorizontalOverflow,
  expectVisibleFocusOnEveryStop,
  pressOn,
  signIn,
} from "./support/a11y-helpers";
import { CHECKER, IDS, MAKER, STORAGE_PATH } from "./support/journey-api";
import { API_PORT } from "./support/stand-in";

/**
 * Signed-in M1 journeys through the stand-in IdP and canned API (E2E_STAND_IN=1; synthetic
 * data only): spreadsheet import (US-401, FR-IMP-001..005) → data-quality findings (US-501,
 * US-502, FR-DQ-006, FR-DQ-020) → change request by a maker, approved by a checker (US-601,
 * FR-CR-001..004) → board pre-check export and download (US-501 AC4, FR-EXP-001..004).
 *
 * Every screen: the data is shown (not only the shell), no WCAG 2.2 AA axe violations, no
 * horizontal scroll at 1366×768, a visible focus indicator on every Tab stop (English), and a
 * Telugu check (with Telugu switched on; ADR-0036). Key actions are driven by the keyboard only (focus, Enter/Space/Tab; Escape
 * closes dialogs and returns focus). Files are chosen through the file chooser that the
 * keyboard opens; the presigned storage POST is answered by page.route (same origin, so the
 * page's CSP `connect-src 'self'` holds).
 */

/** Synthetic spreadsheet (the canned API does not read it). */
const CSV = [
  "Admission No,Student Name,పుట్టిన తేదీ,Gender,Class,Remarks",
  "SYN-2026-101,Synthetica Anjali Devi,03/02/2014,female,6A,",
  "SYN-2026-102,Synthetica Kiran Kumar,04/05/2014,male,6A,",
  "SYN-2026-103,Synthetica Meena Kumari,31/31/2014,female,6B,",
].join("\n");

/** The data is on screen; then axe, 1366×768 overflow and (optionally) every focus stop. */
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

/** Enter opens the dialog with focus inside; axe; Escape closes it and focus returns. */
async function openAndEscape(page: Page, trigger: Locator, name: string) {
  await pressOn(trigger, "Enter", `${name} trigger`);
  const dialog = page.getByRole("dialog", { name });
  await expect(dialog).toBeVisible();
  await expectFocusInsideOpenDialog(page);
  await expectNoAxeViolations(page, `${name} dialog`);
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expectFocusRing(trigger, `${name} trigger after Escape`);
}

// ADR-0036: tagged to run again with Telugu on; with it off each /te step checks the redirect
// to the English page and that no Telugu is shown.
test.describe
  .serial(`M1 journeys: import, findings, change request, pre-check export ${TELUGU}`, () => {
  test.skip(process.env.E2E_STAND_IN !== "1", "set E2E_STAND_IN=1 (needs Valkey at REDIS_URL)");

  test.beforeAll(async ({ request }) => {
    const reset = await request.post(`http://localhost:${API_PORT}/__e2e/reset`);
    expect(reset.status()).toBe(204);
  });

  test.beforeEach(async ({ page }) => {
    // Presigned storage POST (the browser posts files straight to storage, never the BFF).
    await page.route(`**${STORAGE_PATH}`, (route) => route.fulfill({ status: 204 }));
  });

  test("import: upload, map columns, check rows, add them (US-401, FR-IMP-001..005)", async ({
    page,
  }, testInfo) => {
    await signIn(page, "/en/imports", MAKER);
    await expect(page).toHaveURL(/\/en\/imports$/);
    await checkScreen(page, "/en/imports", [
      "Earlier imports",
      "Class list (office format)",
      "Admission register",
    ]);
    await expect(page.getByRole("cell", { name: "42", exact: true })).toBeVisible();

    if (await openTelugu(page, "/te/imports", testInfo))
      await checkScreen(page, "/te/imports", ["గత దిగుమతులు", "Class list (office format)"], {
        focusStops: false,
      });

    // Upload by keyboard: Space on the file input opens the chooser; Tab; Enter submits.
    await page.goto("/en/imports");
    const chooser = page.waitForEvent("filechooser");
    await pressOn(page.getByLabel("Spreadsheet file"), "Space", "spreadsheet file input");
    await (
      await chooser
    ).setFiles({
      name: "class-6-admissions.csv",
      mimeType: "text/csv",
      buffer: Buffer.from(CSV, "utf8"),
    });
    await page.keyboard.press("Tab");
    await expectFocusRing(page.getByLabel("Where does this list come from?"), "source select");
    await expect(page.getByLabel("Where does this list come from?")).toHaveValue(
      "admission_register",
    );
    await pressOn(
      page.getByRole("button", { name: "Upload and read the file" }),
      "Enter",
      "upload button",
    );
    await expect(page).toHaveURL(new RegExp(`/en/imports/${IDS.importNew}$`));

    // Mapping: suggestions from English and Telugu headings are pre-selected.
    await checkScreen(page, "import mapping", [
      "Choose which field each column fills",
      "పుట్టిన తేదీ",
    ]);
    await expect(
      page.getByRole("combobox", { name: "Field for the column Admission No" }),
    ).toHaveValue("admission_no");
    await expect(page.getByRole("combobox", { name: "Field for the column Remarks" })).toHaveValue(
      "ignore",
    );
    await pressOn(page.getByRole("button", { name: "Check the rows" }), "Enter", "check rows");

    // Validated: the rows with errors are shown first, with how to fix them.
    await expect(page.getByRole("heading", { name: "Ready to add" })).toBeVisible();
    await checkScreen(page, "import validated", [
      "Synthetica Meena Kumari",
      "write the date as DD/MM/YYYY, for example 14/03/2012.",
    ]);
    await expect(page.getByLabel("Show")).toHaveValue("error");

    // Commit dialog: Escape closes and returns focus; then skip the error row and add.
    const commit = page.getByRole("button", { name: "Add to student records" });
    await openAndEscape(page, commit, "Add these rows to student records?");
    await pressOn(commit, "Enter", "commit trigger");
    const dialog = page.getByRole("dialog", { name: "Add these rows to student records?" });
    await expect(dialog.getByText("2 of 3 rows are ready to add.")).toBeVisible();
    const skip = dialog.getByRole("checkbox", { name: /skip the 1 rows with errors/ });
    await pressOn(skip, "Space", "skip error rows");
    await expect(skip).toBeChecked();
    await pressOn(dialog.getByRole("button", { name: "Add rows" }), "Enter", "add rows");
    await expect(dialog).toBeHidden();

    await expect(page.getByRole("heading", { name: "Added to student records" })).toBeVisible();
    // After adding, the list shows every row again (FR-IMP-004): the "Rows with errors" filter
    // from checking would now show nothing. The filter stays reachable by keyboard.
    const show = page.getByLabel("Show");
    await expect(show).toHaveValue("all");
    await show.focus();
    await expectFocusRing(show, "rows filter");
    await checkScreen(page, "import committed", [
      "Rows added",
      /you can undo this import until/,
      "Synthetica Anjali Devi",
    ]);

    if (await openTelugu(page, `/te/imports/${IDS.importNew}`, testInfo))
      await checkScreen(
        page,
        "import committed te",
        ["విద్యార్థి రికార్డుల్లో చేర్చబడింది", "చేర్చిన వరుసలు"],
        { focusStops: false },
      );
  });

  test("findings: blockers first, resolve one with a note, accept one as it is (US-501, US-502, FR-DQ-020)", async ({
    page,
  }, testInfo) => {
    await signIn(page, "/en/findings", MAKER);
    await expect(page).toHaveURL(/\/en\/findings$/);
    await expect(page.getByRole("heading", { level: 2, name: "1 blocker" })).toBeVisible();
    await expect(page.getByRole("heading", { level: 2, name: "2 warnings" })).toBeVisible();
    await checkScreen(page, "/en/findings", [
      "Synthetica Ravi Kumar",
      "Date of birth differs. Boards and APAAR need these to match.",
      "••/••/2014",
    ]);

    if (await openTelugu(page, "/te/findings", testInfo))
      await checkScreen(
        page,
        "/te/findings",
        [
          "పుట్టిన తేదీ వేరుగా ఉంది. బోర్డులు, APAAR కోసం ఇవి ఒకేలా ఉండాలి.",
          "Synthetica Ravi Kumar",
        ],
        { focusStops: false },
      );

    // Open the parent-name finding by keyboard, then resolve it with a note.
    await page.goto("/en/findings");
    await pressOn(
      page.getByRole("link", { name: /^Open\s?: DQ-004 Synthetica Ravi Kumar$/ }),
      "Enter",
      "open finding link",
    );
    await expect(page).toHaveURL(new RegExp(`/en/findings/${IDS.findingFather}$`));
    await checkScreen(page, "finding detail", [
      "Problem DQ-004",
      "Synthetica Venkata Rao",
      "Parent name spelled differently across records.",
    ]);
    const resolve = page.getByRole("button", { name: "Resolve", exact: true });
    await openAndEscape(page, resolve, "Resolve this problem");
    await pressOn(resolve, "Enter", "resolve trigger");
    const resolveDialog = page.getByRole("dialog", { name: "Resolve this problem" });
    const note = resolveDialog.getByLabel("What was done");
    await note.focus();
    await expectFocusRing(note, "resolution note");
    await page.keyboard.type("Checked the admission register; the register spelling is right.");
    await pressOn(
      resolveDialog.getByRole("button", { name: "Resolve", exact: true }),
      "Enter",
      "confirm resolve",
    );
    await expect(resolveDialog).toBeHidden();
    await expect(page.getByText("Resolved on")).toBeVisible();
    await checkScreen(
      page,
      "finding resolved",
      ["Checked the admission register; the register spelling is right."],
      { focusStops: false },
    );

    if (await openTelugu(page, `/te/findings/${IDS.findingFather}`, testInfo))
      await checkScreen(page, "finding resolved te", ["పరిష్కరించబడింది", "పరిష్కరించిన తేదీ"], {
        focusStops: false,
      });

    // Accept the gender finding as it is (reason; step-up is the server's call).
    await page.goto(`/en/findings/${IDS.findingGender}`);
    await checkScreen(page, "finding gender", ["Gender differs between records."], {
      focusStops: false,
    });
    const waive = page.getByRole("button", { name: "Accept as it is" });
    await openAndEscape(page, waive, "Accept this problem as it is");
    await pressOn(waive, "Enter", "waive trigger");
    const waiveDialog = page.getByRole("dialog", { name: "Accept this problem as it is" });
    await waiveDialog.getByLabel("Why is it right as it is?").focus();
    await page.keyboard.type("UDISE+ entry is being corrected by the cluster office.");
    await pressOn(
      waiveDialog.getByRole("button", { name: "Accept as it is" }),
      "Enter",
      "confirm waive",
    );
    await expect(waiveDialog).toBeHidden();
    await expect(page.getByText("Accepted on")).toBeVisible();

    if (await openTelugu(page, `/te/findings/${IDS.findingGender}`, testInfo))
      await checkScreen(page, "finding waived te", ["అంగీకరించబడింది"], { focusStops: false });
  });

  test("change request: the maker asks for a correction with evidence (US-601 AC1, FR-CR-001)", async ({
    page,
  }, testInfo) => {
    await signIn(page, `/en/findings/${IDS.findingDob}`, MAKER);
    await checkScreen(page, "finding dob", ["Problem DQ-002", "••/••/2014"], {
      focusStops: false,
    });
    await pressOn(
      page.getByRole("link", { name: "Request a correction" }),
      "Enter",
      "request correction link",
    );
    await expect(page).toHaveURL(/\/en\/change-requests\/new\?/);
    const newUrl = page.url();
    await checkScreen(page, "new change request", [
      "Synthetica Ravi Kumar · SYN-2026-014 · Class 6 · A",
      "Now:",
    ]);
    await expect(page.getByLabel("Field", { exact: true })).toHaveValue("dob");
    await expect(page.getByLabel("Record to correct")).toHaveValue("admission_register");

    if (await openTelugu(page, newUrl.replace("/en/", "/te/"), testInfo))
      await checkScreen(
        page,
        "new change request te",
        ["సవరణ కోసం అభ్యర్థించండి", "Synthetica Ravi Kumar · SYN-2026-014 · Class 6 · A"],
        { focusStops: false },
      );

    // Keyboard only: value, Tab to the reason, the evidence file, Enter to send.
    await page.goto(newUrl);
    await expect(page.getByText("Synthetica Ravi Kumar · SYN-2026-014")).toBeVisible();
    const value = page.getByLabel("Correct value");
    await value.focus();
    await expectFocusRing(value, "correct value");
    await page.keyboard.type("21/06/2014");
    await page.keyboard.press("Tab");
    await expectFocusRing(page.getByLabel("Reason"), "reason");
    await page.keyboard.type("Birth certificate shows the twenty first of June.");
    const chooser = page.waitForEvent("filechooser");
    await pressOn(page.getByLabel("Scan or photo"), "Space", "evidence file input");
    await (
      await chooser
    ).setFiles({
      name: "birth-certificate-synthetic.pdf",
      mimeType: "application/pdf",
      buffer: Buffer.from("%PDF-1.4\n% synthetic evidence for e2e\n", "utf8"),
    });
    await pressOn(page.getByRole("button", { name: "Send for approval" }), "Enter", "send");
    await expect(page.getByText("Request sent")).toBeVisible();
    await pressOn(page.getByRole("link", { name: "Open the request" }), "Enter", "open request");

    await expect(page).toHaveURL(new RegExp(`/en/change-requests/${IDS.changeRequest}$`));
    await checkScreen(page, "change request (maker)", [
      "Waiting for approval",
      "You asked for this",
      "21/06/2014",
      "Synthetica Ravi Kumar",
    ]);
    // SEC-014 / FR-CR-002: the requester is never offered approve.
    await expect(page.getByRole("button", { name: "Approve" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Withdraw" })).toBeVisible();
  });

  test("change request: the checker approves it; the finding clears (US-601 AC2, FR-CR-002)", async ({
    page,
  }, testInfo) => {
    await signIn(page, "/en/change-requests", CHECKER);
    await expect(page).toHaveURL(/\/en\/change-requests$/);
    await checkScreen(page, "/en/change-requests (checker)", ["Waiting for you", "Date of birth"]);

    if (await openTelugu(page, "/te/change-requests", testInfo))
      await checkScreen(page, "/te/change-requests", ["మీ కోసం వేచి ఉంది", "పుట్టిన తేదీ"], {
        focusStops: false,
      });

    await page.goto("/en/change-requests");
    await pressOn(
      page.getByRole("link", { name: /^Open\s?: Date of birth/ }),
      "Enter",
      "open request link",
    );
    await expect(page).toHaveURL(new RegExp(`/en/change-requests/${IDS.changeRequest}$`));
    await checkScreen(page, "change request (checker)", [
      "Birth certificate shows the twenty first of June.",
      "12/06/2014",
    ]);
    const approve = page.getByRole("button", { name: "Approve", exact: true });
    await openAndEscape(page, approve, "Approve this correction");
    await pressOn(approve, "Enter", "approve trigger");
    const dialog = page.getByRole("dialog", { name: "Approve this correction" });
    await dialog.getByLabel("Note (optional)").focus();
    await page.keyboard.type("Checked the birth certificate against the register.");
    await pressOn(dialog.getByRole("button", { name: "Approve" }), "Enter", "confirm approve");
    await expect(dialog).toBeHidden();
    await expect(page.getByText("The new value was recorded as verified")).toBeVisible();
    await checkScreen(page, "change request approved", ["Approved"], { focusStops: false });

    if (await openTelugu(page, `/te/change-requests/${IDS.changeRequest}`, testInfo))
      await checkScreen(page, "change request approved te", ["ఆమోదించబడింది"], {
        focusStops: false,
      });

    // Every finding is now resolved or accepted.
    await page.goto("/en/findings");
    await checkScreen(page, "findings after approval", ["No problems found"], {
      focusStops: false,
    });
  });

  test("pre-check export: make it, wait until ready, download by keyboard (US-501 AC4, FR-EXP-001..004)", async ({
    page,
  }, testInfo) => {
    await signIn(page, "/en/exports/new/precheck", MAKER);
    await expect(page).toHaveURL(/\/en\/exports\/new\/precheck$/);
    await checkScreen(page, "new pre-check", [
      "Full name, Date of birth, Gender, Father's name, Admission number",
    ]);
    await expect(page.getByLabel("Format")).toHaveValue("cisce-registration-2026");

    if (await openTelugu(page, "/te/exports/new/precheck", testInfo))
      await checkScreen(
        page,
        "new pre-check te",
        ["కొత్త ముందస్తు తనిఖీ", "పూర్తి పేరు, పుట్టిన తేదీ, లింగం"],
        { focusStops: false },
      );

    await page.goto("/en/exports/new/precheck");
    await expect(page.getByLabel("Format")).toHaveValue("cisce-registration-2026");
    await pressOn(page.getByRole("button", { name: "Make the pre-check" }), "Enter", "make it");
    await expect(page).toHaveURL(new RegExp(`/en/exports/${IDS.export}$`));

    // Queued, then ready (the page polls while the worker makes the files).
    const download = page.getByRole("button", { name: /^Download Excel \(XLSX\)/ });
    await expect(download).toBeVisible({ timeout: 20_000 });
    await checkScreen(page, "export ready", [
      "Board pre-check · CISCE registration 2026",
      "214",
      "Ready",
    ]);
    const file = page.waitForEvent("download");
    await pressOn(download, "Enter", "download xlsx");
    expect((await file).suggestedFilename()).toBe("precheck-cisce-registration-2026.xlsx");

    if (await openTelugu(page, `/te/exports/${IDS.export}`, testInfo))
      await checkScreen(
        page,
        "export ready te",
        ["బోర్డు ముందస్తు తనిఖీ · CISCE నమోదు 2026", "సిద్ధం"],
        {
          focusStops: false,
        },
      );

    await page.goto("/en/exports");
    await checkScreen(page, "/en/exports", ["Board pre-check · CISCE registration 2026"], {
      focusStops: false,
    });
  });
});
