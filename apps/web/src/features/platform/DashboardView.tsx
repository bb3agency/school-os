import type { PlatformDashboard } from "@schoolos/api-client";
import { useLocale, useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { Alert } from "@/components/ui/Alert";
import { PageHeader } from "@/components/ui/PageHeader";
import { StatCard } from "@/components/ui/StatCard";
import { formatCount, formatDate, formatInr } from "@/lib/format";

type Counts = Record<string, number> | null | undefined;

function sum(counts: Counts): number | null {
  if (!counts) return null;
  return Object.values(counts).reduce((total, value) => total + value, 0);
}

/**
 * C14 dashboard (docs/16 §5.1). The API leaves a tile null when the operator lacks its read
 * permission; such tiles are not shown. Counts only, never student data.
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
  const cards: Array<{ key: string; label: string; value: string | null; hint?: string }> = [];
  const add = (
    key: string,
    present: boolean,
    label: string,
    value: string | null,
    hint?: string,
  ) => {
    if (kpis === null || present) cards.push({ key, label, value, ...(hint ? { hint } : {}) });
  };

  add("mrr", kpis?.mrr_inr != null, t("mrr"), formatInr(kpis?.mrr_inr, locale));
  add("arr", kpis?.arr_inr != null, t("arr"), formatInr(kpis?.arr_inr, locale));
  add(
    "active",
    schools != null,
    t("activeSchools"),
    count(schools ? (schools.active ?? 0) : null),
    schools
      ? t("schoolsHint", {
          suspended: count(schools.suspended ?? 0) ?? "0",
          offboarding: count(schools.offboarding ?? 0) ?? "0",
        })
      : undefined,
  );
  add(
    "tiers",
    kpis?.schools_by_tier != null,
    t("dedicatedSchools"),
    count(kpis?.schools_by_tier ? (kpis.schools_by_tier.dedicated ?? 0) : null),
    kpis?.schools_by_tier
      ? t("sharedHint", { shared: count(kpis.schools_by_tier.shared ?? 0) ?? "0" })
      : undefined,
  );
  add(
    "trials",
    kpis?.trials_running != null,
    t("trials"),
    count(kpis?.trials_running),
    kpis?.trials_ending_14d != null
      ? t("trialsEndingHint", { count: kpis.trials_ending_14d })
      : undefined,
  );
  add(
    "pastDue",
    kpis?.past_due_count != null,
    t("pastDue"),
    count(kpis?.past_due_count),
    kpis?.past_due_amount_inr != null
      ? t("pastDueHint", {
          amount: formatInr(kpis.past_due_amount_inr, locale) ?? "",
          date: formatDate(kpis.oldest_overdue_due_date) ?? "—",
        })
      : undefined,
  );
  add(
    "fleet",
    fleet != null,
    t("fleetHealth"),
    fleet
      ? t("fleetHealthValue", {
          healthy: count(fleet.healthy ?? 0) ?? "0",
          total: count(sum(fleet)) ?? "0",
        })
      : null,
    kpis?.fleet_versions
      ? t("versionsHint", { count: Object.keys(kpis.fleet_versions).length })
      : undefined,
  );
  add(
    "ai",
    kpis?.ai_spend_mtd_inr != null,
    t("aiSpend"),
    formatInr(kpis?.ai_spend_mtd_inr, locale),
  );
  add(
    "tickets",
    tickets != null,
    t("openTickets"),
    count(sum(tickets)),
    kpis?.tickets_sla_breached != null
      ? t("slaBreachedHint", { count: kpis.tickets_sla_breached })
      : undefined,
  );

  return (
    <div className="space-y-6">
      <PageHeader title={t("title")} description={t("description")} />
      {notice}
      <section aria-label={t("kpisLabel")}>
        {cards.length === 0 ? (
          <Alert tone="info">{t("noTiles")}</Alert>
        ) : (
          <dl className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            {cards.map((card) => (
              <StatCard
                key={card.key}
                label={card.label}
                value={card.value}
                unavailableLabel={tc("notAvailable")}
                hint={card.hint}
              />
            ))}
          </dl>
        )}
      </section>
      <Alert tone="info">{tp("noStudentData")}</Alert>
    </div>
  );
}
