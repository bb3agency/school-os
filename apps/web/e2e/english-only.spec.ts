import { expect, test } from "@playwright/test";
import { signIn } from "./support/a11y-helpers";
import {
  expectNoLocaleLinks,
  expectNoTelugu,
  inTelugu,
  LOCALE_COOKIE,
  selectLanguage,
  teluguOn,
} from "./support/telugu";

/**
 * ADR-0036: English first, Telugu hidden. With SOS_TELUGU_ENABLED off (the default project),
 * an old /te URL lands on the same prefix-less English page, a browser that prefers Telugu or
 * remembers it (NEXT_LOCALE=te) still gets English, and no page shows Telugu text, a language
 * switch, Telugu alternates or the Telugu font. No URL carries a locale in either setting
 * (product owner 2026-09-30). The Telugu project keeps the Telugu checks of the other specs
 * (@telugu).
 */

test.describe("English only while Telugu is switched off (ADR-0036)", () => {
  test.beforeEach(({}, testInfo) => {
    test.skip(teluguOn(testInfo), "checks the Telugu-off default");
  });

  test("old /te and /en URLs answer 308 to the same path without a prefix, keeping the query", async ({
    request,
  }) => {
    for (const [path, target] of [
      ["/te", "/"],
      ["/en", "/"],
      ["/te/welcome", "/welcome"],
      ["/en/welcome", "/welcome"],
      ["/te/signed-out?error=signin_failed", "/signed-out?error=signin_failed"],
      ["/en/students?page=2", "/students?page=2"],
      ["/te/platform/schools", "/platform/schools"],
    ] as const) {
      const response = await request.get(path, { maxRedirects: 0 });
      expect(response.status(), path).toBe(308);
      const location = new URL(response.headers()["location"] ?? "", "http://x");
      expect(`${location.pathname}${location.search}`, path).toBe(target);
      expect(response.headers()["content-security-policy"], path).toContain("default-src 'self'");
      expect(response.headers()["x-content-type-options"], path).toBe("nosniff");
      // While Telugu is off an old /te link stores nothing: English whatever it said.
      expect(response.headers()["set-cookie"] ?? "", path).not.toContain(LOCALE_COOKIE);
    }
  });

  test("an old prefix never becomes an open redirect", async ({ request }) => {
    const origin = new URL(test.info().project.use.baseURL ?? "http://localhost:3000").origin;
    for (const start of ["/en//evil.example", "/te//evil.example/x", "/en/%2F%2Fevil.example"]) {
      let path = start;
      for (let hop = 0; hop < 5; hop += 1) {
        const response = await request.get(path, { maxRedirects: 0 });
        if (response.status() < 300 || response.status() >= 400) break;
        const location = response.headers()["location"] ?? "";
        const target = new URL(location, `${origin}${path}`);
        expect(target.origin, `${start} → ${location}`).toBe(origin);
        expect(location.startsWith("//"), `${start} → ${location}`).toBe(false);
        path = `${target.pathname}${target.search}`;
      }
    }
  });

  test("a Telugu browser with a remembered te cookie still gets English, at prefix-less URLs", async ({
    browser,
  }) => {
    const context = await browser.newContext({ locale: "te-IN" });
    await context.addCookies([
      { name: LOCALE_COOKIE, value: "te", url: test.info().project.use.baseURL ?? "" },
    ]);
    const page = await context.newPage();
    const response = await page.goto("/");
    await expect(page).toHaveURL((url) => url.pathname === "/welcome");
    await expect(page.locator("html")).toHaveAttribute("lang", "en");
    expect(response?.headers()["link"] ?? "").not.toMatch(/hreflang="te"|\/te[/>]/);
    await expectNoTelugu(page, "welcome for a Telugu browser");
    await expectNoLocaleLinks(page, "welcome for a Telugu browser");
    // An old /te link lands on the same page without a prefix, still English.
    await page.goto("/te/features?from=bookmark");
    await expect(page).toHaveURL(
      (url) => `${url.pathname}${url.search}` === "/features?from=bookmark",
    );
    await expect(page.locator("html")).toHaveAttribute("lang", "en");
    await expectNoTelugu(page, "features for a Telugu browser");
    await context.close();
  });

  test("the Telugu font is never served", async ({ request }) => {
    for (const file of ["noto-sans-telugu.css", "noto-sans-telugu-telugu-400-normal.woff2"]) {
      expect((await request.get(`/fonts/telugu/${file}`)).status(), file).toBe(404);
    }
  });

  test("public pages show no Telugu and no locale in any link", async ({ page }) => {
    await selectLanguage(page, "te");
    for (const path of [
      "/welcome",
      "/signed-out?error=signin_failed",
      "/signed-out?kind=operator",
    ]) {
      await page.goto(path);
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      await expectNoTelugu(page, path);
      await expectNoLocaleLinks(page, path);
    }
  });
});

