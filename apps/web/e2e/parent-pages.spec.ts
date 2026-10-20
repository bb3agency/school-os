import { expect, test, type BrowserContext, type Page, type Response } from "@playwright/test";
import {
  expectFocusRing,
  expectNoAxeViolations,
  expectNoHorizontalOverflow,
  pressOn,
} from "./support/a11y-helpers";
import { PARENT_NOTICE, PARENT_SCHOOL, PARENT_TOKENS, PAY_REDIRECT } from "./support/r2-api";
import { API_PORT } from "./support/stand-in";

/**
 * Parent link pages on a phone, 375×812 (roadmap R2-3I; ADR-0046, ADR-0044; FR-PUB-008..010,
 * FR-PAY-005, FR-LANG-003; SEC-036; docs/17 §5.8): the notice, the fees due with "Pay online"
 * (the payment provider in fake/stub mode) and the opt-in page ("Read messages in"). Every page:
 * the token from `/p/{token}` is exchanged for the parent cookie and leaves the address bar at
 * once (303 to /p/view), every /p/* document answers `Referrer-Policy: no-referrer`, no
 * horizontal scroll, no WCAG 2.2 AA axe violations. Owner decision D32: the opt-in page has NO
 * "STOP" prompt and no opt-out button.
 *
 * The parent pages are served only on PUBLIC_BASE_URL (src/lib/public-surface.ts). Locally it
 * may be the staff origin itself (plain http on a loopback host), so:
 *
 * 1. Stand-in (synthetic, no database; the default way to run this file):
 *
 *      E2E_STAND_IN=1 PUBLIC_BASE_URL=http://localhost:3000 npx playwright test parent-pages
 *
 *    `PUBLIC_BASE_URL` reaches the `next start` that playwright.config.ts launches (Playwright
 *    passes its own environment to the web server). The canned API (e2e/support/r2-api.ts)
 *    answers `/api/v1/public/links/*` for three fixed synthetic tokens (PARENT_TOKENS) and hands
 *    out an https://rzp.io page for "Pay online", which this spec answers itself.
 *
 * 2. A running local stack seeded with `make seed-synthetic` (never real data, CLAUDE.md
 *    invariant 11), payments in fake mode (`SOS_PAYMENTS_PROVIDER_MODE=fake`): set
 *    E2E_BASE_URL, E2E_PUBLIC_BASE_URL (or PUBLIC_BASE_URL) and one token per page:
 *    E2E_PARENT_NOTICE_TOKEN, E2E_PARENT_DUES_TOKEN, E2E_PARENT_OPTIN_TOKEN. A page whose token
 *    is missing is skipped. Mint them from apps/api against the local seeded database (a link is
 *    shown once; the database keeps only its hash), for example for the opt-in page:
 *
 *      cd apps/api && uv run python -c "
 *      from sqlalchemy import text
 *      from app.core.db import tenant_session
 *      from app.public.service import issue_parent_link
 *      TENANT = '<a synthetic school id from make seed-synthetic>'
 *      with tenant_session(TENANT) as s:
 *          g = s.execute(text('select id from sis.guardians limit 1')).scalar_one()
 *          print(issue_parent_link(s, kind='parent_optin', target_id=g, guardian_id=g).key)
 *      "
 *
 *    For the notice use kind='parent_notice' with target_id = an approved ops.parent_notices id;
 *    for the dues kind='parent_dues' with target_id = a fin.student_fee_accounts id and
 *    guardian_id = a guardian of that account's student (sis.student_guardians). "Pay online"
 *    shows only when the school's payments are connected (fake mode); the fake provider's page
 *    (https://rzp.example.test) is accepted by the web app only under `next dev`, so run the web
 *    in dev mode for the pay step, or it answers "could not be opened" and the step fails.
 *    Content checks that need the canned data run only in mode 1.
 *
 * The specs never touch real Razorpay: every pay host is answered by `context.route`.
 */

type Kind = keyof typeof PARENT_TOKENS;

const ENV_TOKEN: Record<Kind, string> = {
  notice: "E2E_PARENT_NOTICE_TOKEN",
  dues: "E2E_PARENT_DUES_TOKEN",
  optin: "E2E_PARENT_OPTIN_TOKEN",
};

const standIn = process.env.E2E_STAND_IN === "1";
const PUBLIC_BASE = (process.env.E2E_PUBLIC_BASE_URL ?? process.env.PUBLIC_BASE_URL ?? "").replace(
  /\/+$/,
  "",
);

