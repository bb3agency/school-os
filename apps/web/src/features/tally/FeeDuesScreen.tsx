"use client";

import { useLocale, useTranslations } from "next-intl";
import { useId, useMemo, useState } from "react";
import { DataTableV2, type ColumnV2 } from "@/components/console";
import { Alert } from "@/components/ui/Alert";
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
        cell: (row) => <Amount value={row.total_due} />,
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
