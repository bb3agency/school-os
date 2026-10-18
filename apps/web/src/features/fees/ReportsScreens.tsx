"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { useMemo, useState } from "react";
import * as z from "@/lib/zod";
import { DataTableV2, type ColumnV2 } from "@/components/console";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Card } from "@/components/ui/Card";
import type { FilterChip } from "@/components/ui/FilterChips";
import { TextField } from "@/components/ui/Input";
import { Menu, type MenuEntry } from "@/components/ui/Menu";
import { PageHeader } from "@/components/ui/PageHeader";
import { SelectField } from "@/components/ui/Select";
import { StatCard } from "@/components/ui/StatCard";
import { Table, TableScroll, TBody, Td, Th, THead, Tr } from "@/components/ui/Table";
import { Link } from "@/i18n/navigation";
import type { Locale } from "@/i18n/routing";
import { unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
import { formatCount, formatDate, formatInr } from "@/lib/format";
import type { Loadable } from "@/lib/loadable";
import {
  FINANCE_READ,
  KEYS,
  MANAGE,
  REPORT,
  isFeesOff,
  saveText,
  useFeeStatus,
  type DailyReport,
  type Dues,
} from "./data";
import { Amount, FeesGate, ModeText, NoAccess, PrintLink, ReceiptStatusPill } from "./parts";

/**
 * Download a CSV through the API client from a "More" menu item: a stale sign-in first gets
 * the step-up prompt, then the file is saved; nothing is kept in browser storage.
 */
function useCsvDownload(load: () => Promise<{ name: string; text: string }>) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(undefined);
  const run = () => {
    setPending(true);
    setError(undefined);
    load()
      .then(({ name, text }) => saveText(name, text))
      .catch((failure: unknown) => setError(failure))
      .finally(() => setPending(false));
  };
  return { pending, error, run };
}

function mapLoadable<T, U>(value: Loadable<T>, map: (data: T) => U): Loadable<U> {
  return value.status === "ready" ? { status: "ready", data: map(value.data) } : value;
}

type DailyReceipt = DailyReport["receipts"][number];

/**
 * Daily collection report (FR-FEE-018) and day close (FR-FEE-019): totals by mode and by
 * collector, cancellations and refunds of the day, cash and cheques to hand over; A4 print, the
 * day book CSV (step-up) and "Close day" with the totals in the confirmation.
 */
