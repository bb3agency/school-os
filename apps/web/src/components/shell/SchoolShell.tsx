import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { IdleWarning, SessionControls } from "@/components/session/SessionControls";
import { Icon } from "@/components/ui/Icon";
import { LanguageSwitcher } from "@/components/ui/LanguageSwitcher";
import type { NavItem, NavSection } from "@/components/ui/SidebarNav";
import { AskRecents } from "@/features/ask/AskRecents";
import { Link } from "@/i18n/navigation";
import type { SessionKind } from "@/lib/bff/session-client";
import { AppShell } from "./AppShell";
import { SCHOOL_ACTIONS, bottomBarFor } from "./school-actions";

interface SchoolNavItem extends NavItem {
  /** Effective permission (or any of several) needed to see the item (UX only: the API checks every call). */
  permission?: string | readonly string[];
  /** A per-school feature the item belongs to: shown only while it is on for the school. */
  feature?: keyof SchoolFeatures;
}

/** Per-school features switched on by SchoolOS (UX only: their API routes answer 404 while off). */
export interface SchoolFeatures {
  /** M6 Tally connector (`tally.connector.enabled`, ADR-0032 Proposed). */
  tally?: boolean;
  /** R2-1C WhatsApp messages (ADR-0043): shared tier with SOS_MESSAGING_ENABLED only. */
  messaging?: boolean;
  /** R2-1B fee ledger and receipts (`fees.enabled` plus the school's own switch, FR-FEE-001). */
  fees?: boolean;
  /** R3-1C CBSE Appendix IX disclosure (ADR-0051): schools whose boards include CBSE only. */
  disclosure?: boolean;
}

interface SchoolNavSection {
  id: string;
  items: SchoolNavItem[];
}

/** Who is signed in, for the account area at the foot of the sidebar. */
export interface ShellAccount {
  kind: SessionKind;
  displayName?: string | null;
  /** Role keys from GET /me; system roles are shown by name, others are left out. */
  roles?: readonly string[] | null;
}

/**
 * School office console chrome (AppShell, docs/17 §5.2): one sidebar with the school, the
 * grouped menu and the signed-in account; a top bar with the bell and the language switch.
 * `permissions` (from GET /me) hides menu items the user cannot use; null shows all.
 * Hiding is never a security control: every API route checks its permission.
 */
