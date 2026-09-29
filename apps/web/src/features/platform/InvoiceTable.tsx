"use client";

import { PAYMENT_METHODS, type Invoice } from "@schoolos/api-client";
import { useLocale, useTranslations } from "next-intl";
import { z } from "zod";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { Pill, type PillVariant } from "@/components/ui/Badge";
import { TextAreaField, TextField } from "@/components/ui/Input";
import { SelectField } from "@/components/ui/Select";
import { DataTable, type Column } from "@/components/ui/Table";
import { Value } from "@/components/ui/Value";
import { unwrap, useBffClient } from "@/lib/bff/query";
import { formatDate, formatInr } from "@/lib/format";
import type { Loadable } from "@/lib/loadable";
import {
  PAYMENT_REFERENCE_PATTERN,
  isoDate,
  money,
  optionalMoney,
  optionalText,
  reason,
} from "@/lib/validation";
import { PK, useCan } from "./data";
import { InvoiceDownload } from "./InvoiceDownload";
import { Mono } from "./pills";
import { ReasonField } from "./SubscriptionActions";

const INVALIDATE = [PK.invoices, PK.tenants, PK.dashboard] as const;

/** Invoice workflow as pills: draft, issued (awaiting payment), paid, void. */
const INVOICE_PILL: Record<Invoice["status"], PillVariant> = {
  draft: "tag",
  issued: "review",
  paid: "done",
  void: "negative",
};

export function InvoiceStatusPill({ status }: { status: Invoice["status"] }) {
  const t = useTranslations("status.invoice");
  return <Pill variant={INVOICE_PILL[status]}>{t(status)}</Pill>;
}

const paymentSchema = z.object({
  method: z.enum(PAYMENT_METHODS, { error: "chooseOption" }),
  amount_inr: money,
  tds_inr: optionalMoney,
  received_on: isoDate,
  reference: z.string().trim().regex(PAYMENT_REFERENCE_PATTERN, { error: "invalidReference" }),
  notes: optionalText(500),
});

/** Invoice number, or "Draft" until it is issued (numbers are assigned on issue). */
export function InvoiceNumber({ invoice }: { invoice: Invoice }) {
  const t = useTranslations("platform.invoices");
  return invoice.invoice_number ? (
    <span className="font-mono text-xs whitespace-nowrap">{invoice.invoice_number}</span>
  ) : (
    <span className="text-ink-muted">{t("draftNumber")}</span>
  );
}

/**
 * FR-PLT-015..019 (docs/16 §5.8–5.9): invoices with issue, record payment (incl. TDS),
 * void with reason and discard draft. Money is shown exactly as the API sends it.
 */
