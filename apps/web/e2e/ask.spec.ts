import { expect, test } from "@playwright/test";
import {
  expectFocusInsideOpenDialog,
  expectNoAxeViolations,
  expectNoHorizontalOverflow,
  expectVisibleFocusOnEveryStop,
  pressOn,
  signIn,
} from "./support/a11y-helpers";
import { expectNoTelugu, openTelugu, TELUGU, TELUGU_OFF_REASON, teluguOn } from "./support/telugu";

/**
 * Ask the school as a chat (US-801, US-802, FR-KB-005..012, FR-KB-030): the synthetic SSE
 * answer of the stand-in API (e2e/support/stand-in.ts, ask-api.ts) streamed through the real
 * BFF, keyboard only, with axe (WCAG 2.2 AA) and 1366×768 / 375×812 checks (NFR-A11Y-001).
 * Needs E2E_STAND_IN=1 and Valkey.
 */
test.describe("Ask the school: chat, keyboard and axe (stand-in IdP)", () => {
  test.skip(process.env.E2E_STAND_IN !== "1", "set E2E_STAND_IN=1 (needs Valkey at REDIS_URL)");
  test.describe.configure({ mode: "serial" });

  test.beforeEach(async ({ request }) => {
    // Conversations and memory start empty for each test (stand-in state).
    await request.post(`http://localhost:${process.env.E2E_API_PORT ?? 8099}/__e2e/reset`);
  });

  test("ask by keyboard, read the streamed and checked answer with sources, follow up", async ({
    page,
  }) => {
    await signIn(page, "/en/ask", "clerk");
    await expect(page).toHaveURL(/\/en\/ask$/);
    await expect(page.getByRole("heading", { level: 1, name: "Ask the school" })).toBeVisible();
    await expect(page.getByText(/^Good (morning|afternoon|evening), /)).toBeVisible();
    const menu = page.getByRole("navigation", { name: "Main" });
    await expect(menu.getByRole("link", { name: "Ask the school" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    await expect(menu.getByRole("link", { name: "New chat" })).toBeVisible();
    await expectNoAxeViolations(page, "ask empty");
    await expectNoHorizontalOverflow(page, "ask empty");

    // Keyboard only: "/" moves to the box, Enter sends.
    await page.locator("body").click({ position: { x: 5, y: 5 } });
    await page.keyboard.press("/");
    const box = page.getByLabel(/^Your question/);
    await expect(box).toBeFocused();
    await page.keyboard.type("When are the Dasara holidays?");
    await page.keyboard.press("Enter");

    // The URL follows the new conversation; the thread shows the question and the answer.
    await expect(page).toHaveURL(/\/en\/ask\/c\/[0-9a-f-]{36}$/);
    const log = page.getByRole("log", { name: "Conversation" });
    await expect(log.getByText("When are the Dasara holidays?")).toBeVisible();
    await expect(page.getByText("The answer is ready.")).toBeAttached();
    const answer = log.getByRole("article", { name: "Answer" });
    await expect(answer.getByText(/Dasara holidays run from/)).toBeVisible();
    await expect(answer.locator("strong", { hasText: "02/10/2026" })).toBeVisible();
    await expect(answer.getByText(/Worked for \d+ seconds? · 1 source/)).toBeVisible();
    await expect(
      answer.getByRole("link", { name: "Source 1: Dasara holidays circular 2026" }).first(),
    ).toHaveAttribute("href", /#ask-.+-source-1$/);
    await expect(
      answer.getByRole("link", { name: "Dasara holidays circular 2026 (open the document)" }),
    ).toHaveAttribute("href", "/en/documents/0192f3a4-0000-7000-8000-00000000d001");
    await expect(answer.getByRole("button", { name: "Download version 1" })).toBeVisible();
    // The new chat is listed in the sidebar and marked as the current page.
    await expect(menu.getByRole("list", { name: "Recent" }).getByRole("link")).toHaveAttribute(
      "aria-current",
      "page",
    );
    await expect(menu.locator('a[aria-current="page"]')).toHaveCount(1);
    await expectNoAxeViolations(page, "ask answered");
    await expectNoHorizontalOverflow(page, "ask answered");
    await expectVisibleFocusOnEveryStop(page, "ask answered", 80);

    // A citation chip shows its source on focus; Escape closes it.
    const chip = answer
      .getByRole("link", { name: "Source 1: Dasara holidays circular 2026" })
      .first();
    await chip.focus();
    await expect(
      page.getByRole("link", { name: /^Open: Dasara holidays circular 2026/ }),
    ).toBeVisible();
    await expectNoAxeViolations(page, "citation popover");
    await page.keyboard.press("Escape");
    await expect(
      page.getByRole("link", { name: /^Open: Dasara holidays circular 2026/ }),
    ).toHaveCount(0);
    await expect(chip).toBeFocused();

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

    // A follow-up chip asks at once in the same conversation.
    const followups = page.getByRole("group", { name: "Suggested next questions" });
    await pressOn(
      followups.getByRole("button", { name: "When does school reopen after Dasara?" }),
      "Enter",
      "follow-up",
    );
    await expect(log.getByRole("article", { name: "Answer" })).toHaveCount(2);
    await expect(page.getByText("The answer is ready.")).toBeAttached();
    await expectNoAxeViolations(page, "ask follow-up");
  });

  test("Stop aborts a slow answer, keeps the draft marked Stopped and returns focus", async ({
    page,
  }) => {
    await signIn(page, "/en/ask", "clerk");
    const box = page.getByLabel(/^Your question/);
    await box.focus();
    await page.keyboard.type("Answer slowly please");
    await page.keyboard.press("Enter");
    const stop = page.getByRole("button", { name: "Stop" });
    await expect(stop).toBeVisible();
    // The live status line says what is happening (shimmer, still under reduced motion).
    await expect(page.getByText("Understanding your question…").first()).toBeVisible();
    await expect(page.getByRole("group", { name: "Draft answer, not checked yet" })).toBeVisible({
      timeout: 15_000,
    });
    await expectNoAxeViolations(page, "ask preview");
    await pressOn(stop, "Enter", "stop");
    await expect(page.getByText("You stopped the answer.")).toBeAttached();
    await expect(page.getByText("Stopped", { exact: true })).toBeVisible();
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
    await page.keyboard.press("Enter");
    await expect(page.getByText("The answer is ready.")).toBeAttached();
    const answer = page.getByRole("article", { name: "Answer" });
    await expect(answer.getByText("AI answers are not available right now")).toBeVisible();
    await expect(answer.getByText(/AI budget for this month is used up/)).toBeVisible();
    await expect(answer.getByRole("heading", { name: "Matching passages" })).toBeVisible();
    await expect(
      answer.getByRole("link", { name: "Dasara holidays circular 2026 (open the document)" }),
    ).toBeVisible();
    await expect(answer.getByText("Dasara holidays run")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Save as verified answer" })).toHaveCount(0);
    await expectNoAxeViolations(page, "ask search-only");
    await expectNoHorizontalOverflow(page, "ask search-only");
  });

  test("All chats: find, pin, rename and delete by keyboard", async ({ page }) => {
    await signIn(page, "/en/ask", "clerk");
    const box = page.getByLabel(/^Your question/);
    await box.focus();
    await page.keyboard.type("When are the Dasara holidays?");
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/\/en\/ask\/c\//);
    await expect(page.getByText("The answer is ready.")).toBeAttached();

    await page.goto("/en/ask/history");
    await expect(page.getByRole("heading", { level: 1, name: "All chats" })).toBeVisible();
    await expect(
      page.locator("#main").getByRole("link", { name: "Dasara holidays" }),
    ).toBeVisible();
    await expectNoAxeViolations(page, "all chats");
    await expectVisibleFocusOnEveryStop(page, "all chats");

    await pressOn(page.getByRole("button", { name: "Pin Dasara holidays" }), "Enter", "pin");
    await expect(page.getByRole("region", { name: "Pinned" })).toBeVisible();

    await pressOn(page.getByRole("button", { name: "Rename Dasara holidays" }), "Enter", "rename");
    const rename = page.getByRole("dialog", { name: "Rename" });
    await expect(rename).toBeVisible();
    await rename.getByLabel("Chat title").fill("Holiday dates");
    await page.keyboard.press("Enter");
    await expect(page.locator("#main").getByRole("link", { name: "Holiday dates" })).toBeVisible();

    await page.getByLabel("Find a chat").fill("zzz");
    await expect(page.getByText("No chats match")).toBeVisible();
    await page.getByLabel("Find a chat").fill("");

    await pressOn(page.getByRole("button", { name: "Delete Holiday dates" }), "Enter", "delete");
    const confirm = page.getByRole("dialog", { name: "Delete this chat?" });
    await expect(confirm).toBeVisible();
    await expectNoAxeViolations(page, "delete dialog");
    await confirm.getByRole("button", { name: "Delete chat" }).click();
    await expect(page.getByText("No chats yet")).toBeVisible();
  });

  test("Manage memory: add, refused text explained, forget everything", async ({ page }) => {
    await signIn(page, "/en/ask/memory", "clerk");
    await expect(page.getByRole("heading", { level: 1, name: "Memory" })).toBeVisible();
    await expect(
      page.getByText("I work in the school office and prepare circulars."),
    ).toBeVisible();
    await expect(page.getByRole("switch", { name: "Use memory in Ask" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    await expectNoAxeViolations(page, "memory");
    await expectVisibleFocusOnEveryStop(page, "memory");

    const add = page.getByLabel("Add something to remember");
    await add.fill("I prepare the UDISE+ upload every September.");
    await page.getByRole("button", { name: "Remember" }).click();
    await expect(page.getByText("I prepare the UDISE+ upload every September.")).toBeVisible();

    await add.fill("Class 6 Ravi has asthma");
    await page.getByRole("button", { name: "Remember" }).click();
    await expect(page.getByText("This can't be remembered")).toBeVisible();
    await expect(add).toHaveValue("Class 6 Ravi has asthma");

    await page.getByRole("button", { name: "Forget everything" }).click();
    const dialog = page.getByRole("dialog", { name: "Forget everything?" });
    await expect(dialog).toBeVisible();
    await dialog.getByRole("button", { name: "Forget everything" }).click();
    await expect(page.getByText("Nothing remembered yet")).toBeVisible();
  });

  test("phone (375×812): the chat, recents in the menu drawer, English", async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 });
    await signIn(page, "/en/ask", "clerk");
    const box = page.getByLabel(/^Your question/);
    await expect(box).toBeVisible();
    await expectNoHorizontalOverflow(page, "ask phone en");
    await box.fill("When are the Dasara holidays?");
    await page.getByRole("button", { name: "Ask", exact: true }).click();
    await expect(page).toHaveURL(/\/en\/ask\/c\//);
    await expect(page.getByText("The answer is ready.")).toBeAttached();
    await expectNoHorizontalOverflow(page, "ask answered phone en");
    await expectNoAxeViolations(page, "ask answered phone en");
    const boxBottom = await box.evaluate((node) => node.getBoundingClientRect().bottom);
    expect(boxBottom).toBeLessThanOrEqual(812);

    await page.getByRole("button", { name: "Menu", exact: true }).click();
    const drawer = page.getByRole("dialog");
    await expect(drawer.getByRole("link", { name: "New chat" })).toBeVisible();
    await expect(drawer.getByRole("list", { name: "Recent" }).getByRole("link")).not.toHaveCount(0);
    await expectNoAxeViolations(page, "drawer recents en");
  });

  test("with Telugu off: Ask shows no Telugu and /te/ask lands on English (ADR-0036)", async ({
    page,
  }, testInfo) => {
    test.skip(teluguOn(testInfo), "checks the Telugu-off default");
    await signIn(page, "/en/ask", "clerk");
    await expect(page.getByLabel(/^Your question/)).toBeVisible();
    await expectNoTelugu(page, "/en/ask");
    await openTelugu(page, "/te/ask", testInfo);
    await openTelugu(page, "/te/ask/verified", testInfo);
  });

  test(`phone (375×812): the chat, recents in the menu drawer, Telugu ${TELUGU}`, async ({
    page,
  }, testInfo) => {
    test.skip(!teluguOn(testInfo), TELUGU_OFF_REASON);
    await page.setViewportSize({ width: 375, height: 812 });
    await signIn(page, "/te/ask", "clerk");
    const box = page.getByLabel(/^మీ ప్రశ్న/);
    await expect(box).toBeVisible();
    await expectNoHorizontalOverflow(page, "ask phone te");
    await box.fill("దసరా సెలవులు ఎప్పుడు?");
    await page.getByRole("button", { name: "అడగండి" }).click();
    await expect(page).toHaveURL(/\/te\/ask\/c\//);
    await expect(page.getByText("సమాధానం సిద్ధంగా ఉంది.")).toBeAttached();
    await expectNoHorizontalOverflow(page, "ask answered phone te");
    await expectNoAxeViolations(page, "ask answered phone te");
    // The composer stays on screen at the bottom.
    const boxBottom = await box.evaluate((node) => node.getBoundingClientRect().bottom);
    expect(boxBottom).toBeLessThanOrEqual(812);

    await page.getByRole("button", { name: "మెనూ" }).click();
    const drawer = page.getByRole("dialog");
    await expect(drawer.getByRole("link", { name: "కొత్త చాట్" })).toBeVisible();
    await expect(drawer.getByRole("list", { name: "ఇటీవలివి" }).getByRole("link")).toHaveCount(1);
    await expectNoAxeViolations(page, "drawer recents te");
  });

  test(`search, verified answers and Telugu pages pass axe ${TELUGU}`, async ({
    page,
  }, testInfo) => {
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
      // ADR-0036: /te pages only while Telugu is on; otherwise they must land on English.
      if (!path.startsWith("/te")) await page.goto(path);
      else if (!(await openTelugu(page, path, testInfo))) continue;
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      await expect(page.getByText(proof).first()).toBeAttached();
      await expect(page.getByText("Loading…")).toHaveCount(0);
      await expectNoAxeViolations(page, path);
      await expectNoHorizontalOverflow(page, path);
      if (path.startsWith("/en/")) await expectVisibleFocusOnEveryStop(page, path);
    }
  });
});
