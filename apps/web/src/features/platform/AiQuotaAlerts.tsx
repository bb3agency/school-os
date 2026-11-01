"use client";

import type { AiQuotaAlert } from "@schoolos/api-client";
import { useLocale, useTranslations } from "next-intl";
import { Badge } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { formatCount, formatDate, formatDateTime } from "@/lib/format";
import type { Loadable } from "@/lib/loadable";

/**
 * Operator usage view: schools that used 80 % or all of their AI answer bundle's included
 * answers this calendar month (ADR-0038 follow-up, docs/16 §19 Q16; FR-PLT-020). One row per
 * school with its highest alert, recorded once per month and threshold by the usage collector.
 * Counts only. Hidden while loading or failed: the usage table below carries its own states.
 */
export function AiQuotaAlerts({
  alerts,
  schoolName,
}: {
  alerts: Loadable<readonly AiQuotaAlert[]>;
  schoolName: (tenantId: string) => string;
}) {
  const t = useTranslations("platform.usage.quota");
  const locale = useLocale();
  if (alerts.status !== "ready") return null;
  const rows = [...alerts.data].sort((a, b) => b.threshold - a.threshold);
  const month = rows[0]?.month_start;
  return (
    <Card
      tone="muted"
      title={t("title")}
      headingLevel={2}
      description={month ? t("description", { month: formatDate(month) ?? "" }) : undefined}
    >
      {rows.length === 0 ? (
        <p className="text-sm text-ink-muted">{t("none")}</p>
      ) : (
        <ul className="divide-y divide-border">
          {rows.map((row) => (
            <li
              key={row.tenant_id}
              className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 py-2"
            >
              <span className="min-w-0 break-words font-semibold text-ink">
                {schoolName(row.tenant_id)}
              </span>
              <span className="flex flex-wrap items-center gap-3 text-sm text-ink-muted">
                <Badge tone={row.threshold === 100 ? "warning" : "info"}>
                  {row.threshold === 100 ? t("usedUp") : t("near")}
                </Badge>
                <span className="num">
                  {t("usedOf", {
                    used: formatCount(row.answers_used, locale) ?? "0",
                    included: formatCount(row.included_answers, locale) ?? "0",
                  })}
                </span>
                <span className="num">{formatDateTime(row.crossed_at) ?? ""}</span>
              </span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
