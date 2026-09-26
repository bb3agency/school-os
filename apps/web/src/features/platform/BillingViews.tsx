"use client";

import {
  INVOICE_STATUSES,
  SUBSCRIPTION_STATUSES,
  type Plan,
  type PlanInput,
  type Subscription,
} from "@schoolos/api-client";
import { useLocale, useTranslations } from "next-intl";
import { z } from "zod";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { TextField } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { SelectField } from "@/components/ui/Select";
import { DataTable, type Column } from "@/components/ui/Table";
import { Value } from "@/components/ui/Value";
import { planTone, subscriptionTone } from "@/features/status";
import { unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import type { FieldErrors } from "@/lib/forms";
import { formatCount, formatDate, formatInr } from "@/lib/format";
import { PLAN_CODE_PATTERN, money, optionalInt, optionalMoney, text } from "@/lib/validation";
import { PK, useCan, usePlanDirectory, useSchoolDirectory } from "./data";
import { InvoiceTable } from "./InvoiceTable";
import { SubscriptionActions } from "./SubscriptionActions";

/* ------------------------------------------------------------------ plans */

const planSchema = z
  .object({
    code: z
      .string()
      .trim()
      .toLowerCase()
      .regex(PLAN_CODE_PATTERN, { error: "invalidCode" }),
    name: text(100),
    tier: z.enum(["shared", "dedicated"], { error: "chooseOption" }),
    billing_period: z.enum(["monthly", "annual"], { error: "chooseOption" }),
    pricing_model: z.enum(["flat", "per_student"], { error: "chooseOption" }),
    base_price_inr: money,
    per_student_price_inr: optionalMoney,
    included_students: optionalInt(10_000_000),
    gst_rate: z.enum(["0", "5", "12", "18", "28"], { error: "chooseOption" }),
    sac_code: z
      .string()
      .trim()
      .refine((value) => value === "" || /^[0-9]{6}$/.test(value), { error: "invalidSac" })
      .transform((value) => (value === "" ? null : value)),
    trial_days: optionalInt(365),
    "limits.students": optionalInt(10_000_000),
    "limits.staff_users": optionalInt(1_000_000),
    "limits.storage_gb": optionalInt(1_000_000),
    "limits.documents": optionalInt(100_000_000),
    "limits.ai_tokens_month": optionalInt(1_000_000_000_000),
    "limits.ai_budget_inr": optionalMoney,
  })
  .superRefine((value, ctx) => {
    if (value.pricing_model === "per_student" && value.per_student_price_inr === null) {
      ctx.addIssue({ code: "custom", path: ["per_student_price_inr"], message: "required" });
    }
  })
  .transform(
    (value): PlanInput => ({
      code: value.code,
      name: value.name,
      tier: value.tier,
      billing_period: value.billing_period,
      pricing_model: value.pricing_model,
      base_price_inr: value.base_price_inr,
      per_student_price_inr: value.per_student_price_inr,
      included_students: value.included_students,
      gst_rate: value.gst_rate,
      sac_code: value.sac_code,
      trial_days: value.trial_days ?? 30,
      limits: {
        students: value["limits.students"],
        staff_users: value["limits.staff_users"],
        storage_gb: value["limits.storage_gb"],
        documents: value["limits.documents"],
        ai_tokens_month: value["limits.ai_tokens_month"],
        ai_budget_inr: value["limits.ai_budget_inr"],
      },
    }),
  );

const LIMIT_KEYS = [
  "students",
  "staff_users",
  "storage_gb",
  "documents",
  "ai_tokens_month",
  "ai_budget_inr",
] as const;

function limitOf(plan: Plan | undefined, key: (typeof LIMIT_KEYS)[number]): string {
  const value = plan?.limits[key];
  return value === null || value === undefined ? "" : String(value);
}

function PlanFields({ errors, base }: { errors: FieldErrors; base?: Plan | undefined }) {
  const t = useTranslations("platform.plans");
  const tmode = useTranslations("deploymentMode");
  const decimals = (value: string | null | undefined) => value ?? "";
  return (
    <>
      <div className="grid gap-4 md:grid-cols-2">
        <TextField
          name="code"
          label={t("code")}
          hint={base ? t("codeNewVersionHint") : t("codeHint")}
          error={errors.code}
          defaultValue={base?.code ?? ""}
          readOnly={base !== undefined}
          spellCheck={false}
          autoComplete="off"
        />
        <TextField name="name" label={t("colName")} error={errors.name} defaultValue={base?.name ?? ""} />
        <SelectField
          name="tier"
          label={t("colTier")}
          error={errors.tier}
          defaultValue={base?.tier ?? "shared"}
          options={(["shared", "dedicated"] as const).map((value) => ({ value, label: tmode(value) }))}
        />
        <SelectField
          name="billing_period"
          label={t("billingPeriod")}
          error={errors.billing_period}
          defaultValue={base?.billing_period ?? "monthly"}
          options={[
            { value: "monthly", label: t("monthly") },
            { value: "annual", label: t("annual") },
          ]}
        />
        <SelectField
          name="pricing_model"
          label={t("pricingModel")}
          error={errors.pricing_model}
          defaultValue={base?.pricing_model ?? "flat"}
          options={[
            { value: "flat", label: t("flat") },
            { value: "per_student", label: t("perStudent") },
          ]}
        />
        <TextField
          name="base_price_inr"
          label={t("basePrice")}
          hint={t("priceHint")}
          inputMode="decimal"
          error={errors.base_price_inr}
          defaultValue={decimals(base?.base_price_inr)}
        />
        <TextField
          name="per_student_price_inr"
          label={t("perStudentPrice")}
          inputMode="decimal"
          error={errors.per_student_price_inr}
          defaultValue={decimals(base?.per_student_price_inr)}
        />
        <TextField
          name="included_students"
          label={t("includedStudents")}
          inputMode="numeric"
          error={errors.included_students}
          defaultValue={base?.included_students?.toString() ?? ""}
        />
        <SelectField
          name="gst_rate"
          label={t("gstRate")}
          error={errors.gst_rate}
          defaultValue={base ? String(Number(base.gst_rate)) : "18"}
          options={["0", "5", "12", "18", "28"].map((value) => ({ value, label: `${value}%` }))}
        />
        <TextField
          name="sac_code"
          label={t("sacCode")}
          hint={t("sacHint")}
          inputMode="numeric"
          maxLength={6}
          error={errors.sac_code}
          defaultValue={base?.sac_code ?? ""}
        />
        <TextField
          name="trial_days"
          label={t("trialDays")}
          inputMode="numeric"
          error={errors.trial_days}
          defaultValue={base?.trial_days?.toString() ?? "30"}
        />
      </div>
      <fieldset className="space-y-2">
        <legend className="text-sm font-semibold">{t("limitsTitle")}</legend>
        <p className="text-sm text-ink-muted">{t("limitsHint")}</p>
        <div className="grid gap-4 md:grid-cols-3">
          {LIMIT_KEYS.map((key) => (
            <TextField
              key={key}
              name={`limits.${key}`}
              label={t(`limits.${key}`)}
              inputMode={key === "ai_budget_inr" ? "decimal" : "numeric"}
              error={errors[`limits.${key}`]}
              defaultValue={limitOf(base, key)}
            />
          ))}
        </div>
      </fieldset>
    </>
  );
}

/** FR-PLT-010..011 (docs/16 §5.6): versioned plans; a published plan never changes. */
export function PlansScreen({ status = "" }: { status?: string }) {
  const t = useTranslations("platform.plans");
  const tc = useTranslations("common");
  const tstatus = useTranslations("status.plan");
  const tmode = useTranslations("deploymentMode");
  const locale = useLocale();
  const api = useBffClient("operator");
  const can = useCan();
  const manage = can("platform.plans.manage");
  const query = status ? { status } : {};
  const plans = useApiQuery([...PK.plans, "list", query], async () =>
    (await unwrap(api.GET("/api/v1/platform/plans", { params: { query } }))).data,
  );

  const createDialog = (base?: Plan) => (
    <ActionDialog
      triggerLabel={base ? t("newVersion") : t("newPlan")}
      triggerVariant={base ? "secondary" : "primary"}
      triggerSize={base ? "sm" : "md"}
      {...(base ? { triggerDescription: base.name } : {})}
      title={base ? t("newVersionTitle", { code: base.code }) : t("newPlanTitle")}
      description={t("draftNote")}
      confirmLabel={t("saveDraft")}
      stepUp
      schema={planSchema}
      fieldMap={(field) => field}
      invalidate={[PK.plans]}
      submit={(data, key) =>
        unwrap(
          api.POST("/api/v1/platform/plans", {
            params: { header: { "Idempotency-Key": key } },
            body: data,
          }),
        )
      }
    >
      {(errors) => <PlanFields errors={errors} base={base} />}
    </ActionDialog>
  );

  const columns: Column<Plan>[] = [
    { key: "name", header: t("colName"), cell: (row) => row.name },
    {
      key: "code",
      header: t("code"),
      cell: (row) => <code className="font-mono text-xs">{row.code}</code>,
    },
    { key: "version", header: t("colVersion"), className: "tabular-nums", cell: (row) => row.version },
    { key: "tier", header: t("colTier"), cell: (row) => tmode(row.tier) },
    {
      key: "price",
      header: t("colPrice"),
      className: "text-right tabular-nums",
      cell: (row) => (
        <span>
          {formatInr(row.base_price_inr, locale)}
          <span className="text-ink-muted">
            {" "}
            / {row.billing_period === "annual" ? t("perYear") : t("perMonth")}
          </span>
        </span>
      ),
    },
    {
      key: "gst",
      header: t("gstRate"),
      className: "tabular-nums",
      cell: (row) => `${Number(row.gst_rate)}%`,
    },
    {
      key: "students",
      header: t("colStudents"),
      className: "text-right tabular-nums",
      cell: (row) => {
        const value = row.limits.students;
        return <Value>{typeof value === "number" ? formatCount(value, locale) : null}</Value>;
      },
    },
    {
      key: "status",
      header: t("colStatus"),
      cell: (row) => <Badge tone={planTone[row.status]}>{tstatus(row.status)}</Badge>,
    },
    ...(manage
      ? [
          {
            key: "actions",
            header: tc("actions"),
            cell: (row: Plan) => (
              <div className="flex flex-wrap gap-2">
                {row.status === "draft" ? (
                  <ActionDialog
                    triggerLabel={t("publish")}
                    triggerSize="sm"
                    triggerDescription={row.name}
                    title={t("publishTitle", { name: row.name, version: row.version })}
                    description={t("publishBody")}
                    confirmLabel={t("publish")}
                    stepUp
                    schema={z.object({})}
                    invalidate={[PK.plans]}
                    submit={() =>
                      unwrap(
                        api.POST("/api/v1/platform/plans/{plan_id}/publish", {
                          params: { path: { plan_id: row.id } },
                        }),
                      )
                    }
                  />
                ) : null}
                {row.status === "published" ? (
                  <ActionDialog
                    triggerLabel={t("retire")}
                    triggerSize="sm"
                    triggerVariant="ghost"
                    triggerDescription={row.name}
                    title={t("retireTitle", { name: row.name, version: row.version })}
                    description={t("retireBody")}
                    confirmLabel={t("retire")}
                    confirmVariant="danger"
                    stepUp
                    schema={z.object({})}
                    invalidate={[PK.plans]}
                    submit={() =>
                      unwrap(
                        api.POST("/api/v1/platform/plans/{plan_id}/retire", {
                          params: { path: { plan_id: row.id } },
                        }),
                      )
                    }
                  />
                ) : null}
                {createDialog(row)}
              </div>
            ),
          },
        ]
      : []),
  ];

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("title")}
        description={t("description")}
        actions={manage ? createDialog() : undefined}
      />
      <form method="get" className="flex flex-wrap items-end gap-3">
        <SelectField
          name="status"
          label={t("colStatus")}
          placeholder={tc("all")}
          defaultValue={status}
          options={(["draft", "published", "retired"] as const).map((value) => ({
            value,
            label: tstatus(value),
          }))}
          className="w-52"
        />
        <Button type="submit" variant="secondary">
          {tc("applyFilters")}
        </Button>
      </form>
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

/* ---------------------------------------------------------- subscriptions */

/** FR-PLT-012..014 (docs/16 §5.7): subscriptions with status filter and actions. */
export function SubscriptionsScreen({ status = "" }: { status?: string }) {
  const t = useTranslations("platform.subscriptions");
  const tc = useTranslations("common");
  const tstatus = useTranslations("status.subscription");
  const api = useBffClient("operator");
  const { nameOf: schoolName } = useSchoolDirectory();
  const { nameOf: planName } = usePlanDirectory();
  const query = { limit: 200, ...(status ? { status } : {}) };
  const subscriptions = useApiQuery([...PK.subscriptions, "list", query], async () =>
    (await unwrap(api.GET("/api/v1/platform/subscriptions", { params: { query } }))).data,
  );
  const columns: Column<Subscription>[] = [
    { key: "school", header: t("colSchool"), cell: (row) => schoolName(row.tenant_id) },
    {
      key: "plan",
      header: t("colPlan"),
      cell: (row) => (
        <span>
          {planName(row.plan_id)}
          {row.pending_plan_id ? (
            <span className="block text-xs text-ink-muted">
              {t("pendingPlanValue", { plan: planName(row.pending_plan_id) })}
            </span>
          ) : null}
        </span>
      ),
    },
    {
      key: "status",
      header: t("colStatus"),
      cell: (row) => (
        <span className="flex flex-col items-start gap-1">
          <Badge tone={subscriptionTone[row.status]}>{tstatus(row.status)}</Badge>
          {row.cancel_at_period_end ? (
            <span className="text-xs text-ink-muted">{t("cancelsAtPeriodEnd")}</span>
          ) : null}
        </span>
      ),
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
    {
      key: "actions",
      header: tc("actions"),
      cell: (row) => <SubscriptionActions subscription={row} label={schoolName(row.tenant_id)} />,
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

/* --------------------------------------------------------------- invoices */

export interface InvoiceFilters {
  status?: string;
  financialYear?: string;
  tenantId?: string;
}

const FY = /^[0-9]{4}-[0-9]{2}$/;

/** FR-PLT-015..019 (docs/16 §5.8–5.10): list, filter, monthly run, issue, pay, void. */
export function InvoicesScreen({ filters = {} }: { filters?: InvoiceFilters }) {
  const t = useTranslations("platform.invoices");
  const tc = useTranslations("common");
  const tstatus = useTranslations("status.invoice");
  const api = useBffClient("operator");
  const can = useCan();
  const { nameOf, schools } = useSchoolDirectory();
  const query = {
    limit: 200,
    ...(filters.status ? { status: filters.status } : {}),
    ...(filters.financialYear && FY.test(filters.financialYear)
      ? { financial_year: filters.financialYear }
      : {}),
    ...(filters.tenantId ? { tenant_id: filters.tenantId } : {}),
  };
  const invoices = useApiQuery([...PK.invoices, "list", query], async () =>
    (await unwrap(api.GET("/api/v1/platform/invoices", { params: { query } }))).data,
  );

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("title")}
        description={t("description")}
        actions={
          can("platform.invoices.manage") ? (
            <ActionDialog
              triggerLabel={t("runTitle")}
              triggerVariant="primary"
              title={t("runTitle")}
              description={t("runBody")}
              confirmLabel={t("run")}
              schema={z.object({
                month: z
                  .string()
                  .trim()
                  .regex(/^[0-9]{4}-(0[1-9]|1[0-2])$/, { error: "invalidMonth" }),
              })}
              invalidate={[PK.invoices]}
              submit={(data) =>
                unwrap(api.POST("/api/v1/platform/invoice-runs", { body: { month: data.month } }))
              }
              renderResult={(job, close) => (
                <>
                  <Alert tone="success" live title={t("runStarted")}>
                    {t("runStartedBody", { status: job.status })}
                  </Alert>
                  <div className="flex justify-end">
                    <Button onClick={close}>{tc("done")}</Button>
                  </div>
                </>
              )}
            >
              {(errors) => (
                <TextField name="month" type="month" label={t("month")} hint={t("monthHint")} error={errors.month} />
              )}
            </ActionDialog>
          ) : undefined
        }
      />
      <Alert tone="info">
        <p>{t("numberingNote")}</p>
        <p>{t("manualPaymentNote")}</p>
      </Alert>
      <form method="get" className="flex flex-wrap items-end gap-3">
        <SelectField
          name="status"
          label={t("colStatus")}
          placeholder={tc("all")}
          defaultValue={filters.status ?? ""}
          options={INVOICE_STATUSES.map((value) => ({ value, label: tstatus(value) }))}
          className="w-44"
        />
        <TextField
          name="fy"
          label={t("financialYear")}
          hint={t("financialYearHint")}
          defaultValue={filters.financialYear ?? ""}
          pattern="[0-9]{4}-[0-9]{2}"
          className="w-44"
        />
        <SelectField
          name="school"
          label={t("colSchool")}
          placeholder={tc("all")}
          defaultValue={filters.tenantId ?? ""}
          options={schools.map((school) => ({
            value: school.tenant_id,
            label: `${school.school_name} (${school.code})`,
          }))}
          className="w-72"
        />
        <Button type="submit" variant="secondary">
          {tc("applyFilters")}
        </Button>
      </form>
      <InvoiceTable invoices={invoices} caption={t("title")} schoolName={nameOf} />
    </div>
  );
}
