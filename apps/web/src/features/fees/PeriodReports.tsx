"use client";

import { useLocale, useTranslations } from "next-intl";
import { useState, type ReactNode } from "react";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { TextField } from "@/components/ui/Input";
import { Menu, type MenuEntry } from "@/components/ui/Menu";
import { PageHeader } from "@/components/ui/PageHeader";
import { SelectField } from "@/components/ui/Select";
import { StatCard } from "@/components/ui/StatCard";
import { TabNav } from "@/components/ui/TabNav";
import { Table, TableScroll, TBody, Td, Th, THead, Tr } from "@/components/ui/Table";
import type { Locale } from "@/i18n/routing";
import { unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
import { formatCount, formatDate, formatInr } from "@/lib/format";
import { FINANCE_READ, KEYS, REPORT, useFeeStatus } from "./data";
import { Amount, FeesGate, ModeText, NoAccess, PrintLink } from "./parts";
import {
  PERIOD_KEYS,
  monthStart,
  reportFile,
  saveBlob,
  type Ageing,
  type CollectionRegister,
  type DcbRow,
  type ReportTab,
} from "./period";
import { financialYears } from "./tax";

/**
 * Fee reports for the accountant and management (FM-04; FR-FEE-061..074), one tab per report
 * (each tab is its own URL, so only the open report is loaded): collections by period, demand
 * collection balance, registers, ageing and the year-end pack. `finance.read` sees them;
 * `fee.report` downloads them (step-up, audited). Periods are IST dates from the API's "today"
 * (never the PC's clock); free-text reasons are never shown and reach a download only when the
 * user ticks the box.
 */
export function FeeReportsScreen({ tab }: { tab: ReportTab }) {
  const t = useTranslations("fees.periodReports");
  const tn = useTranslations("school.nav");
  const can = useStaffCan();
  const me = useStaffMe();
  const allowed = can(FINANCE_READ);
  const status = useFeeStatus(me !== undefined && allowed);
  const header = (
    <PageHeader
      title={t("title")}
      description={t("description")}
      breadcrumb={[{ label: tn("home"), href: "/" }, { label: t("title") }]}
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
      <TabNav
        label={t("tabsLabel")}
        activeId={tab}
        items={[
          { id: "collections", href: "/fees/reports/collections", label: t("tabs.collections") },
          { id: "dcb", href: "/fees/reports/dcb", label: t("tabs.dcb") },
          { id: "registers", href: "/fees/reports/registers", label: t("tabs.registers") },
          { id: "ageing", href: "/fees/reports/ageing", label: t("tabs.ageing") },
          { id: "year-end", href: "/fees/reports/year-end", label: t("tabs.yearEnd") },
        ]}
      />
      <FeesGate data={status}>
        {(info) => {
          const canDownload = can(REPORT);
          if (tab === "dcb") return <DcbReport canDownload={canDownload} />;
          if (tab === "registers")
            return <RegistersReport today={info.today} canDownload={canDownload} />;
          if (tab === "ageing") return <AgeingReport canDownload={canDownload} />;
          if (tab === "year-end")
            return <YearEndReport financialYear={info.financial_year} canDownload={canDownload} />;
          return <CollectionsReport today={info.today} canDownload={canDownload} />;
        }}
      </FeesGate>
    </div>
  );
}

// --- shared --------------------------------------------------------------------------------------

function useDownload() {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(undefined);
  const run = (load: () => Promise<{ name: string; blob: Blob }>) => {
    setPending(true);
    setError(undefined);
    load()
      .then(({ name, blob }) => saveBlob(name, blob))
      .catch((failure: unknown) => setError(failure))
      .finally(() => setPending(false));
  };
  return { pending, error, run };
}

function PeriodForm({
  today,
  from,
  to,
  onFrom,
  onTo,
}: {
  today: string;
  from: string;
  to: string;
  onFrom: (value: string) => void;
  onTo: (value: string) => void;
}) {
  const t = useTranslations("fees.periodReports");
  return (
    <form
      role="search"
      aria-label={t("periodLabel")}
      className="flex flex-wrap items-end gap-4"
      onSubmit={(e) => e.preventDefault()}
    >
      <TextField
        type="date"
        name="from"
        label={t("from")}
        max={to || today}
        value={from}
        className="num"
        onChange={(e) => onFrom(e.target.value)}
      />
      <TextField
        type="date"
        name="to"
        label={t("to")}
        max={today}
        value={to}
        className="num"
        onChange={(e) => onTo(e.target.value)}
      />
      <p className="pb-2 text-sm text-ink-muted">{t("periodHint")}</p>
    </form>
  );
}

function SimpleTable({
  caption,
  head,
  numeric,
  rows,
}: {
  caption: string;
  head: string[];
  numeric: number[];
  rows: { key: string; cells: ReactNode[] }[];
}) {
  const t = useTranslations("fees.periodReports");
  if (rows.length === 0) return <p className="text-sm text-ink-muted">{t("none")}</p>;
  return (
    <TableScroll label={caption}>
      <Table density="compact">
        <THead>
          <Tr>
            {head.map((h, i) => (
              <Th key={h} numeric={numeric.includes(i)}>
                {h}
              </Th>
            ))}
          </Tr>
        </THead>
        <TBody>
          {rows.map((row) => (
            <Tr key={row.key}>
              {row.cells.map((cell, i) => (
                <Td key={i} numeric={numeric.includes(i)}>
                  {cell}
                </Td>
              ))}
            </Tr>
          ))}
        </TBody>
      </Table>
    </TableScroll>
  );
}

function Downloads({ entries }: { entries: MenuEntry[] }) {
  const t = useTranslations("fees.periodReports");
  if (entries.length === 0) return null;
  return (
    <Menu
      label={t("downloads")}
      variant="button"
      trigger={t("downloads")}
      align="end"
      entries={entries}
    />
  );
}

// --- collections (FR-FEE-061..063) ---------------------------------------------------------------

function CollectionsReport({ today, canDownload }: { today: string; canDownload: boolean }) {
  const t = useTranslations("fees.periodReports");
  const tc = useTranslations("common");
  const locale = useLocale() as Locale;
  const api = useBffClient("staff");
  const [from, setFrom] = useState(monthStart(today));
  const [to, setTo] = useState(today);
  const report = useApiQuery(
    PERIOD_KEYS.collections(from, to),
    () => unwrap(api.GET("/api/v1/fees/reports/collections", { params: { query: { from, to } } })),
    { enabled: from !== "" && to !== "" },
  );
  const download = useDownload();
  const file = (
    part: "receipts" | "heads",
    format: "csv" | "xlsx",
    label: "files.receipts.csv" | "files.receipts.xlsx" | "files.heads.csv",
  ) => ({
    id: `${part}-${format}`,
    label: download.pending ? tc("working") : t(label),
    icon: "file" as const,
    disabled: download.pending,
    onSelect: () =>
      download.run(() =>
        reportFile(
          api.GET("/api/v1/fees/reports/collections/download", {
            params: { query: { from: from || null, to: to || null, part, format } },
            parseAs: "blob",
          }),
          `fees.${format}`,
        ),
      ),
  });
  const entries: MenuEntry[] = canDownload
    ? [
        file("receipts", "csv", "files.receipts.csv"),
        file("receipts", "xlsx", "files.receipts.xlsx"),
        file("heads", "csv", "files.heads.csv"),
      ]
    : [];
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <PeriodForm today={today} from={from} to={to} onFrom={setFrom} onTo={setTo} />
        <div className="flex flex-wrap gap-2">
          <PrintLink
            path={`/api/v1/fees/reports/collections/print?from=${from}&to=${to}`}
            label={t("print")}
          />
          <Downloads entries={entries} />
        </div>
      </div>
      <ApiErrorAlert error={download.error} namespace="fees" />
      <FeesGate data={report}>
        {(r) => (
          <>
            <dl aria-label={t("totalsLabel")} className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <StatCard
                label={t("gross")}
                value={formatInr(r.receipts_total, locale)}
                unavailableLabel={tc("notAvailable")}
                hint={t("receiptsCount", { count: r.receipts_count })}
              />
              <StatCard
                label={t("cancelled")}
                value={formatInr(r.cancelled_total, locale)}
                unavailableLabel={tc("notAvailable")}
              />
              <StatCard
                label={t("refunded")}
                value={formatInr(r.refunded_total, locale)}
                unavailableLabel={tc("notAvailable")}
              />
              <StatCard
                label={t("net")}
                value={formatInr(r.net_total, locale)}
                unavailableLabel={tc("notAvailable")}
              />
            </dl>
            <div className="grid gap-4 lg:grid-cols-2">
              <Card title={t("byHead")} className="min-w-0">
                <SimpleTable
                  caption={t("byHead")}
                  head={[t("columns.head"), t("columns.collectedInr"), t("columns.netInr")]}
                  numeric={[1, 2]}
                  rows={r.by_head.map((h) => ({
                    key: h.head_id ?? "late",
                    cells: [
                      h.head_name,
                      <Amount key="c" value={h.collected} />,
                      <Amount key="n" value={h.net} />,
                    ],
                  }))}
                />
              </Card>
              <Card title={t("byMode")} className="min-w-0">
                <SimpleTable
                  caption={t("byMode")}
                  head={[t("columns.mode"), t("columns.receipts"), t("columns.netInr")]}
                  numeric={[1, 2]}
                  rows={r.by_mode.map((m) => ({
                    key: m.mode,
                    cells: [
                      <ModeText key="m" mode={m.mode} />,
                      formatCount(m.receipts, locale),
                      <Amount key="n" value={m.net} />,
                    ],
                  }))}
                />
              </Card>
              <Card title={t("byCollector")} className="min-w-0">
                <SimpleTable
                  caption={t("byCollector")}
                  head={[t("columns.collector"), t("columns.receipts"), t("columns.grossInr")]}
                  numeric={[1, 2]}
                  rows={r.by_collector.map((x) => ({
                    key: x.membership_id ?? "online",
                    cells: [
                      x.name ?? t("online"),
                      formatCount(x.receipts, locale),
                      <Amount key="g" value={x.gross} />,
                    ],
                  }))}
                />
              </Card>
              <Card title={t("byDay")} className="min-w-0">
                <SimpleTable
                  caption={t("byDay")}
                  head={[t("columns.date"), t("columns.receipts"), t("columns.netInr")]}
                  numeric={[1, 2]}
                  rows={r.by_day.map((d) => ({
                    key: d.date,
                    cells: [
                      formatDate(d.date) ?? d.date,
                      formatCount(d.receipts, locale),
                      <Amount key="n" value={d.net} />,
                    ],
                  }))}
                />
              </Card>
            </div>
            <Card title={t("cancelledReceipts")} description={t("cancelledReceiptsHint")}>
              <CancellationTable rows={r.cancellations} />
            </Card>
          </>
        )}
      </FeesGate>
    </div>
  );
}

