"use client";

import { useQuery } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Card } from "@/components/ui/Card";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { KpiCard } from "@/components/ui/StatCard";
import { COMPLETENESS_KEYS, COUNTS_READ, fetchFollowUpCounts } from "@/features/completeness/data";
import type { Locale } from "@/i18n/routing";
import { useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
import { formatCount, formatDate } from "@/lib/format";
import { CLOSE_REASONS } from "./data";

/**
 * Early-warning follow-up counts for management (FT-13, FR-EW-026..030): numbers for the whole
 * school only, never a name, a student or a flag (08 PRV-004). The flags themselves stay with
 * the class teacher and the principal.
 */
export function FollowUpCountsScreen() {
  const t = useTranslations("followUpCounts");
  const tr = useTranslations("insights.close.reason");
  const tc = useTranslations("common");
  const tn = useTranslations("school.nav");
  const locale = useLocale() as Locale;
  const api = useBffClient("staff");
  const can = useStaffCan();
  const meLoaded = useStaffMe() !== undefined;
  const allowed = can(COUNTS_READ);
  const counts = useQuery({
    queryKey: COMPLETENESS_KEYS.counts(""),
    queryFn: () => fetchFollowUpCounts(api, ""),
    enabled: allowed,
    retry: false,
  });
  const header = (
    <PageHeader
      title={t("title")}
      description={t("description")}
      breadcrumb={[{ label: tn("home"), href: "/" }, { label: t("title") }]}
    />
  );
  if (!meLoaded) {
    return (
      <div className="space-y-5">
        {header}
        <LoadingState label={tc("loading")} />
      </div>
    );
  }
  if (!allowed) {
    return (
      <div className="space-y-5">
        {header}
        <Alert tone="info" title={t("noPermissionTitle")}>
          {t("noPermissionBody")}
        </Alert>
      </div>
    );
  }
  const data = counts.data;
  const show = (value: number) => formatCount(value, locale);
  return (
    <div className="space-y-5">
      {header}
      {counts.error ? <ApiErrorAlert error={counts.error} /> : null}
      {!data ? (
        counts.error ? null : (
          <LoadingState label={tc("loading")} />
        )
      ) : (
        <>
          <p className="text-sm text-ink-muted">
            {t("since", { date: formatDate(data.since) ?? data.since })}
          </p>
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <KpiCard
              label={t("raised")}
              value={show(data.raised)}
              unavailableLabel={tc("notAvailable")}
              comparison={t("onTime", { count: data.actioned_on_time })}
            />
            <KpiCard
              label={t("overdue")}
              value={show(data.overdue)}
              unavailableLabel={tc("notAvailable")}
            />
            <KpiCard
              label={t("reviewDue")}
              value={show(data.review_due)}
              unavailableLabel={tc("notAvailable")}
            />
            <KpiCard
              label={t("open")}
              value={show(data.open)}
              unavailableLabel={tc("notAvailable")}
            />
          </div>
          <Card className="space-y-3">
            <h2 className="text-lg font-semibold text-ink">
              {t("outcomes", { count: data.closed })}
            </h2>
            <dl className="divide-y divide-border">
              {CLOSE_REASONS.map((reason) => (
                <div key={reason} className="flex items-baseline justify-between gap-4 py-2">
                  <dt className="text-sm text-ink">{tr(reason)}</dt>
                  <dd className="num text-ink">{show(data.outcomes[reason] ?? 0)}</dd>
                </div>
              ))}
            </dl>
          </Card>
          <p className="text-xs text-ink-subtle">{t("footnote")}</p>
        </>
      )}
    </div>
  );
}