export function InvoiceTable({
  invoices,
  schoolName,
  caption,
}: {
  invoices: Loadable<readonly Invoice[]>;
  /** Omit on a single school's page. */
  schoolName?: (tenantId: string) => string;
  caption: string;
}) {
  const t = useTranslations("platform.invoices");
  const tm = useTranslations("platform.invoices.methods");
  const locale = useLocale();
  const api = useBffClient("operator");
  const can = useCan();
  const manage = can("platform.invoices.manage");
  const money = (value: string) => (
    <Mono>
      <Value>{formatInr(value, locale)}</Value>
    </Mono>
  );

  function actions(row: Invoice) {
    const path = { invoice_id: row.id };
    const label = row.invoice_number ?? `${t("draftNumber")} ${formatDate(row.period_start) ?? ""}`;
    if (!manage) return <InvoiceDownload invoice={row} label={label} />;
    return (
      <div className="relative flex flex-wrap items-start gap-2">
        <InvoiceDownload invoice={row} label={label} />
        {row.status === "draft" ? (
          <>
            <ActionDialog
              triggerLabel={t("issue")}
              triggerSize="sm"
              triggerDescription={label}
              title={t("issueTitle")}
              description={t("issueBody")}
              confirmLabel={t("issue")}
              schema={z.object({})}
              invalidate={INVALIDATE}
              submit={() =>
                unwrap(
                  api.POST("/api/v1/platform/invoices/{invoice_id}/issue", { params: { path } }),
                )
              }
            />
            <ActionDialog
              triggerLabel={t("discard")}
              triggerSize="sm"
              triggerVariant="ghost"
              triggerDescription={label}
              title={t("discardTitle")}
              description={t("discardBody")}
              confirmLabel={t("discard")}
              confirmVariant="danger"
              schema={z.object({})}
              invalidate={INVALIDATE}
              submit={() =>
                unwrap(api.DELETE("/api/v1/platform/invoices/{invoice_id}", { params: { path } }))
              }
            />
          </>
        ) : null}
        {row.status === "issued" ? (
          <>
            <ActionDialog
              triggerLabel={t("recordPayment")}
              triggerSize="sm"
              triggerDescription={label}
              title={t("recordPaymentTitle")}
              description={t("recordPaymentBody", {
                balance: formatInr(row.balance_due_inr, locale) ?? "",
              })}
              confirmLabel={t("recordPayment")}
              schema={paymentSchema}
              invalidate={INVALIDATE}
              submit={(data, key) =>
                unwrap(
                  api.POST("/api/v1/platform/invoices/{invoice_id}/payments", {
                    params: { path, header: { "Idempotency-Key": key } },
                    body: {
                      method: data.method,
                      amount_inr: data.amount_inr,
                      tds_inr: data.tds_inr ?? "0",
                      received_on: data.received_on,
                      reference: data.reference,
                      notes: data.notes,
                    },
                  }),
                )
              }
            >
              {(errors) => (
                <>
                  <SelectField
                    name="method"
                    label={t("method")}
                    error={errors.method}
                    defaultValue="bank_transfer"
                    options={PAYMENT_METHODS.map((value) => ({ value, label: tm(value) }))}
                  />
                  <div className="grid gap-4 md:grid-cols-2">
                    <TextField
                      name="amount_inr"
                      label={t("amount")}
                      hint={t("amountHint")}
                      inputMode="decimal"
                      error={errors.amount_inr}
                      defaultValue={row.balance_due_inr}
                    />
                    <TextField
                      name="tds_inr"
                      label={t("tds")}
                      hint={t("tdsHint")}
                      inputMode="decimal"
                      error={errors.tds_inr}
                    />
                    <TextField
                      name="received_on"
                      type="date"
                      label={t("receivedOn")}
                      error={errors.received_on}
                    />
                    <TextField
                      name="reference"
                      label={t("reference")}
                      hint={t("referenceHint")}
                      error={errors.reference}
                      maxLength={64}
                      autoComplete="off"
                    />
                  </div>
                  <TextAreaField
                    name="notes"
                    label={t("notes")}
                    error={errors.notes}
                    maxLength={500}
                    rows={2}
                  />
                </>
              )}
            </ActionDialog>
            {row.amount_paid_inr === "0.00" || Number(row.amount_paid_inr) === 0 ? (
              <ActionDialog
                triggerLabel={t("void")}
                triggerSize="sm"
                triggerVariant="danger"
                triggerDescription={label}
                title={t("voidTitle")}
                description={t("voidBody")}
                confirmLabel={t("void")}
                confirmVariant="danger"
                schema={z.object({ reason })}
                invalidate={INVALIDATE}
                submit={(data) =>
                  unwrap(
                    api.POST("/api/v1/platform/invoices/{invoice_id}/void", {
                      params: { path },
                      body: { reason: data.reason },
                    }),
                  )
                }
              >
                {(errors) => <ReasonField error={errors.reason} />}
              </ActionDialog>
            ) : null}
          </>
        ) : null}
      </div>
    );
  }

  const columns: Column<Invoice>[] = [
    { key: "number", header: t("colNumber"), cell: (row) => <InvoiceNumber invoice={row} /> },
    ...(schoolName
      ? [
          {
            key: "school",
            header: t("colSchool"),
            cell: (row: Invoice) => (
              <span className="block min-w-44">{schoolName(row.tenant_id)}</span>
            ),
          },
        ]
      : []),
    {
      key: "period",
      header: t("colPeriod"),
      cell: (row) =>
        t("periodValue", {
          from: formatDate(row.period_start) ?? "",
          to: formatDate(row.period_end) ?? "",
        }),
    },
    {
      key: "issued",
      header: t("colIssued"),
      cell: (row) => <Value>{formatDate(row.issue_date)}</Value>,
    },
    { key: "due", header: t("colDue"), cell: (row) => <Value>{formatDate(row.due_date)}</Value> },
    {
      key: "taxable",
      header: t("colSubtotal"),
      className: "text-right tabular-nums",
      cell: (row) => money(row.taxable_value_inr),
    },
    {
      key: "gst",
      header: t("colGst"),
      className: "text-right tabular-nums",
      cell: (row) => (
        <span className="flex flex-col items-end">
          {money((Number(row.cgst_inr) + Number(row.sgst_inr) + Number(row.igst_inr)).toFixed(2))}
          {Number(row.igst_inr) > 0 ? (
            <span className="text-xs whitespace-nowrap text-ink-muted">
              {t("gstSplitInter", { igst: formatInr(row.igst_inr, locale) ?? "" })}
            </span>
          ) : Number(row.cgst_inr) + Number(row.sgst_inr) > 0 ? (
            <span className="text-xs whitespace-nowrap text-ink-muted">
              {t("gstSplitIntra", {
                cgst: formatInr(row.cgst_inr, locale) ?? "",
                sgst: formatInr(row.sgst_inr, locale) ?? "",
              })}
            </span>
          ) : null}
        </span>
      ),
    },
    {
      key: "total",
      header: t("colTotal"),
      className: "text-right tabular-nums",
      cell: (row) => money(row.total_inr),
    },
    {
      key: "balance",
      header: t("colBalance"),
      className: "text-right tabular-nums",
      cell: (row) => money(row.balance_due_inr),
    },
    {
      key: "status",
      header: t("colStatus"),
      cell: (row) => <InvoiceStatusPill status={row.status} />,
    },
    { key: "actions", header: t("colActions"), cell: actions },
  ];

  return (
    <DataTable
      caption={caption}
      captionHidden
      columns={columns}
      state={invoices}
      rowKey={(row) => row.id}
      emptyTitle={t("emptyTitle")}
      emptyBody={t("emptyBody")}
    />
  );
}