/** The link token for a page: from the environment (a real stack) or the stand-in's fixed one. */
function tokenFor(kind: Kind): string | undefined {
  return process.env[ENV_TOKEN[kind]] || (standIn ? PARENT_TOKENS[kind] : undefined);
}

/** The canned synthetic content is on the page (stand-in token, not one from the environment). */
function canned(kind: Kind): boolean {
  return standIn && !process.env[ENV_TOKEN[kind]];
}

const PAY_HOST = /^https:\/\/(?:[a-z0-9-]+\.)*(?:rzp\.io|razorpay\.com|rzp\.example\.test)\//;
/** A token that no link has (43 base64url characters, like a real one). */
const UNKNOWN_TOKEN = `e2e-unknown-link-${"z".repeat(43)}`.slice(0, 43);

/** Every /p/* document response of the page, for the header checks. */
function recordDocuments(page: Page): Response[] {
  const seen: Response[] = [];
  page.on("response", (response) => {
    const url = new URL(response.url());
    if (response.request().resourceType() === "document" && url.pathname.startsWith("/p/")) {
      seen.push(response);
    }
  });
  return seen;
}

function expectNoReferrer(responses: Response[], label: string) {
  expect(responses.length, `${label}: /p/* documents seen`).toBeGreaterThan(0);
  for (const response of responses) {
    const path = new URL(response.url()).pathname;
    expect(response.headers()["referrer-policy"], `${label}: Referrer-Policy on ${path}`).toBe(
      "no-referrer",
    );
  }
}

/**
 * Opens `/p/{token}`: one 303 to /p/view that sets the HttpOnly parent cookie; the final URL,
 * the history entry and the page never carry the token.
 */
async function openLink(page: Page, context: BrowserContext, token: string): Promise<Response[]> {
  const documents = recordDocuments(page);
  const response = await page.goto(`${PUBLIC_BASE}/p/${token}`);
  expect(response, "the parent page answered").not.toBeNull();
  if (!response) return documents;
  expect(response.status()).toBe(200);
  expect(new URL(page.url()).pathname).toBe("/p/view");
  expect(page.url()).not.toContain(token);

  const exchange = response.request().redirectedFrom();
  expect(exchange, "/p/{token} redirected").not.toBeNull();
  const exchanged = await exchange?.response();
  expect(exchanged?.status()).toBe(303);
  expect(exchanged?.headers().location).toMatch(/(?:^|\/\/[^/]+)\/p\/view$/);
  expect(exchanged?.headers()["referrer-policy"]).toBe("no-referrer");
  expect(response.headers()["cache-control"]).toContain("no-store");

  // The token is in no URL the page can see, and only in the HttpOnly cookie.
  expect(await page.evaluate(() => location.href)).not.toContain(token);
  expect(await page.content()).not.toContain(token);
  const cookie = (await context.cookies(PUBLIC_BASE)).find((c) =>
    /^(?:__Host-)?sos_parent$/.test(c.name),
  );
  expect(cookie, "parent cookie set").toBeDefined();
  expect(cookie?.httpOnly).toBe(true);
  expect(cookie?.sameSite).toBe("Lax");
  return documents;
}

/** The page frame: kicker, one h1, the forwarding warning; axe; no sideways scroll at 375px. */
async function checkParentPage(page: Page, kicker: string, label: string) {
  await expect(page.getByRole("main")).toBeVisible();
  await expect(page.getByText(kicker, { exact: true }).first()).toBeVisible();
  await expect(page.getByRole("heading", { level: 1 })).toHaveCount(1);
  await expect(
    page.getByText("Anyone with this link can see this page. Do not forward it."),
  ).toBeVisible();
  await expectNoAxeViolations(page, label);
  await expectNoHorizontalOverflow(page, label);
}

