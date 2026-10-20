import { expect, test, type Page } from "@playwright/test";
import {
  expectFocusRing,
  expectNoAxeViolations,
  expectNoHorizontalOverflow,
} from "./support/a11y-helpers";
import { CASHIER, R2_IDS, R2_RECEIPT_NUMBER, R2_STUDENT } from "./support/r2-api";
import { API_PORT } from "./support/stand-in";

/**
 * The fee counter, keyboard only at 1366×768 (roadmap R2-3I; US-2102; FR-FEE-010, FR-FEE-022;
 * docs/17 §5.8; NFR-A11Y-001). Stand-in IdP and canned API (E2E_STAND_IN=1; synthetic data
 * only, e2e/support/r2-api.ts): a cashier with `fee.collect` opens /fees/collect, finds the
 * student by typing and the arrow keys, sees what is due, types a part payment, tries the mode
 * keys, saves with Enter, lands on Print (focused), opens the print view with P and starts the
 * next student with N. No mouse click anywhere after sign-in (the stand-in IdP's login form is
 * not part of SchoolOS, and it is submitted with Enter too).
 */

/** Sign in through the stand-in IdP's login form with the keyboard. */
async function signInByKeyboard(page: Page, path: string, subject: string) {
  await page.goto(path);
  const field = page.getByLabel("Subject");
  await field.fill(subject);
  await field.press("Enter");
}

