"use client";

import type { ApiClient } from "@schoolos/api-client";
import { unwrap } from "@/lib/bff/query";

/**
 * Accountant and management reports (FM-04; FR-FEE-061..074): collections by period, demand,
 * collection and balance (DCB), registers, ageing and the year-end pack. Views need
 * `finance.read`; downloads need `fee.report` with step-up and are fetched through the API
 * client (never a link), so a stale sign-in gets the step-up prompt first.
 *
 * LEAD: these routes are new in FM-04 and not in the generated client yet. The types below are
 * hand-written copies of `app/fees/period_schemas.py` and `reportGet`/`reportFile` call the
 * client untyped. After `make openapi`, swap them for `components["schemas"][...]` and
 * `api.GET("/api/v1/fees/reports/...")`.
 */

export type Money = string;

export interface DayTotal {
  date: string;
  receipts: number;
  gross: Money;
  cancelled: Money;
  refunded: Money;
  net: Money;
}

export interface HeadTotal {
  head_id: string | null;
  head_code: string | null;
  head_name: string;
  collected: Money;
  cancelled: Money;
  refunded: Money;
  net: Money;
}

export interface PeriodModeTotal {
  mode: string;
  receipts: number;
  gross: Money;
  cancelled: Money;
  refunded: Money;
  net: Money;
}

export interface PeriodCollectorTotal {
  membership_id: string | null;
  name: string | null;
  receipts: number;
  gross: Money;
}

export interface PeriodCancellation {
  cancellation_id: string;
  receipt_id: string;
  receipt_number: string;
  receipt_date: string;
  cancelled_on: string;
  amount: Money;
  mode: string;
  reason_code: string;
  requested_by_name: string | null;
  approved_by_name: string | null;
}

export interface PeriodRefund {
  refund_id: string;
  voucher_number: string;
  voucher_date: string;
  receipt_number: string;
  amount: Money;
  mode: string;
  requested_by_name: string | null;
  approved_by_name: string | null;
}

export interface CollectionRegister {
  date_from: string;
  date_to: string;
  by_day: DayTotal[];
  by_head: HeadTotal[];
  by_mode: PeriodModeTotal[];
  by_collector: PeriodCollectorTotal[];
  receipts: unknown[];
  receipts_count: number;
  truncated: boolean;
  cancellations: PeriodCancellation[];
  refunds: PeriodRefund[];
  receipts_total: Money;
  cancelled_total: Money;
  refunded_total: Money;
  net_total: Money;
}

export interface DcbRow {
  class_id: string | null;
  class_label: string;
  section_id: string | null;
  section_label: string | null;
  accounts: number;
  demand: Money;
  concessions: Money;
  late_fees: Money;
  adjustments: Money;
  written_off: Money;
  net_demand: Money;
  collected: Money;
  refunded: Money;
  balance: Money;
}

export interface EarlierYearBalance {
  academic_year_id: string;
  academic_year_label: string;
  accounts: number;
  balance: Money;
  written_off: Money;
}

export interface Dcb {
  academic_year_id: string;
  academic_year_label: string;
  as_of: string;
  rows: DcbRow[];
  totals: DcbRow;
  earlier_years: EarlierYearBalance[];
  earlier_years_balance: Money;
}

export interface ConcessionEntry {
  concession_id: string;
  student_id: string;
  display_name: string | null;
  admission_no: string | null;
  class_section: string | null;
  academic_year_label: string | null;
  concession_type: string;
  amount: Money;
  requested_by_name: string | null;
  approved_by_name: string | null;
  approved_on: string;
}

export interface AdjustmentEntry {
  adjustment_id: string;
  student_id: string;
  display_name: string | null;
  admission_no: string | null;
  class_section: string | null;
  academic_year_label: string | null;
  reason_code: string;
  reason_label: string;
  head_name: string | null;
  amount: Money;
  requested_by_name: string | null;
  approved_by_name: string | null;
  approved_on: string;
}

export interface TypeTotal {
  code: string;
  count: number;
  total: Money;
}

