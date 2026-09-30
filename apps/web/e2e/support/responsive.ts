/**
 * Responsive layout checks shared by e2e/responsive.spec.ts (pass/fail, `make e2e` with
 * E2E_STAND_IN=1) and e2e/audit/responsive.audit.ts (the wide sweep with screenshots and a
 * JSON report). NFR-A11Y-001, NFR-I18N-001, CLAUDE.md §8 and §10: no horizontal page scroll
 * and no clipped content at 1366×768 and 375×812, touch targets of at least 24px (WCAG 2.2
 * 2.5.8), card gutters on phones. Synthetic data only (stand-in API + audit fixtures).
 */
import type { Page } from "@playwright/test";

/** The two viewports every screen must pass: the office PC and a phone. */
export const REQUIRED_VIEWPORTS: ReadonlyArray<readonly [number, number]> = [
  [1366, 768],
  [375, 812],
];

export const LOCALES = ["en", "te"] as const;

const T1 = "0192f3a4-0000-7000-8000-000000000001";
const T3 = "0192f3a4-0000-7000-8000-000000000003";
const STUDENT = "0192f3a4-0000-7000-8000-00000000e501";
const FINDING = "0192f3a4-0000-7000-8000-0000000f1001";
const YEAR = "0192f3a4-0000-7000-8000-0000000000a1";
const USER = "0192f3a4-0000-7000-8000-0000000000d1";
const CERTIFICATE = "0192f3a4-0000-7000-8000-0000000ce001";
const FLAG = "0192f3a4-0000-7000-8000-00000000f501";
/** Ask conversation with long, mixed-script content (layout-fixtures.ts). */
const ASK_CHAT = "0192f3a4-0000-7000-8000-00000000e9a1";

export interface ScreenGroup {
  /** Stand-in IdP subject to sign in as (null: public pages). */
  subject: string | null;
  /** Page that starts the sign-in. */
  signInPath: string;
  /** Paths without the locale prefix. */
  pages: string[];
}

/**
 * Every screen under src/app/[locale] with synthetic data behind it. Imports and documents
 * are left to their own feature work (their sheet editors are being rebuilt), dev-only pages
 * are local tools, and the pages that only redirect have no layout of their own.
 */
