import { expect, test } from "@playwright/test";
import {
  expectFocusInsideOpenDialog,
  expectNoAxeViolations,
  expectNoHorizontalOverflow,
  expectVisibleFocusOnEveryStop,
  pressOn,
  signIn,
} from "./support/a11y-helpers";

/**
 * Ask the school (US-801, US-802, FR-KB-005..011, FR-KB-030): the synthetic SSE answer of the
 * stand-in API (e2e/support/stand-in.ts) streamed through the real BFF, keyboard only, with
 * axe (WCAG 2.2 AA) and 1366×768 checks (NFR-A11Y-001). Needs E2E_STAND_IN=1 and Valkey.
 */
test.describe("Ask the school: streamed answer, keyboard and axe (stand-in IdP)", () => {
  test.skip(process.env.E2E_STAND_IN !== "1", "set E2E_STAND_IN=1 (needs Valkey at REDIS_URL)");

  test("ask by keyboard, read the sourced answer, give feedback, open the save dialog", async ({
    page,
  }) => {
    await signIn(page, "/en/ask", "clerk");
    await expect(page).toHaveURL(/\/en\/ask$/);
    await expect(page.getByRole("heading", { level: 1, name: "Ask the school" })).toBeVisible();
    await expect(
      page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Ask the school" }),
    ).toHaveAttribute("aria-current", "page");
    await expectNoAxeViolations(page, "ask empty");

    // Keyboard only: type the question, Ctrl+Enter asks.
    const box = page.getByLabel(/^Your question/);
    await box.focus();
    await page.keyboard.type("When are the Dasara holidays?");
    await page.keyboard.press("Control+Enter");
    await expect(page.getByText("The answer is ready.")).toBeVisible();

    const answer = page.getByRole("region", { name: "Answer" });
    await expect(answer.getByText(/Dasara holidays run from 02\/10\/2026/)).toBeVisible();
    await expect(answer.locator("[aria-live='polite']")).toHaveAttribute("aria-busy", "false");
    await expect(
      answer.getByRole("link", { name: "Source 1: Dasara holidays circular 2026" }).first(),
    ).toHaveAttribute("href", "#ask-source-1");
    await expect(
      answer.getByRole("link", { name: "Dasara holidays circular 2026 (open the document)" }),
    ).toHaveAttribute("href", "/en/documents/0192f3a4-0000-7000-8000-00000000d001");
    await expect(answer.getByRole("button", { name: "Download version 1" })).toBeVisible();
    await expectNoAxeViolations(page, "ask answered");
    await expectNoHorizontalOverflow(page, "ask answered");
    await expectVisibleFocusOnEveryStop(page, "ask answered", 80);

    // Save as verified answer: Enter opens it with focus inside, Escape returns focus.
    const save = page.getByRole("button", { name: "Save as verified answer" });
    await pressOn(save, "Enter", "save as verified");
    const dialog = page.getByRole("dialog", { name: "Save a verified answer" });
    await expect(dialog).toBeVisible();
    await expectFocusInsideOpenDialog(page);
    await expect(dialog.getByLabel(/^Source 1/)).toHaveValue(
      "sos://doc/0192f3a4-0000-7000-8000-00000000d001/v1#p1",
    );
    await expectNoAxeViolations(page, "verified dialog");
    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
    await expect(save).toBeFocused();

    // Feedback by keyboard.
    await pressOn(page.getByRole("button", { name: "Yes, helpful" }), "Enter", "helpful");
    await expect(page.getByText(/Thank you\. Your feedback helps/)).toBeVisible();
  });

  test("Stop aborts a slow answer and returns focus to the question", async ({ page }) => {
    await signIn(page, "/en/ask", "clerk");
    const box = page.getByLabel(/^Your question/);
    await box.focus();
    await page.keyboard.type("Answer slowly please");
    await page.keyboard.press("Control+Enter");
    const stop = page.getByRole("button", { name: "Stop" });
    await expect(page.getByText("Writing the answer…")).toBeVisible();
    // The unchecked preview is marked as a draft while it streams.
    await expect(page.getByRole("group", { name: "Draft answer, not checked yet" })).toBeVisible();
    await expectNoAxeViolations(page, "ask preview");
    await pressOn(stop, "Enter", "stop");
    await expect(page.getByText("You stopped the answer.")).toBeVisible();
    await expect(box).toBeFocused();
    await expectNoAxeViolations(page, "ask stopped");
  });

  test("a search-only fallback (final/done status) shows passages, not an answer", async ({
    page,
  }) => {
    await signIn(page, "/en/ask", "clerk");
    const box = page.getByLabel(/^Your question/);
    await box.focus();
    await page.keyboard.type("Dasara dates? (budget)");
    await page.keyboard.press("Control+Enter");
    await expect(page.getByText("The answer is ready.")).toBeVisible();
    const answer = page.getByRole("region", { name: "Answer" });
    await expect(answer.getByText("AI answers are not available right now")).toBeVisible();
    await expect(answer.getByText(/AI budget for this month is used up/)).toBeVisible();
    await expect(answer.getByRole("heading", { name: "Matching passages" })).toBeVisible();
    await expect(
      answer.getByRole("link", { name: "Dasara holidays circular 2026 (open the document)" }),
    ).toBeVisible();
    await expect(answer.getByText("Dasara holidays run")).toHaveCount(0);
    await expect(answer.getByText("Not found in the school records you can access")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Save as verified answer" })).toHaveCount(0);
    await expectNoAxeViolations(page, "ask search-only");
    await expectNoHorizontalOverflow(page, "ask search-only");
  });

  test("search, verified answers and Telugu pages pass axe", async ({ page }) => {
    await signIn(page, "/en/ask/search", "clerk");
    await expect(page.getByRole("heading", { level: 1, name: "Ask the school" })).toBeVisible();
    await page.getByLabel(/^Search for/).focus();
    await page.keyboard.type("Dasara");
    await page.keyboard.press("Enter");
    await expect(page.getByText("1 passage found.")).toBeVisible();
    await expectNoAxeViolations(page, "search results");
    await expectNoHorizontalOverflow(page, "search results");

    for (const [path, proof] of [
      ["/en/ask/verified", "When are the Dasara holidays?"],
      ["/te/ask", "మీ ప్రశ్న"],
      ["/te/ask/verified", "When are the Dasara holidays?"],
    ] as const) {
      await page.goto(path);
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      await expect(page.getByText(proof).first()).toBeVisible();
      await expect(page.getByText("Loading…")).toHaveCount(0);
      await expectNoAxeViolations(page, path);
      await expectNoHorizontalOverflow(page, path);
      if (path.startsWith("/en/")) await expectVisibleFocusOnEveryStop(page, path);
    }
  });
});
