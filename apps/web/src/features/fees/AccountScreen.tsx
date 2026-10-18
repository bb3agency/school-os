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
  fromPaise,
  KEYS,
  MANAGE,
  REASON_MAX,
  REASON_MIN,
  toPaise,
  useAccount,
  type Account,
  type DueLine,
} from "./data";
import { PaymentLinks } from "@/features/payments/PaymentLinks";
import {
  Amount,
  FeesGate,
  MoreActions,
  NoAccess,
  RequestStatusPill,
  type DialogControl,
  type MoreItem,
} from "./parts";

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

/** Sticky last row of a ledger table: stays in view while the rows scroll. */
const TOTAL_CELL = "sticky bottom-0 z-[1] border-t border-border bg-surface-muted font-semibold";
/** Sticky header cells of a ledger that scrolls inside its own box. */
const HEAD_CELL = "sticky top-0 z-[1] bg-surface-muted";

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
      {(st) =>
        st.entries.length === 0 ? (
          <p>{t("none")}</p>
        ) : (
          <TableScroll label={t("statementTitle")} className="max-h-[65vh] overflow-y-auto">
            <Table density="compact">
              <THead>
                <Tr>
                  <Th className={HEAD_CELL}>{t("columns.date")}</Th>
                  <Th className={HEAD_CELL}>{t("columns.kind")}</Th>
                  <Th className={HEAD_CELL}>{t("columns.line")}</Th>
                  <Th className={HEAD_CELL}>{t("columns.receipt")}</Th>
                  <Th numeric className={HEAD_CELL}>
                    {t("columns.amount")}
                  </Th>
                  <Th numeric className={HEAD_CELL}>
                    {t("columns.balance")}
                  </Th>
                </Tr>
              </THead>
              <TBody>
                {st.entries.map((e) => (
                  <Tr key={e.id}>
                    <Td className="num whitespace-nowrap">{formatDate(e.entry_date)}</Td>
                    <Td>{tk(e.entry_kind)}</Td>
                    <Td className="whitespace-nowrap">
                      {t("instalmentShort", { no: e.instalment_no })} {e.head_code ?? t("lateFee")}
                    </Td>
                    <Td className="whitespace-nowrap">
                      {e.receipt_id ? (
                        <Link
                          href={`/fees/receipts/${e.receipt_id}`}
                          className="num text-primary underline underline-offset-4"
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
              <tfoot>
                <Tr>
                  <Td colSpan={5} className={TOTAL_CELL}>
                    {t("closingBalance")}
                  </Td>
                  <Td numeric className={TOTAL_CELL}>
                    {formatInr(st.balance, locale)}
                  </Td>
                </Tr>
              </tfoot>
            </Table>
          </TableScroll>
        )
      }
    </FeesGate>
  );
}

const DUE_COLUMNS = ["charged", "concession", "late_fee", "adjustment", "paid", "due"] as const;
const DUE_HEADERS: Record<(typeof DUE_COLUMNS)[number], string> = {
  charged: "columns.charged",
  concession: "columns.concession",
  late_fee: "columns.lateFee",
  adjustment: "columns.adjustment",
  paid: "columns.paid",
  due: "columns.due",
};

/** Column totals added as whole paise (never floating-point rupees, FR-FEE-028). */
function sumLines(lines: readonly DueLine[], key: (typeof DUE_COLUMNS)[number]): string {
  return fromPaise(lines.reduce((sum, line) => sum + (toPaise(line[key]) ?? 0), 0));
}

function Dues({ account }: { account: Account }) {
  const t = useTranslations("fees.counter");
  const tf = useTranslations("fees");
  const locale = useLocale() as Locale;
  return (
    <TableScroll label={t("duesTitle")} className="max-h-[65vh] overflow-y-auto">
      <Table density="compact">
        <THead>
          <Tr>
            <Th className={HEAD_CELL}>{t("columns.instalment")}</Th>
            <Th className={HEAD_CELL}>{t("columns.head")}</Th>
            <Th className={HEAD_CELL}>{t("columns.dueOn")}</Th>
            {DUE_COLUMNS.map((key) => (
              <Th key={key} numeric className={HEAD_CELL}>
                {t(DUE_HEADERS[key] as "columns.due")}
              </Th>
            ))}
          </Tr>
        </THead>
        <TBody>
          {account.lines.map((line) => (
            <Tr key={`${line.instalment_no}:${line.head_id ?? "late"}`}>
              <Td>{line.label}</Td>
              <Td>
                {line.head_name ?? t("lateFee")}
                {!line.collectable ? (
                  <span className="block text-xs text-ink-muted">{t("taxable")}</span>
                ) : null}
              </Td>
              <Td className="whitespace-nowrap">
                <span className="num">{line.due_on ? formatDate(line.due_on) : "—"}</span>
                {line.overdue ? (
                  <span className="block text-xs font-semibold text-danger">{t("overdue")}</span>
                ) : null}
              </Td>
              {DUE_COLUMNS.map((key) => (
                <Td key={key} numeric className={key === "due" ? "font-semibold" : undefined}>
                  {formatInr(line[key], locale)}
                </Td>
              ))}
            </Tr>
          ))}
        </TBody>
        <tfoot>
          <Tr>
            <Td colSpan={3} className={TOTAL_CELL}>
              {tf("receipt.total")}
            </Td>
            {DUE_COLUMNS.map((key) => (
              <Td key={key} numeric className={TOTAL_CELL}>
                {formatInr(sumLines(account.lines, key), locale)}
              </Td>
            ))}
          </Tr>
        </tfoot>
      </Table>
    </TableScroll>
  );
}

function ConcessionDialog({ account, control }: { account: Account; control: DialogControl }) {
  const t = useTranslations("fees.account");
  const tf = useTranslations("fees");
  const api = useBffClient("staff");
  const path = { account_id: account.id };
  const targets = account.lines
    .filter((line) => line.head_id !== null)
    .map((line) => ({
      value: `${line.instalment_no}:${line.head_id}`,
      label: `${line.label} · ${line.head_name ?? ""}`,
    }));
  return (
    <ActionDialog
      open={control.open}
      onOpenChange={control.onOpenChange}
      title={t("concessionTitle")}
      description={t("concessionBody")}
      confirmLabel={t("requestConcession")}
      consequence={t("needsApproval")}
      schema={concessionSchema()}
      invalidate={[KEYS.all]}
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
            options={(["sibling", "staff_ward", "merit", "hardship", "rte", "other"] as const).map(
              (v) => ({
                value: v,
                label: tf(`concessionTypes.${v}`),
              }),
            )}
          />
          <SelectField
            name="basis"
            label={t("basis")}
            options={[
              { value: "amount", label: t("basisAmount") },
              { value: "percent", label: t("basisPercent") },
            ]}
          />
          <TextField name="value" label={t("value")} inputMode="decimal" error={errors.value} />
          <SelectField name="target" label={t("line")} error={errors.target} options={targets} />
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
  );
}

function AdjustmentDialog({ account, control }: { account: Account; control: DialogControl }) {
  const t = useTranslations("fees.account");
  const tf = useTranslations("fees");
  const api = useBffClient("staff");
  const path = { account_id: account.id };
  const allTargets = account.lines.map((line) => ({
    value: `${line.instalment_no}:${line.head_id ?? "late"}`,
    label: `${line.label} · ${line.head_name ?? t("lateFee")}`,
  }));
  return (
    <ActionDialog
      open={control.open}
      onOpenChange={control.onOpenChange}
      title={t("adjustmentTitle")}
      description={t("adjustmentBody")}
      confirmLabel={t("requestAdjustment")}
      consequence={t("needsApproval")}
      schema={adjustmentSchema}
      invalidate={[KEYS.all]}
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
          <SelectField name="target" label={t("line")} error={errors.target} options={allTargets} />
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
  return (
    <div className="grid gap-5 lg:grid-cols-2">
      <Card title={t("concessionsTitle")} headingLevel={3} padding="sm">
        <div className="mt-2">
          <FeesGate data={concessions}>
            {(rows) =>
              rows.length === 0 ? (
                <p className="text-ink-muted">{t("none")}</p>
              ) : (
                <ul className="divide-y divide-border">
                  {rows.map((x) => (
                    <li key={x.id} className="space-y-1 py-2">
                      <p className="flex flex-wrap items-center justify-between gap-2">
                        <span className="inline-flex flex-wrap items-center gap-2">
                          <RequestStatusPill status={x.status} />
                          {tf(`concessionTypes.${x.concession_type}`)}
                        </span>
                        <span className="font-semibold">
                          {x.basis === "percent" ? (
                            <span className="num">{`${x.value}%`}</span>
                          ) : (
                            <Amount value={x.value} />
                          )}
                          {x.total_amount ? (
                            <>
                              {" → "}
                              <Amount value={x.total_amount} />
                            </>
                          ) : null}
                        </span>
                      </p>
                      <p className="text-sm text-ink-muted">{x.reason}</p>
                    </li>
                  ))}
                </ul>
              )
            }
          </FeesGate>
        </div>
      </Card>
      <Card title={t("adjustmentsTitle")} headingLevel={3} padding="sm">
        <div className="mt-2">
          <FeesGate data={adjustments}>
            {(rows) =>
              rows.length === 0 ? (
                <p className="text-ink-muted">{t("none")}</p>
              ) : (
                <ul className="divide-y divide-border">
                  {rows.map((x) => (
                    <li key={x.id} className="space-y-1 py-2">
                      <p className="flex flex-wrap items-center justify-between gap-2">
                        <span className="inline-flex flex-wrap items-center gap-2">
                          <RequestStatusPill status={x.status} />
                          {tf(`adjustmentReasons.${x.reason_code}`)}
                        </span>
                        <Amount value={x.amount} className="num font-semibold whitespace-nowrap" />
                      </p>
                      <p className="text-sm text-ink-muted">{x.reason}</p>
                    </li>
                  ))}
                </ul>
              )
            }
          </FeesGate>
        </div>
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
  const tu = useTranslations("ui");
  const tp = useTranslations("payments.links");
  const locale = useLocale() as Locale;
  const can = useStaffCan();
  const me = useStaffMe();
  const allowed = can([COLLECT, FINANCE_READ]);
  const account = useAccount(me !== undefined && allowed ? accountId : null);
  const header = (a: Account | null) => {
    const items: MoreItem[] = [];
    if (a && can(CONCESSION_REQUEST)) {
      items.push({
        id: "concession",
        label: t("requestConcession"),
        dialog: (control) => <ConcessionDialog account={a} control={control} />,
      });
    }
    if (a && can(MANAGE)) {
      items.push({
        id: "adjustment",
        label: t("requestAdjustment"),
        dialog: (control) => <AdjustmentDialog account={a} control={control} />,
      });
    }
    return (
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
          a ? (
            <>
              {can(COLLECT) ? (
                <ButtonLink href={`/fees/collect?account=${a.id}`}>{t("collect")}</ButtonLink>
              ) : null}
              <MoreActions label={tu("moreActions")} items={items} />
            </>
          ) : null
        }
      />
    );
  };
  if (me !== undefined && !allowed) {
    return (
      <div className="space-y-5">
        {header(null)}
        <NoAccess />
      </div>
    );
  }
  return (
    <FeesGate data={account}>
      {(a) => (
        <div className="space-y-5">
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