function CancellationTable({ rows }: { rows: CollectionRegister["cancellations"] }) {
  const t = useTranslations("fees.periodReports");
  const tr = useTranslations("fees.cancelReasons");
  return (
    <SimpleTable
      caption={t("cancelledReceipts")}
      head={[
        t("columns.receipt"),
        t("columns.receiptDate"),
        t("columns.cancelledOn"),
        t("columns.reason"),
        t("columns.amountInr"),
      ]}
      numeric={[4]}
      rows={rows.map((x) => ({
        key: x.cancellation_id,
        cells: [
          <span key="n" className="num font-mono whitespace-nowrap">
            {x.receipt_number}
          </span>,
          formatDate(x.receipt_date) ?? "",
          formatDate(x.cancelled_on) ?? "",
          tr(x.reason_code as "other"),
          <Amount key="a" value={x.amount} />,
        ],
      }))}
    />
  );
}

// --- DCB (FR-FEE-064/065) ------------------------------------------------------------------------

function DcbReport({ canDownload }: { canDownload: boolean }) {
  const t = useTranslations("fees.periodReports");
  const tc = useTranslations("common");
  const locale = useLocale() as Locale;
  const api = useBffClient("staff");
  const years = useApiQuery(KEYS.years, () =>
    unwrap(api.GET("/api/v1/academic-years", { params: { query: { limit: 200 } } })),
  );
  const [yearId, setYearId] = useState("");
  const report = useApiQuery(PERIOD_KEYS.dcb(yearId), () =>
    unwrap(
      api.GET("/api/v1/fees/reports/dcb", {
        params: { query: { academic_year_id: yearId || null } },
      }),
    ),
  );
  const download = useDownload();
  const entries: MenuEntry[] = canDownload
    ? (["csv", "xlsx"] as const).map((format) => ({
        id: format,
        label: download.pending ? tc("working") : t(`files.dcb.${format}`),
        icon: "file" as const,
        disabled: download.pending,
        onSelect: () =>
          download.run(() =>
            reportFile(
              api.GET("/api/v1/fees/reports/dcb/download", {
                params: { query: { academic_year_id: yearId || null, format } },
                parseAs: "blob",
              }),
              `fee-dcb.${format}`,
            ),
          ),
      }))
    : [];
  const line = (r: DcbRow, key: string, label: ReactNode) => ({
    key,
    cells: [
      label,
      formatCount(r.accounts, locale),
      <Amount key="d" value={r.demand} />,
      <Amount key="c" value={r.concessions} />,
      <Amount key="w" value={r.written_off} />,
      <Amount key="n" value={r.net_demand} />,
      <Amount key="p" value={r.collected} />,
      <Amount key="b" value={r.balance} />,
    ],
  });
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <SelectField
          label={t("year")}
          value={yearId}
          onChange={(e) => setYearId(e.target.value)}
          options={[
            { value: "", label: t("currentYear") },
            ...(years.status === "ready"
              ? years.data.data.map((y) => ({ value: y.id, label: y.label }))
              : []),
          ]}
        />
        <Downloads entries={entries} />
      </div>
      <ApiErrorAlert error={download.error} namespace="fees" />
      <FeesGate data={report}>
        {(r) => (
          <>
            <dl aria-label={t("totalsLabel")} className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <StatCard
                label={t("netDemand")}
                value={formatInr(r.totals.net_demand, locale)}
                unavailableLabel={tc("notAvailable")}
              />
              <StatCard
                label={t("collected")}
                value={formatInr(r.totals.collected, locale)}
                unavailableLabel={tc("notAvailable")}
              />
              <StatCard
                label={t("balance")}
                value={formatInr(r.totals.balance, locale)}
                unavailableLabel={tc("notAvailable")}
              />
              <StatCard
                label={t("earlierYears")}
                value={formatInr(r.earlier_years_balance, locale)}
                unavailableLabel={tc("notAvailable")}
              />
            </dl>
            <Card title={t("dcbTitle", { year: r.academic_year_label })} className="min-w-0">
              <SimpleTable
                caption={t("dcbTitle", { year: r.academic_year_label })}
                head={[
                  t("columns.classSection"),
                  t("columns.accounts"),
                  t("columns.demandInr"),
                  t("columns.concessionsInr"),
                  t("columns.writtenOffInr"),
                  t("columns.netDemandInr"),
                  t("columns.collectedInr"),
                  t("columns.balanceInr"),
                ]}
                numeric={[1, 2, 3, 4, 5, 6, 7]}
                rows={[
                  ...r.rows.map((row) =>
                    line(
                      row,
                      `${row.class_id ?? "none"}-${row.section_id ?? "none"}`,
                      row.section_id
                        ? `${row.class_label} ${row.section_label ?? ""}`
                        : `${row.class_label}: ${t("notEnrolled")}`,
                    ),
                  ),
                  line(r.totals, "totals", <strong>{t("allClasses")}</strong>),
                ]}
              />
            </Card>
            <Card title={t("earlierYears")} description={t("earlierYearsHint")}>
              <SimpleTable
                caption={t("earlierYears")}
                head={[
                  t("columns.year"),
                  t("columns.accounts"),
                  t("columns.writtenOffInr"),
                  t("columns.balanceInr"),
                ]}
                numeric={[1, 2, 3]}
                rows={r.earlier_years.map((e) => ({
                  key: e.academic_year_id,
                  cells: [
                    e.academic_year_label,
                    formatCount(e.accounts, locale),
                    <Amount key="w" value={e.written_off} />,
                    <Amount key="b" value={e.balance} />,
                  ],
                }))}
              />
            </Card>
          </>
        )}
      </FeesGate>
    </div>
  );
}

