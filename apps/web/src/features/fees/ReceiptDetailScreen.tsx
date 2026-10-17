"use client";

import { useTranslations } from "next-intl";
import { useState } from "react";
import * as z from "@/lib/zod";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Pill } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Icon } from "@/components/ui/Icon";
import { TextAreaField, TextField } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { SelectField } from "@/components/ui/Select";
import { Table, TableScroll, TBody, Td, Th, THead, Tr } from "@/components/ui/Table";
import { Link } from "@/i18n/navigation";
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
  NoAccess,
  PrintLink,
  ReceiptStatusPill,
  RequestStatusPill,
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

function DownloadPdf({ receipt }: { receipt: Receipt }) {
  const t = useTranslations("fees.receipt");
  const tc = useTranslations("common");
  const api = useBffClient("staff");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(undefined);
  const ready = receipt.pdf_status === "ready" && receipt.status === "issued";
  async function download() {
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
  return (
    <div className="space-y-2">
      <Button onClick={() => void download()} disabled={!ready || pending}>
        <Icon name="arrowDown" className="size-4" />
        {pending ? tc("working") : t("downloadPdf")}
      </Button>
      {!ready && receipt.status === "issued" ? (
        <p className="text-sm text-ink-muted">
          {receipt.pdf_status === "failed" ? t("pdfFailed") : t("pdfPreparing")}
        </p>
      ) : null}
      <ApiErrorAlert error={error} namespace="fees" />
    </div>
  );
}

/**
 * One receipt (FR-FEE-011, FR-FEE-012, FR-FEE-013): its frozen content, status, cancellation
 * and refunds; print, download, and the requests a second person decides (never edited).
 */
export function ReceiptDetailScreen({ receiptId }: { receiptId: string }) {
  const t = useTranslations("fees.receipt");
  const tf = useTranslations("fees");
  const tn = useTranslations("school.nav");
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
  const header = (r: Receipt | null) => (
    <PageHeader
      title={r ? t("title", { number: r.receipt_number }) : tf("nav.receipts")}
      breadcrumb={[
        { label: tn("home"), href: "/" },
        { label: tf("nav.receipts"), href: "/fees/receipts" },
        { label: r?.receipt_number ?? t("loading") },
      ]}
      badge={r ? <ReceiptStatusPill status={r.status} /> : undefined}
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
    <FeesGate data={receipt}>
      {(r) => {
        const refundable = (toPaise(r.amount) ?? 0) - (toPaise(r.refunded) ?? 0);
        const invalidate = [KEYS.all] as const;
        return (
          <div className="space-y-6">
            {header(r)}
            {r.status === "cancelled" ? (
              <Alert
                tone="warning"
                title={t("cancelledTitle", { date: formatDate(r.cancelled_on) ?? "" })}
              >
                {t("cancelledBody")}
              </Alert>
            ) : null}
            <div className="grid gap-4 lg:grid-cols-3">
              <Card title={t("detailsTitle")} className="lg:col-span-2">
                <dl className="grid gap-2 sm:grid-cols-2">
                  <div>
                    <dt className="text-sm text-ink-muted">{t("student")}</dt>
                    <dd>
                      <Link
                        href={`/fees/accounts/${r.account_id}`}
                        className="font-semibold text-primary underline underline-offset-4"
                      >
                        {r.student_name}
                      </Link>{" "}
                      · {r.admission_no} · {r.class_section}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-sm text-ink-muted">{t("date")}</dt>
                    <dd>{formatDate(r.receipt_date)}</dd>
                  </div>
                  <div>
                    <dt className="text-sm text-ink-muted">{t("mode")}</dt>
                    <dd>
                      <ModeText mode={r.mode} />
                      {r.utr ? (
                        <span className="block font-mono">{t("utr", { utr: r.utr })}</span>
                      ) : null}
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
                          {tf(`modeDetails.${k}` as "modeDetails.reference")}: {v}
                        </span>
                      ))}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-sm text-ink-muted">{t("collectedBy")}</dt>
                    <dd>{r.collected_by_name ?? tf("online")}</dd>
                  </div>
                </dl>
                <TableScroll label={t("linesTitle")} className="mt-4">
                  <Table>
                    <THead>
                      <Tr>
                        <Th>{t("instalment")}</Th>
                        <Th>{t("head")}</Th>
                        <Th className="text-right">{t("amount")}</Th>
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
                        <Td numeric className="font-semibold">
                          <Amount value={r.amount} />
                        </Td>
                      </Tr>
                    </TBody>
                  </Table>
                </TableScroll>
                <p className="mt-2 text-sm italic">{r.amount_in_words}</p>
                <p className="mt-2 break-all text-xs text-ink-muted">
                  {t("hash", { hash: r.content_sha256 })}
                </p>
              </Card>
              <Card title={t("actionsTitle")}>
                <div className="flex flex-col gap-3">
                  <PrintLink path={`/api/v1/fees/receipts/${r.id}/print`} label={t("print")} />
                  <DownloadPdf receipt={r} />
                  {r.status === "issued" && !r.cancellation && can([COLLECT, MANAGE]) ? (
                    <ActionDialog
                      triggerLabel={t("requestCancel")}
                      triggerVariant="danger"
                      title={t("cancelTitle")}
                      description={t("cancelBody")}
                      confirmLabel={t("requestCancel")}
                      confirmVariant="danger"
                      consequence={t("cancelConsequence")}
                      schema={cancelSchema}
                      invalidate={invalidate}
                      errorNamespace="fees"
                      submit={(input, key) =>
                        unwrap(
                          api.POST("/api/v1/fees/receipts/{receipt_id}/cancellation", {
                            params: { path: { receipt_id: r.id } },
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
                  ) : null}
                  {r.status === "issued" && refundable > 0 && can(REFUND_REQUEST) ? (
                    <ActionDialog
                      triggerLabel={t("requestRefund")}
                      title={t("refundTitle")}
                      description={r.mode === "razorpay" ? t("refundOnlineBody") : t("refundBody")}
                      confirmLabel={t("requestRefund")}
                      consequence={t("refundConsequence")}
                      schema={refundSchema}
                      invalidate={invalidate}
                      errorNamespace="fees"
                      submit={(input, key) =>
                        unwrap(
                          api.POST("/api/v1/fees/receipts/{receipt_id}/refunds", {
                            params: { path: { receipt_id: r.id } },
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
                            options={(r.mode === "razorpay"
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
                  ) : null}
                </div>
              </Card>
            </div>
            {r.cancellation ? (
              <Card title={t("cancellationTitle")}>
                <p>
                  <RequestStatusPill status={r.cancellation.status} />{" "}
                  {tf(`cancelReasons.${r.cancellation.reason_code}`)} · {r.cancellation.reason}
                </p>
                <p className="text-sm text-ink-muted">
                  {t("requestedBy", {
                    name: r.cancellation.requested_by_name ?? "—",
                    when: formatDateTime(r.cancellation.requested_at) ?? "",
                  })}
                </p>
                {r.cancellation.status === "pending" ? (
                  <Link
                    href="/fees/approvals"
                    className="font-semibold text-primary underline underline-offset-4"
                  >
                    {t("toApprovals")}
                  </Link>
                ) : null}
              </Card>
            ) : null}
            {r.refunds.length ? (
              <Card title={t("refundsTitle")}>
                <ul className="space-y-2">
                  {r.refunds.map((x) => (
                    <li key={x.id}>
                      <RequestStatusPill status={x.status} /> <Amount value={x.amount} /> ·{" "}
                      {tf(`modes.${x.mode === "original_method" ? "razorpay" : x.mode}`)}
                      {x.voucher_number ? ` · ${x.voucher_number}` : ""} · {x.reason}
                      {x.status === "processed" ? (
                        <a
                          href={printHref(`/api/v1/fees/refunds/${x.id}/print`)}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="ml-2 font-semibold text-primary underline underline-offset-4"
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
        );
      }}
    </FeesGate>
  );
}
