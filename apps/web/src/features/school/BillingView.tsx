"use client";

import type { TenantBilling, TenantInvoice, UsageAgainstLimit } from "@schoolos/api-client";
import { useLocale, useTranslations } from "next-intl";
import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { DataTable, type Column } from "@/components/ui/Table";
import { UsageMeter } from "@/components/ui/UsageMeter";
import { Value } from "@/components/ui/Value";
import { invoiceTone, known, subscriptionTone } from "@/features/status";
import { unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import { formatCount, formatDate, formatInr } from "@/lib/format";
import type { Loadable } from "@/lib/loadable";

export interface BillingViewProps {
  billing: Loadable<TenantBilling>;
  invoices: Loadable<readonly TenantInvoice[]>;
}

const METRICS = [
  "students",
  "staff_users",
  "documents",
  "storage_gb",
  "ai_tokens_month",
  "ai_budget_inr",
] as const;
type Metric = (typeof METRICS)[number];

function isMetric(value: string): value is Metric {
  return (METRICS as readonly string[]).includes(value);
}

/** FR-PLT-030 / US-1204 (docs/16 §5.18): plan, usage vs limits and invoices. */
export function BillingView({ billing, invoices }: BillingViewProps) {
  const t = useTranslations("school.billing");
  const tc = useTranslations("common");
  const te = useTranslations("errors");
  const tsub = useTranslations("status.subscription");
  const tinv = useTranslations("status.invoice");
  const locale = useLocale();

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

  const invoiceColumns: Column<TenantInvoice>[] = [
    {
      key: "number",
      header: t("colNumber"),
      cell: (row) => <Value>{row.invoice_number}</Value>,
    },
    {
      key: "period",
      header: t("colPeriod"),
      cell: (row) => `${formatDate(row.period_start) ?? ""} – ${formatDate(row.period_end) ?? ""}`,
    },
    {
      key: "date",
      header: t("colDate"),
      cell: (row) => <Value>{formatDate(row.issue_date)}</Value>,
    },
    { key: "due", header: t("colDue"), cell: (row) => <Value>{formatDate(row.due_date)}</Value> },
    {
      key: "amount",
      header: t("colAmount"),
      className: "text-right tabular-nums",
      cell: (row) => <Value>{formatInr(row.total_inr, locale)}</Value>,
    },
    {
      key: "dueAmount",
      header: t("colAmountDue"),
      className: "text-right tabular-nums",
      cell: (row) => <Value>{formatInr(row.amount_due_inr, locale)}</Value>,
    },
    {
      key: "status",
      header: t("colStatus"),
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
    <div className="space-y-6">
      <PageHeader title={t("title")} description={t("description")} />
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
      <div className="grid gap-6 xl:grid-cols-2">
        <Card title={t("currentPlan")} description={t("changePlanHint")}>
          {billing.status === "loading" ? <LoadingState label={tc("loading")} /> : null}
          {plan && !plan.available ? <Alert tone="info">{t("planUnavailable")}</Alert> : null}
          {plan && plan.available ? (
            <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 text-sm">
              <dt className="text-ink-muted">{t("plan")}</dt>
              <dd className="font-semibold">
                <Value>{plan.plan_name}</Value>
              </dd>
              <dt className="text-ink-muted">{t("status")}</dt>
              <dd>
                {subStatus ? (
                  <Badge tone={subscriptionTone[subStatus]}>{tsub(subStatus)}</Badge>
                ) : (
                  <Value>{plan.status}</Value>
                )}
                {plan.cancel_at_period_end ? (
                  <span className="ml-2 text-ink-muted">{t("endsAtPeriodEnd")}</span>
                ) : null}
              </dd>
              <dt className="text-ink-muted">{t("period")}</dt>
              <dd>
                <Value>
                  {plan.current_period_start && plan.current_period_end
                    ? `${formatDate(plan.current_period_start) ?? ""} – ${formatDate(plan.current_period_end) ?? ""}`
                    : null}
                </Value>
              </dd>
              {plan.status === "trial" ? (
                <>
                  <dt className="text-ink-muted">{t("trialEndsOn")}</dt>
                  <dd>
                    <Value>{formatDate(plan.trial_ends_at)}</Value>
                  </dd>
                </>
              ) : null}
              <dt className="text-ink-muted">{t("amountDue")}</dt>
              <dd className="tabular-nums">
                <Value>{formatInr(plan.amount_due_inr, locale)}</Value>
              </dd>
            </dl>
          ) : null}
        </Card>
        <Card
          title={t("usageTitle")}
          description={
            plan?.usage_date ? t("usageAsOf", { date: formatDate(plan.usage_date) ?? "" }) : undefined
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
      <Card title={t("invoicesTitle")}>
        <DataTable
          caption={t("invoicesTitle")}
          captionHidden
          columns={invoiceColumns}
          state={invoices}
          rowKey={(row) => row.invoice_id}
          emptyTitle={t("emptyTitle")}
          emptyBody={t("emptyBody")}
        />
      </Card>
    </div>
  );
}

/** Loads GET /tenant/billing and /tenant/billing/invoices (tenant.billing.read). */
export function BillingScreen() {
  const api = useBffClient("staff");
  const billing = useApiQuery(["staff", "billing"], () => unwrap(api.GET("/api/v1/tenant/billing")));
  const invoices = useApiQuery(
    ["staff", "billing", "invoices"],
    async () => (await unwrap(api.GET("/api/v1/tenant/billing/invoices"))).data,
  );
  return <BillingView billing={billing} invoices={invoices} />;
}
