"use client";

import type { TenantAiAnswers } from "@schoolos/api-client";
import { useLocale, useTranslations } from "next-intl";
import { Card } from "@/components/ui/Card";
import { UsageMeter } from "@/components/ui/UsageMeter";
import { formatCount, formatDate, formatInr } from "@/lib/format";
import type { Loadable } from "@/lib/loadable";

/**
 * A school's AI answers this calendar month against its bundle (docs/18 R4, FR-PLT-020,
 * ADR-0038): answers so far, the included quota, extra answers and their ex-GST overage, billed
 * on the next invoice. The same numbers the school sees on its Plan and billing page; counts
 * and catalogue prices only, straight from the API (no estimate, no tokens). Hidden while the
 * request is loading or failed: the usage table below carries its own states.
 */
export function AiAnswersMeter({ answers }: { answers: Loadable<TenantAiAnswers> }) {
  const t = useTranslations("platform.usage.ai");
  const locale = useLocale();
  if (answers.status !== "ready") return null;
  const bundle = answers.data.bundle ?? null;
  if (bundle === null) {
    return (
      <Card tone="muted" title={t("title")} headingLevel={3}>
        <p className="text-sm text-ink-muted">{t("none")}</p>
      </Card>
    );
  }
  const included = formatCount(bundle.included_answers, locale) ?? "";
  const counting = bundle.answers_used !== null;
  return (
    <Card
      tone="muted"
      title={t("title")}
      headingLevel={3}
      description={t("description", { month: formatDate(bundle.month_start) ?? "" })}
    >
      <div className="space-y-4">
        <p className="font-semibold text-ink">{t("bundleName", { name: bundle.name })}</p>
        {counting ? (
          <>
            <UsageMeter
              label={t("used")}
              used={bundle.answers_used ?? 0}
              limit={bundle.included_answers}
              valueText={t("usedOf", {
                used: formatCount(bundle.answers_used, locale) ?? "0",
                included,
              })}
            />
            <dl className="grid gap-4 sm:grid-cols-2">
              <div className="rounded-lg bg-surface p-4">
                <dt className="text-sm text-ink-muted">{t("extra")}</dt>
                <dd className="num mt-1 text-ink">
                  {formatCount(bundle.extra_answers ?? 0, locale)}
                </dd>
              </div>
              <div className="rounded-lg bg-surface p-4">
                <dt className="text-sm text-ink-muted">{t("overage")}</dt>
                <dd className="num mt-1 text-ink">
                  {formatInr(bundle.overage_so_far_inr ?? "0", locale)}
                </dd>
              </div>
            </dl>
            <p className="num text-xs text-ink-muted">
              {bundle.answers_counted_to
                ? t("countedTo", {
                    date: formatDate(bundle.answers_counted_to) ?? "",
                    rate: formatInr(bundle.overage_rate_inr, locale) ?? "",
                  })
                : t("notCountedYet")}
            </p>
          </>
        ) : (
          <p className="text-sm text-ink-muted">
            {t("countsFrom", { date: formatDate(bundle.counts_from) ?? "" })}
          </p>
        )}
      </div>
    </Card>
  );
}
