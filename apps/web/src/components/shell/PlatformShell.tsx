import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { Badge } from "@/components/ui/Badge";
import type { IconName } from "@/components/ui/Icon";
import { LanguageSwitcher } from "@/components/ui/LanguageSwitcher";
import type { NavItem, NavSection } from "@/components/ui/SidebarNav";
import { AppShell } from "./AppShell";

type PlatformNavItem = NavItem & { anyOf?: readonly string[] };

/**
 * Platform admin panel chrome (C14). Same layout and card language as the school console,
 * but deliberately different: dark violet rail and top bar with a yellow "Platform admin"
 * badge and yellow focus ring, so an operator always knows they are in the control plane.
 */
export function PlatformShell({
  children,
  headerActions,
  permissions = null,
}: {
  children: ReactNode;
  /** Session controls (operator name, sign out). */
  headerActions?: ReactNode;
  /** Effective permissions from GET /platform/me; null shows every item. UX only. */
  permissions?: readonly string[] | null;
}) {
  const t = useTranslations();
  const groups: Array<{ id: string; icon: IconName; items: PlatformNavItem[] }> = [
    {
      id: "overview",
      icon: "chart",
      items: [
        { href: "/platform", label: t("platform.nav.dashboard"), exact: true, icon: "chart" },
      ],
    },
    {
      id: "schools",
      icon: "building",
      items: [
        {
          href: "/platform/schools",
          label: t("platform.nav.schools"),
          anyOf: ["platform.tenants.read"],
          icon: "building",
        },
        {
          href: "/platform/provision",
          label: t("platform.nav.provision"),
          anyOf: ["platform.tenants.provision"],
          icon: "plus",
        },
        {
          href: "/platform/plans",
          label: t("platform.nav.plans"),
          anyOf: ["platform.subscriptions.read", "platform.plans.manage"],
          icon: "layers",
        },
        {
          href: "/platform/subscriptions",
          label: t("platform.nav.subscriptions"),
          anyOf: ["platform.subscriptions.read"],
          icon: "creditCard",
        },
        {
          href: "/platform/invoices",
          label: t("platform.nav.invoices"),
          anyOf: ["platform.invoices.read"],
          icon: "receipt",
        },
        {
          href: "/platform/usage",
          label: t("platform.nav.usage"),
          anyOf: ["platform.usage.read"],
          icon: "activity",
        },
      ],
    },
    {
      id: "operations",
      icon: "server",
      items: [
        {
          href: "/platform/flags",
          label: t("platform.nav.flags"),
          anyOf: ["platform.flags.read"],
          icon: "flag",
        },
        {
          href: "/platform/fleet",
          label: t("platform.nav.fleet"),
          anyOf: ["platform.fleet.read"],
          icon: "server",
        },
        {
          href: "/platform/announcements",
          label: t("platform.nav.announcements"),
          icon: "megaphone",
        },
        {
          href: "/platform/support",
          label: t("platform.nav.support"),
          anyOf: ["platform.support.read"],
          icon: "lifeBuoy",
        },
      ],
    },
    {
      id: "access",
      icon: "key",
      items: [
        { href: "/platform/break-glass", label: t("platform.nav.breakGlass"), icon: "key" },
        {
          href: "/platform/operators",
          label: t("platform.nav.operators"),
          anyOf: ["platform.operators.manage"],
          icon: "users",
        },
        {
          href: "/platform/audit",
          label: t("platform.nav.audit"),
          anyOf: ["platform.audit.read"],
          icon: "clipboard",
        },
      ],
    },
  ];
  const sections: NavSection[] = groups.map((group) => ({
    id: group.id,
    icon: group.icon,
    label: t(`platform.nav.sections.${group.id}` as "platform.nav.sections.overview"),
    items: group.items
      .filter(
        (item) =>
          !item.anyOf ||
          permissions === null ||
          item.anyOf.some((permission) => permissions.includes(permission)),
      )
      .map(({ href, label, exact, icon }) => ({
        href,
        label,
        ...(exact ? { exact } : {}),
        ...(icon ? { icon } : {}),
      })),
  }));
  return (
    <AppShell
      theme="platform"
      homeHref="/platform"
      navLabel={t("platform.nav.label")}
      sections={sections}
      brand={
        <div className="flex min-w-0 items-center gap-3">
          <p className="truncate text-lg font-semibold">{t("common.appName")}</p>
          <Badge tone="platform">{t("platform.badge")}</Badge>
        </div>
      }
      headerActions={
        <>
          {headerActions}
          <LanguageSwitcher tone="dark" />
        </>
      }
    >
      {children}
    </AppShell>
  );
}
