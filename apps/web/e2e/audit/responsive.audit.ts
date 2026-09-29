/* Throwaway responsive audit (not committed). Writes JSON + screenshots to AUDIT_OUT. */
import { mkdirSync, writeFileSync, existsSync } from "node:fs";
import { join } from "node:path";
import { test, type Page } from "@playwright/test";

const OUT = process.env.AUDIT_OUT ?? "audit-out";
const SHOTS = new Set((process.env.AUDIT_SHOTS ?? "360,768,1366,1920").split(",").map(Number));
const ONLY = process.env.AUDIT_ONLY ? new RegExp(process.env.AUDIT_ONLY) : null;
const LOCALES = (process.env.AUDIT_LOCALES ?? "en,te").split(",");
const VIEWPORTS: Array<[number, number]> = (
  process.env.AUDIT_VP ??
  "360x740,375x812,390x844,414x896,768x1024,1024x768,1280x800,1366x768,1440x900,1920x1080"
)
  .split(",")
  .map((s) => s.split("x").map(Number) as [number, number]);

const T1 = "0192f3a4-0000-7000-8000-000000000001";
const T3 = "0192f3a4-0000-7000-8000-000000000003";
const STUDENT = "0192f3a4-0000-7000-8000-00000000e501";
const FINDING = "0192f3a4-0000-7000-8000-0000000f1001";
const YEAR = "0192f3a4-0000-7000-8000-0000000000a1";
const USER = "0192f3a4-0000-7000-8000-0000000000d1";

type Group = { subject: string | null; signInPath: string; pages: string[] };
const GROUPS: Record<string, Group> = {
  public: {
    subject: null,
    signInPath: "",
    pages: ["/welcome", "/signed-out", "/signed-out?error=signin_failed"],
  },
  multi: { subject: "multi", signInPath: "/en/support", pages: ["/choose-school"] },
  school: {
    subject: "clerk",
    signInPath: "/en/support",
    pages: [
      "",
      "/students",
      `/students/${STUDENT}`,
      "/students/new",
      "/findings",
      `/findings/${FINDING}`,
      "/findings/rules",
      "/change-requests",
      "/change-requests/new",
      "/exports",
      "/exports/new/precheck",
      "/exports/new/student-list",
      "/register-photos",
      "/ask",
      "/ask/search",
      "/ask/verified",
      "/notifications",
      "/audit",
      "/audit/verify",
      "/settings/school",
      "/settings/structure",
      "/settings/structure/promotions",
      `/settings/structure/years/${YEAR}/promotions`,
      "/settings/users",
      "/settings/users/new",
      `/settings/users/${USER}`,
      "/settings/billing",
      "/support",
      "/break-glass",
    ],
  },
  platform: {
    subject: "operator-1",
    signInPath: "/en/platform",
    pages: [
      "/platform",
      "/platform/schools",
      ...[
        "overview",
        "subscription",
        "invoices",
        "usage",
        "deployment",
        "flags",
        "tickets",
      ].map((tab) => `/platform/schools/${T1}?tab=${tab}`),
      `/platform/schools/${T3}`,
      "/platform/provision",
      "/platform/plans",
      "/platform/subscriptions",
      "/platform/invoices",
      "/platform/usage",
      "/platform/fleet",
      "/platform/flags",
      "/platform/announcements",
      "/platform/operators",
      "/platform/support",
      "/platform/audit",
      "/platform/break-glass",
    ],
  },
};

async function signIn(page: Page, path: string, subject: string) {
  await page.goto(path);
  await page.getByLabel("Subject").fill(subject, { timeout: 15000 });
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForLoadState("networkidle");
  if (subject === "multi") {
    await page.getByRole("button", { name: /Sri Saraswati/ }).click();
    await page.waitForLoadState("networkidle");
  }
}

