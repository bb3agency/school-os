"use client";

import { useLocale, useTranslations } from "next-intl";
import * as z from "@/lib/zod";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { ButtonLink } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { TextAreaField, TextField } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { SelectField } from "@/components/ui/Select";
import { StatCard } from "@/components/ui/StatCard";
import { Table, TableScroll, TBody, Td, Th, THead, Tr } from "@/components/ui/Table";
import { Tabs } from "@/components/ui/Tabs";
import { Link } from "@/i18n/navigation";
import type { Locale } from "@/i18n/routing";
import { unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
import { formatDate, formatInr } from "@/lib/format";
import {
  COLLECT,
  CONCESSION_APPROVE,
  CONCESSION_REQUEST,
  FINANCE_READ,
  KEYS,
  MANAGE,
  REASON_MAX,
  REASON_MIN,
  toPaise,
  useAccount,
  type Account,
} from "./data";
import { PaymentLinks } from "@/features/payments/PaymentLinks";
import { Amount, FeesGate, NoAccess, RequestStatusPill } from "./parts";

const reason = z
  .string()
  .trim()
  .min(1, { error: "required" })
  .min(REASON_MIN, { error: "reasonTooShort" })
  .max(REASON_MAX, { error: "tooLong" });
const lineRef = z.string().min(1, { error: "required" });

function concessionSchema() {
  return z.object({
    concession_type: z.enum(["sibling", "staff_ward", "merit", "hardship", "rte", "other"]),
    basis: z.enum(["amount", "percent"]),
    value: z
      .string()
      .trim()
      .refine((v) => (toPaise(v) ?? 0) > 0, { error: "invalidAmount" }),
    target: lineRef,
    reason,
  });
}
const adjustmentSchema = z.object({
  target: lineRef,
  amount: z
    .string()
    .trim()
    .refine((v) => (toPaise(v) ?? 0) !== 0, { error: "invalidAmount" }),
  reason_code: z.enum([
    "late_fee_waiver",
    "plan_change",
    "concession_reversal",
    "correction",
    "other",
  ]),
  reason,
});

function parseTarget(value: string): { instalment_no: number; head_id: string | null } {
  const [no, head] = value.split(":");
  return { instalment_no: Number(no), head_id: head && head !== "late" ? head : null };
}

function Statement({ accountId }: { accountId: string }) {
  const t = useTranslations("fees.account");
  const tk = useTranslations("fees.entryKinds");
  const locale = useLocale() as Locale;
  const api = useBffClient("staff");
  const data = useApiQuery(KEYS.statement(accountId), () =>
    unwrap(
      api.GET("/api/v1/fees/accounts/{account_id}/statement", {
        params: { path: { account_id: accountId } },
      }),
    ),
  );
  return (
    <FeesGate data={data}>
      {(st) => (
        <TableScroll label={t("statementTitle")}>
          <Table>
            <THead>
              <Tr>
                <Th>{t("columns.date")}</Th>
                <Th>{t("columns.kind")}</Th>
                <Th>{t("columns.line")}</Th>
                <Th>{t("columns.receipt")}</Th>
                <Th className="text-right">{t("columns.amount")}</Th>
                <Th className="text-right">{t("columns.balance")}</Th>
              </Tr>
            </THead>
            <TBody>
              {st.entries.map((e) => (
                <Tr key={e.id}>
                  <Td>{formatDate(e.entry_date)}</Td>
                  <Td>{tk(e.entry_kind)}</Td>
                  <Td>
                    {t("instalmentShort", { no: e.instalment_no })} {e.head_code ?? t("lateFee")}
                  </Td>
                  <Td>
                    {e.receipt_id ? (
                      <Link
                        href={`/fees/receipts/${e.receipt_id}`}
                        className="text-primary underline underline-offset-4"
                      >
                        {e.receipt_number}
                      </Link>
                    ) : (
                      "—"
                    )}
                  </Td>
                  <Td numeric>{formatInr(e.amount, locale)}</Td>
                  <Td numeric>{formatInr(e.balance, locale)}</Td>
                </Tr>
              ))}
            </TBody>
          </Table>
        </TableScroll>
      )}
    </FeesGate>
  );
}

function Dues({ account }: { account: Account }) {
  const t = useTranslations("fees.counter");
  const locale = useLocale() as Locale;
  return (
    <TableScroll label={t("duesTitle")}>
      <Table>
        <THead>
          <Tr>
            <Th>{t("columns.instalment")}</Th>
            <Th>{t("columns.head")}</Th>
            <Th>{t("columns.dueOn")}</Th>
            <Th className="text-right">{t("columns.charged")}</Th>
            <Th className="text-right">{t("columns.concession")}</Th>
            <Th className="text-right">{t("columns.lateFee")}</Th>
            <Th className="text-right">{t("columns.adjustment")}</Th>
            <Th className="text-right">{t("columns.paid")}</Th>
            <Th className="text-right">{t("columns.due")}</Th>
          </Tr>
        </THead>
        <TBody>
          {account.lines.map((line) => (
            <Tr key={`${line.instalment_no}:${line.head_id ?? "late"}`}>
              <Td>{line.label}</Td>
              <Td>
                {line.head_name ?? t("lateFee")}
                {!line.collectable ? <span className="block text-xs">{t("taxable")}</span> : null}
              </Td>
              <Td>
                {line.due_on ? formatDate(line.due_on) : "—"}
                {line.overdue ? (
                  <span className="block text-xs font-semibold">{t("overdue")}</span>
                ) : null}
              </Td>
              <Td numeric>{formatInr(line.charged, locale)}</Td>
              <Td numeric>{formatInr(line.concession, locale)}</Td>
              <Td numeric>{formatInr(line.late_fee, locale)}</Td>
              <Td numeric>{formatInr(line.adjustment, locale)}</Td>
              <Td numeric>{formatInr(line.paid, locale)}</Td>
              <Td numeric className="font-semibold">
                {formatInr(line.due, locale)}
              </Td>
            </Tr>
          ))}
        </TBody>
      </Table>
    </TableScroll>
  );
}

function Requests({ account }: { account: Account }) {
  const t = useTranslations("fees.account");
  const tf = useTranslations("fees");
  const can = useStaffCan();
  const api = useBffClient("staff");
  const path = { account_id: account.id };
  const concessions = useApiQuery(
    KEYS.concessions(account.id),
    () => unwrap(api.GET("/api/v1/fees/accounts/{account_id}/concessions", { params: { path } })),
    { enabled: can([FINANCE_READ, CONCESSION_REQUEST, CONCESSION_APPROVE]) },
  );
  const adjustments = useApiQuery(
    KEYS.adjustments(account.id),
    () => unwrap(api.GET("/api/v1/fees/accounts/{account_id}/adjustments", { params: { path } })),
    { enabled: can([FINANCE_READ, MANAGE, CONCESSION_APPROVE]) },
  );
  const targets = account.lines
    .filter((line) => line.head_id !== null)
    .map((line) => ({
      value: `${line.instalment_no}:${line.head_id}`,
      label: `${line.label} · ${line.head_name ?? ""}`,
    }));
  const allTargets = account.lines.map((line) => ({
    value: `${line.instalment_no}:${line.head_id ?? "late"}`,
    label: `${line.label} · ${line.head_name ?? t("lateFee")}`,
  }));
  const invalidate = [KEYS.all] as const;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2">
        {can(CONCESSION_REQUEST) ? (
          <ActionDialog
            triggerLabel={t("requestConcession")}
            title={t("concessionTitle")}
            description={t("concessionBody")}
            confirmLabel={t("requestConcession")}
            consequence={t("needsApproval")}
            schema={concessionSchema()}
            invalidate={invalidate}
            errorNamespace="fees"
            submit={(input, key) =>
              unwrap(
                api.POST("/api/v1/fees/accounts/{account_id}/concessions", {
                  params: { path },
                  headers: { "Idempotency-Key": key },
                  body: {
                    concession_type: input.concession_type,
                    basis: input.basis,
                    value: input.value,
                    targets: [parseTarget(input.target)].map((x) => ({
                      instalment_no: x.instalment_no,
                      head_id: x.head_id ?? "",
                    })),
                    reason: input.reason,
                  },
                }),
              )
            }
          >
            {(errors) => (
              <>
                <SelectField
                  name="concession_type"
                  label={t("concessionType")}
                  options={(
                    ["sibling", "staff_ward", "merit", "hardship", "rte", "other"] as const
                  ).map((v) => ({
                    value: v,
                    label: tf(`concessionTypes.${v}`),
                  }))}
                />
                <SelectField
                  name="basis"
                  label={t("basis")}
                  options={[
                    { value: "amount", label: t("basisAmount") },
                    { value: "percent", label: t("basisPercent") },
                  ]}
                />
                <TextField
                  name="value"
                  label={t("value")}
                  inputMode="decimal"
                  error={errors.value}
                />
                <SelectField
                  name="target"
                  label={t("line")}
                  error={errors.target}
                  options={targets}
                />
                <TextAreaField
                  name="reason"
                  label={t("reason")}
                  hint={t("reasonHint")}
                  error={errors.reason}
                  maxLength={REASON_MAX}
                  rows={3}
                />
              </>
            )}
          </ActionDialog>
        ) : null}
        {can(MANAGE) ? (
          <ActionDialog
            triggerLabel={t("requestAdjustment")}
            title={t("adjustmentTitle")}
            description={t("adjustmentBody")}
            confirmLabel={t("requestAdjustment")}
            consequence={t("needsApproval")}
            schema={adjustmentSchema}
            invalidate={invalidate}
            errorNamespace="fees"
            submit={(input, key) => {
              const target = parseTarget(input.target);
              return unwrap(
                api.POST("/api/v1/fees/accounts/{account_id}/adjustments", {
                  params: { path },
                  headers: { "Idempotency-Key": key },
                  body: {
                    instalment_no: target.instalment_no,
                    head_id: target.head_id,
                    amount: input.amount,
                    reason_code: input.reason_code,
                    reason: input.reason,
                  },
                }),
              );
            }}
          >
            {(errors) => (
              <>
                <SelectField
                  name="target"
                  label={t("line")}
                  error={errors.target}
                  options={allTargets}
                />
                <TextField
                  name="amount"
                  label={t("adjustmentAmount")}
                  hint={t("adjustmentAmountHint")}
                  inputMode="decimal"
                  error={errors.amount}
                />
                <SelectField
                  name="reason_code"
                  label={t("reasonCode")}
                  options={(
                    [
                      "correction",
                      "late_fee_waiver",
                      "concession_reversal",
                      "plan_change",
                      "other",
                    ] as const
                  ).map((v) => ({
                    value: v,
                    label: tf(`adjustmentReasons.${v}`),
                  }))}
                />
                <TextAreaField
                  name="reason"
                  label={t("reason")}
                  hint={t("reasonHint")}
                  error={errors.reason}
                  maxLength={REASON_MAX}
                  rows={3}
                />
              </>
            )}
          </ActionDialog>
        ) : null}
      </div>
      <Card title={t("concessionsTitle")} headingLevel={3}>
        <FeesGate data={concessions}>
          {(rows) =>
            rows.length === 0 ? (
              <p>{t("none")}</p>
            ) : (
              <ul className="space-y-2">
                {rows.map((x) => (
                  <li key={x.id}>
                    <RequestStatusPill status={x.status} />{" "}
                    {tf(`concessionTypes.${x.concession_type}`)} ·{" "}
                    {x.basis === "percent" ? `${x.value}%` : <Amount value={x.value} />}
                    {x.total_amount ? (
                      <>
                        {" "}
                        → <Amount value={x.total_amount} />
                      </>
                    ) : null}{" "}
                    · {x.reason}
                  </li>
                ))}
              </ul>
            )
          }
        </FeesGate>
      </Card>
      <Card title={t("adjustmentsTitle")} headingLevel={3}>
        <FeesGate data={adjustments}>
          {(rows) =>
            rows.length === 0 ? (
              <p>{t("none")}</p>
            ) : (
              <ul className="space-y-2">
                {rows.map((x) => (
                  <li key={x.id}>
                    <RequestStatusPill status={x.status} />{" "}
                    {tf(`adjustmentReasons.${x.reason_code}`)} · <Amount value={x.amount} /> ·{" "}
                    {x.reason}
                  </li>
                ))}
              </ul>
            )
          }
        </FeesGate>
      </Card>
    </div>
  );
}