export interface Registers {
  date_from: string;
  date_to: string;
  concessions: ConcessionEntry[];
  concessions_by_type: TypeTotal[];
  concessions_total: Money;
  adjustments: AdjustmentEntry[];
  adjustments_by_reason: TypeTotal[];
  written_off_total: Money;
  cancellations: PeriodCancellation[];
  cancelled_total: Money;
  refunds: PeriodRefund[];
  refunded_total: Money;
  truncated: boolean;
}

export interface AgeingBucket {
  from_days: number;
  to_days: number | null;
}

export interface AgeingRow {
  student_id: string;
  display_name: string | null;
  admission_no: string | null;
  class_section: string | null;
  bands: Money[];
  earlier_years: Money;
  overdue: Money;
  oldest_due_on: string | null;
}

export interface AgeingClassRow {
  class_section: string | null;
  students: number;
  bands: Money[];
  earlier_years: Money;
  overdue: Money;
}

export interface Ageing {
  as_of: string;
  bands: AgeingBucket[];
  rows: AgeingRow[];
  truncated: boolean;
  by_class: AgeingClassRow[];
  totals: Money[];
  earlier_years_total: Money;
  overdue_total: Money;
  not_yet_due_total: Money;
}

export interface SeriesCheck {
  series: "receipt" | "refund";
  financial_year: string;
  first_number: string | null;
  last_number: string | null;
  used: number;
  cancelled: number;
  counter: number;
  missing_count: number;
  missing: number[];
}

export interface Series {
  financial_year: string;
  series: SeriesCheck[];
  gap_free: boolean;
}

export const REPORT_TABS = ["collections", "dcb", "registers", "ageing", "year-end"] as const;
export type ReportTab = (typeof REPORT_TABS)[number];

export const PERIOD_KEYS = {
  collections: (from: string, to: string) =>
    ["staff", "fees", "reports", "collections", from, to] as const,
  dcb: (yearId: string) => ["staff", "fees", "reports", "dcb", yearId] as const,
  registers: (from: string, to: string) =>
    ["staff", "fees", "reports", "registers", from, to] as const,
  ageing: ["staff", "fees", "reports", "ageing"] as const,
  series: (fy: string) => ["staff", "fees", "reports", "series", fy] as const,
} as const;

type Query = Record<string, string | boolean | undefined>;
type LooseResult = { data?: unknown; error?: unknown; response: Response };
type LooseClient = {
  GET: (
    path: string,
    init?: { params?: { query?: Query }; parseAs?: "json" | "blob" },
  ) => Promise<LooseResult>;
};

function loose(api: ApiClient): LooseClient {
  return api as unknown as LooseClient;
}

function clean(query: Query): Query {
  return Object.fromEntries(Object.entries(query).filter(([, v]) => v !== undefined && v !== ""));
}

/** A report view (JSON) by path under `/api/v1/fees/reports/`. */
export async function reportGet<T>(api: ApiClient, path: string, query: Query = {}): Promise<T> {
  const pending = loose(api).GET(`/api/v1/fees/reports/${path}`, {
    params: { query: clean(query) },
  });
  return (await unwrap(pending as Promise<{ data?: T; error?: unknown; response: Response }>)) as T;
}

/** A report file (CSV, XLSX or ZIP) as a blob, with the name the API gave it. */
export async function reportFile(
  api: ApiClient,
  path: string,
  query: Query,
  fallbackName: string,
): Promise<{ name: string; blob: Blob }> {
  const pending = loose(api).GET(`/api/v1/fees/reports/${path}`, {
    params: { query: clean(query) },
    parseAs: "blob",
  });
  let disposition = "";
  const blob = await unwrap(
    pending.then((result) => {
      disposition = result.response.headers.get("content-disposition") ?? "";
      return result as { data?: Blob; error?: unknown; response: Response };
    }),
  );
  const match = /filename="([^"]+)"/.exec(disposition);
  return { name: match?.[1] ?? fallbackName, blob };
}

/** Save a downloaded file; nothing is kept in browser storage. */
export function saveBlob(filename: string, blob: Blob): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

/** `YYYY-MM-01` of an ISO date. */
export function monthStart(day: string): string {
  return `${day.slice(0, 8)}01`;
}