export const SCREEN_GROUPS: Record<string, ScreenGroup> = {
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
      "/certificates",
      `/certificates/${CERTIFICATE}`,
      `/students/${STUDENT}/certificates/new`,
      `/students/${STUDENT}/certificates/new?type=transfer`,
      "/registers",
      "/exports",
      "/exports/new/precheck",
      "/exports/new/student-list",
      "/register-photos",
      "/ask",
      "/ask/search",
      "/ask/verified",
      // Ask chat (FR-KB-012): a long conversation, all chats, memory.
      `/ask/c/${ASK_CHAT}`,
      "/ask/history",
      "/ask/memory",
      // M4: circulars inbox, tasks and parent notices.
      "/circulars",
      "/tasks",
      "/notices",
      // M5: attendance, marks and early-warning flags.
      "/attendance",
      "/marks",
      "/flags",
      `/flags/${FLAG}`,
      // M6 (ADR-0032 Proposed): Tally screens; with the flag off they say the connector is off.
      "/fees",
      "/settings/tally",
      "/settings/tally/ledgers",
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
      "/settings/retention",
      "/settings/data-export",
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
      ...["overview", "subscription", "invoices", "usage", "deployment", "flags", "tickets"].map(
        (tab) => `/platform/schools/${T1}?tab=${tab}`,
      ),
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

/**
 * Sign in through the stand-in IdP. A user of several schools ("multi") lands on the school
 * picker, which is the screen checked for them.
 */
export async function signInAs(page: Page, path: string, subject: string): Promise<void> {
  await page.goto(path);
  await page.getByLabel("Subject").fill(subject, { timeout: 15_000 });
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForLoadState("networkidle");
}

/** Wait until the data has loaded and the (self-hosted) fonts are in use. */
export async function settle(page: Page): Promise<void> {
  await page.waitForLoadState("networkidle").catch(() => undefined);
  await page
    .getByText(/Loading…|లోడ్/)
    .first()
    .waitFor({ state: "hidden", timeout: 5000 })
    .catch(() => undefined);
  await page.evaluate(() => document.fonts.ready.then(() => undefined));
}

export interface LayoutReport {
  /** documentElement.scrollWidth - clientWidth: > 0 means the page scrolls sideways. */
  docOverflow: number;
  /** Visible elements past the viewport edge and not inside a scroll box that is on screen. */
  bleedViewport: string[];
  /** Elements that stick out of their card without a scroll box in between. */
  bleedCard: string[];
  /** Text cut off by overflow: hidden/clip (x or y; ellipsis included). */
  clipped: string[];
  /** Controls under 24×24 CSS px that meet neither WCAG 2.5.8 exception. */
  smallTargets: string[];
  /** Cards closer than 12px to the screen edge on phones. */
  edge: string[];
}

/** The failures of a report (empty when the screen passes). */
export function problems(report: LayoutReport): string[] {
  return [
    ...(report.docOverflow > 0 ? [`page scrolls sideways by ${report.docOverflow}px`] : []),
    ...report.bleedViewport.map((item) => `past the screen edge: ${item}`),
    ...report.bleedCard.map((item) => `out of its card: ${item}`),
    ...report.clipped.map((item) => `clipped: ${item}`),
    ...report.smallTargets.map((item) => `target under 24px: ${item}`),
    ...report.edge.map((item) => `card touches the edge: ${item}`),
  ];
}

/** Measure the current page (runs in the browser; self-contained for page.evaluate). */
export function detectLayout({ vw }: { vw: number }): LayoutReport {
  const desc = (el: Element) => {
    const cls = (el.getAttribute("class") ?? "").split(/\s+/).slice(0, 6).join(".");
    const text = (el.textContent ?? "").trim().replace(/\s+/g, " ").slice(0, 40);
    return `${el.tagName.toLowerCase()}${el.id ? "#" + el.id : ""}${cls ? "." + cls : ""} "${text}"`;
  };
  // Developer overlays (next dev) are not part of the page.
  const ignored = (el: Element) => el.closest("nextjs-portal, [data-nextjs-toast]") !== null;
  const visible = (el: Element) => {
    if (ignored(el)) return false;
    const s = getComputedStyle(el);
    if (s.display === "none" || s.visibility === "hidden") return false;
    const r = el.getBoundingClientRect();
    if (r.width <= 1 && r.height <= 1) return false;
    if (s.position === "absolute" && s.clip.startsWith("rect(0")) return false;
    return !el.closest("[hidden], dialog:not([open]), .sr-only");
  };
  // The nearest ancestor (below `stop`) that clips or scrolls sideways; SVG boxes clip by
  // default but are graphics, not layout, so they are skipped.
  const scrollAncestor = (el: Element, stop?: Element | null) => {
    for (let p = el.parentElement; p && p !== document.body && p !== stop; p = p.parentElement) {
      if (p instanceof SVGElement) continue;
      const s = getComputedStyle(p);
      if (/(auto|scroll|hidden|clip)/.test(s.overflowX)) return p;
    }
    return null;
  };
  // Past the screen edge unless a clipping or scrolling ancestor on screen contains it.
  const offScreen = (el: Element) => {
    for (let p = scrollAncestor(el); p; p = scrollAncestor(p)) {
      const pr = p.getBoundingClientRect();
      if (pr.right <= vw + 1 && pr.left >= -1) return false;
    }
    return true;
  };
  const isCard = (el: Element) => {
    const c = el.getAttribute("class") ?? "";
    return /\brounded-(xl|2xl)\b/.test(c) && /\bborder\b/.test(c);
  };
  const out: LayoutReport = {
    docOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    bleedViewport: [],
    bleedCard: [],
    clipped: [],
    smallTargets: [],
    edge: [],
  };
  const TARGETS =
    "a[href], button, select, textarea, input:not([type=hidden]):not(.sr-only), summary, " +
    "[role=button], [role=tab], [role=switch], label:has(> input.sr-only)";
  const targets: Array<{ el: Element; r: DOMRect; small: boolean }> = [];
  const reportedVp = new Set<Element>();
  const reportedCard = new Set<Element>();
  for (const el of Array.from(document.body.querySelectorAll("*"))) {
    if (!visible(el)) continue;
    const r = el.getBoundingClientRect();
    if (r.right > vw + 1 || r.left < -1) {
      if (offScreen(el)) {
        if (!el.parentElement || !reportedVp.has(el.parentElement)) {
          out.bleedViewport.push(`${Math.round(r.left)}..${Math.round(r.right)} ${desc(el)}`);
        }
        reportedVp.add(el);
      }
    }
    let card: Element | null = el.parentElement;
    while (card && !isCard(card)) card = card.parentElement;
    // A framed scroll region (TableScroll) is its own card: what it scrolls may be wider.
    if (card && !/(auto|scroll)/.test(getComputedStyle(card).overflowX)) {
      const cr = card.getBoundingClientRect();
      if (r.right > cr.right + 1 || r.left < cr.left - 1) {
        if (!scrollAncestor(el, card)) {
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
      // One documented exception (docs/17 §5.3): a one-line ellipsis whose full text is the
      // link's tooltip and accessible name (Ask recents in the sidebar).
      const fullTextKept =
        s.textOverflow === "ellipsis" &&
        el.closest("[title]")?.getAttribute("title") === (el.textContent ?? "").trim();
      if (/(hidden|clip)/.test(s.overflowX) && el.scrollWidth > el.clientWidth + 1 && !fullTextKept)
        out.clipped.push(
          `${s.textOverflow === "ellipsis" ? "ellipsis " : ""}x ${el.scrollWidth}>${el.clientWidth} ${desc(el)}`,
        );
      if (/(hidden|clip)/.test(s.overflowY) && el.scrollHeight > el.clientHeight + 1)
        out.clipped.push(`y ${el.scrollHeight}>${el.clientHeight} ${desc(el)}`);
    }
    if (el.matches(TARGETS)) {
      // WCAG 2.5.8 inline exception: a link inside a sentence is sized by the line of text.
      const inline =
        s.display === "inline" &&
        (el.parentElement?.textContent ?? "").trim().length >
          (el.textContent ?? "").trim().length + 5;
      if (!inline) targets.push({ el, r, small: r.width < 24 || r.height < 24 });
    }
    if (vw < 768 && isCard(el) && (r.left < 12 || r.right > vw - 12) && !el.closest("dialog"))
      out.edge.push(`${Math.round(r.left)}..${Math.round(r.right)} ${desc(el)}`);
  }
  // WCAG 2.5.8 spacing exception: an undersized target passes when a 24px circle centred on
  // it intersects no other target and no other undersized target's circle.
  const centre = (r: DOMRect) => ({ x: r.left + r.width / 2, y: r.top + r.height / 2 });
  const circleHitsRect = (c: { x: number; y: number }, r: DOMRect) => {
    const dx = Math.max(r.left - c.x, 0, c.x - r.right);
    const dy = Math.max(r.top - c.y, 0, c.y - r.bottom);
    return dx * dx + dy * dy < 12 * 12;
  };
  for (const target of targets) {
    if (!target.small) continue;
    const c = centre(target.r);
    const crowded = targets.some((other) => {
      if (other === target || other.el.contains(target.el) || target.el.contains(other.el))
        return false;
      if (circleHitsRect(c, other.r)) return true;
      if (!other.small) return false;
      const o = centre(other.r);
      return Math.hypot(c.x - o.x, c.y - o.y) < 24;
    });
    if (crowded)
      out.smallTargets.push(
        `${Math.round(target.r.width)}x${Math.round(target.r.height)} ${desc(target.el)}`,
      );
  }
  return out;
}

/** Measure the page as it is now. */
export async function measure(page: Page): Promise<LayoutReport> {
  const width = page.viewportSize()?.width ?? 1366;
  return page.evaluate(detectLayout, { vw: width });
}
