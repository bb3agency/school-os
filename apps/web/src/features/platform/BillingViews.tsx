"use client";

import {
  INVOICE_STATUSES,
  SUBSCRIPTION_STATUSES,
  type AiBundle,
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
import { TextAreaField, TextField } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { SelectField } from "@/components/ui/Select";
import { DataTable, type Column } from "@/components/ui/Table";
import { Value } from "@/components/ui/Value";
import { planTone, subscriptionTone } from "@/features/status";
import { unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import type { FieldErrors } from "@/lib/forms";
import { formatCount, formatDate, formatInr } from "@/lib/format";
import {
  PLAN_CODE_PATTERN,
  money,
  optionalInt,
  optionalMoney,
  optionalText,
  text,
} from "@/lib/validation";
import { Link } from "@/i18n/navigation";
import { PK, readyOr, useAiBundles, useCan, usePlanDirectory, useSchoolDirectory } from "./data";
import { InvoiceTable } from "./InvoiceTable";
import { FilterCard } from "./FilterCard";
import { Mono, TierTag } from "./pills";
import { SubscriptionActions } from "./SubscriptionActions";

/* ------------------------------------------------------------------ plans */

const planSchema = z
  .object({
    code: z.string().trim().toLowerCase().regex(PLAN_CODE_PATTERN, { error: "invalidCode" }),
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
    one_time_fee_inr: optionalMoney,
    description: optionalText(300),
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
  .transform((value): PlanInput => ({
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
    one_time_fee_inr: value.one_time_fee_inr ?? "0",
    description: value.description,
    limits: {
      students: value["limits.students"],
      staff_users: value["limits.staff_users"],
      storage_gb: value["limits.storage_gb"],
      documents: value["limits.documents"],
      ai_tokens_month: value["limits.ai_tokens_month"],
      ai_budget_inr: value["limits.ai_budget_inr"],
    },
  }));

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
        <TextField
          name="name"
          label={t("colName")}
          error={errors.name}
          defaultValue={base?.name ?? ""}
        />
        <SelectField
          name="tier"
          label={t("colTier")}
          error={errors.tier}
          defaultValue={base?.tier ?? "shared"}
          options={(["shared", "dedicated"] as const).map((value) => ({
            value,
            label: tmode(value),
          }))}
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
        <TextField
          name="one_time_fee_inr"
          label={t("oneTimeFee")}
          hint={t("oneTimeFeeHint")}
          inputMode="decimal"
          error={errors.one_time_fee_inr}
          defaultValue={base ? base.one_time_fee_inr : ""}
        />
      </div>
      <TextAreaField
        name="description"
        label={t("planDescription")}
        hint={t("planDescriptionHint")}
        error={errors.description}
        maxLength={300}
        rows={2}
        defaultValue={base?.description ?? ""}
      />
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
  const tn = useTranslations("platform.nav");
  const tc = useTranslations("common");
  const tstatus = useTranslations("status.plan");
  const locale = useLocale();
  const api = useBffClient("operator");
  const can = useCan();
  const manage = can("platform.plans.manage");
  const query = status ? { status } : {};
  const plans = useApiQuery(
    [...PK.plans, "list", query],
    async () => (await unwrap(api.GET("/api/v1/platform/plans", { params: { query } }))).data,
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
    {
      key: "name",
      header: t("colName"),
      cell: (row) => (
        <span className="flex flex-col">
          <span>{row.name}</span>
          {row.description ? (
            <span className="max-w-xs text-xs text-ink-muted">{row.description}</span>
          ) : null}
        </span>
      ),
    },
    {
      key: "code",
      header: t("code"),
      cell: (row) => <code className="font-mono text-xs">{row.code}</code>,
    },
    {
      key: "version",
      header: t("colVersion"),
      className: "tabular-nums",
      cell: (row) => <Mono>v{row.version}</Mono>,
    },
    { key: "tier", header: t("colTier"), cell: (row) => <TierTag tier={row.tier} /> },
    {
      key: "price",
      header: t("colPrice"),
      className: "text-right tabular-nums",
      cell: (row) => (
        <span className="whitespace-nowrap">
          <Mono>{formatInr(row.base_price_inr, locale)}</Mono>
          <span className="text-ink-muted">
            {" "}
            / {row.billing_period === "annual" ? t("perYear") : t("perMonth")}
          </span>
        </span>
      ),
    },
    {
      key: "fee",
      header: t("colOneTimeFee"),
      className: "text-right tabular-nums",
      cell: (row) => (
        <Mono>
          <Value>
            {Number(row.one_time_fee_inr) > 0 ? formatInr(row.one_time_fee_inr, locale) : null}
          </Value>
        </Mono>
      ),
    },
    {
      key: "gst",
      header: t("gstRate"),
      className: "tabular-nums",
      cell: (row) => (
        <span className="flex flex-col">
          <Mono>{`${Number(row.gst_rate)}%`}</Mono>
          <span className="text-xs whitespace-nowrap text-ink-muted">
            {t("sacCode")}: <span className="font-mono">{row.sac_code}</span>
          </span>
        </span>
      ),
    },
    {
      key: "students",
      header: t("colStudents"),
      className: "text-right tabular-nums",
      cell: (row) => {
        const value = row.limits.students;
        return (
          <Mono>
            <Value>{typeof value === "number" ? formatCount(value, locale) : null}</Value>
          </Mono>
        );
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
              <div className="relative flex flex-wrap gap-2">
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
        breadcrumb={[{ label: tn("dashboard"), href: "/platform" }, { label: t("title") }]}
        actions={manage ? createDialog() : undefined}
      />
      <FilterCard clearHref={status ? "/platform/plans" : undefined}>
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
      </FilterCard>
      <DataTable
        caption={t("title")}
        captionHidden
        columns={columns}
        state={plans}
        rowKey={(row) => row.id}
        emptyTitle={t("emptyTitle")}
        emptyBody={t("emptyBody")}
      />
      <AiBundlesTable />
    </div>
  );
}

/** ADR-0037 (docs/16 §5.6): AI answer bundles, a monthly add-on with an answer quota. */
function AiBundlesTable() {
  const t = useTranslations("platform.plans");
  const tstatus = useTranslations("status.plan");
  const locale = useLocale();
  const bundles = useAiBundles();
  const columns: Column<AiBundle>[] = [
    { key: "name", header: t("colBundle"), cell: (row) => row.name },
    {
      key: "answers",
      header: t("colIncluded"),
      className: "text-right tabular-nums",
      cell: (row) => <Mono>{formatCount(row.included_answers, locale)}</Mono>,
    },
    {
      key: "price",
      header: t("colBundlePrice"),
      className: "text-right tabular-nums",
      cell: (row) => <Mono>{formatInr(row.price_inr, locale)}</Mono>,
    },
    {
      key: "extra",
      header: t("colExtra"),
      className: "text-right tabular-nums",
      cell: (row) => <Mono>{formatInr(row.overage_rate_inr, locale)}</Mono>,
    },
    {
      key: "status",
      header: t("colStatus"),
      cell: (row) => <Badge tone={planTone[row.status]}>{tstatus(row.status)}</Badge>,
    },
  ];
  return (
    <section className="space-y-2" aria-labelledby="ai-bundles-title">
      <h2 id="ai-bundles-title" className="text-lg font-semibold">
        {t("bundlesTitle")}
      </h2>
      <p className="max-w-3xl text-sm text-ink-muted">{t("bundlesBody")}</p>
      <DataTable
        caption={t("bundlesTitle")}
        captionHidden
        columns={columns}
        state={bundles}
        rowKey={(row) => row.id}
        emptyTitle={t("bundlesEmptyTitle")}
        emptyBody={t("bundlesEmptyBody")}
      />
    </section>
  );
}

/* ---------------------------------------------------------- subscriptions */

/** FR-PLT-012..014 (docs/16 §5.7): subscriptions with status filter and actions. */
export function SubscriptionsScreen({ status = "" }: { status?: string }) {
  const t = useTranslations("platform.subscriptions");
  const tn = useTranslations("platform.nav");
  const tc = useTranslations("common");
  const tstatus = useTranslations("status.subscription");
  const api = useBffClient("operator");
  const { nameOf: schoolName } = useSchoolDirectory();
  const { nameOf: planName, plans } = usePlanDirectory();
  const bundles = readyOr(useAiBundles(), []);
  const locale = useLocale();
  const feeOf = (planId: string): string | null => {
    const plan = plans.find((row) => row.id === planId);
    return plan && Number(plan.one_time_fee_inr) > 0
      ? formatInr(plan.one_time_fee_inr, locale)
      : null;
  };
  const query = { limit: 200, ...(status ? { status } : {}) };
  const subscriptions = useApiQuery(
    [...PK.subscriptions, "list", query],
    async () =>
      (await unwrap(api.GET("/api/v1/platform/subscriptions", { params: { query } }))).data,
  );
  const columns: Column<Subscription>[] = [
    {
      key: "school",
      header: t("colSchool"),
      cell: (row) => (
        <Link
          href={`/platform/schools/${row.tenant_id}?tab=subscription`}
          className="font-medium text-primary underline-offset-4 hover:underline"
        >
          {schoolName(row.tenant_id)}
        </Link>
      ),
    },
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
          {feeOf(row.plan_id) ? (
            <span className="block text-xs text-ink-muted">
              {t("oneTimeFeeValue", { amount: feeOf(row.plan_id) ?? "" })}
            </span>
          ) : null}
        </span>
      ),
    },
    {
      key: "ai",
      header: t("colAiBundle"),
      cell: (row) => {
        if (!row.ai_bundle_id) return <span className="text-ink-muted">{t("noAiBundle")}</span>;
        const bundle = bundles.find((item) => item.id === row.ai_bundle_id);
        return (
          <span className="flex flex-col">
            <span>
              {bundle
                ? t("aiBundleValue", {
                    name: bundle.name,
                    answers: formatCount(bundle.included_answers, locale) ?? "",
                  })
                : row.ai_bundle_id.slice(0, 8)}
            </span>
            <span className="text-xs text-ink-muted">
              {t("aiBundleFrom", { date: formatDate(row.ai_bundle_from) ?? "" })}
            </span>
          </span>
        );
      },
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
      cell: (row) => (
        <Mono>
          <Value>{formatDate(row.current_period_end)}</Value>
        </Mono>
      ),
    },
    {
      key: "trial",
      header: t("colTrialEnds"),
      cell: (row) => (
        <Mono>
          <Value>{formatDate(row.trial_ends_at)}</Value>
        </Mono>
      ),
    },
    {
      key: "actions",
      header: tc("actions"),
      cell: (row) => <SubscriptionActions subscription={row} label={schoolName(row.tenant_id)} />,
    },
  ];
  return (
    <div className="space-y-6">
      <PageHeader
        title={t("title")}
        description={t("description")}
        breadcrumb={[{ label: tn("dashboard"), href: "/platform" }, { label: t("title") }]}
      />
      <FilterCard clearHref={status ? "/platform/subscriptions" : undefined}>
        <SelectField
          name="status"
          label={t("filterStatus")}
          placeholder={t("allStatuses")}
          defaultValue={status}
          options={SUBSCRIPTION_STATUSES.map((value) => ({ value, label: tstatus(value) }))}
          className="w-64"
        />
      </FilterCard>
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
  const tn = useTranslations("platform.nav");
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
  const invoices = useApiQuery(
    [...PK.invoices, "list", query],
    async () => (await unwrap(api.GET("/api/v1/platform/invoices", { params: { query } }))).data,
  );

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("title")}
        description={t("description")}
        breadcrumb={[{ label: tn("dashboard"), href: "/platform" }, { label: t("title") }]}
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
                <TextField
                  name="month"
                  type="month"
                  label={t("month")}
                  hint={t("monthHint")}
                  error={errors.month}
                />
              )}
            </ActionDialog>
          ) : undefined
        }
      />
      <Alert tone="info" title={t("gstNoteTitle")}>
        <p>{t("numberingNote")}</p>
        <p>{t("gstNote")}</p>
        <p>{t("manualPaymentNote")}</p>
      </Alert>
      <FilterCard
        clearHref={
          filters.status || filters.financialYear || filters.tenantId
            ? "/platform/invoices"
            : undefined
        }
      >
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
      </FilterCard>
      <InvoiceTable invoices={invoices} caption={t("title")} schoolName={nameOf} />
    </div>
  );
}
