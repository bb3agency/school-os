import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { LanguageSwitcher } from "@/components/ui/LanguageSwitcher";
import { SidebarNav, type NavItem } from "@/components/ui/SidebarNav";
import { Link } from "@/i18n/navigation";
import { SkipLink } from "./SkipLink";

interface SchoolNavItem extends NavItem {
  /** Effective permission (or any of several) needed to see the item (UX only: the API checks every call). */
  permission?: string | readonly string[];
}

/**
 * School office console chrome: light header, sidebar navigation, main landmark.
 * `headerActions` holds the session controls (who is signed in, "Lock now").
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
  const all: SchoolNavItem[] = [
    { href: "/", label: t("school.nav.home"), exact: true },
    { href: "/students", label: t("students.nav"), permission: "student.read_basic" },
    { href: "/imports", label: t("imports.nav"), permission: "import.run" },
    { href: "/register-photos", label: t("extraction.nav"), permission: "import.run" },
    { href: "/settings/school", label: t("schoolSettings.nav") },
    { href: "/documents", label: t("documents.nav"), permission: "document.read" },
    { href: "/ask", label: t("ask.nav"), permission: "kb.ask" },
    { href: "/settings/structure", label: t("school.nav.structure") },
    {
      // FR-TEN-011, US-202 AC2: year-end promotion, under the structure it changes.
      href: "/settings/structure/promotions",
      label: t("school.nav.promotions"),
      permission: "tenant.structure.manage",
      nested: true,
      activePattern: "^/settings/structure/years/[^/]+/promotions/?$",
    },
    { href: "/settings/users", label: t("school.nav.users"), permission: "user.manage" },
    {
      href: "/settings/billing",
      label: t("school.nav.billing"),
      permission: "tenant.billing.read",
    },
    { href: "/findings", label: t("findings.nav"), permission: "dq.findings.read" },
    {
      href: "/change-requests",
      label: t("changeRequests.nav"),
      permission: ["student.identity_change.request", "student.identity_change.approve"],
    },
    {
      href: "/exports",
      label: t("exports.nav"),
      permission: ["export.board", "export.portal", "student.export", "export.read_all"],
    },
    { href: "/break-glass", label: t("breakGlass.nav"), permission: "breakglass.approve" },
    { href: "/support", label: t("school.nav.support"), permission: "support.ticket.create" },
    { href: "/audit", label: t("school.nav.audit"), permission: "audit.read" },
  ];
  const items: NavItem[] = all
    .filter(
      (item) =>
        !item.permission ||
        permissions === null ||
        (typeof item.permission === "string" ? [item.permission] : item.permission).some((key) =>
          permissions.includes(key),
        ),
    )
    .map(({ href, label, exact, nested, activePattern }) => ({
      href,
      label,
      ...(exact ? { exact } : {}),
      ...(nested ? { nested } : {}),
      ...(activePattern ? { activePattern } : {}),
    }));
  return (
    <div className="flex min-h-screen flex-col">
      <SkipLink label={t("common.skipToContent")} />
      <header
        className="flex items-center justify-between gap-4 border-b border-border bg-surface px-6 py-3"
        data-print="hide"
      >
        <p className="text-lg font-bold text-primary">{t("common.appName")}</p>
        <div className="flex flex-wrap items-center justify-end gap-4">
          {canSwitchSchool ? (
            <Link
              href="/choose-school"
              className="rounded-md px-2 py-1 text-sm font-semibold text-primary underline"
            >
              {t("school.switchSchool")}
            </Link>
          ) : null}
          {headerActions}
          <LanguageSwitcher languages={languages} />
        </div>
      </header>
      <div className="flex flex-1 flex-col md:flex-row">
        <aside
          className="border-b border-border bg-surface p-4 md:w-60 md:shrink-0 md:border-r md:border-b-0"
          data-print="hide"
        >
          <SidebarNav label={t("school.nav.label")} items={items} />
        </aside>
        <main id="main" tabIndex={-1} className="min-w-0 flex-1 p-6 focus:outline-none print:p-0">
          {banner}
          {children}
        </main>
      </div>
    </div>
  );
}
