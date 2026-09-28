import type { PlatformDashboard } from "@schoolos/api-client";
import { useLocale, useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { Alert } from "@/components/ui/Alert";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Icon, type IconName } from "@/components/ui/Icon";
import { PageHeader } from "@/components/ui/PageHeader";
import { ProgressRing } from "@/components/ui/ProgressRing";
import { KpiCard } from "@/components/ui/StatCard";
import { Link } from "@/i18n/navigation";
import { formatCount, formatDate, formatInr } from "@/lib/format";

type Counts = Record<string, number> | null | undefined;

function sum(counts: Counts): number | null {
  if (!counts) return null;
  return Object.values(counts).reduce((total, value) => total + value, 0);
}

interface Tile {
  key: string;
  label: string;
  value: string | null;
  comparison?: ReactNode;
  aside?: ReactNode;
}

interface AttentionItem {
  key: string;
  icon: IconName;
  text: string;
  href: string;
  linkLabel: string;
}

/**
 * C14 dashboard (docs/16 §5.1). The API leaves a tile null when the operator lacks its read
 * permission; such tiles are not shown. Counts only, never student data. The "needs
 * attention" list is built from the same numbers (nothing is estimated) and links to the
 * filtered list that explains each one.
 */
export function DashboardView({
  kpis,
  notice,
}: {
  kpis: PlatformDashboard | null;
  /** Shown under the page header, e.g. a load error. */
  notice?: ReactNode;
}) {
  const t = useTranslations("platform.dashboard");
  const tp = useTranslations("platform");
  const tc = useTranslations("common");
  const locale = useLocale();
  const count = (value: number | null | undefined) => formatCount(value, locale);

  const schools = kpis?.schools_by_status;
  const fleet = kpis?.fleet_by_status;
  const tickets = kpis?.open_tickets_by_priority;
  const fleetTotal = sum(fleet);
  const cards: Tile[] = [];
  const add = (present: boolean, tile: Tile) => {
    if (kpis === null || present) cards.push(tile);
  };

  add(kpis?.mrr_inr != null, {
    key: "mrr",
    label: t("mrr"),
    value: formatInr(kpis?.mrr_inr, locale),
  });
  add(kpis?.arr_inr != null, {
    key: "arr",
    label: t("arr"),
    value: formatInr(kpis?.arr_inr, locale),
  });
  add(schools != null, {
    key: "active",
    label: t("activeSchools"),
    value: count(schools ? (schools.active ?? 0) : null),
    ...(schools
      ? {
          comparison: t("schoolsHint", {
            suspended: count(schools.suspended ?? 0) ?? "0",
            offboarding: count(schools.offboarding ?? 0) ?? "0",
          }),
        }
      : {}),
  });
  add(kpis?.schools_by_tier != null, {
    key: "tiers",
    label: t("dedicatedSchools"),
    value: count(kpis?.schools_by_tier ? (kpis.schools_by_tier.dedicated ?? 0) : null),
    ...(kpis?.schools_by_tier
      ? { comparison: t("sharedHint", { shared: count(kpis.schools_by_tier.shared ?? 0) ?? "0" }) }
      : {}),
  });
  add(kpis?.trials_running != null, {
    key: "trials",
    label: t("trials"),
    value: count(kpis?.trials_running),
    ...(kpis?.trials_ending_14d != null
      ? { comparison: t("trialsEndingHint", { count: kpis.trials_ending_14d }) }
      : {}),
  });
  add(kpis?.past_due_count != null, {
    key: "pastDue",
    label: t("pastDue"),
    value: count(kpis?.past_due_count),
    ...(kpis?.past_due_amount_inr != null
      ? {
          comparison: t("pastDueHint", {
            amount: formatInr(kpis.past_due_amount_inr, locale) ?? "",
            date: formatDate(kpis.oldest_overdue_due_date) ?? "—",
          }),
        }
      : {}),
  });
  add(fleet != null, {
    key: "fleet",
    label: t("fleetHealth"),
    value: fleet
      ? t("fleetHealthShort", {
          healthy: count(fleet.healthy ?? 0) ?? "0",
          total: count(fleetTotal) ?? "0",
        })
      : null,
    ...(fleet
      ? {
          comparison: (
            <>
              <span>
                {t("fleetHealthValue", {
                  healthy: count(fleet.healthy ?? 0) ?? "0",
                  total: count(fleetTotal) ?? "0",
                })}
              </span>
              {kpis?.fleet_versions ? (
                <>
                  {" · "}
                  <span>
                    {t("versionsHint", { count: Object.keys(kpis.fleet_versions).length })}
                  </span>
                </>
              ) : null}
            </>
          ),
          ...(fleetTotal
            ? {
                aside: (
                  <ProgressRing
                    value={((fleet.healthy ?? 0) / fleetTotal) * 100}
                    label={t("fleetHealthyShare")}
                    size="sm"
                    color={3}
                  />
                ),
              }
            : {}),
        }
      : {}),
  });
  add(kpis?.ai_spend_mtd_inr != null, {
    key: "ai",
    label: t("aiSpend"),
    value: formatInr(kpis?.ai_spend_mtd_inr, locale),
  });
  add(tickets != null, {
    key: "tickets",
    label: t("openTickets"),
    value: count(sum(tickets)),
    ...(kpis?.tickets_sla_breached != null
      ? { comparison: t("slaBreachedHint", { count: kpis.tickets_sla_breached }) }
      : {}),
  });

  // Needs attention: only non-zero numbers the API returned.
  const attention: AttentionItem[] = [];
  if (kpis) {
    const unhealthy = fleet ? (fleet.degraded ?? 0) + (fleet.unreachable ?? 0) : 0;
    if (unhealthy > 0) {
      attention.push({
        key: "fleet",
        icon: "server",
        text: t("attention.fleet", { count: unhealthy }),
        href: fleet?.unreachable
          ? "/platform/fleet?status=unreachable"
          : "/platform/fleet?status=degraded",
        linkLabel: t("attention.openFleet"),
      });
    }
    if ((kpis.past_due_count ?? 0) > 0) {
      attention.push({
        key: "pastDue",
        icon: "receipt",
        text: t("attention.pastDue", { count: kpis.past_due_count ?? 0 }),
        href: "/platform/schools?past_due=true",
        linkLabel: t("attention.openSchools"),
      });
    }
    if ((kpis.tickets_sla_breached ?? 0) > 0) {
      attention.push({
        key: "sla",
        icon: "lifeBuoy",
        text: t("attention.sla", { count: kpis.tickets_sla_breached ?? 0 }),
        href: "/platform/support",
        linkLabel: t("attention.openSupport"),
      });
    }
    if ((kpis.trials_ending_14d ?? 0) > 0) {
      attention.push({
        key: "trials",
        icon: "clock",
        text: t("attention.trials", { count: kpis.trials_ending_14d ?? 0 }),
        href: "/platform/schools?trial_ending=true",
        linkLabel: t("attention.openSchools"),
      });
    }
    if ((schools?.provisioning ?? 0) > 0) {
      attention.push({
        key: "provisioning",
        icon: "building",
        text: t("attention.provisioning", { count: schools?.provisioning ?? 0 }),
        href: "/platform/schools?status=provisioning",
        linkLabel: t("attention.openSchools"),
      });
    }
    if ((schools?.suspended ?? 0) > 0) {
      attention.push({
        key: "suspended",
        icon: "lock",
        text: t("attention.suspended", { count: schools?.suspended ?? 0 }),
        href: "/platform/schools?status=suspended",
        linkLabel: t("attention.openSchools"),
      });
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader title={t("title")} description={t("description")} />
      {notice}
      <section aria-label={t("kpisLabel")}>
        {cards.length === 0 ? (
          <Alert tone="info">{t("noTiles")}</Alert>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {cards.map((card) => (
              <KpiCard
                key={card.key}
                label={card.label}
                value={card.value}
                unavailableLabel={tc("notAvailable")}
                comparison={card.comparison}
                aside={card.aside}
              />
            ))}
          </div>
        )}
      </section>
      {kpis ? (
        <Card title={t("attention.title")} description={t("attention.description")}>
          {attention.length === 0 ? (
            <EmptyState
              icon="checkCircle"
              title={t("attention.noneTitle")}
              body={t("attention.noneBody")}
            />
          ) : (
            <ul className="divide-y divide-border">
              {attention.map((item) => (
                <li
                  key={item.key}
                  className="flex flex-wrap items-center justify-between gap-3 py-3 first:pt-0 last:pb-0"
                >
                  <span className="flex min-w-0 items-center gap-3">
                    <span className="flex size-9 shrink-0 items-center justify-center rounded-full bg-violet-soft text-violet-ink">
                      <Icon name={item.icon} className="size-4.5" />
                    </span>
                    <span className="text-ink">{item.text}</span>
                  </span>
                  <Link
                    href={item.href}
                    className="inline-flex items-center gap-1 rounded-sm text-sm font-medium text-primary underline-offset-4 hover:underline"
                  >
                    {item.linkLabel}
                    <Icon name="arrowRight" className="size-4" />
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Card>
      ) : null}
      <Alert tone="info">{tp("noStudentData")}</Alert>
    </div>
  );
}
