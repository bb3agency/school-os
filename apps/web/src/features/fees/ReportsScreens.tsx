"use client";

import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";
import * as z from "@/lib/zod";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { TextField } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { SelectField } from "@/components/ui/Select";
import { StatCard } from "@/components/ui/StatCard";
import { Table, TableScroll, TBody, Td, Th, THead, Tr } from "@/components/ui/Table";
import { Link } from "@/i18n/navigation";
import type { Locale } from "@/i18n/routing";
import { unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
import { formatDate, formatInr } from "@/lib/format";
import { FINANCE_READ, KEYS, MANAGE, REPORT, saveText, useFeeStatus } from "./data";
import { Amount, FeesGate, ModeText, NoAccess, PrintLink, ReceiptStatusPill } from "./parts";

/** Download a CSV through the API client: a stale sign-in first gets the step-up prompt. */
function CsvButton({
  load,
  label,
}: {
  load: () => Promise<{ name: string; text: string }>;
  label: string;
}) {
  const tc = useTranslations("common");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(undefined);
  return (
    <div className="space-y-2">
      <Button
        variant="secondary"
        onClick={() => {
          setPending(true);
          setError(undefined);
          load()
            .then(({ name, text }) => saveText(name, text))
            .catch((failure: unknown) => setError(failure))
            .finally(() => setPending(false));
        }}
        disabled={pending}
      >
        {pending ? tc("working") : label}
      </Button>
      <ApiErrorAlert error={error} namespace="fees" />
    </div>
  );
}

/**
 * Daily collection report (FR-FEE-018) and day close (FR-FEE-019): totals by mode and by
 * collector, cancellations and refunds of the day, cash and cheques to hand over; A4 print, the
 * day book CSV (step-up) and "Close day" with the totals in the confirmation.
 */
export function DailyScreen() {
  const t = useTranslations("fees.daily");
  const tn = useTranslations("school.nav");
  const tc = useTranslations("common");
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
  const header = (
    <PageHeader
      title={t("title")}
      description={t("description")}
      breadcrumb={[{ label: tn("home"), href: "/" }, { label: t("title") }]}
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
  return (
    <div className="space-y-6">
      {header}
      <FeesGate data={status}>
        {(info) => (
          <>
            <form
              role="search"
              className="flex flex-wrap items-end gap-4"
              onSubmit={(e) => e.preventDefault()}
            >
              <TextField
                type="date"
                name="date"
                label={t("date")}
                max={info.today}
                value={day}
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
                  <dl className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                    <StatCard
                      label={t("receipts")}
                      value={formatInr(r.receipts_total, locale)}
                      unavailableLabel={tc("notAvailable")}
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
                  <div className="flex flex-wrap gap-3">
                    <PrintLink
                      path={`/api/v1/fees/reports/daily/print?date=${r.date}`}
                      label={t("print")}
                    />
                    {can(REPORT) ? (
                      <CsvButton
                        label={t("csv")}
                        load={async () => ({
                          name: `day-book-${r.date}.csv`,
                          text: await unwrap(
                            api.GET("/api/v1/fees/reports/daily.csv", {
                              params: { query: { date: r.date } },
                              parseAs: "text",
                            }),
                          ),
                        })}
                      />
                    ) : null}
                    {can(MANAGE) && !r.closed ? (
                      <ActionDialog
                        triggerLabel={t("close")}
                        title={t("closeTitle", { date: formatDate(r.date) ?? "" })}
                        description={t("closeBody")}
                        confirmLabel={t("close")}
                        consequence={t("closeConsequence", {
                          count: r.receipts.length,
                          net: formatInr(r.net_total, locale) ?? "",
                        })}
                        schema={z.object({})}
                        invalidate={[KEYS.all]}
                        errorNamespace="fees"
                        submit={() =>
                          unwrap(api.POST("/api/v1/fees/day-closes", { body: { date: r.date } }))
                        }
                      />
                    ) : null}
                  </div>
                  <div className="grid gap-4 lg:grid-cols-2">
                    <Card title={t("byMode")}>
                      {r.by_mode.length === 0 ? (
                        <p>{t("none")}</p>
                      ) : (
                        <ul className="space-y-1">
                          {r.by_mode.map((m) => (
                            <li key={m.mode} className="flex justify-between gap-4">
                              <span>
                                <ModeText mode={m.mode} /> · {t("count", { count: m.count })}
                              </span>
                              <Amount value={m.total} />
                            </li>
                          ))}
                        </ul>
                      )}
                    </Card>
                    <Card title={t("byCollector")}>
                      {r.by_collector.length === 0 ? (
                        <p>{t("none")}</p>
                      ) : (
                        <ul className="space-y-1">
                          {r.by_collector.map((x) => (
                            <li
                              key={x.membership_id ?? "online"}
                              className="flex justify-between gap-4"
                            >
                              <span>
                                {x.name ?? t("online")} · {t("count", { count: x.count })}
                              </span>
                              <Amount value={x.total} />
                            </li>
                          ))}
                        </ul>
                      )}
                    </Card>
                  </div>
                  <Card title={t("receiptsTitle")}>
                    {r.receipts.length === 0 ? (
                      <EmptyState icon="inbox" title={t("emptyTitle")} body={t("emptyBody")} />
                    ) : (
                      <TableScroll label={t("receiptsTitle")}>
                        <Table>
                          <THead>
                            <Tr>
                              <Th>{t("columns.number")}</Th>
                              <Th>{t("columns.student")}</Th>
                              <Th>{t("columns.mode")}</Th>
                              <Th>{t("columns.status")}</Th>
                              <Th className="text-right">{t("columns.amount")}</Th>
                            </Tr>
                          </THead>
                          <TBody>
                            {r.receipts.map((x) => (
                              <Tr key={x.id}>
                                <Td>
                                  <Link
                                    href={`/fees/receipts/${x.id}`}
                                    className="text-primary underline underline-offset-4"
                                  >
                                    {x.receipt_number}
                                  </Link>
                                </Td>
                                <Td>
                                  {x.student_name}{" "}
                                  <span className="text-xs text-ink-muted">{x.class_section}</span>
                                </Td>
                                <Td>
                                  <ModeText mode={x.mode} />
                                </Td>
                                <Td>
                                  <ReceiptStatusPill status={x.status} />
                                </Td>
                                <Td numeric>
                                  <Amount value={x.amount} />
                                </Td>
                              </Tr>
                            ))}
                          </TBody>
                        </Table>
                      </TableScroll>
                    )}
                  </Card>
                  {r.cancellations.length || r.refunds.length ? (
                    <Card title={t("reversalsTitle")}>
                      <ul className="space-y-1">
                        {r.cancellations.map((x) => (
                          <li key={x.cancellation_id}>
                            {t("cancelled", { number: x.receipt_number })} ·{" "}
                            <Amount value={x.amount} />
                          </li>
                        ))}
                        {r.refunds.map((x) => (
                          <li key={x.refund_id}>
                            {t("refunded", { voucher: x.voucher_number, number: x.receipt_number })}{" "}
                            · <Amount value={x.amount} />
                          </li>
                        ))}
                      </ul>
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

/** Dues list (FR-FEE-020): students with overdue amounts, oldest first; CSV with step-up. */
export function DuesScreen() {
  const t = useTranslations("fees.dues");
  const tn = useTranslations("school.nav");
  const tc = useTranslations("common");
  const locale = useLocale() as Locale;
  const can = useStaffCan();
  const me = useStaffMe();
  const allowed = can(FINANCE_READ);
  const api = useBffClient("staff");
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
  const header = (
    <PageHeader
      title={t("title")}
      description={t("description")}
      breadcrumb={[{ label: tn("home"), href: "/" }, { label: t("title") }]}
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
  const classOptions =
    classes.status === "ready"
      ? (Array.isArray(classes.data) ? classes.data : classes.data.data).map((k) => ({
          value: k.id,
          label: k.display_en,
        }))
      : [];
  return (
    <div className="space-y-6">
      {header}
      <form
        role="search"
        className="flex flex-wrap items-end gap-4"
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
        {can(REPORT) ? (
          <CsvButton
            label={t("csv")}
            load={async () => ({
              name: "fee-dues.csv",
              text: await unwrap(
                api.GET("/api/v1/fees/dues.csv", {
                  params: { query: classId ? { class_id: classId } : {} },
                  parseAs: "text",
                }),
              ),
            })}
          />
        ) : null}
      </form>
      <FeesGate data={dues}>
        {(d) => (
          <>
            <dl className="grid gap-4 sm:grid-cols-2">
              <StatCard
                label={t("students")}
                value={String(d.students)}
                unavailableLabel={tc("notAvailable")}
              />
              <StatCard
                label={t("total")}
                value={formatInr(d.total_overdue, locale)}
                unavailableLabel={tc("notAvailable")}
              />
            </dl>
            <Card title={t("listTitle")}>
              {d.rows.length === 0 ? (
                <EmptyState icon="inbox" title={t("emptyTitle")} body={t("emptyBody")} />
              ) : (
                <TableScroll label={t("listTitle")}>
                  <Table>
                    <THead>
                      <Tr>
                        <Th>{t("columns.student")}</Th>
                        <Th>{t("columns.class")}</Th>
                        <Th>{t("columns.oldest")}</Th>
                        <Th>{t("columns.lastPayment")}</Th>
                        <Th className="text-right">{t("columns.overdue")}</Th>
                      </Tr>
                    </THead>
                    <TBody>
                      {d.rows.map((row) => (
                        <Tr key={row.account_id}>
                          <Td>
                            <Link
                              href={`/fees/accounts/${row.account_id}`}
                              className="font-semibold text-primary underline underline-offset-4"
                            >
                              {row.display_name ?? t("noName")}
                            </Link>
                            <span className="block text-xs text-ink-muted">{row.admission_no}</span>
                          </Td>
                          <Td>{row.class_section}</Td>
                          <Td>{formatDate(row.oldest_due_on)}</Td>
                          <Td>{formatDate(row.last_payment_on) ?? "—"}</Td>
                          <Td numeric>
                            <Amount value={row.overdue} />
                          </Td>
                        </Tr>
                      ))}
                    </TBody>
                  </Table>
                </TableScroll>
              )}
            </Card>
          </>
        )}
      </FeesGate>
    </div>
  );
}
