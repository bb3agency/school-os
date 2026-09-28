"use client";

import type { UsageDaily } from "@schoolos/api-client";
import { useLocale, useTranslations } from "next-intl";
import { Card } from "@/components/ui/Card";
import { UsageMeter } from "@/components/ui/UsageMeter";
import { formatCount, formatDate } from "@/lib/format";
import type { Loadable } from "@/lib/loadable";
import { usePlanDirectory } from "./data";

/** Point-in-time metrics that the API meters against a plan limit (docs/16 §5.10). */
const METRICS = [
  { key: "students", column: "students_active" },
  { key: "staff_users", column: "staff_users" },
  { key: "documents", column: "documents" },
  { key: "storage_gb", column: "storage_bytes" },
] as const;

/**
 * The school's latest metered day against its plan's limits (FR-PLT-021). Only real
 * numbers: the latest usage row the API returned and the plan's own limits. A metric with no
 * limit shows its number without a meter. AI period totals are not shown here because the
 * daily rows do not carry the billing period's running total.
 */
export function PlanUsageMeters({
  usage,
  planId,
}: {
  usage: Loadable<readonly UsageDaily[]>;
  planId: string | null;
}) {
  const t = useTranslations("platform.usage");
  const tl = useTranslations("platform.plans.limits");
  const locale = useLocale();
  const { plans } = usePlanDirectory();
  const plan = planId ? plans.find((candidate) => candidate.id === planId) : undefined;
  if (usage.status !== "ready" || usage.data.length === 0 || !plan) return null;
  const latest = usage.data.reduce((a, b) => (b.usage_date > a.usage_date ? b : a));
  const gb = (bytes: number) =>
    new Intl.NumberFormat(locale === "te" ? "te-IN" : "en-IN", {
      maximumFractionDigits: 1,
      numberingSystem: "latn",
    }).format(bytes / 1e9);

  return (
    <Card
      tone="muted"
      title={t("limitsTitle")}
      headingLevel={3}
      description={t("limitsDescription", { date: formatDate(latest.usage_date) ?? "" })}
    >
      <div className="grid gap-5 md:grid-cols-2">
        {METRICS.map((metric) => {
          const raw = plan.limits[metric.key];
          const limit = typeof raw === "number" ? raw : null;
          const used = latest[metric.column];
          const storage = metric.key === "storage_gb";
          const usedValue = storage ? used / 1e9 : used;
          const usedText = storage ? gb(used) : (formatCount(used, locale) ?? "0");
          const limitText = limit === null ? null : (formatCount(limit, locale) ?? "");
          const valueText =
            limitText === null
              ? storage
                ? t("meterGbNoLimit", { used: usedText })
                : t("meterNoLimit", { used: usedText })
              : storage
                ? t("meterGb", { used: usedText, limit: limitText })
                : t("meter", { used: usedText, limit: limitText });
          return (
            <UsageMeter
              key={metric.key}
              label={tl(metric.key)}
              used={usedValue}
              limit={limit}
              valueText={valueText}
            />
          );
        })}
      </div>
    </Card>
  );
}