test.describe.serial("parent link pages on a 375px phone (FR-PUB-010, ADR-0046)", () => {
  test.skip(
    !PUBLIC_BASE,
    "set PUBLIC_BASE_URL (or E2E_PUBLIC_BASE_URL) to the public origin, e.g. http://localhost:3000",
  );
  test.use({ viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true });

  test.beforeAll(async ({ request }) => {
    if (!standIn) return;
    const reset = await request.post(`http://localhost:${API_PORT}/__e2e/reset`);
    expect(reset.status()).toBe(204);
  });

  test.beforeEach(async ({ context }) => {
    // Razorpay is never reached: its hosted pages (and the fake provider's) are answered here.
    await context.route(PAY_HOST, (route) =>
      route.fulfill({
        status: 200,
        contentType: "text/html; charset=utf-8",
        body: "<!doctype html><title>Synthetic payment page</title><h1>Synthetic payment page</h1>",
      }),
    );
  });

  test("a link that does not open: the uniform expired page, token gone (FR-PUB-010)", async ({
    page,
  }) => {
    test.skip(!standIn && !process.env.E2E_BASE_URL, "needs the stand-in or a running stack");
    const documents = recordDocuments(page);
    const response = await page.goto(`${PUBLIC_BASE}/p/${UNKNOWN_TOKEN}`);
    expect(response?.status()).toBe(404);
    expect(new URL(page.url()).pathname).toBe("/p/expired");
    expect(page.url()).not.toContain(UNKNOWN_TOKEN);
    await expect(
      page.getByRole("heading", { level: 1, name: "This link has expired or is not valid" }),
    ).toBeVisible();
    // No school is named on the expired page.
    await expect(page.getByText(PARENT_SCHOOL)).toHaveCount(0);
    await expectNoHorizontalOverflow(page, "/p/expired");
    expectNoReferrer(documents, "/p/expired");
  });

  test("notice page (FR-PUB-008)", async ({ page, context }) => {
    const token = tokenFor("notice");
    test.skip(!token, `set ${ENV_TOKEN.notice} or E2E_STAND_IN=1`);
    const documents = await openLink(page, context, token ?? "");
    await checkParentPage(page, "Notice from the school", "/p/view notice");
    if (canned("notice")) {
      await expect(
        page.getByRole("heading", { level: 1, name: PARENT_NOTICE.title }),
      ).toBeVisible();
      await expect(page.getByText(PARENT_NOTICE.firstParagraph)).toBeVisible();
      await expect(page.getByText(`From ${PARENT_SCHOOL}, sent through SchoolOS`)).toBeVisible();
      // The PDF is a plain same-origin link that carries no token.
      await expect(page.getByRole("link", { name: "Open the PDF" })).toHaveAttribute(
        "href",
        "/p/document",
      );
    }
    expectNoReferrer(documents, "notice");
  });

  test("fees due page: Pay online goes to the payment page with no token or referrer (FR-PAY-005)", async ({
    page,
    context,
  }) => {
    const token = tokenFor("dues");
    test.skip(!token, `set ${ENV_TOKEN.dues} or E2E_STAND_IN=1`);
    const documents = await openLink(page, context, token ?? "");
    await checkParentPage(page, "Fees due", "/p/view dues");
    await expect(page.getByRole("heading", { level: 1, name: /^Fees due/ })).toBeVisible();
    await expect(page.getByText("This page never asks for card or bank details.")).toBeVisible();

    const pay = page.getByRole("button", { name: /online/ });
    if (canned("dues")) {
      await expect(
        page.getByRole("heading", { level: 1, name: "Fees due for Lakshmi" }),
      ).toBeVisible();
      await expect(page.getByText("Total due", { exact: true })).toBeVisible();
      await expect(page.getByText("₹19,600.00")).toBeVisible();
      // Two instalments: both ticked; untick term 2 with the keyboard.
      const choose = page.getByRole("group", { name: "Choose what to pay" });
      const term1 = choose.getByRole("checkbox", { name: /Term 1/ });
      const term2 = choose.getByRole("checkbox", { name: /Term 2/ });
      await expect(term1).toBeChecked();
      await expect(term2).toBeChecked();
      await pressOn(term2, "Space", "term 2 checkbox");
      await expect(term2).not.toBeChecked();
      await expect(pay).toHaveText("Pay the ticked fees online");
    } else if ((await pay.count()) === 0) {
      test.skip(true, "Pay online is off for this school: connect payments in fake mode");
    }

    // "Pay online": a plain form post with the CSRF token, then 303 to the payment page.
    const posted = page.waitForRequest(
      (request) => request.method() === "POST" && new URL(request.url()).pathname === "/p/pay",
    );
    const leaving = page.waitForRequest((request) => PAY_HOST.test(request.url()));
    await pressOn(pay, "Enter", "Pay online");
    const form = new URLSearchParams((await posted).postData() ?? "");
    expect(form.get("csrf")).toBeTruthy();
    if (canned("dues")) expect(form.getAll("instalment")).toEqual(["1"]);
    const outbound = await leaving;
    expect(outbound.url()).not.toContain(token ?? "");
    // No Referer: the payment provider never learns the parent page's address.
    expect(outbound.headers().referer).toBeUndefined();
    await expect(page).toHaveURL(PAY_HOST);
    if (canned("dues")) expect(page.url()).toBe(PAY_REDIRECT);
    expectNoReferrer(documents, "dues");
  });

  test("opt-in page: Read messages in, saved, and no STOP prompt or opt-out button (D32, FR-LANG-003)", async ({
    page,
    context,
  }) => {
    const token = tokenFor("optin");
    test.skip(!token, `set ${ENV_TOKEN.optin} or E2E_STAND_IN=1`);
    const documents = await openLink(page, context, token ?? "");
    await checkParentPage(page, "School messages on WhatsApp", "/p/view opt-in");
    await expect(
      page.getByRole("heading", { level: 1, name: "School messages on WhatsApp" }),
    ).toBeVisible();

    const languages = page.getByRole("group", { name: "Read messages in" });
    await expect(languages.getByRole("radio")).toHaveCount(3);
    const english = languages.getByRole("radio", { name: "English", exact: true });
    await expect(languages.getByRole("radio", { name: /Telugu/ })).toBeVisible();
    const school = languages.getByRole("radio", { name: /^The school's choice/ });
    await expect(school).toBeVisible();
    if (canned("optin")) {
      await expect(school).toBeChecked();
      await expect(page.getByText("Notices", { exact: true })).toBeVisible();
      await expect(page.getByText("You get these").first()).toBeVisible();
    }

    // D32: the page offers no way to stop messages; only the language form is on it.
    await expectNoStopPrompt(page);

    // Choose English and save, keyboard only: Space on the choice, Tab to Save, Enter.
    await pressOn(english, "Space", "English choice");
    await expect(english).toBeChecked();
    await page.keyboard.press("Tab");
    const save = page.getByRole("button", { name: "Save my choice" });
    await expectFocusRing(save, "Save my choice");
    const posted = page.waitForRequest(
      (request) => request.method() === "POST" && new URL(request.url()).pathname === "/p/optin",
    );
    await page.keyboard.press("Enter");
    const form = new URLSearchParams((await posted).postData() ?? "");
    expect(form.get("language")).toBe("en");
    expect(form.get("csrf")).toBeTruthy();

    await expect(page).toHaveURL(/\/p\/view\?saved=1$/);
    expect(page.url()).not.toContain(token ?? "");
    await expect(
      page.getByText("Saved. Messages and pages from the school will be in your choice."),
    ).toBeVisible();
    await expect(
      page.getByRole("group", { name: "Read messages in" }).getByRole("radio", {
        name: "English",
        exact: true,
      }),
    ).toBeChecked();
    await expectNoStopPrompt(page);
    await expectNoHorizontalOverflow(page, "/p/view opt-in saved");
    expectNoReferrer(documents, "opt-in");
  });
});

/**
 * Owner decision D32: the parent opt-in page never asks the parent to reply STOP and has no
 * opt-out (stop, unsubscribe) button or link. A category's status may read "Stopped" when the
 * parent stopped it on WhatsApp; that is a status, not a prompt, and is not matched here.
 */
async function expectNoStopPrompt(page: Page) {
  const actions = page.getByRole("button").or(page.getByRole("link"));
  const names = await actions.evaluateAll((elements) =>
    elements.map((el) => (el.getAttribute("aria-label") ?? el.textContent ?? "").trim()),
  );
  for (const name of names) {
    expect(name, "no stop or opt-out control").not.toMatch(/stop|opt[\s-]?out|unsubscribe/i);
  }
  await expect(page.getByRole("button")).toHaveCount(1);
  await expect(page.getByRole("button", { name: "Save my choice" })).toBeVisible();
  const forms = page.locator("form");
  await expect(forms).toHaveCount(1);
  await expect(forms).toHaveAttribute("action", "/p/optin");
  const text = await page.locator("body").innerText();
  expect(text, "no STOP prompt").not.toMatch(/\bSTOP\b/);
  expect(text, "no reply-stop or opt-out wording").not.toMatch(
    /reply\s+["“']?stop|send\s+["“']?stop|opt[\s-]?out|unsubscribe/i,
  );
}
