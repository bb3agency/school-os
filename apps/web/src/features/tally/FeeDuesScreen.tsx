"use client";

import { useLocale, useTranslations } from "next-intl";
import { useId, useMemo, useState } from "react";
import { DataTableV2, type ColumnV2 } from "@/components/console";
import { Alert } from "@/components/ui/Alert";
import { Pill } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { PageHeader } from "@/components/ui/PageHeader";
import { KpiCard } from "@/components/ui/StatCard";
import { Value } from "@/components/ui/Value";
import { Link } from "@/i18n/navigation";
import type { Locale } from "@/i18n/routing";
import { useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
import { formatCount, formatDate, formatInr } from "@/lib/format";
import { CONFIGURE, FINANCE_READ, useDues, type DuesPage } from "./data";
import { Amount, TallyGate } from "./parts";

/**
 * Fee dues synced from Tally (M6, FR-TALLY-007): students with dues from the ledgers a person
 * linked to them, highest first, and the school totals, with the Tally as-of date so the
 * accountant can compare the figures with Tally. `finance.read` (school-wide) only.
 */
type DuesRow = DuesPage["data"][number];

/**
 * Bill-wise dues (D63): the pending bills of the student's linked ledgers in a native
 * disclosure (keyboard and screen-reader friendly, Baseline), with due dates and an "Overdue"
 * pill that repeats the text, never colour alone. Without bill-wise details in Tally the cell
 * says the amount is the ledger balance.
 */
function BillsCell({ row }: { row: DuesRow }) {
  const t = useTranslations("tally.dues");
  const locale = useLocale() as Locale;
  const bills = row.bills ?? [];
  if (bills.length === 0) {
    return <span className="text-sm text-ink-muted">{t("billsNone")}</span>;
  }
  const overdue = row.overdue_bills ?? bills.filter((b) => b.overdue).length;
  return (
    <details className="group min-w-56">
      <summary className="inline-flex min-h-6 cursor-pointer flex-wrap items-center gap-2 text-sm font-semibold text-primary underline-offset-4 hover:underline">
        {t("billsSummary", { count: bills.length })}
        {overdue > 0 ? (
          <Pill variant="negative" icon={null}>
            {t("overdueCount", {
              count: overdue,
              amount: formatInr(row.overdue_due ?? "0", locale) ?? "",
            })}
          </Pill>
        ) : null}
      </summary>
      <ul className="mt-2 space-y-2" aria-label={t("billsLabel")}>
        {bills.map((bill, index) => (
          <li
            key={`${bill.ledger_name}-${bill.reference}-${index}`}
            className="rounded-md border border-border-soft p-2 text-sm"
          >
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="font-semibold text-ink [overflow-wrap:anywhere]">
                {bill.reference}
              </span>
              <Amount value={bill.pending} />
            </div>
            <div className="mt-1 flex flex-wrap items-center gap-2 text-ink-muted">
              <span className="num">
                {bill.due_date
                  ? t("billDue", { date: formatDate(bill.due_date) ?? "" })
                  : t("billNoDueDate")}
              </span>
              {bill.overdue ? (
                <Pill variant="negative" icon={null}>
                  {t("overdue")}
                </Pill>
              ) : null}
              {bill.shared ? (
                <Pill variant="tag" icon={null}>
                  {t("billShared")}
                </Pill>
              ) : null}
            </div>
            {bills.some((b) => b.ledger_name !== bill.ledger_name) ? (
              <p className="mt-1 text-xs text-ink-muted [overflow-wrap:anywhere]">
                {t("billLedger", { ledger: bill.ledger_name })}
              </p>
            ) : null}
          </li>
        ))}
      </ul>
    </details>
  );
}

export function FeeDuesScreen() {
  const t = useTranslations("tally.dues");
  const tn = useTranslations("school.nav");
  const tc = useTranslations("common");
  const tt = useTranslations("tally");
  const locale = useLocale() as Locale;
  const can = useStaffCan();
  const meLoaded = useStaffMe() !== undefined;
  const allowed = can(FINANCE_READ);
  const [cursors, setCursors] = useState<(string | null)[]>([null]);
  const cursor = cursors[cursors.length - 1] ?? null;
  const dues = useDues(cursor, meLoaded && allowed);
  const listId = useId();
  const columns = useMemo<ColumnV2<DuesRow>[]>(
    () => [
      {
        key: "student",
        name: t("columns.student"),
        header: t("columns.student"),
        hideable: false,
        cell: (row) => (
          <Link
            href={`/students/${row.student_id}`}
            className="font-semibold text-primary underline underline-offset-4"
          >
            {row.display_name ?? t("noName")}
          </Link>
        ),
      },
      {
        key: "admissionNo",
        name: t("columns.admissionNo"),
        header: t("columns.admissionNo"),
        cell: (row) => (
          <span className="whitespace-nowrap">
            <Value>{row.admission_no}</Value>
          </span>
        ),
      },
      {
        key: "class",
        name: t("columns.class"),
        header: t("columns.class"),
        cell: (row) => <Value>{row.class_section}</Value>,
      },
      {
        key: "due",
        name: t("columns.due"),
        header: t("columns.dueInr"),
        numeric: true,
        cell: (row) =>
          row.shared_ledger ? (
            <span className="inline-flex flex-wrap items-center gap-2">
              <Amount value={row.total_due} />
              <Pill variant="tag" icon={null}>
                {t("sharedLedger", { count: row.shared_with ?? 1 })}
              </Pill>
            </span>
          ) : (
            <Amount value={row.total_due} />
          ),
      },
      {
        key: "bills",
        name: t("columns.bills"),
        header: t("columns.bills"),
        cell: (row) => <BillsCell row={row} />,
      },
      {
        key: "ledgers",
        name: t("columns.ledgers"),
        header: t("columns.ledgers"),
        numeric: true,
        cell: (row) => formatCount(row.ledgers, locale),
      },
      {
        key: "asOf",
        name: t("columns.asOf"),
        header: t("columns.asOf"),
        cell: (row) => <span className="num whitespace-nowrap">{formatDate(row.as_of)}</span>,
      },
    ],
    [t, locale],
  );

  return (
    <div className="space-y-5">
      <PageHeader
        title={t("title")}
        description={t("description")}
        breadcrumb={[{ label: tn("home"), href: "/" }, { label: t("title") }]}
      />
      {meLoaded && !allowed ? (
        <Alert tone="info" title={t("noAccessTitle")}>
          {t("noAccessBody")}
        </Alert>
      ) : (
        <TallyGate data={meLoaded ? dues : { status: "loading" }}>
          {(page) => (
            <>
              <section
                aria-label={t("kpiLabel")}
                className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4"
              >
                <KpiCard
                  label={t("studentsWithDues")}
                  value={formatCount(page.totals.students_with_dues, locale)}
                  unavailableLabel={tc("notAvailable")}
                />
                <KpiCard
                  label={t("totalDue")}
                  value={formatInr(page.totals.total_due, locale)}
                  unavailableLabel={tc("notAvailable")}
                />
                <KpiCard
                  label={t("unlinked")}
                  value={formatCount(page.totals.unlinked_parties, locale)}
                  unavailableLabel={tc("notAvailable")}
                  comparison={
                    <span className="num">
                      {t("unlinkedHint", {
                        amount: formatInr(page.totals.unlinked_due, locale) ?? "",
                      })}
                    </span>
                  }
                  {...(can(CONFIGURE) ? { href: "/settings/tally/ledgers" } : {})}
                />
                <KpiCard
                  label={t("asOf")}
                  value={formatDate(page.totals.as_of)}
                  unavailableLabel={tc("notAvailable")}
                  comparison={t("asOfHint")}
                  {...(can(CONFIGURE) ? { href: "/settings/tally" } : {})}
                />
              </section>
              {page.totals.unlinked_parties > 0 && can(CONFIGURE) ? (
                <Alert tone="warning" title={t("unlinkedTitle")}>
                  <p>{t("unlinkedBody")}</p>
                  <Link
                    href="/settings/tally/ledgers"
                    className="mt-2 inline-flex font-semibold text-primary underline underline-offset-4"
                  >
                    {t("linkLedgers")}
                  </Link>
                </Alert>
              ) : null}
              <section id={listId} aria-labelledby={`${listId}-title`} className="space-y-3">
                <div className="space-y-1">
                  <h2 id={`${listId}-title`} className="text-lg font-semibold text-ink">
                    {t("listTitle")}
                  </h2>
                  <p className="text-sm text-ink-muted">{t("listDescription")}</p>
                  {page.data.some((row) => row.shared_ledger) ? (
                    <p className="text-sm text-ink-muted">{t("sharedTotalsNote")}</p>
                  ) : null}
                </div>
                <DataTableV2
                  id="tally-dues"
                  caption={t("listTitle")}
                  captionHidden
                  columns={columns}
                  state={{ status: "ready", data: page.data }}
                  rowKey={(row) => row.student_id}
                  emptyTitle={t("emptyTitle")}
                  emptyBody={t("emptyBody")}
                  stacked
                  footer={
                    cursors.length > 1 || page.next_cursor ? (
                      <div className="flex flex-wrap justify-end gap-3">
                        {cursors.length > 1 ? (
                          <Button
                            variant="secondary"
                            size="sm"
                            onClick={() => setCursors((all) => all.slice(0, -1))}
                          >
                            {tt("previous")}
                          </Button>
                        ) : null}
                        {page.next_cursor ? (
                          <Button
                            variant="secondary"
                            size="sm"
                            onClick={() =>
                              setCursors((all) => [...all, page.next_cursor as string | null])
                            }
                          >
                            {tt("next")}
                          </Button>
                        ) : null}
                      </div>
                    ) : undefined
                  }
                />
              </section>
            </>
          )}
        </TallyGate>
      )}
    </div>
  );
}
