"use client";

import type { ApiClient } from "@schoolos/api-client";
import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";
import * as z from "@/lib/zod";
import { Alert } from "@/components/ui/Alert";
import { Card } from "@/components/ui/Card";
import { Label } from "@/components/ui/Label";
import { LoadingState } from "@/components/ui/LoadingState";
import { Select } from "@/components/ui/Select";
import { Table, TableScroll, TBody, Td, Th, THead, Tr } from "@/components/ui/Table";
import type { Locale } from "@/i18n/routing";
import { unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import { formatCount, formatDate, formatInr } from "@/lib/format";
import type { Loadable } from "@/lib/loadable";
import { KEYS, type Account } from "./data";

/**
 * FP-11 (FR-MSG-031..034; ADR-0043 amendment of 2026-11-09): "Messages and estimated Meta cost"
 * per month from what Meta reported on its status webhooks, and the banner for a school whose
 * WhatsApp account is not billed in rupees before Meta's cutoff. Counts and rupees only.
 */

const line = z.object({
  pricing_category: z.string(),
  rate_key: z.string().nullable(),
  purpose: z.string(),
  sent: z.number(),
  billable: z.number(),
  free: z.number(),
  pending: z.number(),
  estimated_cost_inr: z.string(),
});

/**
 * GET /api/v1/messaging/spend (MessagingSpendOut). Hand-written until the generated client
 * carries the route; then use `components["schemas"]["MessagingSpendOut"]` and a typed GET.
 */
export const spendSchema = z.object({
  months: z.array(
    z.object({
      month: z.string(),
      sent: z.number(),
      billable: z.number(),
      pending: z.number(),
      estimated_cost_inr: z.string(),
      lines: z.array(line),
    }),
  ),
  gst_percent: z.string(),
  rates: z.array(
    z.object({ key: z.string(), inr: z.string(), verified: z.boolean(), used: z.boolean() }),
  ),
  rates_checked_on: z.string(),
  next_rate_change: z.string(),
  rates_recheck_due: z.boolean(),
  service_free_per_month: z.number(),
  auto_reply_days: z.number(),
});
export type Spend = z.infer<typeof spendSchema>;

type UntypedGet = {
  GET: (
    path: string,
    init: { params: { query: Record<string, number> } },
  ) => Promise<{ data?: unknown; error?: unknown; response: Response }>;
};

export const SPEND_KEY = [...KEYS.all, "spend"] as const;

export function useSpend(enabled = true): Loadable<Spend> {
  const api: ApiClient = useBffClient("staff");
  return useApiQuery(
    SPEND_KEY,
    async () =>
      spendSchema.parse(
        await unwrap(
          (api as unknown as UntypedGet).GET("/api/v1/messaging/spend", {
            params: { query: { months: 6 } },
          }),
        ),
      ),
    { enabled },
  );
}

const PURPOSES = new Set([
  "optin_confirm",
  "school_notice",
  "absence_alert",
  "fee_reminder",
  "fee_receipt",
  "optin_stopped",
  "auto_reply",
]);
const CATEGORIES = new Set(["utility", "service", "marketing", "authentication"]);

/** "2026-11" → "November 2026" in the reader's language (IST calendar month). */
function monthLabel(month: string, locale: Locale): string {
  const [year = "1970", value = "01"] = month.split("-");
  const date = new Date(Date.UTC(Number(year), Number(value) - 1, 1));
  return new Intl.DateTimeFormat(locale === "te" ? "te-IN" : "en-IN", {
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  }).format(date);
}

/** Today's date in India (IST) as YYYY-MM-DD. */
export function istToday(now: Date = new Date()): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" }).format(now);
}

