import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { Icon, type IconName } from "@/components/ui/Icon";
import { LanguageSwitcher } from "@/components/ui/LanguageSwitcher";
import type { NavItem, NavSection } from "@/components/ui/SidebarNav";
import { Link } from "@/i18n/navigation";
import { AppShell } from "./AppShell";

interface SchoolNavItem extends NavItem {
  /** Effective permission (or any of several) needed to see the item (UX only: the API checks every call). */
  permission?: string | readonly string[];
}

interface SchoolNavSection {
  id: string;
  icon: IconName;
  items: SchoolNavItem[];
}

/**
 * School office console chrome: icon rail, grouped menu panel, top bar and main landmark
 * on the gradient canvas (AppShell).
 * `headerActions` holds the session controls (who is signed in, "Lock now") and the bell.
 * `permissions` (from GET /me) hides menu items the user cannot use; null shows all.
 * Hiding is never a security control: every API route checks its permission.
 */
export function SchoolShell({
  children,
  headerActions,
  permissions = null,
  canSwitchSchool = false,
  languages = null,
  banner,
}: {
  children: ReactNode;
  headerActions?: ReactNode;
  permissions?: readonly string[] | null;
  canSwitchSchool?: boolean;
  /** The school's languages from GET /me `settings` (first is the default; FR-TEN-012). */
  languages?: readonly string[] | null;
  /** Platform announcements (FR-PLT-026), shown above the page. */
  banner?: ReactNode;
}) {
  const t = useTranslations();
  const groups: SchoolNavSection[] = [
    {
      id: "overview",
      icon: "home",
      items: [{ href: "/", label: t("school.nav.home"), exact: true, icon: "home" }],
    },
    {
      id: "records",
      icon: "users",
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
      ],
    },
    {
      id: "checks",
      icon: "shieldCheck",
      items: [
        {
          href: "/findings",
          label: t("findings.nav"),
          permission: "dq.findings.read",
          icon: "shieldCheck",
        },
        {
          href: "/change-requests",
          label: t("changeRequests.nav"),
          permission: ["student.identity_change.request", "student.identity_change.approve"],
          icon: "clipboard",
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
      icon: "sparkles",
      items: [{ href: "/ask", label: t("ask.nav"), permission: "kb.ask", icon: "sparkles" }],
    },
    {
      id: "admin",
      icon: "settings",
      items: [
        { href: "/settings/school", label: t("schoolSettings.nav"), icon: "building" },
        { href: "/settings/structure", label: t("school.nav.structure"), icon: "layers" },
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
    !item.permission ||
    permissions === null ||
    (typeof item.permission === "string" ? [item.permission] : item.permission).some((key) =>
      permissions.includes(key),
    );
  const sections: NavSection[] = groups.map((group) => ({
    id: group.id,
    icon: group.icon,
    label: t(`school.nav.sections.${group.id}` as "school.nav.sections.overview"),
    items: group.items
      .filter(allowed)
      .map(({ href, label, exact, nested, activePattern, icon }) => ({
        href,
        label,
        ...(exact ? { exact } : {}),
        ...(nested ? { nested } : {}),
        ...(activePattern ? { activePattern } : {}),
        ...(icon ? { icon } : {}),
      })),
  }));
  return (
    <AppShell
      theme="school"
      homeHref="/"
      navLabel={t("school.nav.label")}
      sections={sections}
      brand={<p className="truncate text-lg font-semibold text-ink">{t("common.appName")}</p>}
      headerActions={
        <>
          {canSwitchSchool ? (
            <Link
              href="/choose-school"
              className="inline-flex min-h-10 items-center gap-1.5 rounded-full border border-border-soft px-3 text-sm font-medium text-primary hover:bg-primary-soft"
            >
              <Icon name="swap" className="size-4" />
              {t("school.switchSchool")}
            </Link>
          ) : null}
          {headerActions}
          <LanguageSwitcher languages={languages} />
        </>
      }
      banner={banner}
    >
      {children}
    </AppShell>
  );
}
