import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { Badge } from "@/components/ui/Badge";
import { LanguageSwitcher } from "@/components/ui/LanguageSwitcher";
import { SidebarNav, type NavItem } from "@/components/ui/SidebarNav";
import { SkipLink } from "./SkipLink";

/**
 * Platform admin panel chrome (C14). Deliberately different from the school console:
 * dark violet header and sidebar with a yellow "Platform admin" badge, so an operator
 * always knows they are in the control plane.
 */
export function PlatformShell({
  children,
  headerActions,
}: {
  children: ReactNode;
  /** Session controls (operator name, sign out). */
  headerActions?: ReactNode;
}) {
  const t = useTranslations();
  const items: NavItem[] = [
    { href: "/platform", label: t("platform.nav.dashboard"), exact: true },
    { href: "/platform/schools", label: t("platform.nav.schools") },
    { href: "/platform/provision", label: t("platform.nav.provision") },
    { href: "/platform/plans", label: t("platform.nav.plans") },
    { href: "/platform/subscriptions", label: t("platform.nav.subscriptions") },
    { href: "/platform/invoices", label: t("platform.nav.invoices") },
    { href: "/platform/usage", label: t("platform.nav.usage") },
    { href: "/platform/flags", label: t("platform.nav.flags") },
    { href: "/platform/fleet", label: t("platform.nav.fleet") },
    { href: "/platform/announcements", label: t("platform.nav.announcements") },
    { href: "/platform/support", label: t("platform.nav.support") },
    { href: "/platform/break-glass", label: t("platform.nav.breakGlass") },
    { href: "/platform/operators", label: t("platform.nav.operators") },
    { href: "/platform/audit", label: t("platform.nav.audit") },
  ];
  return (
    <div className="flex min-h-screen flex-col">
      <SkipLink label={t("common.skipToContent")} />
      <header
        className="platform-chrome flex items-center justify-between gap-4 bg-platform px-6 py-3 text-platform-ink"
        data-print="hide"
      >
        <div className="flex items-center gap-3">
          <p className="text-lg font-bold">{t("common.appName")}</p>
          <Badge tone="platform">{t("platform.badge")}</Badge>
        </div>
        <div className="flex flex-wrap items-center justify-end gap-4">
          {headerActions}
          <LanguageSwitcher tone="dark" />
        </div>
      </header>
      <div className="flex flex-1 flex-col md:flex-row">
        <aside
          className="platform-chrome bg-platform p-4 text-platform-ink md:w-60 md:shrink-0"
          data-print="hide"
        >
          <SidebarNav label={t("platform.nav.label")} items={items} theme="platform" />
        </aside>
        <main id="main" tabIndex={-1} className="min-w-0 flex-1 p-6 focus:outline-none print:p-0">
          {children}
        </main>
      </div>
    </div>
  );
}
