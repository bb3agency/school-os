import type { Invoice, Plan, Subscription, SubscriptionStatus } from "@schoolos/api-client";
import { useLocale, useTranslations } from "next-intl";
import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { PageHeader } from "@/components/ui/PageHeader";
import { SelectField } from "@/components/ui/Select";
import { DataTable, type Column } from "@/components/ui/Table";
import { Value } from "@/components/ui/Value";
import { invoiceTone, planTone, subscriptionTone } from "@/features/status";
import { formatCount, formatDate, formatInr } from "@/lib/format";
import type { Loadable } from "@/lib/loadable";

/** FR-PLT-010..011: versioned plans, INR, GST 18% shown separately. */
export function PlansView({ plans }: { plans: Loadable<readonly Plan[]> }) {
  const t = useTranslations("platform.plans");
  const tstatus = useTranslations("status.plan");
  const tmode = useTranslations("deploymentMode");
  const locale = useLocale();
  const columns: Column<Plan>[] = [
    { key: "name", header: t("colName"), cell: (row) => row.name },
    { key: "version", header: t("colVersion"), cell: (row) => row.version },
    { key: "tier", header: t("colTier"), cell: (row) => tmode(row.deployment_mode) },
    {
      key: "monthly",
      header: t("colMonthly"),
      className: "text-right tabular-nums",
      cell: (row) => <Value>{formatInr(row.monthly_price_inr, locale)}</Value>,
    },
    {
      key: "annual",
      header: t("colAnnual"),
      className: "text-right tabular-nums",
      cell: (row) => <Value>{formatInr(row.annual_price_inr, locale)}</Value>,
    },
    {
      key: "students",
      header: t("colStudents"),
      className: "text-right tabular-nums",
      cell: (row) => <Value>{formatCount(row.limits.students, locale)}</Value>,
    },
    {
      key: "status",
      header: t("colStatus"),
      cell: (row) => <Badge tone={planTone[row.status]}>{tstatus(row.status)}</Badge>,
    },
  ];
  return (
    <div className="space-y-6">
      <PageHeader
        title={t("title")}
        description={t("description")}
        actions={<Button disabled>{t("newPlan")}</Button>}
      />
      <DataTable
        caption={t("title")}
        captionHidden
        columns={columns}
        state={plans}
        rowKey={(row) => row.id}
        emptyTitle={t("emptyTitle")}
        emptyBody={t("emptyBody")}
      />
    </div>
  );
}

const SUBSCRIPTION_STATUSES = [
  "trial",
  "active",
  "past_due",
  "suspended",
  "cancelled",
] as const satisfies readonly SubscriptionStatus[];

/** FR-PLT-012..014: subscriptions with status filter (GET form, works without JS). */
export function SubscriptionsView({
  subscriptions,
  status = "",
}: {
  subscriptions: Loadable<readonly Subscription[]>;
  status?: string;
}) {
  const t = useTranslations("platform.subscriptions");
  const tc = useTranslations("common");
  const tstatus = useTranslations("status.subscription");
  const columns: Column<Subscription>[] = [
    { key: "school", header: t("colSchool"), cell: (row) => row.school_name },
    { key: "plan", header: t("colPlan"), cell: (row) => row.plan_name },
    {
      key: "status",
      header: t("colStatus"),
      cell: (row) => <Badge tone={subscriptionTone[row.status]}>{tstatus(row.status)}</Badge>,
    },
    {
      key: "period",
      header: t("colPeriodEnd"),
      cell: (row) => <Value>{formatDate(row.current_period_end)}</Value>,
    },
    {
      key: "trial",
      header: t("colTrialEnds"),
      cell: (row) => <Value>{formatDate(row.trial_ends_at)}</Value>,
    },
  ];
  return (
    <div className="space-y-6">
      <PageHeader title={t("title")} description={t("description")} />
      <form method="get" className="flex flex-wrap items-end gap-3">
        <SelectField
          name="status"
          label={t("filterStatus")}
          placeholder={t("allStatuses")}
          defaultValue={status}
          options={SUBSCRIPTION_STATUSES.map((value) => ({ value, label: tstatus(value) }))}
          className="w-64"
        />
        <Button type="submit" variant="secondary">
          {tc("applyFilters")}
        </Button>
      </form>
      <DataTable
        caption={t("title")}
        captionHidden
        columns={columns}
        state={subscriptions}
        rowKey={(row) => row.id}
        emptyTitle={t("emptyTitle")}
        emptyBody={t("emptyBody")}
      />
    </div>
  );
}

/** FR-PLT-015..019: invoices (draft → issued → paid/void), GST shown separately. */
export function InvoicesView({ invoices }: { invoices: Loadable<readonly Invoice[]> }) {
  const t = useTranslations("platform.invoices");
  const tstatus = useTranslations("status.invoice");
  const locale = useLocale();
  const money = (value: string) => <Value>{formatInr(value, locale)}</Value>;
  const columns: Column<Invoice>[] = [
    { key: "number", header: t("colNumber"), cell: (row) => <Value>{row.number}</Value> },
    { key: "school", header: t("colSchool"), cell: (row) => row.school_name },
    {
      key: "issued",
      header: t("colIssued"),
      cell: (row) => <Value>{formatDate(row.issue_date)}</Value>,
    },
    { key: "due", header: t("colDue"), cell: (row) => <Value>{formatDate(row.due_date)}</Value> },
    {
      key: "subtotal",
      header: t("colSubtotal"),
      className: "text-right tabular-nums",
      cell: (row) => money(row.subtotal_inr),
    },
    {
      key: "gst",
      header: t("colGst"),
      className: "text-right tabular-nums",
      cell: (row) => money(row.gst_inr),
    },
    {
      key: "total",
      header: t("colTotal"),
      className: "text-right tabular-nums",
      cell: (row) => money(row.total_inr),
    },
    {
      key: "status",
      header: t("colStatus"),
      cell: (row) => <Badge tone={invoiceTone[row.status]}>{tstatus(row.status)}</Badge>,
    },
  ];
  return (
    <div className="space-y-6">
      <PageHeader title={t("title")} description={t("description")} />
      <Alert tone="info">
        <p>{t("numberingNote")}</p>
        <p>{t("manualPaymentNote")}</p>
      </Alert>
      <DataTable
        caption={t("title")}
        captionHidden
        columns={columns}
        state={invoices}
        rowKey={(row) => row.id}
        emptyTitle={t("emptyTitle")}
        emptyBody={t("emptyBody")}
      />
    </div>
  );
}
