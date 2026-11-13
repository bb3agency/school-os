"use client";

import type { Invoice } from "@schoolos/api-client";
import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";
import { DataTableV2 } from "@/components/console/DataTableV2";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Pill, type PillVariant } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import type { Column } from "@/components/ui/Table";
import { useApiMutation, useApiQuery, useBffClient } from "@/lib/bff/query";
import { formatDate, formatInr } from "@/lib/format";
import * as z from "@/lib/zod";
import { PK, useCan } from "./data";
import {
  createPaymentLink,
  fetchPaymentLinks,
  linkAction,
  type InvoicePaymentLink,
  type LinkStatus,
} from "./payment-links";
import { Figure, MonoTime } from "./pills";

const LINK_PILL: Record<LinkStatus, PillVariant> = {
  active: "progress",
  paid: "done",
  cancelled: "tag",
  expired: "tag",
  needs_review: "review",
};

/** A payment link's short URL with "Copy link" (the clipboard is Baseline; failure says so). */
function LinkUrl({ url }: { url: string }) {
  const t = useTranslations("invoicePayments");
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");
  async function copy() {
    try {
      await navigator.clipboard.writeText(url);
      setState("copied");
    } catch {
      setState("failed");
    }
  }
  return (
    <span className="flex max-w-64 min-w-40 flex-col items-start gap-1">
      <span className="font-mono text-xs [overflow-wrap:anywhere] text-ink">{url}</span>
      <Button variant="secondary" size="sm" onClick={() => void copy()}>
        {t("copy")}
      </Button>
      <span role="status" className="text-xs text-ink-muted">
        {state === "copied" ? t("copied") : state === "failed" ? t("copyFailed") : ""}
      </span>
    </span>
  );
}

/**
 * FA-13 (docs/16 §5.9a; FR-PAY-017..024): the invoice's Razorpay payment links. Operators with
 * `platform.invoices.manage` make a link for the balance due of an issued invoice, check a link
 * when a payment seems missing, and cancel an active one. Paid links show the payment; a link
 * paid but not applied ("Needs review") says why, so the operator refunds it in Razorpay.
 */
export function InvoicePaymentLinks({ invoice }: { invoice: Invoice }) {
  const t = useTranslations("invoicePayments");
  const locale = useLocale();
  const inr = (value: string) => formatInr(value, locale) ?? value;
  const api = useBffClient("operator");
  const can = useCan();
  const manage = can("platform.invoices.manage");
  const key = [...PK.invoices, invoice.id, "payment-links"] as const;
  const invalidate = [PK.invoices, PK.tenants, PK.dashboard] as const;
  const links = useApiQuery(key, () => fetchPaymentLinks(api, invoice.id));
  const check = useApiMutation((id: string) => linkAction(api, id, "check"), { invalidate });
  const balance = Number(invoice.balance_due_inr);
  const data = links.status === "ready" ? links.data : null;
  const rows =
    links.status === "ready" ? { status: "ready" as const, data: links.data.data } : links;
  const hasActive = data?.data.some((l) => l.status === "active") ?? false;
  const canCreate =
    manage && data?.enabled === true && invoice.status === "issued" && balance > 0 && !hasActive;

  function actions(row: InvoicePaymentLink) {
    if (!manage || row.status !== "active") return null;
    const label = t("rowLabel", { amount: inr(row.amount_inr) });
    return (
      <span className="flex flex-wrap gap-2">
        <Button
          variant="secondary"
          size="sm"
          loading={check.isPending && check.variables === row.id}
          disabled={check.isPending}
          onClick={() => check.mutate(row.id)}
        >
          {t("check")}
        </Button>
        <ActionDialog
          triggerLabel={t("cancel")}
          triggerSize="sm"
          triggerDescription={label}
          title={t("cancelTitle")}
          description={t("cancelBody")}
          consequence={t("cancelConsequence")}
          confirmLabel={t("cancel")}
          confirmVariant="danger"
          schema={z.object({})}
          invalidate={invalidate}
          errorNamespace="invoicePayments"
          submit={() => linkAction(api, row.id, "cancel")}
        />
      </span>
    );
  }

  const columns: Column<InvoicePaymentLink>[] = [
    {
      key: "created",
      header: t("colCreated"),
      cell: (row) => (
        <span className="flex flex-col gap-0.5">
          <MonoTime value={row.created_at} />
          <span className="text-xs whitespace-nowrap text-ink-muted">
            {t("expires", { date: formatDate(row.expire_by) ?? "" })}
          </span>
        </span>
      ),
    },
    {
      key: "amount",
      header: t("colAmount"),
      numeric: true,
      cell: (row) => <Figure>{inr(row.amount_inr)}</Figure>,
    },
    {
      key: "status",
      header: t("colStatus"),
      stack: "wide" as const,
      cell: (row) => (
        <span className="flex min-w-28 flex-col items-start gap-1 sm:max-w-60">
          <Pill variant={LINK_PILL[row.status]}>{t(`status.${row.status}`)}</Pill>
          {row.review_reason ? (
            <span className="text-xs [overflow-wrap:anywhere] text-ink">
              {t(`review.${row.review_reason}`)}
            </span>
          ) : null}
        </span>
      ),
    },
    {
      key: "link",
      header: t("colLink"),
      cell: (row) =>
        row.status === "active" ? (
          <LinkUrl url={row.short_url} />
        ) : (
          <span className="text-xs text-ink-muted">{t("linkClosed")}</span>
        ),
    },
    ...(manage
      ? [{ key: "actions", header: t("colActions"), cell: actions, stack: "actions" as const }]
      : []),
  ];

  return (
    <div className="space-y-4">
      {data && !data.enabled ? <p className="text-sm text-ink-muted">{t("off")}</p> : null}
      {data?.enabled ? <p className="text-sm text-ink-muted">{t("intro")}</p> : null}
      {canCreate ? (
        <ActionDialog
          triggerLabel={t("create")}
          title={t("createTitle")}
          description={t("createBody", { amount: inr(invoice.balance_due_inr) })}
          consequence={t("createConsequence")}
          confirmLabel={t("create")}
          schema={z.object({})}
          invalidate={[key, ...invalidate]}
          errorNamespace="invoicePayments"
          submit={(_data, idempotencyKey) => createPaymentLink(api, invoice.id, idempotencyKey)}
        />
      ) : null}
      <ApiErrorAlert error={check.error} namespace="invoicePayments" />
      <DataTableV2
        id="platform.invoice-payment-links"
        caption={t("title")}
        captionHidden
        columns={columns}
        state={rows}
        density="compact"
        stacked
        rowKey={(row) => row.id}
        emptyTitle={t("emptyTitle")}
        emptyBody={t("emptyBody")}
      />
    </div>
  );
}