export function DailyScreen() {
  const t = useTranslations("fees.daily");
  const tn = useTranslations("school.nav");
  const tc = useTranslations("common");
  const tu = useTranslations("ui");
  const locale = useLocale() as Locale;
  const can = useStaffCan();
  const me = useStaffMe();
  const allowed = can(FINANCE_READ);
  const status = useFeeStatus(me !== undefined && allowed);
  const [date, setDate] = useState("");
  const day = date || (status.status === "ready" ? status.data.today : "");
  const api = useBffClient("staff");
  const report = useApiQuery(
    KEYS.daily(day),
    () => unwrap(api.GET("/api/v1/fees/reports/daily", { params: { query: { date: day } } })),
    { enabled: me !== undefined && allowed && day !== "" },
  );
  const current = report.status === "ready" ? report.data : null;
  const csv = useCsvDownload(async () => {
    const reportDate = current?.date ?? day;
    return {
      name: `day-book-${reportDate}.csv`,
      text: await unwrap(
        api.GET("/api/v1/fees/reports/daily.csv", {
          params: { query: { date: reportDate } },
          parseAs: "text",
        }),
      ),
    };
  });
  const receiptColumns = useMemo<ColumnV2<DailyReceipt>[]>(
    () => [
      {
        key: "number",
        name: t("columns.number"),
        header: t("columns.number"),
        hideable: false,
        cell: (x) => (
          <Link
            href={`/fees/receipts/${x.id}`}
            className="num font-mono whitespace-nowrap text-primary underline underline-offset-4"
          >
            {x.receipt_number}
          </Link>
        ),
      },
      {
        key: "student",
        name: t("columns.student"),
        header: t("columns.student"),
        cell: (x) => (
          <>
            {x.student_name} <span className="text-xs text-ink-muted">{x.class_section}</span>
          </>
        ),
      },
      {
        key: "mode",
        name: t("columns.mode"),
        header: t("columns.mode"),
        cell: (x) => <ModeText mode={x.mode} />,
      },
      {
        key: "status",
        name: t("columns.status"),
        header: t("columns.status"),
        cell: (x) => <ReceiptStatusPill status={x.status} />,
      },
      {
        key: "amount",
        name: t("columns.amount"),
        header: t("columns.amountInr"),
        numeric: true,
        cell: (x) => <Amount value={x.amount} />,
      },
    ],
    [t],
  );

  const moreEntries: MenuEntry[] =
    current && can(REPORT)
      ? [
          {
            id: "csv",
            label: csv.pending ? tc("working") : t("csv"),
            icon: "file",
            disabled: csv.pending,
            onSelect: csv.run,
          },
        ]
      : [];
  const header = (
    <PageHeader
      title={t("title")}
      description={t("description")}
      breadcrumb={[{ label: tn("home"), href: "/" }, { label: t("title") }]}
      actions={
        current ? (
          <>
            {can(MANAGE) && !current.closed ? (
              <ActionDialog
                triggerLabel={t("close")}
                triggerVariant="primary"
                title={t("closeTitle", { date: formatDate(current.date) ?? "" })}
                description={t("closeBody")}
                confirmLabel={t("close")}
                consequence={t("closeConsequence", {
                  count: current.receipts.length,
                  net: formatInr(current.net_total, locale) ?? "",
                })}
                schema={z.object({})}
                invalidate={[KEYS.all]}
                errorNamespace="fees"
                submit={() =>
                  unwrap(api.POST("/api/v1/fees/day-closes", { body: { date: current.date } }))
                }
              >
                {() => (
                  <dl className="grid grid-cols-2 gap-x-4 gap-y-2 rounded-md border border-border p-3 text-sm">
                    <dt className="text-ink-muted">{t("receipts")}</dt>
                    <dd className="num text-end">{formatInr(current.receipts_total, locale)}</dd>
                    <dt className="text-ink-muted">{t("net")}</dt>
                    <dd className="num text-end font-semibold">
                      {formatInr(current.net_total, locale)}
                    </dd>
                    <dt className="text-ink-muted">{t("cash")}</dt>
                    <dd className="num text-end">{formatInr(current.cash_to_hand_over, locale)}</dd>
                    <dt className="text-ink-muted">{t("cheques")}</dt>
                    <dd className="num text-end">
                      {formatInr(current.cheque_to_hand_over, locale)}
                    </dd>
                  </dl>
                )}
              </ActionDialog>
            ) : null}
            <PrintLink
              path={`/api/v1/fees/reports/daily/print?date=${current.date}`}
              label={t("print")}
            />
            {moreEntries.length > 0 ? (
              <Menu
                label={tu("moreActions")}
                variant="button"
                trigger={tu("more")}
                align="end"
                entries={moreEntries}
              />
            ) : null}
          </>
        ) : null
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
  return (
    <div className="space-y-5">
      {header}
      <ApiErrorAlert error={csv.error} namespace="fees" />
      <FeesGate data={status}>
        {(info) => (
          <>
            <form
              role="search"
              aria-label={t("filterLabel")}
              className="flex flex-wrap items-end gap-4"
              onSubmit={(e) => e.preventDefault()}
            >
              <TextField
                type="date"
                name="date"
                label={t("date")}
                max={info.today}
                value={day}
                className="num"
                onChange={(e) => setDate(e.target.value)}
              />
            </form>
            <FeesGate data={report}>
              {(r) => (
                <>
                  {r.closed ? (
                    <Alert tone="info" title={t("closedTitle")}>
                      {t("closedBody", {
                        name: r.closed.closed_by_name ?? "—",
                        count: r.closed.receipt_count,
                      })}
                    </Alert>
                  ) : null}
                  <dl
                    aria-label={t("totalsLabel")}
                    className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4"
                  >
                    <StatCard
                      label={t("receipts")}
                      value={formatInr(r.receipts_total, locale)}
                      unavailableLabel={tc("notAvailable")}
                      hint={t("count", { count: r.receipts.length })}
                    />
                    <StatCard
                      label={t("net")}
                      value={formatInr(r.net_total, locale)}
                      unavailableLabel={tc("notAvailable")}
                    />
                    <StatCard
                      label={t("cash")}
                      value={formatInr(r.cash_to_hand_over, locale)}
                      unavailableLabel={tc("notAvailable")}
                    />
                    <StatCard
                      label={t("cheques")}
                      value={formatInr(r.cheque_to_hand_over, locale)}
                      unavailableLabel={tc("notAvailable")}
                    />
                  </dl>
                  <div className="grid gap-4 lg:grid-cols-2">
                    <Card title={t("byMode")} className="min-w-0">
                      {r.by_mode.length === 0 ? (
                        <p>{t("none")}</p>
                      ) : (
                        <TableScroll label={t("byMode")}>
                          <Table density="compact">
                            <THead>
                              <Tr>
                                <Th>{t("columns.mode")}</Th>
                                <Th numeric>{t("columns.count")}</Th>
                                <Th numeric>{t("columns.amountInr")}</Th>
                              </Tr>
                            </THead>
                            <TBody>
                              {r.by_mode.map((m) => (
                                <Tr key={m.mode}>
                                  <Td>
                                    <ModeText mode={m.mode} />
                                  </Td>
                                  <Td numeric>{formatCount(m.count, locale)}</Td>
                                  <Td numeric>
                                    <Amount value={m.total} />
                                  </Td>
                                </Tr>
                              ))}
                            </TBody>
                          </Table>
                        </TableScroll>
                      )}
                    </Card>
                    <Card title={t("byCollector")} className="min-w-0">
                      {r.by_collector.length === 0 ? (
                        <p>{t("none")}</p>
                      ) : (
                        <TableScroll label={t("byCollector")}>
                          <Table density="compact">
                            <THead>
                              <Tr>
                                <Th>{t("columns.collector")}</Th>
                                <Th numeric>{t("columns.count")}</Th>
                                <Th numeric>{t("columns.amountInr")}</Th>
                              </Tr>
                            </THead>
                            <TBody>
                              {r.by_collector.map((x) => (
                                <Tr key={x.membership_id ?? "online"}>
                                  <Td>{x.name ?? t("online")}</Td>
                                  <Td numeric>{formatCount(x.count, locale)}</Td>
                                  <Td numeric>
                                    <Amount value={x.total} />
                                  </Td>
                                </Tr>
                              ))}
                            </TBody>
                          </Table>
                        </TableScroll>
                      )}
                    </Card>
                  </div>
                  <DataTableV2
                    id="fees-daily-receipts"
                    caption={t("receiptsTitle")}
                    columns={receiptColumns}
                    state={{ status: "ready", data: r.receipts }}
                    rowKey={(x) => x.id}
                    emptyTitle={t("emptyTitle")}
                    emptyBody={t("emptyBody")}
                    stacked
                  />
                  {r.cancellations.length || r.refunds.length ? (
                    <Card title={t("reversalsTitle")} className="min-w-0">
                      <TableScroll label={t("reversalsTitle")}>
                        <Table density="compact">
                          <THead>
                            <Tr>
                              <Th>{t("columns.reversal")}</Th>
                              <Th numeric>{t("columns.amountInr")}</Th>
                            </Tr>
                          </THead>
                          <TBody>
                            {r.cancellations.map((x) => (
                              <Tr key={x.cancellation_id}>
                                <Td>
                                  <span className="num">
                                    {t("cancelled", { number: x.receipt_number })}
                                  </span>
                                </Td>
                                <Td numeric>
                                  <Amount value={x.amount} />
                                </Td>
                              </Tr>
                            ))}
                            {r.refunds.map((x) => (
                              <Tr key={x.refund_id}>
                                <Td>
                                  <span className="num">
                                    {t("refunded", {
                                      voucher: x.voucher_number,
                                      number: x.receipt_number,
                                    })}
                                  </span>
                                </Td>
                                <Td numeric>
                                  <Amount value={x.amount} />
                                </Td>
                              </Tr>
                            ))}
                          </TBody>
                        </Table>
                      </TableScroll>
                    </Card>
                  ) : null}
                </>
              )}
            </FeesGate>
          </>
        )}
      </FeesGate>
    </div>
  );
}

type DuesRow = Dues["rows"][number];

/** Dues list (FR-FEE-020): students with overdue amounts, oldest first; CSV with step-up. */
export function DuesScreen() {
  const t = useTranslations("fees.dues");
  const tn = useTranslations("school.nav");
  const tc = useTranslations("common");
  const tu = useTranslations("ui");
  const locale = useLocale() as Locale;
  const can = useStaffCan();
  const me = useStaffMe();
  const allowed = can(FINANCE_READ);
  const api = useBffClient("staff");
  const queryClient = useQueryClient();
  const [classId, setClassId] = useState("");
  const classes = useApiQuery(
    ["staff", "fees", "classes"],
    () => unwrap(api.GET("/api/v1/classes", { params: { query: { limit: 200 } } })),
    { enabled: me !== undefined && allowed },
  );
  const dues = useApiQuery(
    KEYS.dues("", classId),
    () =>
      unwrap(
        api.GET("/api/v1/fees/dues", { params: { query: classId ? { class_id: classId } : {} } }),
      ),
    { enabled: me !== undefined && allowed },
  );
  const csv = useCsvDownload(async () => ({
    name: "fee-dues.csv",
    text: await unwrap(
      api.GET("/api/v1/fees/dues.csv", {
        params: { query: classId ? { class_id: classId } : {} },
        parseAs: "text",
      }),
    ),
  }));
  const columns = useMemo<ColumnV2<DuesRow>[]>(
    () => [
      {
        key: "student",
        name: t("columns.student"),
        header: t("columns.student"),
        hideable: false,
        cell: (row) => (
          <>
            <Link
              href={`/fees/accounts/${row.account_id}`}
              className="font-semibold text-primary underline underline-offset-4"
            >
              {row.display_name ?? t("noName")}
            </Link>
            <span className="block font-mono text-xs whitespace-nowrap text-ink-muted">
              {row.admission_no}
            </span>
          </>
        ),
      },
      {
        key: "class",
        name: t("columns.class"),
        header: t("columns.class"),
        cell: (row) => row.class_section,
      },
      {
        key: "oldest",
        name: t("columns.oldest"),
        header: t("columns.oldest"),
        cell: (row) => (
          <span className="num whitespace-nowrap">{formatDate(row.oldest_due_on)}</span>
        ),
      },
      {
        key: "lastPayment",
        name: t("columns.lastPayment"),
        header: t("columns.lastPayment"),
        cell: (row) => (
          <span className="num whitespace-nowrap">{formatDate(row.last_payment_on) ?? "—"}</span>
        ),
      },
      {
        key: "overdue",
        name: t("columns.overdue"),
        header: t("columns.overdueInr"),
        numeric: true,
        cell: (row) => <Amount value={row.overdue} />,
      },
    ],
    [t],
  );
  const header = (
    <PageHeader
      title={t("title")}
      description={t("description")}
      breadcrumb={[{ label: tn("home"), href: "/" }, { label: t("title") }]}
      actions={
        allowed && can(REPORT) ? (
          <Menu
            label={tu("moreActions")}
            variant="button"
            trigger={tu("more")}
            align="end"
            entries={[
              {
                id: "csv",
                label: csv.pending ? tc("working") : t("csv"),
                icon: "file",
                disabled: csv.pending,
                onSelect: csv.run,
              },
            ]}
          />
        ) : null
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
  const classOptions =
    classes.status === "ready"
      ? (Array.isArray(classes.data) ? classes.data : classes.data.data).map((k) => ({
          value: k.id,
          label: k.display_en,
        }))
      : [];
  const chips: FilterChip[] = classId
    ? [
        {
          id: "class",
          label: t("class"),
          value: classOptions.find((option) => option.value === classId)?.label ?? "",
        },
      ]
    : [];
  return (
    <div className="space-y-5">
      {header}
      <ApiErrorAlert error={csv.error} namespace="fees" />
      {isFeesOff(dues) ? (
        <FeesGate data={dues}>{() => null}</FeesGate>
      ) : (
        <>
          {dues.status === "ready" ? (
            <dl aria-label={t("totalsLabel")} className="grid gap-3 sm:grid-cols-2">
              <StatCard
                label={t("students")}
                value={formatCount(dues.data.students, locale)}
                unavailableLabel={tc("notAvailable")}
              />
              <StatCard
                label={t("total")}
                value={formatInr(dues.data.total_overdue, locale)}
                unavailableLabel={tc("notAvailable")}
              />
            </dl>
          ) : null}
          <DataTableV2
            id="fees-dues"
            caption={t("listTitle")}
            columns={columns}
            state={mapLoadable(dues, (d) => d.rows)}
            rowKey={(row) => row.account_id}
            toolbar={
              <form
                role="search"
                aria-label={t("filterLabel")}
                className="flex flex-wrap items-end gap-3"
                onSubmit={(e) => e.preventDefault()}
              >
                <SelectField
                  name="class_id"
                  label={t("class")}
                  placeholder={t("allClasses")}
                  value={classId}
                  onChange={(e) => setClassId(e.target.value)}
                  options={classOptions}
                />
              </form>
            }
            chips={chips}
            onRemoveChip={() => setClassId("")}
            onClearFilters={() => setClassId("")}
            emptyTitle={t("emptyTitle")}
            emptyBody={t("emptyBody")}
            noResultsTitle={t("noResultsTitle")}
            onRetry={() => void queryClient.invalidateQueries({ queryKey: KEYS.dues("", classId) })}
            stacked
          />
        </>
      )}
    </div>
  );
}