// --- registers (FR-FEE-066/067) ------------------------------------------------------------------

function RegistersReport({ today, canDownload }: { today: string; canDownload: boolean }) {
  const t = useTranslations("fees.periodReports");
  const tc = useTranslations("common");
  const tt = useTranslations("fees.concessionTypes");
  const locale = useLocale() as Locale;
  const api = useBffClient("staff");
  const [from, setFrom] = useState(monthStart(today));
  const [to, setTo] = useState(today);
  const [reasons, setReasons] = useState(false);
  const report = useApiQuery(
    PERIOD_KEYS.registers(from, to),
    () => unwrap(api.GET("/api/v1/fees/reports/registers", { params: { query: { from, to } } })),
    { enabled: from !== "" && to !== "" },
  );
  const download = useDownload();
  const kinds = ["concessions", "adjustments", "cancellations", "refunds"] as const;
  const entries: MenuEntry[] = canDownload
    ? kinds.map((kind) => ({
        id: kind,
        label: download.pending ? tc("working") : t(`files.${kind}.csv`),
        icon: "file" as const,
        disabled: download.pending,
        onSelect: () =>
          download.run(() =>
            reportFile(
              api.GET("/api/v1/fees/reports/registers/download", {
                params: {
                  query: {
                    from: from || null,
                    to: to || null,
                    kind,
                    format: "csv",
                    ...(reasons ? { include_reasons: true } : {}),
                  },
                },
                parseAs: "blob",
              }),
              `fee-${kind}.csv`,
            ),
          ),
      }))
    : [];
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <PeriodForm today={today} from={from} to={to} onFrom={setFrom} onTo={setTo} />
        {canDownload ? (
          <div className="flex flex-wrap items-center gap-4">
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                name="include_reasons"
                checked={reasons}
                onChange={(e) => setReasons(e.target.checked)}
              />
              {t("includeReasons")}
            </label>
            <Downloads entries={entries} />
          </div>
        ) : null}
      </div>
      <ApiErrorAlert error={download.error} namespace="fees" />
      <FeesGate data={report}>
        {(r) => (
          <>
            {r.truncated ? (
              <Alert tone="info" title={t("truncatedTitle")}>
                {t("truncatedBody")}
              </Alert>
            ) : null}
            <dl aria-label={t("totalsLabel")} className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <StatCard
                label={t("concessions")}
                value={formatInr(r.concessions_total, locale)}
                unavailableLabel={tc("notAvailable")}
              />
              <StatCard
                label={t("writtenOff")}
                value={formatInr(r.written_off_total, locale)}
                unavailableLabel={tc("notAvailable")}
              />
              <StatCard
                label={t("cancelled")}
                value={formatInr(r.cancelled_total, locale)}
                unavailableLabel={tc("notAvailable")}
              />
              <StatCard
                label={t("refunded")}
                value={formatInr(r.refunded_total, locale)}
                unavailableLabel={tc("notAvailable")}
              />
            </dl>
            <Card title={t("concessions")} className="min-w-0">
              <SimpleTable
                caption={t("concessions")}
                head={[
                  t("columns.date"),
                  t("columns.student"),
                  t("columns.type"),
                  t("columns.approvedBy"),
                  t("columns.amountInr"),
                ]}
                numeric={[4]}
                rows={r.concessions.map((x) => ({
                  key: x.concession_id,
                  cells: [
                    formatDate(x.approved_on) ?? "",
                    x.display_name ?? t("noName"),
                    tt(x.concession_type as "other"),
                    x.approved_by_name ?? "—",
                    <Amount key="a" value={x.amount} />,
                  ],
                }))}
              />
            </Card>
            <Card title={t("adjustments")} className="min-w-0">
              <SimpleTable
                caption={t("adjustments")}
                head={[
                  t("columns.date"),
                  t("columns.student"),
                  t("columns.year"),
                  t("columns.reason"),
                  t("columns.approvedBy"),
                  t("columns.amountInr"),
                ]}
                numeric={[5]}
                rows={r.adjustments.map((x) => ({
                  key: x.adjustment_id,
                  cells: [
                    formatDate(x.approved_on) ?? "",
                    x.display_name ?? t("noName"),
                    x.academic_year_label ?? "—",
                    x.reason_label,
                    x.approved_by_name ?? "—",
                    <Amount key="a" value={x.amount} />,
                  ],
                }))}
              />
            </Card>
            <Card title={t("cancelledReceipts")} className="min-w-0">
              <CancellationTable rows={r.cancellations} />
            </Card>
            <Card title={t("refunds")} className="min-w-0">
              <SimpleTable
                caption={t("refunds")}
                head={[
                  t("columns.voucher"),
                  t("columns.date"),
                  t("columns.receipt"),
                  t("columns.mode"),
                  t("columns.amountInr"),
                ]}
                numeric={[4]}
                rows={r.refunds.map((y) => ({
                  key: y.refund_id,
                  cells: [
                    <span key="v" className="num font-mono whitespace-nowrap">
                      {y.voucher_number}
                    </span>,
                    formatDate(y.voucher_date) ?? "",
                    y.receipt_number,
                    <ModeText key="m" mode={y.mode} />,
                    <Amount key="a" value={y.amount} />,
                  ],
                }))}
              />
            </Card>
          </>
        )}
      </FeesGate>
    </div>
  );
}

