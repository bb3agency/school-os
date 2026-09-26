import type { CurrentSubscription, TenantInvoice, UsageAgainstLimit } from "@schoolos/api-client";
import { useLocale, useTranslations } from "next-intl";
import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { DataTable, type Column } from "@/components/ui/Table";
import { UsageMeter } from "@/components/ui/UsageMeter";
import { Value } from "@/components/ui/Value";
import { invoiceTone, subscriptionTone } from "@/features/status";
import { formatBytes, formatCount, formatDate, formatInr } from "@/lib/format";
import type { Loadable } from "@/lib/loadable";

export interface BillingViewProps {
  /** `ready(null)` when the school has no subscription record yet. */
  subscription: Loadable<CurrentSubscription | null>;
  invoices: Loadable<readonly TenantInvoice[]>;
}

type UsageKey = keyof CurrentSubscription["usage"];
const USAGE_KEYS = [
  "students",
  "active_users",
  "storage_bytes",
  "documents",
  "ai_questions",
] as const;
const USAGE_LABEL = {
  students: "students",
  active_users: "activeUsers",
  storage_bytes: "storage",
  documents: "documents",
  ai_questions: "aiQuestions",
} as const satisfies Record<UsageKey, string>;

/** FR-PLT-030 / US-1204: plan, usage against limits and invoices for tenant.billing.read. */
export function BillingView({ subscription, invoices }: BillingViewProps) {
  const t = useTranslations("school.billing");
  const tc = useTranslations("common");
  const tsub = useTranslations("status.subscription");
  const tinv = useTranslations("status.invoice");
  const locale = useLocale();

  function formatUsage(key: UsageKey, value: UsageAgainstLimit): string {
    const fmt = (n: number) =>
      (key === "storage_bytes" ? formatBytes(n, locale) : formatCount(n, locale)) ?? String(n);
    if (value.limit === null) return t("usageNoLimit", { used: fmt(value.used) });
    return t("usageOf", { used: fmt(value.used), limit: fmt(value.limit) });
  }

  const invoiceColumns: Column<TenantInvoice>[] = [
    { key: "number", header: t("colNumber"), cell: (row) => row.number },
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
      key: "status",
      header: t("colStatus"),
      cell: (row) => <Badge tone={invoiceTone[row.status]}>{tinv(row.status)}</Badge>,
    },
  ];

  return (
    <div className="space-y-6">
      <PageHeader title={t("title")} description={t("description")} />
      <div className="grid gap-6 xl:grid-cols-2">
        <Card title={t("currentPlan")} description={t("changePlanHint")}>
          {subscription.status === "loading" ? <LoadingState label={tc("loading")} /> : null}
          {subscription.status === "error" ? (
            <Alert tone="danger" title={tc("loadErrorTitle")}>
              {tc("loadErrorBody")}
            </Alert>
          ) : null}
          {subscription.status === "ready" && subscription.data === null ? (
            <Alert tone="info">{t("planUnavailable")}</Alert>
          ) : null}
          {subscription.status === "ready" && subscription.data !== null ? (
            <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 text-sm">
              <dt className="text-ink-muted">{t("plan")}</dt>
              <dd className="font-semibold">{subscription.data.plan_name}</dd>
              <dt className="text-ink-muted">{t("status")}</dt>
              <dd>
                <Badge tone={subscriptionTone[subscription.data.status]}>
                  {tsub(subscription.data.status)}
                </Badge>
              </dd>
              {subscription.data.status === "trial" ? (
                <>
                  <dt className="text-ink-muted">{t("trialEndsOn")}</dt>
                  <dd>
                    <Value>{formatDate(subscription.data.trial_ends_at)}</Value>
                  </dd>
                </>
              ) : (
                <>
                  <dt className="text-ink-muted">{t("renewsOn")}</dt>
                  <dd>
                    <Value>{formatDate(subscription.data.current_period_end)}</Value>
                  </dd>
                </>
              )}
            </dl>
          ) : null}
        </Card>
        <Card title={t("usageTitle")}>
          {subscription.status === "ready" && subscription.data !== null ? (
            <div className="space-y-4">
              {USAGE_KEYS.map((key) => {
                const value = subscription.data?.usage[key];
                if (!value) return null;
                return (
                  <UsageMeter
                    key={key}
                    label={t(`usage.${USAGE_LABEL[key]}`)}
                    used={value.used}
                    limit={value.limit}
                    valueText={formatUsage(key, value)}
                  />
                );
              })}
            </div>
          ) : subscription.status === "loading" ? (
            <LoadingState label={tc("loading")} />
          ) : (
            <p className="text-sm text-ink-muted">{t("planUnavailable")}</p>
          )}
        </Card>
      </div>
      <Card title={t("invoicesTitle")}>
        <DataTable
          caption={t("invoicesTitle")}
          captionHidden
          columns={invoiceColumns}
          state={invoices}
          rowKey={(row) => row.id}
          emptyTitle={t("emptyTitle")}
          emptyBody={t("emptyBody")}
        />
      </Card>
    </div>
  );
}
