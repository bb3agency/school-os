"use client";

import { useLocale, useTranslations } from "next-intl";
import * as z from "@/lib/zod";
import { Badge } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { createBffFetch } from "@/lib/bff/fetch";
import { ApiError } from "@/lib/bff/query";
import { formatCount, formatDateTime, formatInr } from "@/lib/format";
import type { Loadable } from "@/lib/loadable";

/**
 * `GET /platform/usage/limit-alerts` (`PlanLimitAlertOut`; docs/16 §5.10, D57). Not in the
 * generated client yet: validated here with a local schema that matches the API. Decimals come
 * as strings.
 */
export const planLimitAlertSchema = z.object({
  tenant_id: z.string(),
  metric: z.string(),
  threshold: z.union([z.literal(80), z.literal(100)]),
  period_start: z.string(),
  usage_value: z.union([z.string(), z.number()]),
  limit_value: z.union([z.string(), z.number()]),
  crossed_at: z.string(),
});
export type PlanLimitAlert = z.infer<typeof planLimitAlertSchema>;

export async function loadPlanLimitAlerts(locale: string): Promise<PlanLimitAlert[]> {
  const send = createBffFetch({ kind: "operator", locale });
  const response = await send(
    new Request(`${window.location.origin}/bff/api/v1/platform/usage/limit-alerts`),
  );
  const body: unknown = await response.json().catch(() => ({}));
  if (!response.ok) {
    const problem = (body ?? {}) as { code?: string };
    throw new ApiError(response.status, problem.code, problem);
  }
  return z.array(planLimitAlertSchema).parse(body);
}

/**
 * Operator usage view: each school's crossings of 80 % and 100 % of its plan limits in its
 * current billing period (docs/16 §5.10, §19 Q16; FR-PLT-021, D57), limit reached first, then
 * newest. The owner and principal are told in the app; limits never block work. Counts only.
 * Hidden while loading or failed: the usage table below carries its own states.
 */
export function PlanLimitAlerts({
  alerts,
  schoolName,
}: {
  alerts: Loadable<readonly PlanLimitAlert[]>;
  schoolName: (tenantId: string) => string;
}) {
  const t = useTranslations("platform.usage.limitAlerts");
  const locale = useLocale();
  if (alerts.status !== "ready") return null;
  const rows = [...alerts.data].sort(
    (a, b) => b.threshold - a.threshold || b.crossed_at.localeCompare(a.crossed_at),
  );
  const value = (metric: string, raw: string | number) =>
    metric === "ai_budget_inr"
      ? (formatInr(raw, locale) ?? "")
      : (formatCount(Number(raw), locale) ?? String(raw));
  return (
    <Card tone="muted" title={t("title")} headingLevel={2} description={t("description")}>
      {rows.length === 0 ? (
        <p className="text-sm text-ink-muted">{t("none")}</p>
      ) : (
        <ul className="divide-y divide-border">
          {rows.map((row) => (
            <li
              key={`${row.tenant_id}:${row.period_start}:${row.metric}:${row.threshold}`}
              className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 py-2"
            >
              <span className="min-w-0 break-words font-semibold text-ink">
                {schoolName(row.tenant_id)}
              </span>
              <span className="flex flex-wrap items-center gap-3 text-sm text-ink-muted">
                <span>{t("metric", { metric: row.metric })}</span>
                <Badge tone={row.threshold === 100 ? "warning" : "info"}>
                  {row.threshold === 100 ? t("reached") : t("near")}
                </Badge>
                <span className="num">
                  {t("usedOf", {
                    used: value(row.metric, row.usage_value),
                    limit: value(row.metric, row.limit_value),
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
