"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { useMemo, useState } from "react";
import * as z from "@/lib/zod";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Pill } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Input, TextField } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { SelectField } from "@/components/ui/Select";
import { Table, TableScroll, TBody, Td, Th, THead, Tr } from "@/components/ui/Table";
import type { Locale } from "@/i18n/routing";
import { newIdempotencyKey, unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
import { formatDate, formatInr } from "@/lib/format";
import {
  FINANCE_READ,
  fromPaise,
  ifMatch,
  KEYS,
  MANAGE,
  toPaise,
  useHeads,
  type Head,
  type Plan,
  type PlanApply,
} from "./data";
import { FeesGate, NoAccess } from "./parts";

const headSchema = z.object({
  code: z
    .string()
    .trim()
    .regex(/^[A-Za-z0-9][A-Za-z0-9_-]{0,19}$/, { error: "invalidHeadCode" }),
  name: z.string().trim().min(1, { error: "required" }).max(80, { error: "tooLong" }),
  kind: z.enum(["tuition", "admission", "exam", "books", "transport", "other"]),
  taxable: z
    .string()
    .optional()
    .transform((v) => v === "on"),
});

interface Column {
  label: string;
  due_on: string;
}

/**
 * The plan editor (docs/17 §5.8): a grid of heads × instalments with live totals; each head's
 * annual amount is the sum of its row (so instalments always add up, FR-FEE-003). Saves a draft.
 */
function PlanEditor({
  heads,
  years,
  classes,
  onSaved,
}: {
  heads: Head[];
  years: { id: string; label: string; starts_on: string; ends_on: string; is_current: boolean }[];
  classes: { id: string; display_en: string }[];
  onSaved: () => void;
}) {
  const t = useTranslations("fees.setup");
  const locale = useLocale() as Locale;
  const api = useBffClient("staff");
  const current = years.find((y) => y.is_current) ?? years[0];
  const [yearId, setYearId] = useState(current?.id ?? "");
  const [classId, setClassId] = useState(classes[0]?.id ?? "");
  const [name, setName] = useState("");
  const [category, setCategory] = useState("");
  const [columns, setColumns] = useState<Column[]>([
    { label: t("term", { no: 1 }), due_on: current?.starts_on ?? "" },
  ]);
  const active = heads.filter((h) => h.active);
  const [cells, setCells] = useState<Record<string, string>>({});
  const [lateKind, setLateKind] = useState("none");
  const [lateAmount, setLateAmount] = useState("");
  const [lateGrace, setLateGrace] = useState("0");
  const [lateCap, setLateCap] = useState("");
  const [error, setError] = useState<unknown>(undefined);
  const [problem, setProblem] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const cell = (headId: string, col: number) => cells[`${headId}:${col}`] ?? "";
  const rowTotal = (headId: string) =>
    columns.reduce((sum, _, i) => sum + (toPaise(cell(headId, i) || "0") ?? 0), 0);
  const colTotal = (col: number) =>
    active.reduce((sum, h) => sum + (toPaise(cell(h.id, col) || "0") ?? 0), 0);

  async function save() {
    setProblem(null);
    setError(undefined);
    const lines = active
      .filter((h) => rowTotal(h.id) > 0)
      .map((h) => ({ head_id: h.id, annual_amount: fromPaise(rowTotal(h.id)) }));
    const bad = Object.values(cells).some(
      (v) => v !== "" && (toPaise(v) === null || (toPaise(v) ?? 0) < 0),
    );
    if (
      !name.trim() ||
      lines.length === 0 ||
      bad ||
      columns.some((c) => !c.due_on || !c.label.trim())
    ) {
      setProblem(t("editorIncomplete"));
      return;
    }
    setSaving(true);
    try {
      await unwrap(
        api.POST("/api/v1/fees/plans", {
          headers: { "Idempotency-Key": newIdempotencyKey() },
          body: {
            academic_year_id: yearId,
            class_id: classId,
            category_code: category.trim() || null,
            name: name.trim(),
            lines,
            instalments: columns.map((c, i) => ({
              instalment_no: i + 1,
              label: c.label.trim(),
              due_on: c.due_on,
              amounts: Object.fromEntries(
                active
                  .filter((h) => (toPaise(cell(h.id, i) || "0") ?? 0) > 0)
                  .map((h) => [h.id, fromPaise(toPaise(cell(h.id, i)) ?? 0)]),
              ),
            })),
            late_fee_rule:
              lateKind === "none"
                ? { kind: "none", grace_days: 0 }
                : {
                    kind: lateKind as "flat_per_instalment" | "per_day",
                    amount: lateAmount,
                    grace_days: Number(lateGrace || "0"),
                    cap: lateKind === "per_day" ? lateCap : null,
                  },
          },
        }),
      );
      onSaved();
    } catch (failure) {
      setError(failure);
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card title={t("editorTitle")} description={t("editorBody")}>
      <div className="grid gap-4 md:grid-cols-4">
        <SelectField
          name="year"
          label={t("year")}
          value={yearId}
          onChange={(e) => setYearId(e.target.value)}
          options={years.map((y) => ({ value: y.id, label: y.label }))}
        />
        <SelectField
          name="class"
          label={t("class")}
          value={classId}
          onChange={(e) => setClassId(e.target.value)}
          options={classes.map((c) => ({ value: c.id, label: c.display_en }))}
        />
        <TextField
          name="name"
          label={t("planName")}
          value={name}
          maxLength={80}
          onChange={(e) => setName(e.target.value)}
        />
        <TextField
          name="category"
          label={t("category")}
          hint={t("categoryHint")}
          value={category}
          maxLength={32}
          onChange={(e) => setCategory(e.target.value.toLowerCase())}
        />
      </div>
      <TableScroll label={t("gridLabel")} className="mt-4">
        <Table>
          <THead>
            <Tr>
              <Th>{t("head")}</Th>
              {columns.map((c, i) => (
                <Th key={i}>
                  <Input
                    aria-label={t("instalmentLabel", { no: i + 1 })}
                    value={c.label}
                    maxLength={40}
                    onChange={(e) =>
                      setColumns((all) =>
                        all.map((x, j) => (j === i ? { ...x, label: e.target.value } : x)),
                      )
                    }
                  />
                  <Input
                    type="date"
                    aria-label={t("instalmentDue", { no: i + 1 })}
                    value={c.due_on}
                    onChange={(e) =>
                      setColumns((all) =>
                        all.map((x, j) => (j === i ? { ...x, due_on: e.target.value } : x)),
                      )
                    }
                  />
                </Th>
              ))}
              <Th className="text-right">{t("annual")}</Th>
            </Tr>
          </THead>
          <TBody>
            {active.map((h) => (
              <Tr key={h.id}>
                <Td>
                  {h.name} <span className="text-xs text-ink-muted">{h.code}</span>
                  {!h.gst_exempt ? <span className="block text-xs">{t("taxable")}</span> : null}
                </Td>
                {columns.map((_, i) => (
                  <Td key={i}>
                    <Input
                      inputMode="decimal"
                      aria-label={t("cellLabel", { head: h.name, no: i + 1 })}
                      value={cell(h.id, i)}
                      onChange={(e) =>
                        setCells((all) => ({ ...all, [`${h.id}:${i}`]: e.target.value }))
                      }
                    />
                  </Td>
                ))}
                <Td numeric>{formatInr(fromPaise(rowTotal(h.id)), locale)}</Td>
              </Tr>
            ))}
            <Tr>
              <Td className="font-semibold">{t("instalmentTotal")}</Td>
              {columns.map((_, i) => (
                <Td key={i} numeric className="font-semibold">
                  {formatInr(fromPaise(colTotal(i)), locale)}
                </Td>
              ))}
              <Td numeric className="font-semibold">
                {formatInr(fromPaise(columns.reduce((s, _, i) => s + colTotal(i), 0)), locale)}
              </Td>
            </Tr>
          </TBody>
        </Table>
      </TableScroll>
      <div className="mt-2 flex gap-2">
        <Button
          variant="secondary"
          size="sm"
          disabled={columns.length >= 24}
          onClick={() =>
            setColumns((all) => [...all, { label: t("term", { no: all.length + 1 }), due_on: "" }])
          }
        >
          {t("addInstalment")}
        </Button>
        <Button
          variant="ghost"
          size="sm"
          disabled={columns.length <= 1}
          onClick={() => setColumns((all) => all.slice(0, -1))}
        >
          {t("removeInstalment")}
        </Button>
      </div>
      <div className="mt-4 grid gap-4 md:grid-cols-4">
        <SelectField
          name="late_kind"
          label={t("lateRule")}
          value={lateKind}
          onChange={(e) => setLateKind(e.target.value)}
          options={[
            { value: "none", label: t("lateNone") },
            { value: "flat_per_instalment", label: t("lateFlat") },
            { value: "per_day", label: t("latePerDay") },
          ]}
        />
        {lateKind !== "none" ? (
          <>
            <TextField
              name="late_amount"
              label={t("lateAmount")}
              inputMode="decimal"
              value={lateAmount}
              onChange={(e) => setLateAmount(e.target.value)}
            />
            <TextField
              name="late_grace"
              label={t("lateGrace")}
              inputMode="numeric"
              value={lateGrace}
              onChange={(e) => setLateGrace(e.target.value)}
            />
          </>
        ) : null}
        {lateKind === "per_day" ? (
          <TextField
            name="late_cap"
            label={t("lateCap")}
            inputMode="decimal"
            value={lateCap}
            onChange={(e) => setLateCap(e.target.value)}
          />
        ) : null}
      </div>
      {problem ? (
        <Alert tone="danger" live title={problem} className="mt-4">
          {t("editorIncompleteBody")}
        </Alert>
      ) : null}
      <ApiErrorAlert error={error} namespace="fees" className="mt-4" />
      <Button className="mt-4" onClick={() => void save()} loading={saving}>
        {t("saveDraft")}
      </Button>
    </Card>
  );
}

function PlanRow({ plan, classes }: { plan: Plan; classes: Map<string, string> }) {
  const t = useTranslations("fees.setup");
  const locale = useLocale() as Locale;
  const can = useStaffCan();
  const api = useBffClient("staff");
  const queryClient = useQueryClient();
  const [summary, setSummary] = useState<PlanApply | null>(null);
  const [error, setError] = useState<unknown>(undefined);
  const invalidate = [KEYS.all] as const;
  const manage = can(MANAGE);
  async function apply(dryRun: boolean) {
    setError(undefined);
    try {
      const out = await unwrap(
        api.POST("/api/v1/fees/plans/{plan_id}/apply", {
          params: { path: { plan_id: plan.id } },
          body: { dry_run: dryRun, include_past_instalments: false },
        }),
      );
      setSummary(out);
      if (!dryRun) await queryClient.invalidateQueries({ queryKey: KEYS.all });
    } catch (failure) {
      setError(failure);
    }
  }
  return (
    <li className="space-y-2 border-b border-border pb-4 last:border-0">
      <p className="font-semibold">
        {plan.name} · {classes.get(plan.class_id) ?? ""}
        {plan.category_code ? ` · ${plan.category_code}` : ""} ·{" "}
        {t("version", { no: plan.plan_version })}{" "}
        <Pill
          variant={plan.status === "active" ? "done" : plan.status === "draft" ? "progress" : "tag"}
        >
          {t(`status.${plan.status}`)}
        </Pill>
      </p>
      <p className="text-sm">
        {t("planSummary", {
          total: formatInr(plan.annual_total, locale) ?? "",
          count: plan.instalments.length,
          accounts: plan.accounts,
        })}
      </p>
      <ul className="text-sm text-ink-muted">
        {plan.instalments.map((i) => (
          <li key={i.instalment_no}>
            {i.label} · {formatDate(i.due_on)} · {formatInr(i.total, locale)}
          </li>
        ))}
      </ul>
      {manage ? (
        <div className="flex flex-wrap gap-2">
          {plan.status === "draft" ? (
            <ActionDialog
              triggerLabel={t("activate")}
              title={t("activateTitle")}
              confirmLabel={t("activate")}
              consequence={
                plan.previous_plan_id ? t("activateNewVersion") : t("activateConsequence")
              }
              schema={z.object({})}
              invalidate={invalidate}
              errorNamespace="fees"
              submit={() =>
                unwrap(
                  api.POST("/api/v1/fees/plans/{plan_id}/activate", {
                    params: { path: { plan_id: plan.id } },
                    headers: { "If-Match": ifMatch(plan.version) },
                  }),
                )
              }
            />
          ) : null}
          {plan.status === "active" ? (
            <>
              <Button variant="secondary" onClick={() => void apply(true)}>
                {t("applyPreview")}
              </Button>
              <ActionDialog
                triggerLabel={t("newVersion")}
                title={t("newVersionTitle")}
                description={t("newVersionBody")}
                confirmLabel={t("newVersion")}
                schema={z.object({})}
                invalidate={invalidate}
                errorNamespace="fees"
                submit={() =>
                  unwrap(
                    api.POST("/api/v1/fees/plans/{plan_id}/new-version", {
                      params: { path: { plan_id: plan.id } },
                    }),
                  )
                }
              />
              {plan.previous_plan_id ? (
                <ActionDialog
                  triggerLabel={t("proposeDifferences")}
                  title={t("proposeTitle")}
                  description={t("proposeBody")}
                  confirmLabel={t("proposeDifferences")}
                  consequence={t("proposeConsequence")}
                  schema={z.object({
                    reason: z
                      .string()
                      .trim()
                      .min(10, { error: "reasonTooShort" })
                      .max(1000, { error: "tooLong" }),
                  })}
                  invalidate={invalidate}
                  errorNamespace="fees"
                  submit={(input) =>
                    unwrap(
                      api.POST("/api/v1/fees/plans/{plan_id}/adjustments", {
                        params: { path: { plan_id: plan.id } },
                        body: input,
                      }),
                    )
                  }
                >
                  {(errors) => (
                    <TextField name="reason" label={t("proposeReason")} error={errors.reason} />
                  )}
                </ActionDialog>
              ) : null}
            </>
          ) : null}
        </div>
      ) : null}
      {summary ? (
        <Alert
          tone={summary.dry_run ? "info" : "success"}
          live
          title={summary.dry_run ? t("previewTitle") : t("appliedTitle")}
        >
          <p>
            {t("applySummary", {
              students: summary.students,
              created: summary.accounts_created,
              present: summary.already_present,
              charges: summary.charges_posted,
              skipped: summary.skipped.length,
            })}
          </p>
          {summary.dry_run ? (
            <Button className="mt-2" onClick={() => void apply(false)}>
              {t("applyNow")}
            </Button>
          ) : null}
        </Alert>
      ) : null}
      <ApiErrorAlert error={error} namespace="fees" />
    </li>
  );
}

/** Fee setup (US-2101; FR-FEE-002..005): heads, plans, activation, applying to a class. */
export function SetupScreen() {
  const t = useTranslations("fees.setup");
  const tf = useTranslations("fees");
  const tn = useTranslations("school.nav");
  const can = useStaffCan();
  const me = useStaffMe();
  const allowed = can([FINANCE_READ, MANAGE]);
  const enabled = me !== undefined && allowed;
  const api = useBffClient("staff");
  const queryClient = useQueryClient();
  const heads = useHeads(enabled);
  const plans = useApiQuery(KEYS.plans, () => unwrap(api.GET("/api/v1/fees/plans")), { enabled });
  const years = useApiQuery(
    ["staff", "fees", "years"],
    () => unwrap(api.GET("/api/v1/academic-years", { params: { query: { limit: 200 } } })),
    { enabled },
  );
  const classes = useApiQuery(
    ["staff", "fees", "classes"],
    () => unwrap(api.GET("/api/v1/classes", { params: { query: { limit: 200 } } })),
    { enabled },
  );
  const classNames = useMemo(
    () =>
      new Map(
        classes.status === "ready"
          ? classes.data.data.map((c) => [c.id, c.display_en] as const)
          : [],
      ),
    [classes],
  );
  const [editing, setEditing] = useState(false);
  const header = (
    <PageHeader
      title={t("title")}
      description={t("description")}
      breadcrumb={[{ label: tn("home"), href: "/" }, { label: t("title") }]}
    />
  );
  if (me !== undefined && !allowed) {
    return (
      <div className="space-y-6">
        {header}
        <NoAccess />
      </div>
    );
  }
  return (
    <div className="space-y-6">
      {header}
      <FeesGate data={heads}>
        {(rows) => (
          <>
            <Card
              title={t("headsTitle")}
              description={t("headsBody")}
              actions={
                can(MANAGE) ? (
                  <ActionDialog
                    triggerLabel={t("addHead")}
                    title={t("addHeadTitle")}
                    confirmLabel={t("addHead")}
                    schema={headSchema}
                    invalidate={[KEYS.heads]}
                    errorNamespace="fees"
                    submit={(input, key) =>
                      unwrap(
                        api.POST("/api/v1/fees/heads", {
                          headers: { "Idempotency-Key": key },
                          body: {
                            code: input.code.toUpperCase(),
                            name: input.name,
                            kind: input.kind,
                            gst_exempt: !input.taxable,
                            sort_order: 100,
                          },
                        }),
                      )
                    }
                  >
                    {(errors) => (
                      <>
                        <TextField
                          name="code"
                          label={t("headCode")}
                          hint={t("headCodeHint")}
                          error={errors.code}
                          maxLength={20}
                        />
                        <TextField
                          name="name"
                          label={t("headName")}
                          error={errors.name}
                          maxLength={80}
                        />
                        <SelectField
                          name="kind"
                          label={t("headKind")}
                          options={(
                            ["tuition", "admission", "exam", "books", "transport", "other"] as const
                          ).map((k) => ({
                            value: k,
                            label: tf(`headKinds.${k}`),
                          }))}
                        />
                        <label className="flex items-center gap-2">
                          <input type="checkbox" name="taxable" />
                          {t("headTaxable")}
                        </label>
                        <p className="text-sm text-ink-muted">{t("headTaxableHint")}</p>
                      </>
                    )}
                  </ActionDialog>
                ) : null
              }
            >
              {rows.length === 0 ? (
                <p>{t("noHeads")}</p>
              ) : (
                <ul className="flex flex-wrap gap-2">
                  {rows.map((h) => (
                    <li key={h.id}>
                      <Pill variant={h.active ? "tag" : "sample"}>
                        {h.code} · {h.name}
                        {!h.gst_exempt ? ` · ${t("taxable")}` : ""}
                        {!h.active ? ` · ${t("inactive")}` : ""}
                      </Pill>
                    </li>
                  ))}
                </ul>
              )}
            </Card>
            <Card
              title={t("plansTitle")}
              actions={
                can(MANAGE) && !editing ? (
                  <Button onClick={() => setEditing(true)} disabled={rows.length === 0}>
                    {t("newPlan")}
                  </Button>
                ) : null
              }
            >
              <FeesGate data={plans}>
                {(list) =>
                  list.length === 0 ? (
                    <p>{t("noPlans")}</p>
                  ) : (
                    <ul className="space-y-4">
                      {list.map((p) => (
                        <PlanRow key={p.id} plan={p} classes={classNames} />
                      ))}
                    </ul>
                  )
                }
              </FeesGate>
            </Card>
            {editing && years.status === "ready" && classes.status === "ready" ? (
              <PlanEditor
                heads={rows}
                years={years.data.data}
                classes={classes.data.data}
                onSaved={() => {
                  setEditing(false);
                  void queryClient.invalidateQueries({ queryKey: KEYS.plans });
                }}
              />
            ) : null}
          </>
        )}
      </FeesGate>
    </div>
  );
}
