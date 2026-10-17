"use client";

import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";
import { Alert } from "@/components/ui/Alert";
import { Icon } from "@/components/ui/Icon";
import { formatCount } from "@/lib/format";
import { noticeOfReason } from "./answer";
import { bannerKey, bannerOf, showsNumbers, usedPercent, type Usage } from "./usage";

/**
 * The Ask page's answer meter and limit banners (R2.4; FR-KB-040, FR-KB-041; docs/17 §5.8
 * "Ask usage").
 *
 * - {@link AskUsageMeter}: for `tenant.billing.read` holders only (the API sends the numbers
 *   only to them): "312 of 1,000 answers this month", or "312 answers this month" without a
 *   bundle. No colour alarm below 80 %; from `near_limit` amber text "Near your monthly
 *   answers" (the words carry it, not the colour alone).
 * - {@link AskUsageBanner}: above the composer. Everyone sees it while answers are search-only,
 *   with the reason and what to do; billing readers also see the near-limit notice. Dismissible
 *   for this page view; a new month or another state shows it again.
 */

export function AskUsageMeter({ usage }: { usage: Usage | undefined }) {
  const t = useTranslations("ask.usage");
  const locale = useLocale();
  if (!showsNumbers(usage)) return null;
  const used = formatCount(usage.answers_used, locale) ?? "0";
  const included =
    usage.included_answers == null ? null : (formatCount(usage.included_answers, locale) ?? "");
  const near = usage.state === "near_limit";
  const text = included === null ? t("meterNoBundle", { used }) : t("meterOf", { used, included });
  return (
    <p
      className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-ink-muted tabular-nums"
      data-testid="ask-usage-meter"
    >
      <Icon name="sparkles" className="size-3.5 shrink-0 text-primary" />
      <span>{text}</span>
      {usage.included_answers != null && usedPercent(usage) !== null ? (
        <meter
          aria-label={t("meterLabel")}
          aria-valuetext={text}
          className="h-1.5 w-24"
          min={0}
          max={usage.included_answers}
          low={usage.included_answers * 0.8}
          high={usage.included_answers}
          optimum={0}
          value={Math.min(usage.answers_used, usage.included_answers)}
        />
      ) : null}
      {near ? <span className="font-semibold text-warning-ink">{t("near")}</span> : null}
    </p>
  );
}

export function AskUsageBanner({ usage }: { usage: Usage | undefined }) {
  const t = useTranslations("ask.usage");
  const tk = useTranslations("ask.kbErrors");
  const [dismissed, setDismissed] = useState<string | null>(null);
  const banner = bannerOf(usage);
  if (!usage || !banner) return null;
  const key = bannerKey(usage, banner);
  if (dismissed === key) return null;
  const title = banner.kind === "near_limit" ? t("nearTitle") : t("searchOnlyTitle");
  return (
    <div className="relative mb-3" data-testid="ask-usage-banner">
      <Alert tone={banner.kind === "near_limit" ? "info" : "warning"} title={title}>
        {banner.kind === "near_limit" ? (
          <p>{t("nearBody")}</p>
        ) : (
          <>
            <p>{tk(noticeOfReason(banner.reason) ?? "unavailable")}</p>
            <p className="mt-1">
              {banner.reason === "ai_budget_exhausted" && showsNumbers(usage)
                ? t("nextBudgetBilling")
                : t(`next.${banner.reason}`)}
            </p>
          </>
        )}
      </Alert>
      <button
        type="button"
        onClick={() => setDismissed(key)}
        aria-label={t("dismiss", { title })}
        className="absolute top-2 right-2 inline-flex size-8 items-center justify-center rounded-md text-ink-muted hover:bg-surface-muted hover:text-ink pointer-coarse:size-11"
      >
        <Icon name="close" className="size-4" />
      </button>
    </div>
  );
}
