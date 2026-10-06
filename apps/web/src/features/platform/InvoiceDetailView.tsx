"use client";

import {
  PAYMENT_METHODS,
  type Invoice,
  type Payment,
  type PaymentMethod,
} from "@schoolos/api-client";
import { useQueryClient } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { z } from "zod";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { Alert } from "@/components/ui/Alert";
import { Pill, type PillVariant } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { DataTable, type Column } from "@/components/ui/Table";
import { Value } from "@/components/ui/Value";
import { ApiError, unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import { formatDate, formatInr } from "@/lib/format";
import type { Loadable } from "@/lib/loadable";
import { reason } from "@/lib/validation";
import { PK, useCan, useSchoolDirectory } from "./data";
import { InvoiceDownload } from "./InvoiceDownload";
import { InvoiceStatusPill } from "./InvoiceTable";
import { Mono, MonoTime } from "./pills";
import { ReasonField } from "./SubscriptionActions";

/** Everything a reversal can change: this invoice, its payments, lists, the school, KPIs. */
const INVALIDATE = [PK.invoices, PK.tenants, PK.dashboard] as const;

/** Payment states as solid status pills (docs/17 §4): kept on record, or reversed. */
const PAYMENT_PILL: Record<Payment["status"], PillVariant> = {
  recorded: "done",
  reversed: "negative",
};

export function PaymentStatusPill({ status }: { status: Payment["status"] }) {
  const t = useTranslations("platform.invoices.payments");
  return <Pill variant={PAYMENT_PILL[status]}>{t(`status.${status}`)}</Pill>;
}

function useMoney() {
  const locale = useLocale();
  return (value: string | null | undefined) => formatInr(value, locale) ?? "";
}

/**
 * FR-PLT-018 (docs/16 §5.9): an invoice's payments, newest received first, with "Reverse
 * payment" on recorded ones for operators holding `platform.invoices.manage`. Reversed rows
 * stay listed with who reversed them, when and why. After a reversal (or a 409/404 that
 * means the list is stale) the invoice and its payments are fetched again.
 */
export function InvoicePayments({
  invoice,
  payments,
}: {
  invoice: Invoice;
  payments: Loadable<readonly Payment[]>;
}) {
  const t = useTranslations("platform.invoices.payments");
  const tm = useTranslations("platform.invoices.methods");
  const inr = useMoney();
  const api = useBffClient("operator");
  const queryClient = useQueryClient();
  const can = useCan();
  const manage = can("platform.invoices.manage");

  async function reverse(row: Payment, why: string): Promise<Payment> {
    try {
      return await unwrap(
        api.POST("/api/v1/platform/payments/{payment_id}/reverse", {
          params: { path: { payment_id: row.id } },
          body: { reason: why },
        }),
      );
    } catch (failure) {
      // Already reversed or gone: show the current state behind the error message.
      if (failure instanceof ApiError && (failure.status === 409 || failure.status === 404)) {
        await Promise.all(
          INVALIDATE.map((queryKey) => queryClient.invalidateQueries({ queryKey })),
        );
      }
      throw failure;
    }
  }

  function action(row: Payment) {
    if (!manage || row.status !== "recorded") return null;
    const total = (Number(row.amount_inr) + Number(row.tds_inr)).toFixed(2);
    const label = t("rowLabel", {
      amount: inr(row.amount_inr),
      date: formatDate(row.received_on) ?? "",
      reference: row.reference,
    });
    return (
      <ActionDialog
        triggerLabel={t("reverse")}
        triggerSize="sm"
        triggerDescription={label}
        title={t("reverseTitle")}
        description={t("reverseBody", { payment: label })}
        note={t("reverseNote")}
        consequence={t("reverseConsequence", { amount: inr(total) })}
        confirmLabel={t("reverse")}
        confirmVariant="danger"
        schema={z.object({ reason })}
        invalidate={INVALIDATE}
        errorNamespace="platform.invoices.payments"
        submit={(data) => reverse(row, data.reason)}
      >
        {(errors) => <ReasonField error={errors.reason} label={t("reason")} />}
      </ActionDialog>
    );
  }

  const columns: Column<Payment>[] = [
    {
      key: "received",
      header: t("colReceived"),
      cell: (row) => <span className="whitespace-nowrap">{formatDate(row.received_on)}</span>,
    },
    {
      key: "amount",
      header: t("colAmount"),
      numeric: true,
      cell: (row) => (
        <span className="flex flex-col items-start sm:items-end">
          <Mono>{inr(row.amount_inr)}</Mono>
          {Number(row.tds_inr) > 0 ? (
            <span className="text-xs whitespace-nowrap text-ink-muted">
              {t("tdsValue", { tds: inr(row.tds_inr) })}
            </span>
          ) : null}
        </span>
      ),
    },
    {
      // Method with the reference under it: one column keeps the table inside 1366×768.
      key: "method",
      header: t("colMethod"),
      cell: (row) => (
        <span className="flex max-w-40 min-w-24 flex-col gap-0.5">
          <span>
            {(PAYMENT_METHODS as readonly string[]).includes(row.method)
              ? tm(row.method as PaymentMethod)
              : row.method}
          </span>
          <span className="font-mono text-xs [overflow-wrap:anywhere] text-ink-muted">
            <span className="sr-only">{t("colReference")}: </span>
            <span>{row.reference}</span>
          </span>
        </span>
      ),
    },
    {
      key: "recorded",
      header: t("colRecordedBy"),
      cell: (row) => (
        <span className="flex min-w-28 flex-col gap-0.5">
          <Value>{row.recorded_by_name ?? null}</Value>
          <MonoTime value={row.recorded_at} />
        </span>
      ),
    },
    {
      key: "status",
      header: t("colStatus"),
      // On a phone card the status and a reversal's reason take the card's full width.
      stack: "wide" as const,
      cell: (row) => (
        <span className="flex min-w-32 flex-col items-start gap-1 sm:max-w-60">
          <PaymentStatusPill status={row.status} />
          {row.status === "reversed" ? (
            <span className="text-xs [overflow-wrap:anywhere] text-ink-muted">
              {t("reversedBy", {
                name: row.reversed_by_name ?? t("unknownOperator"),
                date: formatDate(row.reversed_at) ?? "",
              })}
            </span>
          ) : null}
          {row.status === "reversed" && row.reversal_reason ? (
            <span className="text-xs [overflow-wrap:anywhere] text-ink">
              {t("reversalReason", { reason: row.reversal_reason })}
            </span>
          ) : null}
        </span>
      ),
    },
    ...(manage
      ? [{ key: "actions", header: t("colActions"), cell: action, stack: "actions" as const }]
      : []),
  ];

  return (
    <div className="space-y-4">
      {invoice.status === "issued" || invoice.status === "paid" ? null : (
        <p className="text-sm text-ink-muted">{t("onlyIssued")}</p>
      )}
      <DataTable
        caption={t("title")}
        captionHidden
        columns={columns}
        state={payments}
        density="compact"
        // Below 640px each payment is a card (docs/17 §5.7): the reversal reason gets the
        // card's width instead of a narrow status column that made the row ~460px tall.
        stacked
        rowKey={(row) => row.id}
        emptyTitle={t("emptyTitle")}
        emptyBody={t("emptyBody")}
      />
    </div>
  );
}

/** One invoice (docs/16 §5.8–5.9): totals, PDF, and its payments with "Reverse payment". */
export function InvoiceDetailScreen({ invoiceId }: { invoiceId: string }) {
  const t = useTranslations("platform.invoices");
  const tn = useTranslations("platform.nav");
  const tc = useTranslations("common");
  const te = useTranslations("errors");
  const inr = useMoney();
  const api = useBffClient("operator");
  const { nameOf } = useSchoolDirectory();
  const path = { invoice_id: invoiceId };
  const invoice = useApiQuery([...PK.invoices, invoiceId], () =>
    unwrap(api.GET("/api/v1/platform/invoices/{invoice_id}", { params: { path } })),
  );
  const payments = useApiQuery([...PK.invoices, invoiceId, "payments"], () =>
    unwrap(api.GET("/api/v1/platform/invoices/{invoice_id}/payments", { params: { path } })),
  );

  if (invoice.status === "loading") return <LoadingState label={tc("loading")} />;
  if (invoice.status !== "ready") {
    return (
      <Alert tone="danger" title={tc("loadErrorTitle")}>
        {invoice.status === "error" && invoice.reason
          ? te(`load.${invoice.reason}`)
          : tc("loadErrorBody")}
      </Alert>
    );
  }
  const data = invoice.data;
  const number = data.invoice_number ?? t("draftNumber");

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow={t("detailEyebrow")}
        title={number}
        description={nameOf(data.tenant_id)}
        breadcrumb={[
          { label: tn("dashboard"), href: "/platform" },
          { label: t("title"), href: "/platform/invoices" },
          { label: number },
        ]}
        badge={<InvoiceStatusPill status={data.status} />}
        actions={<InvoiceDownload invoice={data} label={number} />}
      />
      <Card title={t("summary")}>
        <dl className="grid grid-cols-label-value gap-x-4 gap-y-3 text-sm">
          <dt className="text-ink-muted">{t("colPeriod")}</dt>
          <dd>
            {t("periodValue", {
              from: formatDate(data.period_start) ?? "",
              to: formatDate(data.period_end) ?? "",
            })}
          </dd>
          <dt className="text-ink-muted">{t("colIssued")}</dt>
          <dd>
            <Value>{formatDate(data.issue_date)}</Value>
          </dd>
          <dt className="text-ink-muted">{t("colDue")}</dt>
          <dd>
            <Value>{formatDate(data.due_date)}</Value>
          </dd>
          <dt className="text-ink-muted">{t("colTotal")}</dt>
          <dd>
            <Mono>{inr(data.total_inr)}</Mono>
          </dd>
          <dt className="text-ink-muted">{t("amountPaid")}</dt>
          <dd>
            <Mono>{inr(data.amount_paid_inr)}</Mono>
          </dd>
          <dt className="text-ink-muted">{t("tdsReceived")}</dt>
          <dd>
            <Mono>{inr(data.tds_inr)}</Mono>
          </dd>
          <dt className="text-ink-muted">{t("colBalance")}</dt>
          <dd>
            <Mono>{inr(data.balance_due_inr)}</Mono>
          </dd>
        </dl>
      </Card>
      <Card title={t("payments.title")}>
        <InvoicePayments invoice={data} payments={payments} />
      </Card>
    </div>
  );
}
