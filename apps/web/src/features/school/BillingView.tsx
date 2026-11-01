"use client";

import type {
  SchoolAiBundle,
  TenantBilling,
  TenantInvoice,
  UsageAgainstLimit,
} from "@schoolos/api-client";
import { useQueryClient } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { useId } from "react";
import { DataTableV2, type ColumnV2 } from "@/components/console";
import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { UsageMeter } from "@/components/ui/UsageMeter";
import { Value } from "@/components/ui/Value";
import { invoiceTone, known, subscriptionTone } from "@/features/status";
import { unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import { formatCount, formatDate, formatInr } from "@/lib/format";
import type { Loadable } from "@/lib/loadable";

export interface BillingViewProps {
  billing: Loadable<TenantBilling>;
  invoices: Loadable<readonly TenantInvoice[]>;
  /** Loads the invoices again after an error ("Try again"). */
  onRetryInvoices?: () => void;
}

const METRICS = [
  "students",
  "staff_users",
  "documents",
  "storage_gb",
  // No "ai_tokens_month": schools never see tokens; the AI answers card shows their AI use
  // (ADR-0038; owner decision 2026-10-04).
  "ai_budget_inr",
] as const;
type Metric = (typeof METRICS)[number];

function isMetric(value: string): value is Metric {
  return (METRICS as readonly string[]).includes(value);
}

/**
 * The school's AI answer bundle (ADR-0038, docs/16 §5.18): name, included answers, this
 * month's answers against them, and the ex-GST prices. Facts from the API only: no cost
 * estimate, no tokens.
 */
function AiAnswersCard({ bundle }: { bundle: SchoolAiBundle | null }) {
  const t = useTranslations("school.billing.ai");
  const locale = useLocale();
  if (bundle === null) {
    return (
      <Card title={t("title")}>
        <p className="text-sm text-ink-muted">{t("none")}</p>
      </Card>
    );
  }
  const included = formatCount(bundle.included_answers, locale) ?? "";
  return (
    <Card title={t("title")} description={t("description")}>
      <div className="space-y-5">
        <p className="text-2xl font-semibold text-ink">
          <span className="sr-only">{t("bundle")}: </span>
          {t("bundleName", { name: bundle.name })}
        </p>
        <dl className="grid gap-4 sm:grid-cols-3">
          <div className="rounded-lg bg-surface-muted p-4">
            <dt className="text-sm text-ink-muted">{t("included")}</dt>
            <dd className="num mt-1 text-ink">{t("perMonth", { value: included })}</dd>
          </div>
          <div className="rounded-lg bg-surface-muted p-4">
            <dt className="text-sm text-ink-muted">{t("price")}</dt>
            <dd className="num mt-1 text-ink">
              {t("pricePerMonth", { amount: formatInr(bundle.price_inr, locale) ?? "" })}
            </dd>
          </div>
          <div className="rounded-lg bg-surface-muted p-4">
            <dt className="text-sm text-ink-muted">{t("overage")}</dt>
            <dd className="num mt-1 text-ink">
              {t("overageRate", { amount: formatInr(bundle.overage_rate_inr, locale) ?? "" })}
            </dd>
          </div>
        </dl>
        {bundle.answers_used !== null ? (
          <div className="space-y-2">
            <UsageMeter
              label={t("thisMonth")}
              used={bundle.answers_used}
              limit={bundle.included_answers}
              valueText={t("usedOf", {
                used: formatCount(bundle.answers_used, locale) ?? "0",
                included,
              })}
            />
            <p className="num text-xs text-ink-muted">
              {bundle.answers_counted_to
                ? t("countedTo", { date: formatDate(bundle.answers_counted_to) ?? "" })
                : t("notCountedYet")}
            </p>
            {bundle.extra_answers ? (
              <p className="num text-sm font-semibold text-ink">
                {t("extraSoFar", {
                  count: bundle.extra_answers,
                  extra: formatCount(bundle.extra_answers, locale) ?? "",
                  amount: formatInr(bundle.overage_so_far_inr, locale) ?? "",
                })}
              </p>
            ) : null}
          </div>
        ) : (
          <p className="text-sm text-ink-muted">
            {t("countsFrom", { date: formatDate(bundle.counts_from) ?? "" })}
          </p>
        )}
        <p className="text-xs text-ink-muted">{t("overageNote")}</p>
      </div>
    </Card>
  );
}

/** FR-PLT-030 / US-1204 (docs/16 §5.18): plan, usage vs limits, AI answers and invoices. */
export function BillingView({ billing, invoices, onRetryInvoices }: BillingViewProps) {
  const t = useTranslations("school.billing");
  const tc = useTranslations("common");
  const te = useTranslations("errors");
  const tsub = useTranslations("status.subscription");
  const tinv = useTranslations("status.invoice");
  const locale = useLocale();
  const invoicesHeadingId = useId();

  function formatMetric(metric: Metric, value: string): string {
    if (metric === "ai_budget_inr") return formatInr(value, locale) ?? value;
    if (metric === "storage_gb") return t("gigabytes", { value: Number(value) });
    return formatCount(Number(value), locale) ?? value;
  }

  function usageText(item: UsageAgainstLimit & { metric: Metric }): string {
    const used = formatMetric(item.metric, item.used);
    if (item.limit === null) return t("usageNoLimit", { used });
    return t("usageOf", { used, limit: formatMetric(item.metric, item.limit) });
  }

  const invoiceColumns: ColumnV2<TenantInvoice>[] = [
    {
      key: "number",
      header: t("colNumber"),
      name: t("colNumber"),
      className: "whitespace-nowrap",
      cell: (row) => (
        <span className="font-mono text-xs">
          <Value>{row.invoice_number}</Value>
        </span>
      ),
    },
    {
      key: "period",
      header: t("colPeriod"),
      name: t("colPeriod"),
      className: "whitespace-nowrap",
      cell: (row) => (
        <span className="num">
          {`${formatDate(row.period_start) ?? ""} – ${formatDate(row.period_end) ?? ""}`}
        </span>
      ),
    },
    {
      key: "date",
      header: t("colDate"),
      name: t("colDate"),
      className: "whitespace-nowrap",
      cell: (row) => (
        <span className="num">
          <Value>{formatDate(row.issue_date)}</Value>
        </span>
      ),
    },
    {
      key: "due",
      header: t("colDue"),
      name: t("colDue"),
      className: "whitespace-nowrap",
      cell: (row) => (
        <span className="num">
          <Value>{formatDate(row.due_date)}</Value>
        </span>
      ),
    },
    {
      key: "amount",
      header: t("colAmount"),
      name: t("colAmount"),
      numeric: true,
      cell: (row) => <Value>{formatInr(row.total_inr, locale)}</Value>,
    },
    {
      key: "dueAmount",
      header: t("colAmountDue"),
      name: t("colAmountDue"),
      numeric: true,
      cell: (row) => <Value>{formatInr(row.amount_due_inr, locale)}</Value>,
    },
    {
      key: "status",
      header: t("colStatus"),
      name: t("colStatus"),
      cell: (row) => {
        const status = known(invoiceTone, row.status);
        return status ? (
          <Badge tone={invoiceTone[status]}>{tinv(status)}</Badge>
        ) : (
          <Badge>{row.status}</Badge>
        );
      },
    },
  ];

  const plan = billing.status === "ready" ? billing.data : null;
  const subStatus = plan?.status ? known(subscriptionTone, plan.status) : null;
  const usage = (plan?.usage ?? []).filter((item): item is UsageAgainstLimit & { metric: Metric } =>
    isMetric(item.metric),
  );

  return (
    <div className="space-y-5">
      <PageHeader
        title={t("title")}
        description={t("description")}
        breadcrumb={[{ label: t("home"), href: "/" }, { label: t("title") }]}
      />
      {billing.status === "error" ? (
        <Alert tone="danger" title={tc("loadErrorTitle")}>
          {billing.reason ? te(`load.${billing.reason}`) : tc("loadErrorBody")}
        </Alert>
      ) : null}
      {plan && plan.available && plan.status === "past_due" ? (
        <Alert tone="warning" title={t("pastDueTitle")}>
          {t("pastDueBody", {
            amount: formatInr(plan.amount_due_inr, locale) ?? "",
            date: formatDate(plan.grace_ends_on) ?? "—",
          })}
        </Alert>
      ) : null}
      <div className="grid gap-4 xl:grid-cols-2 [&>*]:min-w-0">
        <Card title={t("currentPlan")} description={t("changePlanHint")}>
          {billing.status === "loading" ? <LoadingState label={tc("loading")} /> : null}
          {plan && !plan.available ? <Alert tone="info">{t("planUnavailable")}</Alert> : null}
          {plan && plan.available ? (
            <div className="space-y-5">
              <div className="flex flex-wrap items-center gap-3">
                <p className="text-2xl font-semibold text-ink">
                  <span className="sr-only">{t("plan")}: </span>
                  <Value>{plan.plan_name}</Value>
                </p>
                {subStatus ? (
                  <Badge tone={subscriptionTone[subStatus]}>
                    <span className="sr-only">{t("status")}: </span>
                    {tsub(subStatus)}
                  </Badge>
                ) : (
                  <Value>{plan.status}</Value>
                )}
                {plan.cancel_at_period_end ? (
                  <span className="text-sm text-ink-muted">{t("endsAtPeriodEnd")}</span>
                ) : null}
              </div>
              <dl className="grid gap-4 sm:grid-cols-2">
                <div className="rounded-lg bg-surface-muted p-4">
                  <dt className="text-sm text-ink-muted">{t("amountDue")}</dt>
                  <dd className="num mt-1 text-[1.75rem] leading-8 font-semibold text-ink">
                    <Value>{formatInr(plan.amount_due_inr, locale)}</Value>
                  </dd>
                </div>
                <div className="rounded-lg bg-surface-muted p-4">
                  <dt className="text-sm text-ink-muted">{t("period")}</dt>
                  <dd className="num mt-2 text-sm text-ink">
                    <Value>
                      {plan.current_period_start && plan.current_period_end
                        ? `${formatDate(plan.current_period_start) ?? ""} – ${formatDate(plan.current_period_end) ?? ""}`
                        : null}
                    </Value>
                  </dd>
                  {plan.status === "trial" ? (
                    <>
                      <dt className="mt-3 text-sm text-ink-muted">{t("trialEndsOn")}</dt>
                      <dd className="num mt-1 text-sm text-ink">
                        <Value>{formatDate(plan.trial_ends_at)}</Value>
                      </dd>
                    </>
                  ) : null}
                </div>
              </dl>
            </div>
          ) : null}
        </Card>
        <Card
          title={t("usageTitle")}
          description={
            plan?.usage_date
              ? t("usageAsOf", { date: formatDate(plan.usage_date) ?? "" })
              : undefined
          }
        >
          {billing.status === "loading" ? <LoadingState label={tc("loading")} /> : null}
          {plan && plan.available && usage.length > 0 ? (
            <div className="space-y-4">
              {usage.map((item) => (
                <UsageMeter
                  key={item.metric}
                  label={t(`usage.${item.metric}`)}
                  used={Number(item.used)}
                  limit={item.limit === null ? null : Number(item.limit)}
                  valueText={usageText(item)}
                />
              ))}
              <p className="text-xs text-ink-muted">{t("limitsNote")}</p>
            </div>
          ) : billing.status === "ready" ? (
            <p className="text-sm text-ink-muted">{t("usageUnavailable")}</p>
          ) : null}
        </Card>
      </div>
      {plan && plan.available ? <AiAnswersCard bundle={plan.ai_bundle ?? null} /> : null}
      <section aria-labelledby={invoicesHeadingId} className="space-y-3">
        <div className="space-y-1">
          <h2 id={invoicesHeadingId} className="text-lg font-semibold text-ink">
            {t("invoicesTitle")}
          </h2>
          <p className="text-sm text-ink-muted">{t("invoicesNote")}</p>
        </div>
        <DataTableV2
          id="school-invoices"
          caption={t("invoicesTitle")}
          captionHidden
          columns={invoiceColumns}
          state={invoices}
          rowKey={(row) => row.invoice_id}
          emptyTitle={t("emptyTitle")}
          emptyBody={t("emptyBody")}
          {...(onRetryInvoices ? { onRetry: onRetryInvoices } : {})}
          stacked
        />
      </section>
    </div>
  );
}

/** Loads GET /tenant/billing and /tenant/billing/invoices (tenant.billing.read). */
export function BillingScreen() {
  const api = useBffClient("staff");
  const queryClient = useQueryClient();
  const billing = useApiQuery(["staff", "billing"], () =>
    unwrap(api.GET("/api/v1/tenant/billing")),
  );
  const invoices = useApiQuery(
    ["staff", "billing", "invoices"],
    async () => (await unwrap(api.GET("/api/v1/tenant/billing/invoices"))).data,
  );
  return (
    <BillingView
      billing={billing}
      invoices={invoices}
      onRetryInvoices={() =>
        void queryClient.invalidateQueries({ queryKey: ["staff", "billing", "invoices"] })
      }
    />
  );
}
