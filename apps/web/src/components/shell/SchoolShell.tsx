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
      items: [{ href: "/", label: t("school.nav.home"), exact: true, icon: "home" }],
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
  const allowed = (item: SchoolNavItem) =>
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
