"use client";

import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";
import { StatCard } from "@/components/ui/StatCard";
import { Table, TableScroll, TBody, Td, Th, THead, Tr } from "@/components/ui/Table";
import { Value } from "@/components/ui/Value";
import { Link } from "@/i18n/navigation";
import type { Locale } from "@/i18n/routing";
import { useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
import { formatCount, formatDate, formatInr } from "@/lib/format";
import { CONFIGURE, FINANCE_READ, useDues } from "./data";
import { Amount, TallyGate } from "./parts";

/**
 * Fee dues synced from Tally (M6, FR-TALLY-007): students with dues from the ledgers a person
 * linked to them, highest first, and the school totals, with the Tally as-of date so the
 * accountant can compare the figures with Tally. `finance.read` (school-wide) only.
 */
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

  return (
    <div className="space-y-6">
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
              <dl className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                <StatCard
                  label={t("studentsWithDues")}
                  value={formatCount(page.totals.students_with_dues, locale)}
                  unavailableLabel={tc("notAvailable")}
                />
                <StatCard
                  label={t("totalDue")}
                  value={formatInr(page.totals.total_due, locale)}
                  unavailableLabel={tc("notAvailable")}
                />
                <StatCard
                  label={t("unlinked")}
                  value={formatCount(page.totals.unlinked_parties, locale)}
                  unavailableLabel={tc("notAvailable")}
                  hint={t("unlinkedHint", {
                    amount: formatInr(page.totals.unlinked_due, locale) ?? "",
                  })}
                />
                <StatCard
                  label={t("asOf")}
                  value={formatDate(page.totals.as_of)}
                  unavailableLabel={tc("notAvailable")}
                  hint={t("asOfHint")}
                />
              </dl>
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
              <Card title={t("listTitle")} description={t("listDescription")}>
                {page.data.length === 0 ? (
                  <EmptyState icon="inbox" title={t("emptyTitle")} body={t("emptyBody")} />
                ) : (
                  <>
                    <TableScroll label={t("listTitle")}>
                      <Table>
                        <THead>
                          <Tr>
                            <Th>{t("columns.student")}</Th>
                            <Th>{t("columns.admissionNo")}</Th>
                            <Th>{t("columns.class")}</Th>
                            <Th className="text-right">{t("columns.due")}</Th>
                            <Th>{t("columns.ledgers")}</Th>
                            <Th>{t("columns.asOf")}</Th>
                          </Tr>
                        </THead>
                        <TBody>
                          {page.data.map((row) => (
                            <Tr key={row.student_id}>
                              <Td>
                                <Link
                                  href={`/students/${row.student_id}`}
                                  className="font-semibold text-primary underline underline-offset-4"
                                >
                                  {row.display_name ?? t("noName")}
                                </Link>
                              </Td>
                              <Td>
                                <Value>{row.admission_no}</Value>
                              </Td>
                              <Td>
                                <Value>{row.class_section}</Value>
                              </Td>
                              <Td className="text-right">
                                <Amount value={row.total_due} />
                              </Td>
                              <Td>{formatCount(row.ledgers, locale)}</Td>
                              <Td>{formatDate(row.as_of)}</Td>
                            </Tr>
                          ))}
                        </TBody>
                      </Table>
                    </TableScroll>
                    <div className="mt-4 flex flex-wrap gap-3">
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
                  </>
                )}
              </Card>
            </>
          )}
        </TallyGate>
      )}
    </div>
  );
}
