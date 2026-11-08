import { expect, test, type Locator, type Page } from "@playwright/test";
import {
  expectFocusInsideOpenDialog,
  expectNoAxeViolations,
  expectNoHorizontalOverflow,
  pressOn,
  signIn,
} from "./support/a11y-helpers";
import { DPO_OFFICE, DPO_PRINCIPAL, R3_IDS, R3_STUDENT, SECURITY_OPERATOR } from "./support/r3-api";
import { API_PORT } from "./support/stand-in";

/**
 * Browser journeys for the screens built in D56-D62 (roadmap D66; docs/13, NFR-A11Y-001),
 * against the stand-in IdP and the canned API (e2e/support/stand-in.ts, r3-api.ts; synthetic
 * data only), each with axe (WCAG 2.2 AA) and no sideways scroll at 1366×768:
 *
 * 1. Operator dashboard plan-limit tile and the usage page's plan-limit list (FR-PLT-021, D57).
 * 2. Breach "Edit facts" → "Count from the school's records" picker → counts filled → saved,
 *    keyboard only (FR-DPDP-033, D59). Only the counts are saved, never the list (D41).
 * 3. Privacy request "Mark as withdrawn" (FR-DPDP-005, D60).
 * 4. Notice-issue dialog with "Also make the PDF" (FR-DPDP-002, FR-DPDP-003, D60).
 * 5. Breach draft print page (FR-DPDP-036, D60): print media hides the console chrome; also
 *    at 375 px.
 * 6. Reopen-told channel radios on the incident page (D56): the channel only, no note.
 *
 * Needs E2E_STAND_IN=1 (and Valkey at REDIS_URL), like the other signed-in journeys. The
 * stand-in sessions are always fresh, so step-up routes (the count, breach writes) answer
 * without a 428, as for every other breach write in the R3 journeys.
 */

const STAND_IN = process.env.E2E_STAND_IN === "1";
const STAND_IN_REASON = "set E2E_STAND_IN=1 (needs Valkey at REDIS_URL)";
const OPERATOR = "operator-1";

async function resetStandIn(page: Page) {
  const reset = await page.request.post(`http://localhost:${API_PORT}/__e2e/reset`);
  expect(reset.status()).toBe(204);
}

/** Focus is inside this dialog (the picker opens over the edit dialog: two are open). */
async function expectFocusInside(dialog: Locator) {
  await expect
    .poll(() => dialog.evaluate((node) => node.contains(document.activeElement)))
    .toBe(true);
}

/**
 * The JSON body of the last write the stand-in API received, once it is `method` to a path
 * matching `path` (through the BFF, as the API sees it).
 */
async function lastWrite(page: Page, method: string, path: RegExp) {
  let body: Record<string, unknown> | null = null;
  await expect
    .poll(async () => {
      const answer = await page.request.get(`http://localhost:${API_PORT}/__e2e/r3/last-write`);
      const write = (await answer.json()) as {
        method: string;
        path: string;
        body: Record<string, unknown>;
      } | null;
      body = write && write.method === method && path.test(write.path) ? write.body : null;
      return body !== null;
    })
    .toBe(true);
  return body as unknown as Record<string, unknown>;
}

