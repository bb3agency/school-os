import { expect, test } from "@playwright/test";
import { signIn } from "./support/a11y-helpers";
import { expectNoTelugu, openTelugu, teluguOn } from "./support/telugu";

/**
 * ADR-0036: English first, Telugu hidden. With SOS_TELUGU_ENABLED off (the default project),
 * every /te URL lands on the same English page, a browser that prefers Telugu or remembers it
 * still gets English, and no page shows Telugu text, a language switch, Telugu alternates or
 * the Telugu font. The Telugu project keeps the Telugu checks of the other specs (@telugu).
 */

test.describe("English only while Telugu is switched off (ADR-0036)", () => {
  test.beforeEach(({}, testInfo) => {
    test.skip(teluguOn(testInfo), "checks the Telugu-off default");
  });

  test("/te URLs redirect to the same English page, keeping the query", async ({ request }) => {
    for (const [path, target] of [
      ["/te", "/en"],
      ["/te/welcome", "/en/welcome"],
      ["/te/signed-out?error=signin_failed", "/en/signed-out?error=signin_failed"],
      ["/te/students?page=2", "/en/students?page=2"],
      ["/te/platform/schools", "/en/platform/schools"],
    ] as const) {
      const response = await request.get(path, { maxRedirects: 0 });
      expect(response.status(), path).toBe(307);
      const location = new URL(response.headers()["location"] ?? "", "http://x");
      expect(`${location.pathname}${location.search}`, path).toBe(target);
      expect(response.headers()["content-security-policy"], path).toContain("default-src 'self'");
    }
  });

  test("a Telugu browser or a remembered te cookie still gets English, with no te alternates", async ({
    browser,
  }) => {
    const context = await browser.newContext({ locale: "te-IN" });
    await context.addCookies([
      { name: "NEXT_LOCALE", value: "te", url: test.info().project.use.baseURL ?? "" },
    ]);
    const page = await context.newPage();
    const response = await page.goto("/");
    await expect(page).toHaveURL(/\/en\/welcome$/);
    await expect(page.locator("html")).toHaveAttribute("lang", "en");
    expect(response?.headers()["link"] ?? "").not.toMatch(/hreflang="te"|\/te[/>]/);
    await expectNoTelugu(page, "welcome for a Telugu browser");
    await context.close();
  });

  test("the Telugu font is never served", async ({ request }) => {
    for (const file of ["noto-sans-telugu.css", "noto-sans-telugu-telugu-400-normal.woff2"]) {
      expect((await request.get(`/fonts/telugu/${file}`)).status(), file).toBe(404);
    }
  });

  test("public pages show no Telugu", async ({ page }) => {
    for (const path of [
      "/en/welcome",
      "/en/signed-out?error=signin_failed",
      "/en/signed-out?kind=operator",
    ]) {
      await page.goto(path);
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      await expectNoTelugu(page, path);
    }
  });
});

test.describe("English only, signed in (stand-in IdP, ADR-0036)", () => {
  test.skip(process.env.E2E_STAND_IN !== "1", "set E2E_STAND_IN=1 (needs Valkey at REDIS_URL)");
  test.beforeEach(({}, testInfo) => {
    test.skip(teluguOn(testInfo), "checks the Telugu-off default");
  });

  test("school pages: no Telugu fields, columns, previews or switch; /te lands on English", async ({
    page,
  }, testInfo) => {
    test.setTimeout(120_000);
    await signIn(page, "/en/settings/structure", "clerk");
    await expect(page).toHaveURL(/\/en\/settings\/structure$/);
    for (const [path, proof] of [
      ["/en/settings/structure", "Class 6"],
      ["/en/settings/school", "STATE_AP, CBSE"],
      ["/en/settings/users", "Synthetic Teacher"],
      ["/en/settings/users/new", "Invite"],
      ["/en/documents", "Dasara holidays circular 2026"],
      ["/en/documents/new", "Upload"],
      ["/en/ask", "Your question"],
      ["/en/ask/memory", "I work in the school office"],
      ["/en/support", "Support"],
    ] as const) {
      await page.goto(path);
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      await expect(page.getByText(proof).first()).toBeAttached();
      await expect(page.getByText("Loading…")).toHaveCount(0);
      await expectNoTelugu(page, path);
      expect(await page.locator("input[name$='_te'], textarea[name$='_te']").count(), path).toBe(0);
    }
    // Add class: no Telugu name field.
    await page.goto("/en/settings/structure");
    await page.getByRole("button", { name: "Add class" }).click();
    const dialog = page.getByRole("dialog", { name: "Add class" });
    await expect(dialog.getByLabel("Name in English")).toBeVisible();
    await expect(dialog.getByLabel("Name in Telugu")).toHaveCount(0);
    await page.keyboard.press("Escape");

    for (const path of ["/te/settings/structure", "/te/settings/users", "/te/ask"]) {
      await openTelugu(page, path, testInfo);
    }
  });

  test("platform pages: no switch, no Telugu banner fields; /te lands on English", async ({
    page,
  }, testInfo) => {
    await signIn(page, "/en/platform", "operator-1");
    await expect(page.getByRole("heading", { level: 1, name: "Platform dashboard" })).toBeVisible();
    for (const path of ["/en/platform", "/en/platform/announcements", "/en/platform/provision"]) {
      await page.goto(path);
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      await expectNoTelugu(page, path);
    }
    await openTelugu(page, "/te/platform/schools", testInfo);
  });
});