/** A student's fee account (FR-FEE-021): dues, statement with running balance, requests. */
export function AccountScreen({ accountId }: { accountId: string }) {
  const t = useTranslations("fees.account");
  const tf = useTranslations("fees");
  const tn = useTranslations("school.nav");
  const tc = useTranslations("common");
  const tp = useTranslations("payments.links");
  const locale = useLocale() as Locale;
  const can = useStaffCan();
  const me = useStaffMe();
  const allowed = can([COLLECT, FINANCE_READ]);
  const account = useAccount(me !== undefined && allowed ? accountId : null);
  const header = (a: Account | null) => (
    <PageHeader
      title={a?.display_name ?? t("title")}
      description={
        a ? [a.admission_no, a.class_section, a.plan_name].filter(Boolean).join(" · ") : undefined
      }
      breadcrumb={[
        { label: tn("home"), href: "/" },
        { label: tf("nav.receipts"), href: "/fees/receipts" },
        { label: t("title") },
      ]}
      actions={
        a && can(COLLECT) ? (
          <ButtonLink href={`/fees/collect?account=${a.id}`}>{t("collect")}</ButtonLink>
        ) : null
      }
    />
  );
  if (me !== undefined && !allowed) {
    return (
      <div className="space-y-6">
        {header(null)}
        <NoAccess />
      </div>
    );
  }
  return (
    <FeesGate data={account}>
      {(a) => (
        <div className="space-y-6">
          {header(a)}
          <dl className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <StatCard
              label={t("charged")}
              value={formatInr(a.totals.charged, locale)}
              unavailableLabel={tc("notAvailable")}
            />
            <StatCard
              label={t("paid")}
              value={formatInr(a.totals.paid, locale)}
              unavailableLabel={tc("notAvailable")}
            />
            <StatCard
              label={t("balance")}
              value={formatInr(a.totals.balance, locale)}
              unavailableLabel={tc("notAvailable")}
            />
            <StatCard
              label={t("overdue")}
              value={formatInr(a.totals.overdue, locale)}
              unavailableLabel={tc("notAvailable")}
            />
          </dl>
          {a.pending_requests > 0 ? (
            <p>
              <Link
                href="/fees/approvals"
                className="font-semibold text-primary underline underline-offset-4"
              >
                {t("pending", { count: a.pending_requests })}
              </Link>
            </p>
          ) : null}
          <Tabs
            label={t("tabsLabel")}
            items={[
              { id: "dues", label: t("tabDues"), panel: <Dues account={a} /> },
              { id: "statement", label: t("tabStatement"), panel: <Statement accountId={a.id} /> },
              { id: "requests", label: t("tabRequests"), panel: <Requests account={a} /> },
              { id: "links", label: tp("tab"), panel: <PaymentLinks account={a} /> },
            ]}
          />
        </div>
      )}
    </FeesGate>
  );
}