export function SchoolShell({
  children,
  topbarActions,
  account = null,
  schoolName = null,
  permissions = null,
  canSwitchSchool = false,
  languages = null,
  features = {},
  banner,
}: {
  children: ReactNode;
  /** Top bar tools before the language switch (the notification bell). */
  topbarActions?: ReactNode;
  /** Who is signed in ("Lock now" and the idle warning come with it); null: none shown. */
  account?: ShellAccount | null;
  /** The active school's name (GET /me/schools), shown at the top of the sidebar. */
  schoolName?: string | null;
  permissions?: readonly string[] | null;
  canSwitchSchool?: boolean;
  /** The school's languages from GET /me `settings` (first is the default; FR-TEN-012). */
  languages?: readonly string[] | null;
  /** Features switched on for this school; items of a feature that is off are hidden. */
  features?: SchoolFeatures;
  /** Platform announcements (FR-PLT-026), shown above the page. */
  banner?: ReactNode;
}) {
  const t = useTranslations();
  const tr = useTranslations("school.users.roles");
  const groups: SchoolNavSection[] = [
    {
      id: "overview",
      items: [
        { href: "/", label: t("school.nav.home"), exact: true, icon: "home" },
        // FA-05 (FR-BRF-001): the weekly management brief, owner and principal only.
        { href: "/brief", label: t("brief.nav"), permission: "brief.read", icon: "chart" },
        {
          // FT-13 (FR-EW-026): early-warning follow-up counts for management, no names.
          href: "/follow-up-counts",
          label: t("followUpCounts.nav"),
          permission: "insights.counts_read",
          icon: "flag",
        },
      ],
    },
    {
      id: "records",
      items: [
        {
          href: "/students",
          label: t("students.nav"),
          permission: "student.read_basic",
          icon: "users",
        },
        {
          // R3-1B (ADR-0049, US-2603): families' answers on the school's optional purposes.
          href: "/consents",
          label: t("consents.nav"),
          permission: ["consent.read", "consent.record"],
          icon: "checkCircle",
        },
        { href: "/imports", label: t("imports.nav"), permission: "import.run", icon: "upload" },
        {
          href: "/register-photos",
          label: t("extraction.nav"),
          permission: "import.run",
          icon: "camera",
        },
        {
          href: "/documents",
          label: t("documents.nav"),
          permission: "document.read",
          icon: "folder",
        },
        {
          // US-1101..US-1107: certificates and their register.
          href: "/certificates",
          label: t("certificates.nav"),
          permission: ["certificate.read", "certificate.issue", "certificate.approve"],
          icon: "receipt",
        },
        {
          // US-1106, FR-REG-001..004: register print views.
          href: "/registers",
          label: t("certificates.registers.nav"),
          permission: "register.read",
          icon: "clipboard",
        },
      ],
    },
    {
      // M5 (US-1701..US-1709): attendance, marks and early-warning flags.
      id: "classroom",
      items: [
        {
          href: "/attendance",
          label: t("attendance.nav"),
          permission: "attendance.read",
          icon: "check",
        },
        {
          href: "/marks",
          label: t("marks.nav"),
          permission: ["marks.read", "marks.record", "exam.manage"],
          icon: "chart",
        },
        {
          // FT-14 (FR-ATT-024): attendance and marks entry completeness per section.
          href: "/completeness",
          label: t("completeness.nav"),
          permission: "completeness.read",
          icon: "clipboard",
        },
        {
          // FT-08 (FR-YRE-019): year-end results and the UDISE+ progression sheet.
          href: "/year-end",
          label: t("yearEnd.nav"),
          permission: "year_result.read",
          icon: "clipboard",
        },
        { href: "/flags", label: t("insights.nav"), permission: "insights.read", icon: "flag" },
      ],
    },
    {
      id: "checks",
      items: [
        {
          href: "/findings",
          label: t("findings.nav"),
          permission: "dq.findings.read",
          icon: "shieldCheck",
        },
        {
          // US-504..US-506 (ADR-0040): board and portal readiness (AP SSC 2027, APAAR).
          href: "/findings/readiness",
          label: t("readiness.nav"),
          permission: "dq.readiness.read",
          icon: "checkCircle",
        },
        {
          // FO-10 (FR-STU-026..029): board candidate medium, languages, subjects, photo tick.
          href: "/students/board-candidates",
          label: t("boardCandidates.nav"),
          permission: ["student.update_nonidentity", "dq.readiness.read"],
          icon: "clipboard",
        },
        {
          // FO-03 (FR-UDI-031..040): what to repeat in UDISE+ after a TC, a leaver, an
          // admission or a promotion.
          href: "/findings/udise-todo",
          label: t("udiseTodo.nav"),
          permission: "dq.readiness.read",
          icon: "checkCircle",
        },
        {
          // R3-1D (US-3101, ADR-0053): portal and board windows with reminders to staff.
          href: "/findings/calendar",
          label: t("portalCalendar.nav"),
          permission: "dq.readiness.read",
          icon: "calendar",
        },
        {
          // FO-02 (FR-UDI-001..008): UDISE+ correction requests to the MEO office.
          href: "/findings/udise-corrections",
          label: t("udise.nav"),
          permission: "dq.readiness.manage",
          icon: "file",
        },
        {
          // FA-08 (FR-DQ-049..060): fix-owner letters to parents and the board office.
          href: "/findings/fix-letters",
          label: t("fixLetters.nav"),
          permission: "dq.readiness.manage",
          icon: "file",
        },
        {
          href: "/change-requests",
          label: t("changeRequests.nav"),
          permission: ["student.identity_change.request", "student.identity_change.approve"],
          icon: "clipboard",
        },
        {
          // ADR-0039, US-1901..US-1903: parents' APAAR consent decisions and forms.
          href: "/apaar",
          label: t("apaar.nav"),
          permission: ["apaar.consent.read", "apaar.consent.record"],
          icon: "check",
        },
        {
          href: "/exports",
          label: t("exports.nav"),
          permission: ["export.board", "export.portal", "student.export", "export.read_all"],
          icon: "file",
        },
        {
          // FO-06 (FR-EXP-009..019): enrolment statistics in the UDISE+ table shapes.
          href: "/exports/enrolment-statistics",
          label: t("enrolmentStats.nav"),
          permission: "export.portal",
          icon: "file",
        },
        {
          // FO-11 (FR-EXP-020..028): scheme verification attendance lists and certificates.
          href: "/exports/scheme-packs",
          label: t("schemePacks.nav"),
          permission: ["attendance.read", "certificate.issue"],
          icon: "file",
        },
      ],
    },
    {
      // R3 privacy duties (docs/17 §5.10): requests, complaints and notices (R3-1A, ADR-0048),
      // the breach register and consent purposes (R3-1B, ADR-0049/0050). One section: the
      // sidebar keys sections by id.
      id: "privacy",
      items: [
        {
          href: "/privacy/requests",
          label: t("privacy.nav.requests"),
          permission: "privacy.read",
          icon: "shieldCheck",
        },
        {
          href: "/privacy/notices",
          label: t("privacy.nav.notices"),
          permission: "privacy.read",
          icon: "file",
        },
        {
          // FC-03: DPDP readiness checklist (not legal advice).
          href: "/privacy/readiness",
          label: t("privacy.nav.readiness"),
          permission: "privacy.read",
          icon: "checkCircle",
        },
        {
          href: "/privacy/breaches",
          label: t("breaches.nav"),
          permission: "privacy.breach.read",
          icon: "alert",
        },
        {
          // FC-12 legal holds (ADR-0059): owner and principal only.
          href: "/privacy/holds",
          label: t("holds.nav"),
          permission: "privacy.hold.manage",
          icon: "lock",
        },
        {
          href: "/settings/consent-purposes",
          label: t("consents.purposes.nav"),
          permission: "tenant.settings.manage",
          icon: "checkCircle",
        },
      ],
    },
    {
      id: "ask",
      items: [
        {
          href: "/ask",
          label: t("ask.nav"),
          permission: "kb.ask",
          icon: "sparkles",
          // FR-KB-012: New chat, recent chats, All chats and Memory, while an Ask page is open.
          sub: <AskRecents />,
          subActivePattern: "^/ask/(c/[^/]+|history|memory)/?$",
        },
      ],
    },
    {
      // M4 (US-1601..US-1606): circulars read with AI, the tasks they become, parent notices.
      id: "work",
      items: [
        { href: "/tasks", label: t("tasks.nav"), permission: "task.read", icon: "calendar" },
        {
          href: "/circulars",
          label: t("circulars.nav"),
          permission: "document.read",
          icon: "inbox",
        },
        {
          href: "/notices",
          label: t("notices.nav"),
          permission: "notice.draft",
          icon: "megaphone",
        },
        {
          // R2-1C (US-2208): WhatsApp message history and delivery log.
          href: "/messages",
          label: t("messaging.nav.messages"),
          permission: "message.read",
          feature: "messaging",
          icon: "inbox",
        },
        {
          // M6 (US-1804, FR-TALLY-007): fee dues synced from Tally; behind the school's flag.
          href: "/fees",
          label: t("tally.nav.dues"),
          permission: "finance.read",
          feature: "tally",
          icon: "chart",
        },
      ],
    },
    {
      // R2-1B (ADR-0042; docs/17 §5.8): fee counter, receipts, approvals, reports and setup.
      id: "fees",
      items: [
        {
          href: "/fees/collect",
          label: t("fees.nav.collect"),
          permission: "fee.collect",
          feature: "fees",
          icon: "receipt",
        },
        {
          href: "/fees/receipts",
          label: t("fees.nav.receipts"),
          permission: ["fee.collect", "finance.read"],
          feature: "fees",
          icon: "file",
          activePattern: "^/fees/(receipts|accounts)(/.*)?$",
        },
        {
          // FM-06 (FR-FEE-055): tuition fee certificates for income tax.
          href: "/fees/tax-certificates",
          label: t("fees.nav.taxCertificates"),
          permission: ["fee.collect", "finance.read"],
          feature: "fees",
          icon: "file",
        },
        {
          href: "/fees/approvals",
          label: t("fees.nav.approvals"),
          permission: [
            "fee.concession.approve",
            "fee.receipt.cancel",
            "fee.refund.approve",
            "fee.concession.request",
            "fee.refund.request",
            "fee.manage",
            "fee.collect",
          ],
          feature: "fees",
          icon: "check",
        },
        {
          href: "/fees/daily",
          label: t("fees.nav.daily"),
          permission: "finance.read",
          feature: "fees",
          icon: "calendar",
        },
        {
          href: "/fees/dues",
          label: t("fees.nav.dues"),
          permission: "finance.read",
          feature: "fees",
          icon: "chart",
        },
        {
          // FM-02 (FR-FEE-095): add-on fees; a transport in-charge reads who is on them.
          href: "/fees/addons",
          label: t("fees.nav.addons"),
          permission: ["fee.addon.read", "finance.read", "fee.manage"],
          feature: "fees",
          icon: "layers",
        },
        {
          // FM-01 (FR-FEE-038): students who left with fees still open.
          href: "/fees/left-with-dues",
          label: t("fees.nav.leftWithDues"),
          permission: "finance.read",
          feature: "fees",
          icon: "chart",
        },
        {
          // FM-04 (FR-FEE-061..074): period, DCB, registers, ageing and the year-end pack.
          href: "/fees/reports/collections",
          label: t("fees.nav.reports"),
          permission: "finance.read",
          feature: "fees",
          icon: "chart",
          activePattern: "^/fees/reports(/|$)",
        },
        {
          // R2-2E (US-2109): bank statements, UPI confirmations and exceptions.
          href: "/fees/reconcile",
          label: t("fees.nav.reconcile"),
          permission: "fee.reconcile",
          feature: "fees",
          icon: "refresh",
          activePattern: "^/fees/reconcile/?$",
        },
        {
          // FM-08 (FR-FEE-131..141): cheques received, deposited, cleared or returned.
          href: "/fees/cheques",
          label: t("fees.nav.cheques"),
          permission: ["fee.collect", "fee.reconcile"],
          feature: "fees",
          icon: "file",
        },
        {
          // R2-2D (US-2111, FR-PAY-010): online payment exceptions and the daily check.
          href: "/fees/reconcile/online",
          label: t("payments.nav.reconcile"),
          permission: "fee.reconcile",
          feature: "fees",
          icon: "check",
        },
        {
          href: "/fees/setup",
          label: t("fees.nav.setup"),
          permission: ["fee.manage", "finance.read"],
          feature: "fees",
          icon: "layers",
        },
      ],
    },
    {
      id: "admin",
      items: [
        { href: "/settings/school", label: t("schoolSettings.nav"), icon: "building" },
        {
          // GET /academic-years, /classes and /sections need student.read_basic.
          href: "/settings/structure",
          label: t("school.nav.structure"),
          permission: "student.read_basic",
          icon: "layers",
        },
        {
          // FR-TEN-011, US-202 AC2: year-end promotion, under the structure it changes.
          href: "/settings/structure/promotions",
          label: t("school.nav.promotions"),
          permission: "tenant.structure.manage",
          nested: true,
          activePattern: "^/settings/structure/years/[^/]+/promotions/?$",
        },
        {
          href: "/settings/users",
          label: t("school.nav.users"),
          permission: "user.manage",
          icon: "users",
        },
        {
          href: "/settings/billing",
          label: t("school.nav.billing"),
          permission: "tenant.billing.read",
          icon: "creditCard",
        },
        {
          // FR-ADM-002: how long working data is kept (owner, principal).
          href: "/settings/retention",
          label: t("admin.nav.retention"),
          permission: "tenant.settings.manage",
          icon: "clock",
        },
        {
          // FR-DPDP-004/006: the privacy contact, the request target and the record classes.
          href: "/settings/privacy",
          label: t("privacy.nav.settings"),
          permission: ["tenant.settings.manage", "privacy.read"],
          icon: "lock",
        },
        {
          // FR-ADM-001: the school's full data export (owner).
          href: "/settings/data-export",
          label: t("admin.nav.dataExport"),
          permission: "tenant.export_all",
          icon: "inbox",
        },
        {
          // M6 (US-1801..US-1803): the Tally connector; behind the school's flag.
          href: "/settings/tally",
          label: t("tally.nav.connector"),
          permission: ["tally.device.manage", "tally.configure", "finance.read"],
          feature: "tally",
          icon: "server",
        },
        {
          // FM-03 (FR-TALLY-011..030): the Tally voucher file; needs fees, not the connector.
          href: "/settings/tally/vouchers",
          label: t("tally.nav.vouchers"),
          permission: ["tally.configure", "fee.report"],
          feature: "fees",
          icon: "inbox",
        },
        {
          // R2-3J (FR-MSG-014, US-2205): receipt paper, GST line and fee reminder days.
          href: "/settings/fees",
          label: t("fees.nav.feeSettings"),
          permission: ["fee.manage", "finance.read"],
          feature: "fees",
          icon: "settings",
        },
        {
          // R2-2E (US-2109 AC1, FR-FEE-015): the school's UPI ID and static QR.
          href: "/settings/upi",
          label: t("fees.nav.upiSettings"),
          permission: ["fee.manage", "fee.collect", "finance.read", "fee.reconcile"],
          feature: "fees",
          icon: "creditCard",
        },
        {
          // R2-2D (US-2107, ADR-0042): the school's own Razorpay account (online fee payments).
          href: "/settings/payments",
          label: t("payments.nav.settings"),
          permission: ["payments.configure", "finance.read"],
          feature: "fees",
          icon: "creditCard",
        },
        {
          // FC-01: the school's compliance certificates (fire NOC, recognition, affiliation).
          href: "/compliance",
          label: t("compliance.nav"),
          permission: "compliance.read",
          icon: "checkCircle",
        },
        {
          // FC-09: safety drills and periodic checks (fire drills, extinguishers, CCTV).
          href: "/compliance/checks",
          label: t("complianceChecks.nav"),
          permission: "compliance.read",
          icon: "checkCircle",
        },
        {
          // FC-10: the school's policy library (review dates, who has read each policy).
          href: "/compliance/policies",
          label: t("compliancePolicies.nav"),
          permission: "compliance.read",
          icon: "file",
        },
        {
          // FC-10 (FR-COMP-063): policies the signed-in member is asked to read; any member.
          href: "/compliance/acknowledge",
          label: t("myPolicies.nav"),
          icon: "file",
        },
        {
          // R3-1C (US-2901, ADR-0051): the CBSE Appendix IX disclosure, CBSE schools only.
          href: "/settings/disclosure",
          label: t("disclosure.nav"),
          permission: "disclosure.edit",
          feature: "disclosure",
          icon: "file",
        },
        {
          // R2-1C (US-2201, ADR-0043): the school's own WhatsApp number and its settings.
          href: "/settings/whatsapp",
          label: t("messaging.nav.settings"),
          permission: ["messaging.configure", "message.read"],
          feature: "messaging",
          icon: "megaphone",
        },
        {
          href: "/break-glass",
          label: t("breakGlass.nav"),
          permission: "breakglass.approve",
          icon: "key",
        },
        {
          href: "/support",
          label: t("school.nav.support"),
          permission: "support.ticket.create",
          icon: "lifeBuoy",
        },
        {
          href: "/audit",
          label: t("school.nav.audit"),
          permission: "audit.read",
          icon: "activity",
        },
      ],
    },
  ];
  const allowed = (item: Pick<SchoolNavItem, "permission" | "feature">) =>
    (!item.feature || features[item.feature] === true) &&
    (!item.permission ||
      permissions === null ||
      (typeof item.permission === "string" ? [item.permission] : item.permission).some((key) =>
        permissions.includes(key),
      ));
  const sections: NavSection[] = groups.map((group) => ({
    id: group.id,
    label: t(`school.nav.sections.${group.id}` as "school.nav.sections.overview"),
    items: group.items
      .filter(allowed)
      .map(({ href, label, exact, nested, activePattern, icon, sub, subActivePattern }) => ({
        href,
        label,
        ...(exact ? { exact } : {}),
        ...(nested ? { nested } : {}),
        ...(activePattern ? { activePattern } : {}),
        ...(icon ? { icon } : {}),
        ...(sub ? { sub } : {}),
        ...(subActivePattern ? { subActivePattern } : {}),
      })),
  }));
  // Palette quick actions and the phone bottom bar: the same filter as the menu (docs/17 §5.9).
  const actions = SCHOOL_ACTIONS.filter(allowed).map((action) => ({
    id: action.id,
    label: t(`ui.palette.${action.labelKey}`),
    href: action.href,
    icon: action.icon,
  }));
  const bottomBar = bottomBarFor(account?.roles, sections);
  // System roles by name ("Principal, Class teacher"); custom roles have no fixed label here.
  const role =
    (account?.roles ?? [])
      .filter((key) => tr.has(key as "owner"))
      .map((key) => tr(key as "owner"))
      .join(", ") || null;
  const context =
    schoolName || canSwitchSchool ? (
      <div className="rounded-lg bg-surface-muted px-3 py-2.5 collapsed:bg-transparent collapsed:p-0">
        {schoolName ? (
          <p className="collapsed:sr-only">
            <span className="block text-xs text-ink-subtle">{t("shell.currentSchool")}</span>
            <span className="block text-sm font-semibold text-ink break-anywhere">
              {schoolName}
            </span>
          </p>
        ) : null}
        {canSwitchSchool ? (
          <Link
            href="/choose-school"
            data-tooltip={t("school.switchSchool")}
            className="-mx-1 mt-1 inline-flex min-h-8 items-center gap-1.5 pointer-coarse:min-h-11 rounded-md px-1 text-sm font-semibold text-primary hover:underline collapsed:mx-0 collapsed:mt-0 collapsed:flex collapsed:size-10 collapsed:justify-center collapsed:px-0 collapsed:hover:bg-surface-muted"
          >
            <Icon name="swap" className="size-4" />
            <span className="collapsed:sr-only">{t("school.switchSchool")}</span>
          </Link>
        ) : null}
      </div>
    ) : null;
  return (
    <AppShell
      theme="school"
      homeHref="/"
      navLabel={t("school.nav.label")}
      sections={sections}
      actions={actions}
      // D72: the palette's "Students" group only for people who can read students (UX only;
      // POST /students/search checks the permission and the caller's scope). Unknown
      // permissions (null) do not offer it.
      studentSearch={
        permissions?.includes("student.read_basic") === true ? (account?.kind ?? "staff") : null
      }
      bottomBar={bottomBar}
      context={context}
      account={
        account ? (
          <SessionControls
            variant="sidebar"
            kind={account.kind}
            displayName={account.displayName ?? null}
            role={role}
          />
        ) : null
      }
      session={account ? <IdleWarning kind={account.kind} /> : null}
      topbarActions={
        <>
          {topbarActions}
          <LanguageSwitcher languages={languages} />
        </>
      }
      banner={banner}
    >
      {children}
    </AppShell>
  );
}