// --- ageing (FR-FEE-068) -------------------------------------------------------------------------

function AgeingReport({ canDownload }: { canDownload: boolean }) {
  const t = useTranslations("fees.periodReports");
  const tc = useTranslations("common");
  const locale = useLocale() as Locale;
  const api = useBffClient("staff");
  const report = useApiQuery(PERIOD_KEYS.ageing, () =>
    unwrap(api.GET("/api/v1/fees/reports/ageing")),
  );
  const download = useDownload();
  const entries: MenuEntry[] = canDownload
    ? (["csv", "xlsx"] as const).map((format) => ({
        id: format,
        label: download.pending ? tc("working") : t(`files.ageing.${format}`),
        icon: "file" as const,
        disabled: download.pending,
        onSelect: () =>
          download.run(() =>
            reportFile(
              api.GET("/api/v1/fees/reports/ageing/download", {
                params: { query: { format } },
                parseAs: "blob",
              }),
              `fee-ageing.${format}`,
            ),
          ),
      }))
    : [];
  const bucketLabel = (b: Ageing["bands"][number]) =>
    b.to_days === null
      ? t("bucketOver", { days: b.from_days - 1 })
      : t("bucketRange", { from: b.from_days, to: b.to_days });
  return (
    <div className="space-y-5">
      <div className="flex justify-end">
        <Downloads entries={entries} />
      </div>
      <ApiErrorAlert error={download.error} namespace="fees" />
      <FeesGate data={report}>
        {(r) => (
          <>
            <dl aria-label={t("totalsLabel")} className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
              {r.bands.map((b, i) => (
                <StatCard
                  key={b.from_days}
                  label={bucketLabel(b)}
                  value={formatInr(r.totals[i] ?? "0.00", locale)}
                  unavailableLabel={tc("notAvailable")}
                />
              ))}
              <StatCard
                label={t("earlierYears")}
                value={formatInr(r.earlier_years_total, locale)}
                unavailableLabel={tc("notAvailable")}
              />
            </dl>
            <p className="text-sm text-ink-muted">
              {t("ageingNote", {
                date: formatDate(r.as_of) ?? r.as_of,
                notDue: formatInr(r.not_yet_due_total, locale) ?? "",
              })}
            </p>
            <Card title={t("byClass")} className="min-w-0">
              <SimpleTable
                caption={t("byClass")}
                head={[
                  t("columns.classSection"),
                  t("columns.students"),
                  ...r.bands.map(bucketLabel),
                  t("earlierYears"),
                  t("columns.overdueInr"),
                ]}
                numeric={Array.from({ length: r.bands.length + 3 }, (_, i) => i + 1)}
                rows={r.by_class.map((row) => ({
                  key: row.class_section ?? "none",
                  cells: [
                    row.class_section ?? t("noClass"),
                    formatCount(row.students, locale),
                    ...row.bands.map((v, i) => <Amount key={i} value={v} />),
                    <Amount key="e" value={row.earlier_years} />,
                    <Amount key="o" value={row.overdue} />,
                  ],
                }))}
              />
            </Card>
            {r.truncated ? (
              <Alert tone="info" title={t("truncatedTitle")}>
                {t("truncatedBody")}
              </Alert>
            ) : null}
            <Card title={t("byStudent")} className="min-w-0">
              <SimpleTable
                caption={t("byStudent")}
                head={[
                  t("columns.student"),
                  t("columns.classSection"),
                  t("earlierYears"),
                  t("columns.overdueInr"),
                  t("columns.oldest"),
                ]}
                numeric={[2, 3]}
                rows={r.rows.map((row) => ({
                  key: row.student_id,
                  cells: [
                    row.display_name ?? t("noName"),
                    row.class_section ?? t("noClass"),
                    <Amount key="e" value={row.earlier_years} />,
                    <Amount key="o" value={row.overdue} />,
                    formatDate(row.oldest_due_on) ?? "—",
                  ],
                }))}
              />
            </Card>
          </>
        )}
      </FeesGate>
    </div>
  );
}

