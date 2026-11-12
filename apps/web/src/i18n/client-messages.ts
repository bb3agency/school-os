/**
 * WP-04 (audit 2026-11-08): the message namespaces each part of the app sends to the browser.
 *
 * `NextIntlClientProvider` without `messages` serialises the whole catalogue (560 KB of
 * en.json) into every page. Instead the `[locale]` root layout sends `root` and each area wraps
 * its pages in `<ClientMessages scope=…>` with its own list. A nested provider replaces the
 * messages of the outer one, so each list also holds the shared namespaces its components use.
 *
 * `client-messages.test.ts` walks the imports from each area's layouts and pages and fails when
 * a namespace a component asks for is missing here; `app/english-only.test.tsx` renders every
 * page with these lists and fails on a missing message.
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
  /** The school console, app/[locale]/(school). Everything but platform, marketing, public. */
  school: [
    "academicStructure",
    "admin",
    "apaar",
    "ask",
    "attendance",
    "auth",
    "breaches",
    "breakGlass",
    "brief",
    "certificates",
    "changeRequests",
    "circulars",
    "common",
    "completeness",
    "compliance",
    "consents",
    "deploymentMode",
    "disclosure",
    "documents",
    "errors",
    "exports",
    "extraction",
    "fees",
    "findings",
    "followUpCounts",
    "holds",
    "imports",
    "insights",
    "language",
    "marks",
    "messaging",
    "notices",
    "notifications",
    "payments",
    "portalCalendar",
    "privacy",
    "readiness",
    "school",
    "schoolCalendar",
    "schoolSettings",
    "sheets",
    "shell",
    "status",
    "students",
    "support",
    "suspended",
    "tally",
    "tasks",
    "touch",
    "udiseFollowup",
    "udiseTodo",
    "udise",
    "ui",
    "validation",
    "yearEnd",
  ],
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

/** The named top-level namespaces of `messages` (those that exist). */
export function pickMessages<T extends Record<string, unknown>>(
  messages: T,
  namespaces: readonly string[],
): Partial<T> {
  const out: Partial<T> = {};
  for (const name of namespaces) {
    if (name in messages) out[name as keyof T] = messages[name as keyof T];
  }
  return out;
}
