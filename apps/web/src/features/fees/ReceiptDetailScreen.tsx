"use client";

import { useTranslations } from "next-intl";
import { useState } from "react";
import * as z from "@/lib/zod";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Pill } from "@/components/ui/Badge";
import { buttonClasses } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Icon } from "@/components/ui/Icon";
import { TextAreaField, TextField } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { SelectField } from "@/components/ui/Select";
import { Table, TableScroll, TBody, Td, Th, THead, Tr } from "@/components/ui/Table";
import { Link } from "@/i18n/navigation";
import { cn } from "@/lib/cn";
import { unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
import { formatDate, formatDateTime } from "@/lib/format";
import {
  COLLECT,
  FINANCE_READ,
  KEYS,
  MANAGE,
  REASON_MAX,
  REASON_MIN,
  REFUND_REQUEST,
  printHref,
  toPaise,
  type Receipt,
} from "./data";
import {
  Amount,
  FeesGate,
  ModeText,
  MoreActions,
  NoAccess,
  ReceiptStatusPill,
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
const cancelSchema = z.object({
  reason_code: z.enum([
    "wrong_student",
    "wrong_amount",
    "cheque_dishonoured",
    "duplicate_entry",
    "other",
  ]),
  reason,
});
const refundSchema = z.object({
  amount: z
    .string()
    .trim()
    .refine((v) => (toPaise(v) ?? 0) > 0, { error: "invalidAmount" }),
  mode: z.enum(["original_method", "cash", "cheque", "bank_transfer"]),
  reason,
});

/** The stored PDF through a short-lived link (the API client runs any step-up first). */
function useDownloadPdf(receipt: Receipt | null) {
  const api = useBffClient("staff");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(undefined);
  const ready = receipt?.pdf_status === "ready" && receipt.status === "issued";
  async function download() {
    if (!receipt) return;
    setPending(true);
    setError(undefined);
    try {
      const link = await unwrap(
        api.GET("/api/v1/fees/receipts/{receipt_id}/download-url", {
          params: { path: { receipt_id: receipt.id } },
        }),
      );
      window.location.assign(link.url);
    } catch (failure) {
      setError(failure);
    } finally {
      setPending(false);
    }
  }
  return { ready, pending, error, download };
}

function CancelDialog({ receipt, control }: { receipt: Receipt; control: DialogControl }) {
  const t = useTranslations("fees.receipt");
  const tf = useTranslations("fees");
  const api = useBffClient("staff");
  return (
    <ActionDialog
      open={control.open}
      onOpenChange={control.onOpenChange}
      title={t("cancelTitle")}
      description={t("cancelBody")}
      confirmLabel={t("requestCancel")}
      confirmVariant="danger"
      consequence={t("cancelConsequence")}
      schema={cancelSchema}
      invalidate={[KEYS.all]}
      errorNamespace="fees"
      submit={(input, key) =>
        unwrap(
          api.POST("/api/v1/fees/receipts/{receipt_id}/cancellation", {
            params: { path: { receipt_id: receipt.id } },
            headers: { "Idempotency-Key": key },
            body: input,
          }),
        )
      }
    >
      {(errors) => (
        <>
          <SelectField
            name="reason_code"
            label={t("reasonCode")}
            error={errors.reason_code}
            options={(
              [
                "wrong_student",
                "wrong_amount",
                "cheque_dishonoured",
                "duplicate_entry",
                "other",
              ] as const
            ).map((v) => ({ value: v, label: tf(`cancelReasons.${v}`) }))}
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

function RefundDialog({ receipt, control }: { receipt: Receipt; control: DialogControl }) {
  const t = useTranslations("fees.receipt");
  const tf = useTranslations("fees");
  const api = useBffClient("staff");
  return (
    <ActionDialog
      open={control.open}
      onOpenChange={control.onOpenChange}
      title={t("refundTitle")}
      description={receipt.mode === "razorpay" ? t("refundOnlineBody") : t("refundBody")}
      confirmLabel={t("requestRefund")}
      consequence={t("refundConsequence")}
      schema={refundSchema}
      invalidate={[KEYS.all]}
      errorNamespace="fees"
      submit={(input, key) =>
        unwrap(
          api.POST("/api/v1/fees/receipts/{receipt_id}/refunds", {
            params: { path: { receipt_id: receipt.id } },
            headers: { "Idempotency-Key": key },
            body: { amount: input.amount, mode: input.mode, reason: input.reason },
          }),
        )
      }
    >
      {(errors) => (
        <>
          <TextField
            name="amount"
            label={t("refundAmount")}
            inputMode="decimal"
            error={errors.amount}
          />
          <SelectField
            name="mode"
            label={t("refundMode")}
            error={errors.mode}
            options={(receipt.mode === "razorpay"
              ? (["original_method"] as const)
              : (["cash", "cheque", "bank_transfer"] as const)
            ).map((v) => ({
              value: v,
              label: tf(`modes.${v}`),
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

/** The frozen receipt as a paper-like card (A5/A4 print view is the API's own page). */
function ReceiptPreview({ r, pdfNote }: { r: Receipt; pdfNote: string | null }) {
  const t = useTranslations("fees.receipt");
  const tf = useTranslations("fees");
  const cancelled = r.status === "cancelled";
  return (
    <article
      aria-label={t("previewLabel")}
      className="relative mx-auto w-full max-w-3xl min-w-0 overflow-hidden rounded-xl border border-border bg-surface p-4 sm:p-8"
    >
      {cancelled ? (
        <span
          aria-hidden="true"
          className="pointer-events-none absolute end-6 top-6 -rotate-12 rounded-md border-2 border-danger px-3 py-1 text-lg font-semibold tracking-widest text-danger uppercase"
        >
          {tf("receiptStatus.cancelled")}
        </span>
      ) : null}
      <header className="flex flex-wrap items-end justify-between gap-x-6 gap-y-2 border-b border-border pb-4">
        <div className="min-w-0">
          <p className="text-sm text-ink-muted">{tf("counter.receiptNumber")}</p>
          <p
            className={cn(
              "num text-2xl font-semibold whitespace-nowrap text-ink",
              cancelled && "line-through",
            )}
          >
            {r.receipt_number}
          </p>
        </div>
        <div className="text-end">
          <p className="text-sm text-ink-muted">{t("date")}</p>
          <p className="num text-lg font-semibold whitespace-nowrap">
            {formatDate(r.receipt_date)}
          </p>
        </div>
      </header>
      <dl className="grid gap-x-6 gap-y-3 py-4 sm:grid-cols-2">
        <div className="min-w-0">
          <dt className="text-sm text-ink-muted">{t("student")}</dt>
          <dd>
            <Link
              href={`/fees/accounts/${r.account_id}`}
              className="font-semibold text-primary underline underline-offset-4"
            >
              {r.student_name}
            </Link>
            <span className="block text-sm text-ink-muted">
              {[r.admission_no, r.class_section].filter(Boolean).join(" · ")}
            </span>
          </dd>
        </div>
        <div className="min-w-0">
          <dt className="text-sm text-ink-muted">{t("mode")}</dt>
          <dd>
            <ModeText mode={r.mode} />
            {r.utr ? <span className="block font-mono">{t("utr", { utr: r.utr })}</span> : null}
            {r.mode === "upi_qr" ? (
              <span className="mt-1 block">
                <Pill variant={r.bank_confirmed_at ? "done" : "review"}>
                  {r.bank_confirmed_at
                    ? t("bankSeen", { date: formatDate(r.bank_confirmed_at) ?? "" })
                    : t("bankNotSeen")}
                </Pill>
              </span>
            ) : null}
            {Object.entries(r.mode_details).map(([k, v]) => (
              <span key={k} className="block text-sm">
                {tf(`modeDetails.${k}` as "modeDetails.reference")}:{" "}
                <span className="num">{v}</span>
              </span>
            ))}
          </dd>
        </div>
        <div className="min-w-0">
          <dt className="text-sm text-ink-muted">{t("collectedBy")}</dt>
          <dd>{r.collected_by_name ?? tf("online")}</dd>
        </div>
      </dl>
      <TableScroll label={t("linesTitle")}>
        <Table>
          <THead>
            <Tr>
              <Th>{t("instalment")}</Th>
              <Th>{t("head")}</Th>
              <Th numeric>{t("amount")}</Th>
            </Tr>
          </THead>
          <TBody>
            {r.lines.map((line) => (
              <Tr key={`${line.instalment_no}-${line.head_code ?? "late"}`}>
                <Td>{line.label}</Td>
                <Td>{line.head_name}</Td>
                <Td numeric>
                  <Amount value={line.amount} />
                </Td>
              </Tr>
            ))}
            <Tr>
              <Td colSpan={2} className="font-semibold">
                {t("total")}
              </Td>
              <Td numeric className="text-lg font-semibold">
                <Amount value={r.amount} />
              </Td>
            </Tr>
          </TBody>
        </Table>
      </TableScroll>
      <p className="mt-3 text-sm italic">{r.amount_in_words}</p>
      <footer className="mt-4 space-y-1 border-t border-border pt-3">
        {pdfNote ? <p className="text-sm text-ink-muted">{pdfNote}</p> : null}
        <p className="text-xs break-all text-ink-muted">{t("hash", { hash: r.content_sha256 })}</p>
      </footer>
    </article>
  );
}

/**
 * One receipt (FR-FEE-011, FR-FEE-012, FR-FEE-013): its frozen content, status, cancellation
 * and refunds; Print first, then download and the requests a second person decides (never
 * edited) under "More".
 */
export function ReceiptDetailScreen({ receiptId }: { receiptId: string }) {
  const t = useTranslations("fees.receipt");
  const tf = useTranslations("fees");
  const tn = useTranslations("school.nav");
  const tu = useTranslations("ui");
  const tc = useTranslations("common");
  const can = useStaffCan();
  const me = useStaffMe();
  const allowed = can([COLLECT, FINANCE_READ]);
  const api = useBffClient("staff");
  const receipt = useApiQuery(
    KEYS.receipt(receiptId),
    () =>
      unwrap(
        api.GET("/api/v1/fees/receipts/{receipt_id}", {
          params: { path: { receipt_id: receiptId } },
        }),
      ),
    { enabled: me !== undefined && allowed },
  );
  const pdf = useDownloadPdf(receipt.status === "ready" ? receipt.data : null);
  const header = (r: Receipt | null) => {
    const items: MoreItem[] = [];
    if (r) {
      items.push({
        id: "download",
        label: pdf.pending ? tc("working") : t("downloadPdf"),
        disabled: !pdf.ready || pdf.pending,
        onSelect: () => void pdf.download(),
      });
      const refundable = (toPaise(r.amount) ?? 0) - (toPaise(r.refunded) ?? 0);
      if (r.status === "issued" && refundable > 0 && can(REFUND_REQUEST)) {
        items.push({
          id: "refund",
          label: t("requestRefund"),
          dialog: (control) => <RefundDialog receipt={r} control={control} />,
        });
      }
      if (r.status === "issued" && !r.cancellation && can([COLLECT, MANAGE])) {
        items.push({
          id: "cancel",
          label: t("requestCancel"),
          danger: true,
          dialog: (control) => <CancelDialog receipt={r} control={control} />,
        });
      }
    }
    return (
      <PageHeader
        title={r ? t("title", { number: r.receipt_number }) : tf("nav.receipts")}
        description={r ? [r.student_name, r.class_section].filter(Boolean).join(" · ") : undefined}
        breadcrumb={[
          { label: tn("home"), href: "/" },
          { label: tf("nav.receipts"), href: "/fees/receipts" },
          { label: r?.receipt_number ?? t("loading") },
        ]}
        badge={r ? <ReceiptStatusPill status={r.status} /> : undefined}
        actions={
          r ? (
            <>
              <a
                href={printHref(`/api/v1/fees/receipts/${r.id}/print`)}
                target="_blank"
                rel="noopener noreferrer"
                className={buttonClasses("primary", "md")}
              >
                <Icon name="file" className="size-4" />
                {t("print")}
                <span className="sr-only"> ({tf("newTab")})</span>
              </a>
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
    <FeesGate data={receipt}>
      {(r) => {
        const pdfNote =
          r.status === "issued" && r.pdf_status !== "ready"
            ? r.pdf_status === "failed"
              ? t("pdfFailed")
              : t("pdfPreparing")
            : null;
        const side = Boolean(r.cancellation) || r.refunds.length > 0;
        return (
          <div className="space-y-5">
            {header(r)}
            <ApiErrorAlert error={pdf.error} namespace="fees" />
            {r.status === "cancelled" ? (
              <Alert
                tone="warning"
                title={t("cancelledTitle", { date: formatDate(r.cancelled_on) ?? "" })}
              >
                {t("cancelledBody")}
              </Alert>
            ) : null}
            <div
              className={cn(
                "grid gap-5",
                side && "lg:grid-cols-[minmax(0,1fr)_minmax(0,20rem)] lg:items-start",
              )}
            >
              <ReceiptPreview r={r} pdfNote={pdfNote} />
              {side ? (
                <div className="min-w-0 space-y-5">
                  {r.cancellation ? (
                    <Card title={t("cancellationTitle")} padding="sm">
                      <div className="mt-2 space-y-2">
                        <p>
                          <RequestStatusPill status={r.cancellation.status} />{" "}
                          {tf(`cancelReasons.${r.cancellation.reason_code}`)}
                        </p>
                        <p className="text-sm">{r.cancellation.reason}</p>
                        <p className="text-sm text-ink-muted">
                          {t("requestedBy", {
                            name: r.cancellation.requested_by_name ?? "—",
                            when: formatDateTime(r.cancellation.requested_at) ?? "",
                          })}
                        </p>
                        {r.cancellation.status === "pending" ? (
                          <Link
                            href="/fees/approvals"
                            className="text-sm font-semibold text-primary underline underline-offset-4"
                          >
                            {t("toApprovals")}
                          </Link>
                        ) : null}
                      </div>
                    </Card>
                  ) : null}
                  {r.refunds.length ? (
                    <Card title={t("refundsTitle")} padding="sm">
                      <ul className="mt-2 divide-y divide-border">
                        {r.refunds.map((x) => (
                          <li key={x.id} className="space-y-1 py-2">
                            <p className="flex flex-wrap items-center justify-between gap-2">
                              <RequestStatusPill status={x.status} />
                              <Amount value={x.amount} className="num font-semibold" />
                            </p>
                            <p className="text-sm">
                              {tf(`modes.${x.mode === "original_method" ? "razorpay" : x.mode}`)}
                              {x.voucher_number ? (
                                <>
                                  {" · "}
                                  <span className="num whitespace-nowrap">{x.voucher_number}</span>
                                </>
                              ) : null}
                            </p>
                            <p className="text-sm text-ink-muted">{x.reason}</p>
                            {x.status === "processed" ? (
                              <a
                                href={printHref(`/api/v1/fees/refunds/${x.id}/print`)}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="text-sm font-semibold text-primary underline underline-offset-4"
                              >
                                {t("printVoucher")}
                                <span className="sr-only"> ({tf("newTab")})</span>
                              </a>
                            ) : null}
                          </li>
                        ))}
                      </ul>
                    </Card>
                  ) : null}
                </div>
              ) : null}
            </div>
          </div>
        );
      }}
    </FeesGate>
  );
}
