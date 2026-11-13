import { SCHOOL_AREAS } from "./school-areas";

/**
 * WP-04 (audit 2026-11-08): the message namespaces each part of the app sends to the browser.
 *
 * `NextIntlClientProvider` without `messages` serialises the whole catalogue (560 KB of
 * en.json) into every page. Instead the `[locale]` root layout sends `root` and each area wraps
 * its pages in `<ClientMessages scope=…>` with its own list. A nested provider replaces the
 * messages of the outer one, so each list also holds the shared namespaces its components use.
 * The school console is split further (PERF-013..015): its layout sends `schoolBase` and each
 * of its areas (`SCHOOL_AREAS`) sends its own list. An entry is a namespace or a subtree
 * (`"fees.nav"`).
 *
 * `client-messages.test.ts` walks the imports from each area's layouts and pages and fails when
 * a message path a component asks for is missing here; `app/english-only.test.tsx` renders
 * every page with these lists and fails on a missing message.
 */
export const CLIENT_NAMESPACES = {
  /** Root layout and the pages outside an area: sign-in states, school picker, not found. */
  root: [
    "auth",
    "chooseSchool",
    "common",
    "devSignIn",
    "errors",
    "invitations",
    "language",
    "metadata",
    "noAccess",
    "shell",
    "status",
  ],
  /**
   * The school console's layout, app/[locale]/(school)/layout.tsx: the shell (menu labels
   * only, `<area>.nav`), session controls, banners, notification bell and privacy footer. Each
   * area's pages get their own list (`SCHOOL_AREAS`, school-areas.ts) from the area's layout.
   */
  schoolBase: [
    "admin.nav",
    "admin.suspended",
    "apaar.nav",
    "ask.chat",
    "ask.memory",
    "ask.nav",
    "attendance.nav",
    "auth",
    "breaches.nav",
    "breakGlass.nav",
    "brief.nav",
    "certificates.nav",
    "certificates.registers.nav",
    "changeRequests.nav",
    "circulars.nav",
    "common",
    "completeness.nav",
    "compliance.nav",
    "complianceChecks.nav",
    "compliancePolicies.nav",
    "consents.nav",
    "consents.purposes.nav",
    "disclosure.nav",
    "documents.nav",
    "enrolmentStats.nav",
    "errors.api",
    "errors.field",
    "errors.stepUp",
    "exports.nav",
    "extraction.nav",
    "fees.nav",
    "findings.nav",
    "fixLetters.nav",
    "followUpCounts.nav",
    "holds.nav",
    "imports.nav",
    "insights.nav",
    "language",
    "marks.nav",
    "messaging.nav",
    "myPolicies.nav",
    "notices.nav",
    "notifications",
    "payments.nav",
    "portalCalendar.nav",
    "privacy.footer",
    "privacy.nav",
    "readiness.nav",
    "schemePacks.nav",
    "school.announcements",
    "school.nav",
    "school.switchSchool",
    "school.users.roles",
    "schoolSettings.nav",
    "shell",
    "students.nav",
    "suspended",
    "tally.nav",
    "tasks.nav",
    "udise.nav",
    "udiseTodo.nav",
    "ui",
    "validation",
    "yearEnd.nav",
  ],
  ...SCHOOL_AREAS,
  /** The operator console, app/[locale]/platform. */
  platform: [
    "auth",
    "breaches",
    "common",
    "deploymentMode",
    "errors",
    "findings",
    "language",
    "metadata",
    "payments",
    "platform",
    "privacy",
    "shell",
    "status",
    "students",
    "support",
    "ui",
    "validation",
  ],
  /** The public marketing pages (welcome, features, pricing, security, about). */
  marketing: ["common", "marketing", "ui"],
  /** Local development pages (dev sign-in, UI reference). */
  dev: [
    "ask",
    "common",
    "devSignIn",
    "devUi",
    "documents",
    "errors",
    "findings",
    "language",
    "school",
    "tally",
    "ui",
    "validation",
  ],
} as const satisfies Record<string, readonly string[]>;

export type ClientScope = keyof typeof CLIENT_NAMESPACES;

type Tree = Record<string, unknown>;

/**
 * The named parts of `messages` (those that exist): a top-level namespace (`"fees"`) or a
 * subtree (`"fees.nav"`), kept at the same path. The source objects are never changed.
 */
export function pickMessages<T extends Tree>(messages: T, paths: readonly string[]): Partial<T> {
  const out: Tree = {};
  // Shorter paths first, so a subtree under a namespace that is sent whole is skipped.
  const sorted = [...paths].sort((a, b) => a.split(".").length - b.split(".").length);
  const taken: string[] = [];
  for (const path of sorted) {
    if (taken.some((t) => path === t || path.startsWith(`${t}.`))) continue;
    const parts = path.split(".");
    let source: unknown = messages;
    for (const part of parts) {
      source =
        source && typeof source === "object" && part in source ? (source as Tree)[part] : undefined;
    }
    if (source === undefined) continue;
    let target = out;
    for (const part of parts.slice(0, -1)) {
      target[part] ??= {};
      target = target[part] as Tree;
    }
    target[parts[parts.length - 1] as string] = source;
    taken.push(path);
  }
  return out as Partial<T>;
}