// --- year-end pack and receipt series (FR-FEE-070/071) -------------------------------------------

function YearEndReport({
  financialYear,
  canDownload,
}: {
  financialYear: string;
  canDownload: boolean;
}) {
  const t = useTranslations("fees.periodReports");
  const tc = useTranslations("common");
  const locale = useLocale() as Locale;
  const api = useBffClient("staff");
  const [fy, setFy] = useState(financialYear);
  const series = useApiQuery(PERIOD_KEYS.series(fy), () =>
    unwrap(
      api.GET("/api/v1/fees/reports/receipt-series", {
        params: { query: { financial_year: fy } },
      }),
    ),
  );
  const download = useDownload();
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <SelectField
          label={t("financialYear")}
          value={fy}
          onChange={(e) => setFy(e.target.value)}
          options={financialYears(financialYear).map((y) => ({ value: y, label: y }))}
        />
        {canDownload ? (
          <Button
            variant="primary"
            disabled={download.pending}
            onClick={() =>
              download.run(() =>
                reportFile(
                  api.GET("/api/v1/fees/reports/year-end-pack", {
                    params: { query: { financial_year: fy } },
                    parseAs: "blob",
                  }),
                  `fee-year-end-${fy}.zip`,
                ),
              )
            }
          >
            {download.pending ? tc("working") : t("pack")}
          </Button>
        ) : null}
      </div>
      <p className="text-sm text-ink-muted">{t("packHint")}</p>
      <ApiErrorAlert error={download.error} namespace="fees" />
      <FeesGate data={series}>
        {(s) => (
          <>
            {s.gap_free ? (
              <Alert tone="success" title={t("gapFreeTitle")}>
                {t("gapFreeBody")}
              </Alert>
            ) : (
              <Alert tone="warning" title={t("gapsTitle")}>
                {t("gapsBody")}
              </Alert>
            )}
            <Card title={t("seriesTitle", { year: s.financial_year })} className="min-w-0">
              <SimpleTable
                caption={t("seriesTitle", { year: s.financial_year })}
                head={[
                  t("columns.series"),
                  t("columns.first"),
                  t("columns.last"),
                  t("columns.used"),
                  t("columns.cancelledCount"),
                  t("columns.missing"),
                ]}
                numeric={[3, 4, 5]}
                rows={s.series.map((x) => ({
                  key: x.series,
                  cells: [
                    t(`series.${x.series}`),
                    <span key="f" className="num font-mono">
                      {x.first_number ?? "—"}
                    </span>,
                    <span key="l" className="num font-mono">
                      {x.last_number ?? "—"}
                    </span>,
                    formatCount(x.used, locale),
                    formatCount(x.cancelled, locale),
                    x.missing_count === 0
                      ? formatCount(0, locale)
                      : `${formatCount(x.missing_count, locale)}: ${x.missing.join(", ")}`,
                  ],
                }))}
              />
            </Card>
          </>
        )}
      </FeesGate>
    </div>
  );
}
