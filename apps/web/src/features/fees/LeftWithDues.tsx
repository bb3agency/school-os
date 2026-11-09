"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { useMemo } from "react";
import { DataTableV2, type ColumnV2 } from "@/components/console";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Pill } from "@/components/ui/Badge";
import { Menu, type MenuEntry } from "@/components/ui/Menu";
import { PageHeader } from "@/components/ui/PageHeader";
import { StatCard } from "@/components/ui/StatCard";
import type { Locale } from "@/i18n/routing";
import { unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
import { formatCount, formatDate, formatInr } from "@/lib/format";
import { FINANCE_READ, KEYS, REPORT, isFeesOff, type LeftWithDuesRow } from "./data";
import { Amount, FeesGate, NoAccess } from "./parts";
import { mapLoadable, useCsvDownload } from "./ReportsScreens";

/**
 * "Left with dues" (FM-01; FR-FEE-038, FR-FEE-040, FR-FEE-042): students whose record status
 * is left (withdrawn, TC issued) or graduated with an open balance, for management follow-up
 * (`finance.read` school-wide). Information only: it is never printed on a TC and never blocks
 * or delays one, which the page says up front. The CSV needs `fee.report` and step-up, like the
 * other fee report downloads (fetched through the API client, never a link).
 */
export function LeftWithDuesScreen() {
  const t = useTranslations("fees.leftWithDues");
  const tn = useTranslations("school.nav");
  const tc = useTranslations("common");
  const tu = useTranslations("ui");
  const locale = useLocale() as Locale;
  const can = useStaffCan();
  const me = useStaffMe();
  const allowed = can(FINANCE_READ);
  const api = useBffClient("staff");
  const queryClient = useQueryClient();
  const report = useApiQuery(
    KEYS.leftWithDues,
    () => unwrap(api.GET("/api/v1/fees/reports/left-with-dues")),
    { enabled: me !== undefined && allowed },
  );
  const csv = useCsvDownload(async () => ({
    name: "fees-left-with-dues.csv",
    text: await unwrap(api.GET("/api/v1/fees/reports/left-with-dues.csv", { parseAs: "text" })),
  }));
  const columns = useMemo<ColumnV2<LeftWithDuesRow>[]>(
    () => [
      {
        key: "student",
        name: t("columns.student"),
        header: t("columns.student"),
        hideable: false,
        cell: (row) => (
          <>
            <span className="font-semibold">{row.display_name ?? t("noName")}</span>
            <span className="block font-mono text-xs whitespace-nowrap text-ink-muted">
              {row.admission_no}
            </span>
          </>
        ),
      },
      {
        key: "status",
        name: t("columns.status"),
        header: t("columns.status"),
        cell: (row) => (
          <Pill variant="tag" size="sm" icon={null}>
            {t(`status.${row.status}`)}
          </Pill>
        ),
      },
      {
        key: "accounts",
        name: t("columns.accounts"),
        header: t("columns.accounts"),
        numeric: true,
        cell: (row) => <span className="num">{formatCount(row.accounts, locale)}</span>,
      },
      {
        key: "oldest",
        name: t("columns.oldest"),
        header: t("columns.oldest"),
        cell: (row) => (
          <span className="num whitespace-nowrap">{formatDate(row.oldest_due_on) ?? "—"}</span>
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
        key: "writtenOff",
        name: t("columns.writtenOff"),
        header: t("columns.writtenOffInr"),
        numeric: true,
        cell: (row) => <Amount value={row.written_off} />,
      },
      {
        key: "balance",
        name: t("columns.balance"),
        header: t("columns.balanceInr"),
        numeric: true,
        cell: (row) => (
          <Amount value={row.balance} className="num font-semibold whitespace-nowrap" />
        ),
      },
    ],
    [t, locale],
  );
  const moreEntries: MenuEntry[] =
    allowed && can(REPORT)
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
        moreEntries.length > 0 ? (
          <Menu
            label={tu("moreActions")}
            variant="button"
            trigger={tu("more")}
            align="end"
            entries={moreEntries}
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
  return (
    <div className="space-y-5">
      {header}
      <Alert tone="info" title={t("infoTitle")}>
        {t("infoBody")}
      </Alert>
      <ApiErrorAlert error={csv.error} namespace="fees" />
      {isFeesOff(report) ? (
        <FeesGate data={report}>{() => null}</FeesGate>
      ) : (
        <>
          {report.status === "ready" ? (
            <dl aria-label={t("totalsLabel")} className="grid gap-3 sm:grid-cols-3">
              <StatCard
                label={t("students")}
                value={formatCount(report.data.students, locale)}
                unavailableLabel={tc("notAvailable")}
              />
              <StatCard
                label={t("total")}
                value={formatInr(report.data.total_balance, locale)}
                unavailableLabel={tc("notAvailable")}
              />
              <StatCard
                label={t("writtenOffTotal")}
                value={formatInr(report.data.written_off_total, locale)}
                unavailableLabel={tc("notAvailable")}
              />
            </dl>
          ) : null}
          <DataTableV2
            id="fees-left-with-dues"
            caption={t("listTitle")}
            columns={columns}
            state={mapLoadable(report, (r) => r.rows)}
            rowKey={(row) => row.student_id}
            emptyTitle={t("emptyTitle")}
            emptyBody={t("emptyBody")}
            onRetry={() => void queryClient.invalidateQueries({ queryKey: KEYS.leftWithDues })}
            stacked
          />
        </>
      )}
    </div>
  );
}
