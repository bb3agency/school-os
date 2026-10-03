import { readFile } from "node:fs/promises";
import { expect, test, type Download, type Locator, type Page } from "@playwright/test";
import {
  expectFocusRing,
  expectNoAxeViolations,
  expectNoHorizontalOverflow,
  pressOn,
  signIn,
} from "./support/a11y-helpers";
import { MAKER } from "./support/journey-api";
import { SHEET_IDS } from "./support/sheet-api";
import { API_PORT } from "./support/stand-in";

/**
 * Sheet editor, view → edit → export (US-401 AC5/AC6, US-701 AC5; FR-IMP-008, FR-IMP-009,
 * FR-DOC-009..011; docs/17 §4.1), signed in through the stand-in IdP with the canned API in
 * e2e/support/sheet-api.ts (E2E_STAND_IN=1; synthetic data only).
 *
 * Keyboard only for the grid (WAI-ARIA grid: one tab stop, arrows, Enter/F2 to edit, Enter to
 * save). Every screen: no WCAG 2.2 AA axe violations and no page-level sideways scroll at
 * 1366×768 and at 375px (the grid scrolls inside its own region). Downloads are read back: CSV
 * with a UTF-8 BOM, the edit included, formula-like text neutralised.
 */

async function checkScreen(page: Page, label: string) {
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await expect(page.getByText("Loading…")).toHaveCount(0);
  await expectNoAxeViolations(page, label);
  await expectNoHorizontalOverflow(page, label);
  await page.setViewportSize({ width: 375, height: 812 });
  await expectNoHorizontalOverflow(page, `${label} at 375px`);
  await page.setViewportSize({ width: 1366, height: 768 });
}

/** The grid cell of a data row (0-based on the page) and a 0-based grid column. */
function cell(grid: Locator, row: number, column: number): Locator {
  return grid
    .getByRole("row")
    .nth(row + 1)
    .getByRole("gridcell")
    .nth(column);
}

async function csvOf(download: Download): Promise<string> {
  const path = await download.path();
  return readFile(path, "utf8");
}