test.describe("D57-D62 screens (stand-in IdP)", () => {
  test.skip(!STAND_IN, STAND_IN_REASON);

  test.beforeEach(async ({ page }) => {
    await resetStandIn(page);
  });

  test("operator: plan-limit tile on the dashboard and the list on the usage page (FR-PLT-021, D57)", async ({
    page,
  }) => {
    await signIn(page, "/platform", OPERATOR);
    await expect(page).toHaveURL((url) => url.pathname === "/platform");
    await expect(page.getByText("Schools at a plan limit")).toBeVisible();
    await expect(page.getByText("1 more at 80% this billing period")).toBeVisible();
    await expect(
      page.getByText("2 schools are at 80% or more of a plan limit this billing period"),
    ).toBeVisible();
    await expectNoAxeViolations(page, "/platform with the plan-limit tile");
    await expectNoHorizontalOverflow(page, "/platform");

    await pressOn(page.getByRole("link", { name: "Open usage" }), "Enter", "open usage");
    await expect(page).toHaveURL((url) => url.pathname === "/platform/usage");
    await expect(
      page.getByRole("heading", { level: 2, name: "Plan-limit alerts this billing period" }),
    ).toBeVisible();
    const items = page.getByRole("listitem").filter({ hasText: /Limit reached|80% reached/ });
    await expect(items).toHaveCount(2);
    // Limit reached first, then 80 %.
    await expect(items.nth(0)).toContainText("Sri Saraswati High School");
    await expect(items.nth(0)).toContainText("Students");
    await expect(items.nth(0)).toContainText("Limit reached");
    await expect(items.nth(0)).toContainText("1,500 of 1,500");
    await expect(items.nth(1)).toContainText("Sample Model School");
    await expect(items.nth(1)).toContainText("Storage (GB)");
    await expect(items.nth(1)).toContainText("80% reached");
    await expectNoAxeViolations(page, "/platform/usage plan-limit list");
    await expectNoHorizontalOverflow(page, "/platform/usage");
  });

  test("breach: count the affected people from the records, keyboard only, then save (FR-DPDP-033, D59)", async ({
    page,
  }) => {
    test.setTimeout(90_000);
    await signIn(page, `/privacy/breaches/${R3_IDS.breach}`, DPO_PRINCIPAL);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();

    await pressOn(page.getByRole("button", { name: "Edit facts" }), "Enter", "edit facts");
    const edit = page.getByRole("dialog", { name: "Edit the facts" });
    await expect(edit).toBeVisible();
    await expectFocusInsideOpenDialog(page);

    const trigger = edit.getByRole("button", { name: "Count from the school's records" });
    await pressOn(trigger, "Enter", "open picker");
    const picker = page.getByRole("dialog", { name: "Count the people affected" });
    await expect(picker).toBeVisible();
    await expectFocusInside(picker);
    // Nothing picked: no count yet.
    await expect(picker.getByRole("button", { name: "Count", exact: true })).toBeDisabled();
    await expect(picker.getByRole("button", { name: "Use these counts" })).toBeDisabled();

    // A whole section, one student found by name, and all staff.
    await pressOn(picker.getByRole("checkbox", { name: "Class 6 · B" }), "Space", "section B");
    await expect(picker.getByRole("checkbox", { name: "Class 6 · B" })).toBeChecked();
    const search = picker.getByRole("searchbox", {
      name: "Find a student by name or admission number",
    });
    await search.focus();
    await page.keyboard.type("Synthetica Ravi");
    // Enter searches; it must not submit the edit form behind the picker.
    await page.keyboard.press("Enter");
    await expect(picker.getByText("1 student found")).toBeVisible();
    await expect(edit).toBeVisible();
    await pressOn(
      picker.getByRole("button", { name: new RegExp(`^Add ${R3_STUDENT.name}`) }),
      "Enter",
      "add student",
    );
    await expect(picker.getByText("1 student picked")).toBeVisible();
    await pressOn(
      picker.getByRole("checkbox", { name: "All staff of the school" }),
      "Space",
      "all staff",
    );
    await expectNoAxeViolations(page, "affected-people picker");

    await pressOn(picker.getByRole("button", { name: "Count", exact: true }), "Enter", "count");
    expect(await lastWrite(page, "POST", /\/affected:count$/)).toMatchObject({
      all_students: false,
      section_ids: ["0192f3a4-0000-7000-8000-000000000510"],
      student_ids: [R3_IDS.student],
      all_staff: true,
    });
    await expect(picker.getByText("2 children, 4 parents and guardians, 4 staff")).toBeVisible();
    await expectNoAxeViolations(page, "affected-people picker with counts");

    // Use: the picker closes, the edit dialog stays, the numbers are in its count fields.
    await pressOn(picker.getByRole("button", { name: "Use these counts" }), "Enter", "use");
    await expect(picker).toBeHidden();
    await expect(edit).toBeVisible();
    const children = edit.getByRole("textbox", { name: "Children" });
    await expect(children).toBeFocused();
    await expect(children).toHaveValue("2");
    await expect(edit.getByRole("textbox", { name: "Parents and guardians" })).toHaveValue("4");
    await expect(edit.getByRole("textbox", { name: "Staff", exact: true })).toHaveValue("4");

    // Save: only the numbers go out (D41), never the picked list.
    await pressOn(edit.getByRole("button", { name: "Save the facts" }), "Enter", "save facts");
    const patch = await lastWrite(page, "PATCH", new RegExp(`/privacy/breaches/${R3_IDS.breach}$`));
    expect(patch).toMatchObject({ approx_children: 2, approx_guardians: 4, approx_staff: 4 });
    expect(Object.keys(patch)).not.toContain("student_ids");
    expect(Object.keys(patch)).not.toContain("section_ids");
    await expect(edit).toBeHidden();
    const people = page.getByText("People affected (about)").locator("..");
    await expect(people).toContainText("2 children");
    await expect(people).toContainText("4 guardians");
    await expect(people).toContainText("4 staff");
    await expectNoAxeViolations(page, "breach detail after the counts were saved");
  });

  test("privacy request: Mark as withdrawn (FR-DPDP-005, D60)", async ({ page }) => {
    await signIn(page, `/privacy/requests/${R3_IDS.erasureRequest}`, DPO_PRINCIPAL);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await pressOn(
      page.getByRole("button", { name: "Mark as withdrawn" }),
      "Enter",
      "withdraw trigger",
    );
    const dialog = page.getByRole("dialog", { name: "Mark this request as withdrawn?" });
    await expect(dialog).toBeVisible();
    await expectFocusInsideOpenDialog(page);
    await expect(dialog).toContainText("cannot be opened again");
    await expectNoAxeViolations(page, "withdraw dialog");
    // YP-03 (FR-DPDP-016): an erasure request is withdrawn only with a written note.
    const note = "The parent phoned on 3 Nov and confirmed by the registered number.";
    await dialog.getByLabel("How they withdrew it").fill(note);
    await pressOn(
      dialog.getByRole("button", { name: "Mark as withdrawn" }),
      "Enter",
      "withdraw confirm",
    );
    expect(
      await lastWrite(page, "PATCH", new RegExp(`/privacy/requests/${R3_IDS.erasureRequest}$`)),
    ).toEqual({ status: "withdrawn", withdrawal_note: note });
    await expect(dialog).toBeHidden();
    await expect(page.getByText("Withdrawn", { exact: true }).first()).toBeVisible();
    // A withdrawn request is closed: the action is gone.
    await expect(page.getByRole("button", { name: "Mark as withdrawn" })).toHaveCount(0);
    await expectNoAxeViolations(page, "withdrawn request");
  });

  test("notices: record an issued notice and also make its PDF (FR-DPDP-003, D60)", async ({
    page,
  }) => {
    await signIn(page, "/privacy/notices", DPO_OFFICE);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await pressOn(
      page.getByRole("button", { name: "Record that we issued it" }),
      "Enter",
      "record trigger",
    );
    const dialog = page.getByRole("dialog", { name: "Record that we issued a notice" });
    await expect(dialog).toBeVisible();
    await dialog.getByLabel("How it was given out").selectOption("paper");
    await pressOn(
      dialog.getByRole("checkbox", { name: /^Also make the PDF of this notice/ }),
      "Space",
      "make pdf",
    );
    await expectNoAxeViolations(page, "notice-issue dialog");
    const pdf = page.waitForRequest(
      (request) =>
        request.method() === "POST" &&
        /\/privacy\/templates\/notice_parents\/pdf$/.test(request.url()),
    );
    await pressOn(dialog.getByRole("button", { name: "Record it" }), "Enter", "record");
    await pdf;
    await expect(dialog.getByText("Notice recorded")).toBeVisible();
    await expect(dialog.getByText(/The PDF is being made/)).toBeVisible();
    const download = dialog.getByRole("button", { name: /^Download PDF/ });
    await expect(download).toBeVisible({ timeout: 20_000 });
    await expectNoAxeViolations(page, "notice recorded with its PDF");
    const link = page.waitForResponse(
      (response) =>
        response.url().includes("/download-url") && response.request().method() === "GET",
    );
    await pressOn(download, "Enter", "download pdf");
    expect((await link).status()).toBe(200);
    // The result's own Close button (the dialog's × in the header has the same name).
    await pressOn(
      dialog.getByRole("button", { name: "Close", exact: true }).last(),
      "Enter",
      "close",
    );
    await expect(dialog).toBeHidden();
    const history = page.getByRole("table", { name: "When we issued them" });
    await expect(history.getByRole("row").filter({ hasText: "On paper" })).toHaveCount(2);
  });

  test("breach draft print page: A4 sheet, print hides the chrome (FR-DPDP-036, D60)", async ({
    page,
  }) => {
    await signIn(page, `/privacy/breaches/${R3_IDS.breach}`, DPO_PRINCIPAL);
    await pressOn(
      page.getByRole("button", { name: "Show the family notice" }),
      "Enter",
      "show draft",
    );
    await pressOn(page.getByRole("link", { name: "Open the print page" }), "Enter", "print page");
    await expect(page).toHaveURL(
      (url) => url.pathname === `/privacy/breaches/${R3_IDS.breach}/drafts/family_notice/print`,
    );
    await expect(
      page.getByRole("heading", { level: 1, name: "Print: Family notice" }),
    ).toBeVisible();
    const sheet = page.getByRole("article", { name: "Notice to families" });
    await expect(sheet).toBeVisible();
    await expect(sheet).toContainText("Sri Saraswati High School");
    await expect(sheet).toContainText("A printed class list was left on the bus");
    await expect(sheet).toContainText("Template 2026-10");
    await expectNoAxeViolations(page, "draft print page");
    await expectNoHorizontalOverflow(page, "draft print page");

    // Print media: only the sheet; no navigation, header, buttons or "nothing is sent" line.
    await page.emulateMedia({ media: "print" });
    await expect(sheet).toBeVisible();
    await expect(page.getByRole("heading", { level: 1 })).toBeHidden();
    await expect(page.getByRole("button", { name: "Print" })).toBeHidden();
    await expect(page.getByText("Nothing is sent from here.")).toBeHidden();
    await expect(page.getByRole("navigation")).toHaveCount(0);
    // Nothing visible outside the sheet but the page's own wrappers.
    const strays = await page.evaluate(() => {
      const article = document.querySelector("article");
      return [...document.body.querySelectorAll("a, button, input, select, nav, header, h1")]
        .filter((node) => !article?.contains(node) && !node.contains(article))
        .filter((node) => {
          // Shown and bigger than a screen-reader-only 1 px box (the skip link stays in the tree).
          const box = node.getBoundingClientRect();
          return getComputedStyle(node).visibility !== "hidden" && box.width > 1 && box.height > 1;
        })
        .map((node) => `${node.tagName}:${node.textContent?.trim().slice(0, 40) ?? ""}`);
    });
    expect(strays, "chrome visible in print").toEqual([]);
    await page.emulateMedia({ media: "screen" });

    // A phone: the sheet fits 375 px without sideways scroll.
    await page.setViewportSize({ width: 375, height: 812 });
    await page.reload();
    await expect(page.getByRole("article", { name: "Notice to families" })).toBeVisible();
    await expectNoHorizontalOverflow(page, "draft print page at 375 px");
    await expectNoAxeViolations(page, "draft print page at 375 px");
  });

  test("incident: record how a dedicated school was told of a reopen, channel only (D56)", async ({
    page,
  }) => {
    await signIn(page, `/platform/incidents/${R3_IDS.reopenedIncident}`, SECURITY_OPERATOR);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await pressOn(
      page.getByRole("button", { name: "Record that the school was told" }),
      "Enter",
      "told trigger",
    );
    const dialog = page.getByRole("dialog", { name: "Record that Vidya Nilayam was told" });
    await expect(dialog).toBeVisible();
    await expectFocusInsideOpenDialog(page);
    // No free-text note: three fixed channels.
    await expect(dialog.getByRole("radio")).toHaveCount(3);
    await expect(dialog.getByRole("textbox")).toHaveCount(0);
    // Saving without a channel says so.
    await pressOn(
      dialog.getByRole("button", { name: "Record that the school was told" }),
      "Enter",
      "told without channel",
    );
    await expect(dialog.getByText("Choose one of the options.")).toBeVisible();
    await pressOn(dialog.getByRole("radio", { name: "By e-mail" }), "Space", "email");
    await expect(dialog.getByRole("radio", { name: "By e-mail" })).toBeChecked();
    await expectNoAxeViolations(page, "reopen-told dialog");
    await pressOn(
      dialog.getByRole("button", { name: "Record that the school was told" }),
      "Enter",
      "told confirm",
    );
    expect(await lastWrite(page, "POST", /\/reopen:told$/)).toEqual({
      tenant_id: "0192f3a4-0000-7000-8000-000000000002",
      reopen_seq: 1,
      channel: "email",
    });
    await expect(dialog).toBeHidden();
    await expect(page.getByText(/^Reopen received /)).toBeVisible();
    await expect(page.getByRole("button", { name: "Record that the school was told" })).toHaveCount(
      0,
    );
    await expectNoAxeViolations(page, "incident after the reopen was told");
    await expectNoHorizontalOverflow(page, "incident detail");
  });
});
