import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { IdleWarning, SessionControls } from "@/components/session/SessionControls";
import { Badge } from "@/components/ui/Badge";
import { LanguageSwitcher } from "@/components/ui/LanguageSwitcher";
import type { NavItem, NavSection } from "@/components/ui/SidebarNav";
import { AppShell } from "./AppShell";

type PlatformNavItem = NavItem & { anyOf?: readonly string[] };

/**
 * Platform admin panel chrome (C14). Same single sidebar as the school console (AppShell,
 * docs/17 §5.2), but deliberately different: a dark violet sidebar and top bar, a yellow
 * "Platform admin" badge, yellow active markers and a yellow focus ring, so an operator
 * always knows they are in the control plane.
 */
export function PlatformShell({
  children,
  account = null,
  permissions = null,
}: {
  children: ReactNode;
  /** The signed-in operator (name, role keys); "Sign out" and the idle warning come with it. */
  account?: { displayName?: string | null; roles?: readonly string[] | null } | null;
  /** Effective permissions from GET /platform/me; null shows every item. UX only. */
  permissions?: readonly string[] | null;
}) {
  const t = useTranslations();
  const tr = useTranslations("platform.operators.roles");
  const groups: Array<{ id: string; items: PlatformNavItem[] }> = [
    {
      id: "overview",
      items: [
        { href: "/platform", label: t("platform.nav.dashboard"), exact: true, icon: "chart" },
      ],
    },
    {
      id: "schools",
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
  const role =
    (account?.roles ?? [])
      .filter((key) => tr.has(key as "platform_owner"))
      .map((key) => tr(key as "platform_owner"))
      .join(", ") || null;
  const badge = <Badge tone="platform">{t("platform.badge")}</Badge>;
  return (
    <AppShell
      theme="platform"
      homeHref="/platform"
      navLabel={t("platform.nav.label")}
      sections={sections}
      context={<div className="px-1.5 collapsed:sr-only">{badge}</div>}
      brandBadge={badge}
      account={
        account ? (
          <SessionControls
            variant="sidebar"
            kind="operator"
            tone="dark"
            displayName={account.displayName ?? null}
            role={role}
          />
        ) : null
      }
      session={account ? <IdleWarning kind="operator" /> : null}
      topbarActions={<LanguageSwitcher tone="dark" />}
    >
      {children}
    </AppShell>
  );
}
