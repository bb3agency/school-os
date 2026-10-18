"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";
import { DataTableV2, type ColumnV2 } from "@/components/console/DataTableV2";
import { Pill } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";
import { Value } from "@/components/ui/Value";
import type { Locale } from "@/i18n/routing";
import { Link } from "@/i18n/navigation";
import { unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
import { formatCount, formatDate, formatDateTime, formatInr } from "@/lib/format";
import type { Loadable } from "@/lib/loadable";
import {
  KEYS,
  LIVE_REFRESH_MS,
  READ,
  hhmm,
  isUnavailable,
  useAutomatic,
  type Broadcast,
  type BroadcastReport,
  type SendRun,
} from "./data";
import { DeliveryProgress } from "./SendNoticePanel";
import { MessagingGate } from "./WhatsAppSettingsScreen";

type SectionRow = BroadcastReport["sections"][number];

function useCount(): (n: number | undefined) => string {
  const locale = useLocale() as Locale;
  return (n) => formatCount(n ?? 0, locale) ?? "0";
}

/**
 * Recent notice broadcasts on /messages with their live delivery counts (FR-MSG-017, docs/17
 * §5.8 "Broadcasts and history"); each opens its report. School-wide `message.read` only (a
 * class teacher's scoped grant sees the delivery log, not school-wide reports).
 */
export function BroadcastsCard() {
  const t = useTranslations("messaging.broadcasts");
  const api = useBffClient("staff");
  const queryClient = useQueryClient();
  const count = useCount();
  const [cursor, setCursor] = useState<string | null>(null);
  const page = useApiQuery([...KEYS.broadcasts, cursor ?? ""], () =>
    unwrap(
      api.GET("/api/v1/messaging/broadcasts", {
        params: { query: { limit: 20, ...(cursor ? { cursor } : {}) } },
      }),
    ),
  );
  if (page.status === "error" && (page.reason === "forbidden" || page.reason === "not_found")) {
    return null;
  }
  const rows: Loadable<readonly Broadcast[]> =
    page.status === "ready" ? { status: "ready", data: page.data.items } : page;
  const next = page.status === "ready" ? (page.data.next_cursor ?? null) : null;
  const columns: ColumnV2<Broadcast>[] = [
    {
      key: "notice",
      header: t("columns.notice"),
      cell: (item) => (
        <Link
          href={`/messages/broadcasts/${item.id}`}
          className="font-semibold text-primary underline-offset-4 hover:underline"
        >
          {item.notice_title ?? t("untitled")}
          {item.resend ? <span className="block text-sm text-ink-muted">{t("resent")}</span> : null}
        </Link>
      ),
    },
    {
      key: "when",
      header: t("columns.when"),
      className: "num whitespace-nowrap",
      cell: (item) => <Value>{formatDateTime(item.confirmed_at ?? item.created_at)}</Value>,
    },
    {
      key: "recipients",
      header: t("columns.recipients"),
      numeric: true,
      cell: (item) => <Value>{count(item.queued)}</Value>,
    },
    {
      key: "delivery",
      header: t("columns.delivery"),
      cell: (item) => <DeliveryProgress broadcast={item} />,
    },
  ];
  return (
    <Card title={t("title")} description={t("description")}>
      <DataTableV2
        id="broadcasts"
        caption={t("title")}
        captionHidden
        columns={columns}
        state={rows}
        rowKey={(item) => item.id}
        stacked
        emptyTitle={t("emptyTitle")}
        emptyBody={t("emptyBody")}
        onRetry={() => void queryClient.invalidateQueries({ queryKey: KEYS.broadcasts })}
        footer={
          next ? (
            <Button variant="secondary" size="sm" onClick={() => setCursor(next)}>
              {t("more")}
            </Button>
          ) : undefined
        }
      />
    </Card>
  );
}

/**
 * Absence alerts, fee reminders and receipt messages (US-2205..US-2207): whether each is on,
 * when it runs, messages waiting, and the last two weeks of runs with their outcomes. The
 * switches, times and no-message dates are changed in WhatsApp settings; reminder days are the
 * fee settings' (fee reminders follow the school's offsets, default 3 days before, on the due
 * date and 7 days after).
 */
export function AutomaticCard() {
  const t = useTranslations("messaging.automatic");
  const count = useCount();
  const auto = useAutomatic();
  if (auto.status === "error" && (auto.reason === "forbidden" || auto.reason === "not_found")) {
    return null;
  }
  const columns: ColumnV2<SendRun>[] = [
    {
      key: "day",
      header: t("columns.day"),
      className: "num whitespace-nowrap",
      cell: (run) => <Value>{formatDate(run.run_on)}</Value>,
    },
    { key: "job", header: t("columns.job"), cell: (run) => t(`jobs.${run.job}`) },
    {
      key: "queued",
      header: t("columns.queued"),
      numeric: true,
      cell: (run) => <Value>{count(run.queued)}</Value>,
    },
    {
      key: "delivered",
      header: t("columns.delivered"),
      numeric: true,
      cell: (run) => <Value>{count((run.counts.delivered ?? 0) + (run.counts.read ?? 0))}</Value>,
    },
    {
      key: "failed",
      header: t("columns.failed"),
      numeric: true,
      cell: (run) => <Value>{count((run.counts.failed ?? 0) + (run.counts.skipped ?? 0))}</Value>,
    },
  ];
  const runs: Loadable<readonly SendRun[]> =
    auto.status === "ready" ? { status: "ready", data: auto.data.runs } : auto;
  return (
    <Card title={t("title")} description={t("description")}>
      <div className="space-y-4">
        {auto.status === "ready" ? (
          <ul className="grid gap-3 sm:grid-cols-3">
            {(
              [
                ["absence", auto.data.absence_alerts_enabled, hhmm(auto.data.absence_alert_time)],
                ["reminders", auto.data.fee_reminders_enabled, hhmm(auto.data.fee_reminder_time)],
                ["receipts", auto.data.fee_receipts_enabled, null],
              ] as const
            ).map(([key, on, time]) => (
              <li key={key} className="space-y-1">
                <span className="block font-semibold text-ink">{t(`kinds.${key}`)}</span>
                <span className="flex flex-wrap items-center gap-2 text-sm text-ink-muted">
                  <Pill variant={on ? "done" : "sample"}>{on ? t("on") : t("off")}</Pill>
                  {time ? t("at", { time }) : t("afterReceipt")}
                </span>
              </li>
            ))}
          </ul>
        ) : null}
        {auto.status === "ready" && auto.data.queued_now > 0 ? (
          <p className="text-sm text-ink">{t("waiting", { count: count(auto.data.queued_now) })}</p>
        ) : null}
        <DataTableV2
          id="automatic-runs"
          caption={t("runs")}
          columns={columns}
          state={runs}
          rowKey={(run) => `${run.job}:${run.run_on}`}
          stacked
          emptyTitle={t("emptyTitle")}
          emptyBody={t("emptyBody")}
        />
        <p className="text-sm text-ink-muted">
          {t("changeNote")}{" "}
          <Link href="/settings/whatsapp" className="text-primary underline underline-offset-4">
            {t("openSettings")}
          </Link>
        </p>
      </div>
    </Card>
  );
}

/**
 * One broadcast's delivery report (FR-MSG-017, US-2204 AC2): live counts by status, by class
 * and section, and failure and skip reasons in words. Counts only; asked again every few
 * seconds while the broadcast is still sending.
 */
export function BroadcastReportScreen({ broadcastId }: { broadcastId: string }) {
  const t = useTranslations("messaging.broadcasts");
  const tm = useTranslations("messaging");
  const tn = useTranslations("school.nav");
  const tr = useTranslations("messaging.log.reasons");
  const can = useStaffCan();
  const me = useStaffMe();
  const api = useBffClient("staff");
  const queryClient = useQueryClient();
  const count = useCount();
  const locale = useLocale() as Locale;
  const report = useApiQuery(
    KEYS.broadcast(broadcastId),
    () =>
      unwrap(
        api.GET("/api/v1/messaging/broadcasts/{broadcast_id}", {
          params: { path: { broadcast_id: broadcastId } },
        }),
      ),
    {
      enabled: me !== undefined && can(READ),
      // Live while sending; stop asking once it is done.
      refetchInterval: (data) => (data?.broadcast.status === "done" ? false : LIVE_REFRESH_MS),
    },
  );
  const sections: Loadable<readonly SectionRow[]> =
    report.status === "ready" ? { status: "ready", data: report.data.sections } : report;
  const columns: ColumnV2<SectionRow>[] = [
    {
      key: "section",
      header: t("columns.section"),
      cell: (row) => row.label ?? t("noSection"),
    },
    ...(["queued", "sent", "delivered", "read", "failed", "skipped"] as const).map(
      (status): ColumnV2<SectionRow> => ({
        key: status,
        header: tm(`log.statuses.${status}`),
        numeric: true,
        cell: (row) => <Value>{count(row.counts[status])}</Value>,
      }),
    ),
  ];
  const title =
    report.status === "ready" ? (report.data.broadcast.notice_title ?? t("untitled")) : t("report");
  return (
    <div className="space-y-5">
      <PageHeader
        title={title}
        description={t("reportDescription")}
        breadcrumb={[
          { label: tn("home"), href: "/" },
          { label: tm("logTitle"), href: "/messages" },
          { label: t("report") },
        ]}
      />
      {me !== undefined && !can(READ) ? (
        <EmptyState icon="lock" title={tm("noAccessTitle")} body={tm("noAccessBody")} />
      ) : isUnavailable(report) && report.status === "error" ? (
        <EmptyState icon="search" title={t("notFoundTitle")} body={t("notFoundBody")} />
      ) : (
        <MessagingGate data={report}>
          {(data) => (
            <>
              <Card>
                <div className="space-y-3">
                  <DeliveryProgress broadcast={data.broadcast} />
                  <p className="text-sm text-ink-muted">
                    {t("reportMeta", {
                      when:
                        formatDateTime(data.broadcast.confirmed_at ?? data.broadcast.created_at) ??
                        "",
                      cost: formatInr(data.broadcast.estimated_cost_inr, locale) ?? "",
                    })}
                  </p>
                  <Link
                    href={`/notices/${data.broadcast.notice_id}`}
                    className="text-sm text-primary underline underline-offset-4"
                  >
                    {t("openNotice")}
                  </Link>
                </div>
              </Card>
              <DataTableV2
                id="broadcast-sections"
                caption={t("bySection")}
                columns={columns}
                state={sections}
                rowKey={(row) => row.section_id ?? "none"}
                stacked
                emptyTitle={t("emptyTitle")}
                onRetry={() =>
                  void queryClient.invalidateQueries({ queryKey: KEYS.broadcast(broadcastId) })
                }
              />
              {Object.keys(data.failure_reasons).length > 0 ? (
                <Card title={t("reasonsTitle")}>
                  <ul className="space-y-1 text-sm text-ink">
                    {Object.entries(data.failure_reasons).map(([code, n]) => (
                      <li key={code} className="flex justify-between gap-4">
                        <span>
                          {tr.has(code as "no_optin") ? tr(code as "no_optin") : tr("other")}
                        </span>
                        <span className="num">{count(n)}</span>
                      </li>
                    ))}
                  </ul>
                </Card>
              ) : null}
            </>
          )}
        </MessagingGate>
      )}
    </div>
  );
}
