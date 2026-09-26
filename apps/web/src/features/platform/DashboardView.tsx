import type { PlatformKpis } from "@schoolos/api-client";
import { useLocale, useTranslations } from "next-intl";
import { Alert } from "@/components/ui/Alert";
import { PageHeader } from "@/components/ui/PageHeader";
import { StatCard } from "@/components/ui/StatCard";
import { formatCount, formatInr } from "@/lib/format";

/** C14 dashboard. Every KPI shows "—" until its value is available. */
export function DashboardView({ kpis }: { kpis: PlatformKpis | null }) {
  const t = useTranslations("platform");
  const tc = useTranslations("common");
  const locale = useLocale();
  const count = (value: number | null | undefined) => formatCount(value, locale);

  const healthy = kpis?.fleet_healthy ?? null;
  const total = kpis?.fleet_total ?? null;
  const fleetHealth =
    healthy !== null && total !== null
      ? t("dashboard.fleetHealthValue", {
          healthy: count(healthy) ?? "",
          total: count(total) ?? "",
        })
      : null;

  const cards: Array<{ key: string; label: string; value: string | null }> = [
    { key: "mrr", label: t("dashboard.mrr"), value: formatInr(kpis?.mrr_inr, locale) },
    { key: "arr", label: t("dashboard.arr"), value: formatInr(kpis?.arr_inr, locale) },
    { key: "active", label: t("dashboard.activeSchools"), value: count(kpis?.active_schools) },
    { key: "trials", label: t("dashboard.trials"), value: count(kpis?.trial_schools) },
    { key: "pastDue", label: t("dashboard.pastDue"), value: count(kpis?.past_due_subscriptions) },
    { key: "fleet", label: t("dashboard.fleetHealth"), value: fleetHealth },
    {
      key: "ai",
      label: t("dashboard.aiSpend"),
      value: formatInr(kpis?.ai_spend_month_inr, locale),
    },
  ];

  return (
    <div className="space-y-6">
      <PageHeader title={t("dashboard.title")} description={t("dashboard.description")} />
      <section aria-label={t("dashboard.kpisLabel")}>
        <dl className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {cards.map((card) => (
            <StatCard
              key={card.key}
              label={card.label}
              value={card.value}
              unavailableLabel={tc("notAvailable")}
            />
          ))}
        </dl>
      </section>
      <Alert tone="info">{t("noStudentData")}</Alert>
    </div>
  );
}