function detect({ vw, vh }: { vw: number; vh: number }) {
  const desc = (el: Element) => {
    const cls = (el.getAttribute("class") ?? "").split(/\s+/).slice(0, 6).join(".");
    const text = (el.textContent ?? "").trim().replace(/\s+/g, " ").slice(0, 40);
    return `${el.tagName.toLowerCase()}${el.id ? "#" + el.id : ""}${cls ? "." + cls : ""} "${text}"`;
  };
  const visible = (el: Element) => {
    const s = getComputedStyle(el);
    if (s.display === "none" || s.visibility === "hidden") return false;
    const r = el.getBoundingClientRect();
    if (r.width <= 1 && r.height <= 1) return false;
    if (s.position === "absolute" && s.clip.startsWith("rect(0")) return false;
    return !el.closest("[hidden], dialog:not([open]), .sr-only");
  };
  const scrollAncestor = (el: Element, stop?: Element | null) => {
    for (let p = el.parentElement; p && p !== document.body && p !== stop; p = p.parentElement) {
      const s = getComputedStyle(p);
      if (/(auto|scroll|hidden|clip)/.test(s.overflowX)) return p;
    }
    return null;
  };
  const isCard = (el: Element) => {
    const c = el.getAttribute("class") ?? "";
    return /\brounded-(xl|2xl)\b/.test(c) && /\bborder\b/.test(c);
  };
  const out = {
    docOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    bleedViewport: [] as string[],
    bleedCard: [] as string[],
    clipped: [] as string[],
    smallTargets: [] as string[],
    edge: [] as string[],
    gutter: null as null | { left: number; right: number },
  };
  const reportedVp = new Set<Element>();
  const reportedCard = new Set<Element>();
  for (const el of Array.from(document.body.querySelectorAll("*"))) {
    if (!visible(el)) continue;
    const r = el.getBoundingClientRect();
    if (r.right > vw + 1 || r.left < -1) {
      const sa = scrollAncestor(el);
      const saR = sa?.getBoundingClientRect();
      if (!sa || (saR && (saR.right > vw + 1 || saR.left < -1))) {
        if (!el.parentElement || !reportedVp.has(el.parentElement)) {
          out.bleedViewport.push(`${Math.round(r.left)}..${Math.round(r.right)} ${desc(el)}`);
        }
        reportedVp.add(el);
      }
    }
    let card: Element | null = el.parentElement;
    while (card && !isCard(card)) card = card.parentElement;
    if (card) {
      const cr = card.getBoundingClientRect();
      if (r.right > cr.right + 1 || r.left < cr.left - 1) {
        const sa = scrollAncestor(el, card);
        if (!sa) {
          if (!el.parentElement || !reportedCard.has(el.parentElement))
            out.bleedCard.push(
              `${Math.round(r.left)}..${Math.round(r.right)} card ${Math.round(cr.left)}..${Math.round(cr.right)} ${desc(el)}`,
            );
          reportedCard.add(el);
        }
      }
    }
    const s = getComputedStyle(el);
    const scrollable = /(auto|scroll)/.test(s.overflowX) || /(auto|scroll)/.test(s.overflowY);
    if (!scrollable) {
      if (/(hidden|clip)/.test(s.overflowX) && el.scrollWidth > el.clientWidth + 1)
        out.clipped.push(
          `${s.textOverflow === "ellipsis" ? "ellipsis " : ""}x ${el.scrollWidth}>${el.clientWidth} ${desc(el)}`,
        );
      if (/(hidden|clip)/.test(s.overflowY) && el.scrollHeight > el.clientHeight + 1)
        out.clipped.push(`y ${el.scrollHeight}>${el.clientHeight} ${desc(el)}`);
    }
    const interactive = el.matches(
      "a[href], button, select, textarea, input:not([type=hidden]):not(.sr-only), summary, [role=button], [role=tab], [role=switch], label:has(> input.sr-only)",
    );
    if (interactive && (r.width < 24 || r.height < 24)) {
      const inline = s.display === "inline" && (el.parentElement?.textContent ?? "").trim().length > (el.textContent ?? "").trim().length + 5;
      if (!inline) out.smallTargets.push(`${Math.round(r.width)}x${Math.round(r.height)} ${desc(el)}`);
    }
    if (vw < 768 && isCard(el) && (r.left < 12 || r.right > vw - 12) && !el.closest("dialog"))
      out.edge.push(`${Math.round(r.left)}..${Math.round(r.right)} ${desc(el)}`);
  }
  const main = document.querySelector("main");
  if (main) {
    const first = main.firstElementChild ?? main;
    const r = first.getBoundingClientRect();
    out.gutter = { left: Math.round(r.left), right: Math.round(vw - r.right) };
  }
  void vh;
  return out;
}

async function settle(page: Page) {
  await page.waitForLoadState("networkidle").catch(() => undefined);
  await page
    .getByText(/Loading…|లోడ్/)
    .first()
    .waitFor({ state: "hidden", timeout: 5000 })
    .catch(() => undefined);
  await page.evaluate(() => document.fonts.ready);
}

for (const [groupName, group] of Object.entries(GROUPS)) {
  test(`audit ${groupName}`, async ({ page }) => {
    mkdirSync(join(OUT, "shots"), { recursive: true });
    const fixturesPath = join(process.cwd(), "e2e", "audit", "fixtures.ts");
    if (existsSync(fixturesPath) && process.env.AUDIT_FIXTURES !== "0") {
      const mod = (await import("./fixtures")) as { installFixtures: (p: Page) => Promise<void> };
      await mod.installFixtures(page);
    }
    await page.setViewportSize({ width: 1366, height: 768 });
    if (group.subject) await signIn(page, group.signInPath, group.subject);
    const results: Record<string, unknown> = {};
    for (const locale of LOCALES) {
      for (const path of group.pages) {
        const url = `/${locale}${path}`;
        if (ONLY && !ONLY.test(url)) continue;
        await page.setViewportSize({ width: 1366, height: 768 });
        await page.goto(url);
        await settle(page);
        for (const [w, h] of VIEWPORTS) {
          await page.setViewportSize({ width: w, height: h });
          await page.waitForTimeout(150);
          const r2 = await page.evaluate(detect, { vw: w, vh: h });
          results[`${url} @${w}x${h}`] = r2;
          if (SHOTS.has(w) && (locale === "en" || w === 360 || w === 1366)) {
            const name = `${url.replace(/[/?=&]/g, "_")}_${w}.png`;
            await page.screenshot({ path: join(OUT, "shots", name), fullPage: true });
          }
        }
      }
    }
    writeFileSync(join(OUT, `${groupName}.json`), JSON.stringify(results, null, 1));
  });
}
