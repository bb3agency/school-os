import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { LanguageSwitcher } from "@/components/ui/LanguageSwitcher";
import { SidebarNav, type NavItem } from "@/components/ui/SidebarNav";
import { SkipLink } from "./SkipLink";

/**
 * School office console chrome: light header, sidebar navigation, main landmark.
 * `headerActions` holds the session controls (who is signed in, "Lock now").
 */
export function SchoolShell({
  children,
  headerActions,
}: {
  children: ReactNode;
  headerActions?: ReactNode;
}) {
  const t = useTranslations();
  const items: NavItem[] = [
    { href: "/", label: t("school.nav.home"), exact: true },
    { href: "/settings/structure", label: t("school.nav.structure") },
    { href: "/settings/users", label: t("school.nav.users") },
    { href: "/settings/billing", label: t("school.nav.billing") },
    { href: "/audit", label: t("school.nav.audit") },
  ];
  return (
    <div className="flex min-h-screen flex-col">
      <SkipLink label={t("common.skipToContent")} />
      <header
        className="flex items-center justify-between gap-4 border-b border-border bg-surface px-6 py-3"
        data-print="hide"
      >
        <p className="text-lg font-bold text-primary">{t("common.appName")}</p>
        <div className="flex flex-wrap items-center justify-end gap-4">
          {headerActions}
          <LanguageSwitcher />
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
          {children}
        </main>
      </div>
    </div>
  );
}