test.describe("English only, signed in (stand-in IdP, ADR-0036)", () => {
  test.skip(process.env.E2E_STAND_IN !== "1", "set E2E_STAND_IN=1 (needs Valkey at REDIS_URL)");
  test.beforeEach(({}, testInfo) => {
    test.skip(teluguOn(testInfo), "checks the Telugu-off default");
  });

  test("school pages: no Telugu fields, columns, previews or switch; a te cookie changes nothing", async ({
    page,
  }, testInfo) => {
    test.setTimeout(120_000);
    await signIn(page, "/settings/structure", "clerk");
    await expect(page).toHaveURL((url) => url.pathname === "/settings/structure");
    for (const [path, proof] of [
      ["/settings/structure", "Class 6"],
      ["/settings/school", "STATE_AP, CBSE"],
      ["/settings/users", "Synthetic Teacher"],
      ["/settings/users/new", "Invite"],
      ["/documents", "Dasara holidays circular 2026"],
      ["/documents/new", "Upload"],
      ["/ask", "Your question"],
      ["/ask/memory", "I work in the school office"],
      ["/support", "Support"],
    ] as const) {
      await page.goto(path);
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      await expect(page.getByText(proof).first()).toBeAttached();
      await expect(page.getByText("Loading…")).toHaveCount(0);
      await expectNoTelugu(page, path);
      await expectNoLocaleLinks(page, path);
      expect(await page.locator("input[name$='_te'], textarea[name$='_te']").count(), path).toBe(0);
    }
    // Add class: no Telugu name field.
    await page.goto("/settings/structure");
    await page.getByRole("button", { name: "Add class" }).click();
    const dialog = page.getByRole("dialog", { name: "Add class" });
    await expect(dialog.getByLabel("Name in English")).toBeVisible();
    await expect(dialog.getByLabel("Name in Telugu")).toHaveCount(0);
    await page.keyboard.press("Escape");

    for (const path of ["/settings/structure", "/settings/users", "/ask"]) {
      expect(await inTelugu(page, path, testInfo), path).toBe(false);
    }
    // An old /te link to a school page lands on the prefix-less English page.
    await page.goto("/te/settings/users?page=1");
    await expect(page).toHaveURL(
      (url) => `${url.pathname}${url.search}` === "/settings/users?page=1",
    );
    await expectNoTelugu(page, "/te/settings/users");
  });

  test("platform pages: no switch, no Telugu banner fields; a te cookie changes nothing", async ({
    page,
  }, testInfo) => {
    await signIn(page, "/platform", "operator-1");
    await expect(page.getByRole("heading", { level: 1, name: "Platform dashboard" })).toBeVisible();
    await expect(page).toHaveURL((url) => url.pathname === "/platform");
    for (const path of ["/platform", "/platform/announcements", "/platform/provision"]) {
      await page.goto(path);
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      await expectNoTelugu(page, path);
      await expectNoLocaleLinks(page, path);
    }
    expect(await inTelugu(page, "/platform/schools", testInfo)).toBe(false);
  });
});
