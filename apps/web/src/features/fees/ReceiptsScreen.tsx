"use client";

import { useTranslations } from "next-intl";
import { useState } from "react";
import { Button, ButtonLink } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { TextField } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { SelectField } from "@/components/ui/Select";
import { Table, TableScroll, TBody, Td, Th, THead, Tr } from "@/components/ui/Table";
import { Link } from "@/i18n/navigation";
import { unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
import { formatDate } from "@/lib/format";
import { COLLECT, FINANCE_READ, KEYS, type ReceiptListItem, type ReceiptMode } from "./data";
import { Amount, FeesGate, ModeText, NoAccess, ReceiptStatusPill } from "./parts";

interface Filters {
  date: string;
  mode: string;
  status: string;
}

/**
 * Receipts, newest first (docs/17 §5.8): filters are a date and codes only (no names in the
 * URL); cancelled receipts are struck through with the day they were cancelled.
 */
export function ReceiptsScreen() {
  const t = useTranslations("fees.receipts");
  const tf = useTranslations("fees");
  const tn = useTranslations("school.nav");
  const can = useStaffCan();
  const me = useStaffMe();
  const allowed = can([COLLECT, FINANCE_READ]);
  const api = useBffClient("staff");
  const [filters, setFilters] = useState<Filters>({ date: "", mode: "", status: "" });
  const [cursors, setCursors] = useState<(string | null)[]>([null]);
  const cursor = cursors[cursors.length - 1] ?? null;
  const list = useApiQuery(
    KEYS.receipts(filters, cursor),
    () =>
      unwrap(
        api.GET("/api/v1/fees/receipts", {
          params: {
            query: {
              limit: 50,
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
      <div className="space-y-6">
        {header}
        <NoAccess />
      </div>
    );
  }
  function set(key: keyof Filters, value: string) {
    setFilters((f) => ({ ...f, [key]: value }));
    setCursors([null]);
  }
  return (
    <div className="space-y-6">
      {header}
      <form
        role="search"
        className="grid gap-4 md:grid-cols-3"
        onSubmit={(e) => e.preventDefault()}
      >
        <TextField
          type="date"
          name="date"
          label={t("filterDate")}
          value={filters.date}
          onChange={(e) => set("date", e.target.value)}
        />
        <SelectField
          name="mode"
          label={t("filterMode")}
          placeholder={t("any")}
          value={filters.mode}
          onChange={(e) => set("mode", e.target.value)}
          options={(["cash", "cheque", "bank_transfer", "upi_qr", "razorpay"] as const).map(
            (m) => ({
              value: m,
              label: tf(`modes.${m}`),
            }),
          )}
        />
        <SelectField
          name="status"
          label={t("filterStatus")}
          placeholder={t("any")}
          value={filters.status}
          onChange={(e) => set("status", e.target.value)}
          options={(["issued", "cancelled"] as const).map((s) => ({
            value: s,
            label: tf(`receiptStatus.${s}`),
          }))}
        />
      </form>
      <FeesGate data={list}>
        {(page) => (
          <Card title={t("listTitle")}>
            {page.data.length === 0 ? (
              <EmptyState icon="inbox" title={t("emptyTitle")} body={t("emptyBody")} />
            ) : (
              <>
                <TableScroll label={t("listTitle")}>
                  <Table>
                    <THead>
                      <Tr>
                        <Th>{t("columns.number")}</Th>
                        <Th>{t("columns.date")}</Th>
                        <Th>{t("columns.student")}</Th>
                        <Th>{t("columns.mode")}</Th>
                        <Th>{t("columns.collector")}</Th>
                        <Th>{t("columns.status")}</Th>
                        <Th className="text-right">{t("columns.amount")}</Th>
                      </Tr>
                    </THead>
                    <TBody>
                      {page.data.map((r: ReceiptListItem) => (
                        <Tr key={r.id}>
                          <Td>
                            <Link
                              href={`/fees/receipts/${r.id}`}
                              className={`font-semibold text-primary underline underline-offset-4 ${r.status === "cancelled" ? "line-through" : ""}`}
                            >
                              {r.receipt_number}
                            </Link>
                          </Td>
                          <Td>{formatDate(r.receipt_date)}</Td>
                          <Td>
                            {r.student_name}
                            <span className="block text-xs text-ink-muted">{r.class_section}</span>
                          </Td>
                          <Td>
                            <ModeText mode={r.mode} />
                          </Td>
                          <Td>{r.collected_by_name ?? tf("online")}</Td>
                          <Td>
                            <ReceiptStatusPill status={r.status} />
                            {r.cancelled_on ? (
                              <span className="block text-xs">
                                {t("cancelledOn", { date: formatDate(r.cancelled_on) ?? "" })}
                              </span>
                            ) : r.cancellation_pending ? (
                              <span className="block text-xs">{t("cancellationPending")}</span>
                            ) : null}
                          </Td>
                          <Td numeric>
                            <Amount value={r.amount} />
                          </Td>
                        </Tr>
                      ))}
                    </TBody>
                  </Table>
                </TableScroll>
                <div className="mt-4 flex gap-3">
                  {cursors.length > 1 ? (
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={() => setCursors((c) => c.slice(0, -1))}
                    >
                      {tf("previous")}
                    </Button>
                  ) : null}
                  {page.next_cursor ? (
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={() => setCursors((c) => [...c, page.next_cursor ?? null])}
                    >
                      {tf("next")}
                    </Button>
                  ) : null}
                </div>
              </>
            )}
          </Card>
        )}
      </FeesGate>
    </div>
  );
}