function dayBefore(iso: string): string {
  const date = new Date(`${iso}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() - 1);
  return date.toISOString().slice(0, 10);
}

/**
 * FR-MSG-034: a connected account not billed in rupees. Before the cutoff (config
 * `sending.inr_required_from`) a warning with the steps; from it, sending has stopped.
 */
export function InrBanner({ account, today }: { account: Account; today?: string }) {
  const t = useTranslations("messaging.account");
  if (account.currency_state !== "not_inr") return null;
  const cutoff = account.inr_required_from;
  const before = (today ?? istToday()) < cutoff;
  return (
    <Alert
      tone={before ? "warning" : "danger"}
      title={
        before ? t("inrTitle", { date: formatDate(dayBefore(cutoff)) ?? "" }) : t("inrStoppedTitle")
      }
      className="mb-4"
    >
      <p>
        {before
          ? t("inrBody", { date: formatDate(cutoff) ?? "" })
          : t("inrStoppedBody", { date: formatDate(cutoff) ?? "" })}
      </p>
      <ol className="mt-2 list-decimal space-y-1 pl-5">
        <li>{t("inrSteps.open")}</li>
        <li>{t("inrSteps.currency")}</li>
        <li>{t("inrSteps.settle")}</li>
        <li>{t("inrSteps.back")}</li>
      </ol>
    </Alert>
  );
}

function Breakdown({ month, locale }: { month: Spend["months"][number]; locale: Locale }) {
  const t = useTranslations("messaging.spend");
  const tp = useTranslations("messaging.log.purposes");
  if (month.lines.length === 0) return <p className="text-sm text-ink-muted">{t("noneInMonth")}</p>;
  return (
    <TableScroll label={t("breakdownLabel", { month: monthLabel(month.month, locale) })}>
      <Table>
        <THead>
          <Tr>
            <Th>{t("columns.purpose")}</Th>
            <Th>{t("columns.category")}</Th>
            <Th numeric>{t("columns.sent")}</Th>
            <Th numeric>{t("columns.charged")}</Th>
            <Th numeric>{t("columns.pending")}</Th>
            <Th numeric>{t("columns.cost")}</Th>
          </Tr>
        </THead>
        <TBody>
          {month.lines.map((item) => (
            <Tr key={`${item.pricing_category}-${item.purpose}`}>
              <Td>
                {item.purpose === "auto_reply"
                  ? t("autoReply")
                  : PURPOSES.has(item.purpose)
                    ? tp(item.purpose as "school_notice")
                    : item.purpose}
              </Td>
              <Td>
                {CATEGORIES.has(item.pricing_category)
                  ? t(`categories.${item.pricing_category}` as "categories.utility")
                  : item.pricing_category}
                {item.rate_key === "utility_in_window" ? ` (${t("inWindow")})` : ""}
              </Td>
              <Td numeric>{formatCount(item.sent, locale)}</Td>
              <Td numeric>{formatCount(item.billable, locale)}</Td>
              <Td numeric>{formatCount(item.pending, locale)}</Td>
              <Td numeric>{formatInr(item.estimated_cost_inr, locale)}</Td>
            </Tr>
          ))}
        </TBody>
      </Table>
    </TableScroll>
  );
}

function SpendBody({ spend }: { spend: Spend }) {
  const t = useTranslations("messaging.spend");
  const locale = useLocale() as Locale;
  const [chosen, setChosen] = useState(spend.months[0]?.month ?? "");
  const month = spend.months.find((m) => m.month === chosen) ?? spend.months[0];
  const unverified = spend.rates.some((r) => r.used && !r.verified);
  return (
    <div className="space-y-4">
      {spend.rates_recheck_due ? (
        <Alert tone="warning" title={t("recheckTitle")}>
          {t("recheckBody", {
            date: formatDate(spend.next_rate_change) ?? "",
            checked: formatDate(spend.rates_checked_on) ?? "",
          })}
        </Alert>
      ) : null}
      <TableScroll label={t("monthsLabel")}>
        <Table>
          <THead>
            <Tr>
              <Th>{t("columns.month")}</Th>
              <Th numeric>{t("columns.sent")}</Th>
              <Th numeric>{t("columns.charged")}</Th>
              <Th numeric>{t("columns.pending")}</Th>
              <Th numeric>{t("columns.cost")}</Th>
            </Tr>
          </THead>
          <TBody>
            {spend.months.map((item) => (
              <Tr key={item.month}>
                <Th scope="row" className="text-sm font-normal text-ink">
                  {monthLabel(item.month, locale)}
                </Th>
                <Td numeric>{formatCount(item.sent, locale)}</Td>
                <Td numeric>{formatCount(item.billable, locale)}</Td>
                <Td numeric>{formatCount(item.pending, locale)}</Td>
                <Td numeric>{formatInr(item.estimated_cost_inr, locale)}</Td>
              </Tr>
            ))}
          </TBody>
        </Table>
      </TableScroll>
      {month ? (
        <div className="space-y-3">
          <div className="max-w-xs">
            <Label htmlFor="spend-month">{t("monthPicker")}</Label>
            <Select
              id="spend-month"
              value={month.month}
              onChange={(event) => setChosen(event.target.value)}
              options={spend.months.map((m) => ({
                value: m.month,
                label: monthLabel(m.month, locale),
              }))}
            />
          </div>
          <Breakdown month={month} locale={locale} />
        </div>
      ) : null}
      <ul className="list-disc space-y-1 pl-5 text-sm text-ink-muted">
        <li>{t("noteEstimate", { gst: spend.gst_percent })}</li>
        <li>
          {t(unverified ? "noteRatesUnverified" : "noteRates", {
            date: formatDate(spend.rates_checked_on) ?? "",
          })}
        </li>
        <li>
          {t("noteService", {
            free: formatCount(spend.service_free_per_month, locale) ?? "",
            days: spend.auto_reply_days,
          })}
        </li>
      </ul>
    </div>
  );
}

/** FR-MSG-032: for school-wide `message.read` holders (a section-scoped grant gets 403: hidden). */
export function SpendCard({ enabled }: { enabled: boolean }) {
  const t = useTranslations("messaging.spend");
  const tc = useTranslations("common");
  const spend = useSpend(enabled);
  if (!enabled) return null;
  // A section-scoped grant (403) or messaging off (404 module_unavailable): nothing to show.
  if (spend.status === "error" && (spend.reason === "forbidden" || spend.reason === "not_found"))
    return null;
  if (spend.status === "unavailable") return null;
  return (
    <Card title={t("title")} description={t("description")}>
      {spend.status === "loading" ? (
        <LoadingState label={tc("loading")} />
      ) : spend.status === "error" ? (
        <Alert tone="danger" title={tc("loadErrorTitle")}>
          {tc("loadErrorBody")}
        </Alert>
      ) : (
        <SpendBody spend={spend.data} />
      )}
    </Card>
  );
}
