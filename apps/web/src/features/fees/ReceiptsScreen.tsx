"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { DataTableV2, type ColumnV2 } from "@/components/console/DataTableV2";
import { ButtonLink } from "@/components/ui/Button";
import type { FilterChip } from "@/components/ui/FilterChips";
import { TextField } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { Pagination } from "@/components/ui/Pagination";
import { SelectField } from "@/components/ui/Select";
import { Link } from "@/i18n/navigation";
import { unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
import { formatDate } from "@/lib/format";
import type { Loadable } from "@/lib/loadable";
import {
  COLLECT,
  FINANCE_READ,
  isFeesOff,
  KEYS,
  type ReceiptListItem,
  type ReceiptMode,
} from "./data";
import { Amount, FeesGate, ModeText, NoAccess, ReceiptStatusPill } from "./parts";

interface Filters {
  date: string;
  mode: string;
  status: string;
}

const NO_FILTERS: Filters = { date: "", mode: "", status: "" };
const PAGE_SIZE = 50;
const MODES = ["cash", "cheque", "bank_transfer", "upi_qr", "razorpay"] as const;
const STATUSES = ["issued", "cancelled"] as const;

/**
 * Receipts, newest first (docs/17 §5.8): filters are a date and codes only (no names in the
 * URL); cancelled receipts are struck through with the day they were cancelled. Receipt
 * numbers, dates and amounts are never cut.
 */
export function ReceiptsScreen() {
  const t = useTranslations("fees.receipts");
  const tf = useTranslations("fees");
  const tn = useTranslations("school.nav");
  const can = useStaffCan();
  const me = useStaffMe();
  const allowed = can([COLLECT, FINANCE_READ]);
  const api = useBffClient("staff");
  const queryClient = useQueryClient();
  const [filters, setFilters] = useState<Filters>(NO_FILTERS);
  const [cursors, setCursors] = useState<(string | null)[]>([null]);
  const cursor = cursors[cursors.length - 1] ?? null;
  const list = useApiQuery(
    KEYS.receipts(filters, cursor),
    () =>
      unwrap(
        api.GET("/api/v1/fees/receipts", {
          params: {
            query: {
              limit: PAGE_SIZE,
              ...(cursor ? { cursor } : {}),
              ...(filters.date ? { date: filters.date } : {}),
              ...(filters.mode ? { mode: filters.mode as ReceiptMode } : {}),
              ...(filters.status ? { status: filters.status as "issued" | "cancelled" } : {}),
            },
          },
        }),
      ),
    { enabled: me !== undefined && allowed },
  );
  const header = (
    <PageHeader
      title={t("title")}
      description={t("description")}
      breadcrumb={[{ label: tn("home"), href: "/" }, { label: t("title") }]}
      actions={
        can(COLLECT) ? <ButtonLink href="/fees/collect">{tf("nav.collect")}</ButtonLink> : null
      }
    />
  );
  if (me !== undefined && !allowed) {
    return (
      <div className="space-y-5">
        {header}
        <NoAccess />
      </div>
    );
  }
  function set(key: keyof Filters, value: string) {
    setFilters((f) => ({ ...f, [key]: value }));
    setCursors([null]);
  }
  function clear() {
    setFilters(NO_FILTERS);
    setCursors([null]);
  }

  const chips: FilterChip[] = [
    ...(filters.date
      ? [{ id: "date", label: t("filterDate"), value: formatDate(filters.date) ?? filters.date }]
      : []),
    ...(filters.mode
      ? [{ id: "mode", label: t("filterMode"), value: tf(`modes.${filters.mode as "cash"}`) }]
      : []),
    ...(filters.status
      ? [
          {
            id: "status",
            label: t("filterStatus"),
            value: tf(`receiptStatus.${filters.status as "issued"}`),
          },
        ]
      : []),
  ];

  const rows: Loadable<readonly ReceiptListItem[]> =
    list.status === "ready" ? { status: "ready", data: list.data.data } : list;
  const pageIndex = cursors.length - 1;
  const shown = list.status === "ready" ? list.data.data.length : 0;
  const nextCursor = list.status === "ready" ? list.data.next_cursor : null;

  const columns: ColumnV2<ReceiptListItem>[] = [
    {
      key: "number",
      header: t("columns.number"),
      className: "whitespace-nowrap",
      cell: (r) => (
        <Link
          href={`/fees/receipts/${r.id}`}
          className={`num font-semibold text-primary underline underline-offset-4 ${r.status === "cancelled" ? "line-through" : ""}`}
        >
          {r.receipt_number}
        </Link>
      ),
    },
    {
      key: "date",
      header: t("columns.date"),
      className: "whitespace-nowrap",
      cell: (r) => <span className="num">{formatDate(r.receipt_date)}</span>,
    },
    {
      key: "student",
      header: t("columns.student"),
      cell: (r) => (
        <>
          {r.student_name}
          <span className="block text-xs text-ink-muted">{r.class_section}</span>
        </>
      ),
    },
    { key: "mode", header: t("columns.mode"), cell: (r) => <ModeText mode={r.mode} /> },
    {
      key: "collector",
      header: t("columns.collector"),
      cell: (r) => r.collected_by_name ?? tf("online"),
    },
    {
      key: "status",
      header: t("columns.status"),
      cell: (r) => (
        <>
          <ReceiptStatusPill status={r.status} />
          {r.cancelled_on ? (
            <span className="num block text-xs whitespace-nowrap text-ink-muted">
              {t("cancelledOn", { date: formatDate(r.cancelled_on) ?? "" })}
            </span>
          ) : r.cancellation_pending ? (
            <span className="block text-xs text-ink-muted">{t("cancellationPending")}</span>
          ) : null}
        </>
      ),
    },
    {
      key: "amount",
      header: t("columns.amount"),
      numeric: true,
      cell: (r) => <Amount value={r.amount} />,
    },
  ];

  return (
    <div className="space-y-5">
      {header}
      {isFeesOff(list) ? (
        <FeesGate data={list}>{() => null}</FeesGate>
      ) : (
        <DataTableV2
          id="fees-receipts"
          // Phones: one card per row (docs/17 §5.7).
          stacked
          caption={t("listTitle")}
          captionHidden
          density="compact"
          columns={columns}
          state={rows}
          rowKey={(r) => r.id}
          toolbar={
            <form
              role="search"
              aria-label={t("listTitle")}
              className="flex flex-wrap items-end gap-3"
              onSubmit={(e) => e.preventDefault()}
            >
              <TextField
                type="date"
                name="date"
                label={t("filterDate")}
                className="w-44 [&_input]:num"
                value={filters.date}
                onChange={(e) => set("date", e.target.value)}
              />
              <SelectField
                name="mode"
                label={t("filterMode")}
                placeholder={t("any")}
                className="w-44"
                value={filters.mode}
                onChange={(e) => set("mode", e.target.value)}
                options={MODES.map((m) => ({ value: m, label: tf(`modes.${m}`) }))}
              />
              <SelectField
                name="status"
                label={t("filterStatus")}
                placeholder={t("any")}
                className="w-40"
                value={filters.status}
                onChange={(e) => set("status", e.target.value)}
                options={STATUSES.map((s) => ({ value: s, label: tf(`receiptStatus.${s}`) }))}
              />
            </form>
          }
          chips={chips}
          onRemoveChip={(id) => set(id as keyof Filters, "")}
          onClearFilters={clear}
          emptyTitle={t("emptyTitle")}
          emptyBody={t("firstUseBody")}
          emptyAction={
            can(COLLECT) ? (
              <ButtonLink href="/fees/collect" variant="secondary">
                {tf("nav.collect")}
              </ButtonLink>
            ) : undefined
          }
          noResultsTitle={t("emptyBody")}
          onRetry={() =>
            void queryClient.invalidateQueries({ queryKey: KEYS.receipts(filters, cursor) })
          }
          footer={
            list.status === "ready" && (shown > 0 || pageIndex > 0) ? (
              <Pagination
                from={pageIndex * PAGE_SIZE + 1}
                to={pageIndex * PAGE_SIZE + shown}
                hasPrevious={pageIndex > 0}
                hasNext={nextCursor !== null && nextCursor !== undefined}
                onPrevious={() => setCursors((c) => c.slice(0, -1))}
                onNext={() => setCursors((c) => [...c, nextCursor ?? null])}
              />
            ) : undefined
          }
        />
      )}
    </div>
  );
}