test.describe.serial("sheet editor: view, edit, export", () => {
  test.skip(process.env.E2E_STAND_IN !== "1", "set E2E_STAND_IN=1 (needs Valkey at REDIS_URL)");

  test.beforeAll(async ({ request }) => {
    const reset = await request.post(`http://localhost:${API_PORT}/__e2e/reset`);
    expect(reset.status()).toBe(204);
  });

  test("import: check rows, correct a cell in the sheet, download it (FR-IMP-008/009)", async ({
    page,
  }) => {
    await signIn(page, `/imports/${SHEET_IDS.import}`, MAKER);
    await expect(page).toHaveURL(new RegExp(`/imports/${SHEET_IDS.import}$`));
    // "Check rows": the row with a problem points to the sheet.
    const fix = page.getByRole("link", { name: "Correct cells in the sheet" });
    await expect(fix).toBeVisible();
    await checkScreen(page, "import check rows");
    await pressOn(fix, "Enter", "correct in sheet link");

    await expect(page).toHaveURL(new RegExp(`/imports/${SHEET_IDS.import}/sheet$`));
    const grid = page.getByRole("grid", { name: "Rows in your file" });
    await expect(grid).toBeVisible();
    await expect(grid.getByText("Synthetica Lakshmi Prasanna")).toBeVisible();
    await expect(grid.getByText("Has errors")).toBeVisible();
    // Sticky header: column letter, the file's heading and the field it fills.
    await expect(grid.getByRole("columnheader", { name: /Admission No/ })).toContainText("Fills:");
    await checkScreen(page, "import sheet");

    // Keyboard: the check cell of row 2, down to row 3, right to "Admission No", Enter.
    const check = cell(grid, 0, 0);
    await check.focus();
    await expectFocusRing(check, "first grid cell");
    await page.keyboard.press("ArrowDown");
    await page.keyboard.press("ArrowRight");
    await expectFocusRing(cell(grid, 1, 1), "row 3 admission number");
    await page.keyboard.press("Enter");
    const input = page.getByRole("textbox", { name: "Change Admission No, row 3" });
    await expect(input).toBeFocused();
    await input.fill("SYN-2026-202");
    await page.keyboard.press("Enter");
    await expect(page.getByRole("status").filter({ hasText: "Row 3 saved" })).toHaveText(
      "Row 3 saved. It has no problems now.",
    );
    await expect(cell(grid, 1, 1)).toContainText("SYN-2026-202");
    await expect(cell(grid, 1, 1)).toContainText("Changed");
    await expect(cell(grid, 1, 1)).toBeFocused();
    await expectNoAxeViolations(page, "import sheet after an edit");

    // Find on this page.
    await page.getByRole("searchbox", { name: "Find on this page" }).fill("Arjun");
    await expect(grid.getByText("Synthetica Arjun Rao")).toBeVisible();
    await expect(grid.getByText("Synthetica Ravi Teja")).toHaveCount(0);
    await page.getByRole("searchbox", { name: "Find on this page" }).fill("");

    // Download with the edit: UTF-8 BOM, the new value, formulas neutralised.
    const file = page.waitForEvent("download");
    await pressOn(page.getByRole("button", { name: "Download CSV" }), "Enter", "download csv");
    const download = await file;
    expect(download.suggestedFilename()).toBe("import-0192f3a4-sheet.csv");
    const text = await csvOf(download);
    expect(text.startsWith("﻿")).toBe(true);
    expect(text).toContain("SYN-2026-202,Synthetica Lakshmi Prasanna");
    expect(text).toContain("'=1+1");
  });

  test("document: preview, edit, download and save as a new version (FR-DOC-009..011)", async ({
    page,
  }) => {
    await signIn(page, `/documents/${SHEET_IDS.document}`, MAKER);
    await expect(page).toHaveURL(new RegExp(`/documents/${SHEET_IDS.document}$`));
    const preview = page.getByRole("grid", { name: "First rows of the sheet" });
    await expect(preview.getByText("Synthetica Parent One")).toBeVisible();
    await expect(preview).toHaveAttribute("aria-readonly", "true");
    await checkScreen(page, "document with sheet preview");

    await pressOn(page.getByRole("link", { name: "Open as a sheet" }), "Enter", "open sheet");
    await expect(page).toHaveURL(new RegExp(`/documents/${SHEET_IDS.document}/sheet$`));
    const grid = page.getByRole("grid", { name: "Rows" });
    await expect(grid.getByText("R-002")).toBeVisible();
    await checkScreen(page, "document sheet");

    // Row 2, "Amount" (third column): F2 edits, Enter keeps the change on the page.
    const amount = cell(grid, 0, 2);
    await amount.focus();
    await page.keyboard.press("F2");
    const input = page.getByRole("textbox", { name: "Change Amount, row 2" });
    await input.fill("1250");
    await page.keyboard.press("Enter");
    await expect(amount).toContainText("1250");
    await expect(amount).toContainText("Not saved yet");
    await expect(page.getByText("1 unsaved change")).toBeVisible();
    await expectNoAxeViolations(page, "document sheet with an unsaved change");

    const file = page.waitForEvent("download");
    await pressOn(page.getByRole("button", { name: "Download CSV" }), "Enter", "download csv");
    const text = await csvOf(await file);
    expect(text.startsWith("﻿")).toBe(true);
    expect(text).toContain("R-001,Synthetica Parent One,1250");

    await pressOn(
      page.getByRole("button", { name: "Save as new version" }),
      "Enter",
      "save version",
    );
    await expect(page.getByRole("status").filter({ hasText: "Saved as version 2" })).toBeVisible();
    await expectNoAxeViolations(page, "document sheet after saving");
  });
});