test.describe.serial("fee counter: keyboard only at 1366×768 (US-2102, FR-FEE-010)", () => {
  test.skip(process.env.E2E_STAND_IN !== "1", "set E2E_STAND_IN=1 (needs Valkey at REDIS_URL)");
  test.use({ viewport: { width: 1366, height: 768 } });

  test.beforeAll(async ({ request }) => {
    const reset = await request.post(`http://localhost:${API_PORT}/__e2e/reset`);
    expect(reset.status()).toBe(204);
  });

  test("find the student, see the dues, take cash, save and land on Print (FR-FEE-010, FR-FEE-022)", async ({
    page,
    context,
  }) => {
    await signInByKeyboard(page, "/fees/collect", CASHIER);
    await expect(page).toHaveURL(/:\d+\/fees\/collect$/);
    await expect(page.getByRole("heading", { level: 1, name: "Collect fees" })).toBeVisible();
    // The keys are always on screen (docs/17 §5.8).
    const shortcuts = page.getByRole("region", { name: "Keyboard shortcuts" });
    await expect(shortcuts).toBeVisible();
    await expect(shortcuts).toContainText("Find the student");
    await expect(shortcuts).toContainText("Save and print");

    // Focus starts in the search (a combobox over a listbox of matches).
    const search = page.getByRole("combobox", { name: "Student name or admission number" });
    await expectFocusRing(search, "search on arrival");
    await expectNoAxeViolations(page, "/fees/collect");
    await expectNoHorizontalOverflow(page, "/fees/collect");

    // Type; two students match; the arrow keys move the active option; Enter picks.
    await page.keyboard.type("Synthetica Laksh");
    const matches = page.getByRole("listbox", { name: "Matching students" });
    await expect(matches.getByRole("option")).toHaveCount(2);
    await expect(search).toHaveAttribute("aria-expanded", "true");
    const first = matches.getByRole("option", { name: new RegExp(R2_STUDENT.name) });
    const second = matches.getByRole("option", { name: new RegExp(R2_STUDENT.other) });
    await expect(first).toHaveAttribute("aria-selected", "true");
    await page.keyboard.press("ArrowDown");
    await expect(second).toHaveAttribute("aria-selected", "true");
    const secondId = await second.getAttribute("id");
    expect(secondId).toBeTruthy();
    await expect(search).toHaveAttribute("aria-activedescendant", secondId ?? "");
    await page.keyboard.press("ArrowUp");
    await expect(first).toHaveAttribute("aria-selected", "true");
    // Focus never leaves the search box while choosing (aria-activedescendant).
    await expect(search).toBeFocused();
    await page.keyboard.press("Enter");

    // The dues: the student, the lines with something due, the total due now.
    const dues = page.getByRole("region", { name: "What is due" });
    await expect(page.getByText(R2_STUDENT.admissionNo).first()).toBeVisible();
    await expect(dues.getByRole("row")).toHaveCount(4); // header + 3 lines
    await expect(dues).toContainText("Tuition fee");
    await expect(dues).toContainText("Late fee");
    await expect(dues).toContainText("Overdue");
    await expect(page.getByText("Total due now")).toBeVisible();
    await expect(page.getByText("₹19,600.00").filter({ visible: true }).first()).toBeVisible();

    // The amount is pre-filled with what is due now, focused and selected for typing.
    const payment = page.getByRole("form", { name: "Take the payment" });
    const amount = payment.getByRole("textbox", { name: /^Amount \(₹\)/ });
    await expectFocusRing(amount, "amount after picking the student");
    await expect(amount).toHaveValue("19600.00");
    await page.keyboard.type("7600");
    await expect(amount).toHaveValue("7600");
    // Oldest due first: term 1 tuition, then its late fee.
    const allocation = payment.getByRole("region", { name: "Where the money goes" });
    await expect(allocation).toContainText("₹7,500.00");
    await expect(allocation).toContainText("₹100.00");
    await expectNoAxeViolations(page, "/fees/collect with dues");
    await expectNoHorizontalOverflow(page, "/fees/collect with dues");

    // Tab to the mode: cash is chosen; Q switches to cheque (its fields appear), C back to cash.
    await page.keyboard.press("Tab");
    const cash = payment.getByRole("radio", { name: /^Cash/ });
    const cheque = payment.getByRole("radio", { name: /^Cheque/ });
    await expect(cash).toBeFocused();
    await expect(cash).toBeChecked();
    await expect(payment.getByRole("group", { name: "Paid by" })).toBeVisible();
    await page.keyboard.press("q");
    await expect(cheque).toBeChecked();
    await expect(cheque).toBeFocused();
    await expect(payment.getByRole("textbox", { name: "Cheque number" })).toBeVisible();
    await page.keyboard.press("c");
    await expect(cash).toBeChecked();
    await expect(cash).toBeFocused();
    await expect(payment.getByRole("textbox", { name: "Cheque number" })).toHaveCount(0);

    // Enter on the mode saves (an idempotent POST with the amount and mode, no allocation).
    const posted = page.waitForRequest(
      (request) => request.method() === "POST" && request.url().endsWith("/api/v1/fees/receipts"),
    );
    await page.keyboard.press("Enter");
    const request = await posted;
    expect(request.postDataJSON()).toMatchObject({
      account_id: R2_IDS.account,
      amount: "7600.00",
      mode: "cash",
      allocations: null,
    });
    expect(request.headers()["idempotency-key"]).toBeTruthy();

    // Saved: the receipt number large, Print focused (P), Next student (N).
    await expect(page.getByRole("heading", { name: "Receipt saved" })).toBeVisible();
    const saved = page.getByRole("status").filter({ hasText: R2_RECEIPT_NUMBER });
    await expect(saved).toContainText(R2_STUDENT.name);
    await expect(saved).toContainText("₹7,600.00");
    const print = page.getByRole("link", { name: /^Print/ });
    await expectFocusRing(print, "Print after saving");
    await expect(print).toHaveAttribute("aria-keyshortcuts", "P");
    await expect(print).toHaveAttribute(
      "href",
      `/bff/api/v1/fees/receipts/${R2_IDS.receipt}/print`,
    );
    await expect(page.getByRole("button", { name: /^Next student/ })).toBeVisible();
    await expectNoAxeViolations(page, "/fees/collect saved");
    await expectNoHorizontalOverflow(page, "/fees/collect saved");

    // P opens the print view in a new tab (answered here: the API's print page is not under test).
    await context.route("**/bff/api/v1/fees/receipts/*/print", (route) =>
      route.fulfill({
        status: 200,
        contentType: "text/html; charset=utf-8",
        body: "<!doctype html><title>Synthetic receipt print view</title><p>Synthetic receipt</p>",
      }),
    );
    const popup = context.waitForEvent("page");
    await page.keyboard.press("p");
    const printView = await popup;
    await printView.waitForLoadState();
    expect(new URL(printView.url()).pathname).toBe(
      `/bff/api/v1/fees/receipts/${R2_IDS.receipt}/print`,
    );
    await printView.close();

    // N starts the next student: back to an empty search, focused.
    await page.bringToFront();
    await page.keyboard.press("n");
    await expect(page.getByRole("heading", { name: "Receipt saved" })).toHaveCount(0);
    await expect(search).toBeFocused();
    await expect(search).toHaveValue("");
  });
});
