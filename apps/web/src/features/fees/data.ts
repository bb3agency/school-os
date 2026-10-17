"use client";

import type { components } from "@schoolos/api-client";
import { unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import type { Loadable } from "@/lib/loadable";

/**
 * Fee ledger and receipts (R2-1B; ADR-0042; docs/17 §5.8). Every route answers 404
 * `module_hidden` while fees are off for the school (flag and school setting, FR-FEE-001): the
 * screens then say so. Fee data is personal financial data (C2): it is shown on these screens
 * only, never put into a URL (searches are POST bodies; links carry ids only) and never stored in
 * the browser. Amounts travel as exact decimal strings ("1250.00"); the screens add and compare
 * them as whole paise (integers), never as floating-point rupees (FR-FEE-028).
 */

export const FINANCE_READ = "finance.read";
export const COLLECT = "fee.collect";
export const MANAGE = "fee.manage";
export const CONCESSION_REQUEST = "fee.concession.request";
export const CONCESSION_APPROVE = "fee.concession.approve";
export const RECEIPT_CANCEL = "fee.receipt.cancel";
export const REFUND_REQUEST = "fee.refund.request";
export const REFUND_APPROVE = "fee.refund.approve";
export const REPORT = "fee.report";
export const FEE_PERMISSIONS = [
  FINANCE_READ,
  COLLECT,
  MANAGE,
  CONCESSION_REQUEST,
  CONCESSION_APPROVE,
  RECEIPT_CANCEL,
  REFUND_REQUEST,
  REFUND_APPROVE,
  REPORT,
] as const;
export const REASON_MIN = 10;
export const REASON_MAX = 1000;

type S = components["schemas"];
export type FeeStatus = S["FeeStatusOut"];
export type FeeSettings = S["FeeSettingsOut"];
export type Head = S["HeadOut"];
export type Plan = S["FeePlanOut"];
export type PlanApply = S["PlanApplyOut"];
export type AccountSummary = S["AccountSummary"];
export type Account = S["AccountOut"];
export type DueLine = S["DueLine"];
export type Statement = S["StatementOut"];
export type Receipt = S["ReceiptOut"];
export type ReceiptListItem = S["ReceiptListItem"];
export type ApprovalItem = S["ApprovalItem"];
export type Concession = S["ConcessionOut"];
export type Adjustment = S["AdjustmentOut"];
export type DailyReport = S["DailyReportOut"];
export type Dues = S["DuesOut"];
export type CounterMode = "cash" | "cheque" | "bank_transfer" | "upi_qr";
export type ReceiptMode = Receipt["mode"];

export const KEYS = {
  all: ["staff", "fees"] as const,
  status: ["staff", "fees", "status"] as const,
  settings: ["staff", "fees", "settings"] as const,
  heads: ["staff", "fees", "heads"] as const,
  plans: ["staff", "fees", "plans"] as const,
  account: (id: string) => ["staff", "fees", "account", id] as const,
  statement: (id: string) => ["staff", "fees", "statement", id] as const,
  concessions: (id: string) => ["staff", "fees", "concessions", id] as const,
  adjustments: (id: string) => ["staff", "fees", "adjustments", id] as const,
  receipts: (filters: object, cursor: string | null) =>
    ["staff", "fees", "receipts", filters, cursor] as const,
  receipt: (id: string) => ["staff", "fees", "receipt", id] as const,
  approvals: ["staff", "fees", "approvals"] as const,
  daily: (date: string) => ["staff", "fees", "daily", date] as const,
  dues: (sectionId: string, classId: string) =>
    ["staff", "fees", "dues", sectionId, classId] as const,
} as const;

/** `W/"3"` for If-Match (the API's ETag format). */
export function ifMatch(version: number): string {
  return `W/"${version}"`;
}

// --- exact money (FR-FEE-028) ----------------------------------------------------------------------

const MONEY = /^-?\d{1,12}(\.\d{1,2})?$/;

/** "1250.5" -> 125050 paise; null when it is not an amount with at most two decimals. */
export function toPaise(value: string): number | null {
  const text = value.trim().replace(/,/g, "");
  if (!MONEY.test(text)) return null;
  const negative = text.startsWith("-");
  const [whole = "0", frac = ""] = text.replace("-", "").split(".");
  const paise = Number(whole) * 100 + Number((frac + "00").slice(0, 2));
  return negative ? -paise : paise;
}

/** 125050 -> "1250.50" (what the API takes). */
export function fromPaise(paise: number): string {
  const sign = paise < 0 ? "-" : "";
  const abs = Math.abs(paise);
  return `${sign}${Math.floor(abs / 100)}.${String(abs % 100).padStart(2, "0")}`;
}

/** Oldest due first over the collectable lines with something due (FR-FEE-010 default). */
export function defaultAllocation(
  lines: readonly DueLine[],
  amountPaise: number,
): { line: DueLine; paise: number }[] {
  let left = amountPaise;
  const out: { line: DueLine; paise: number }[] = [];
  for (const line of lines) {
    const due = toPaise(line.due) ?? 0;
    if (!line.collectable || due <= 0 || left <= 0) continue;
    const take = Math.min(due, left);
    out.push({ line, paise: take });
    left -= take;
  }
  return out;
}

/** UTR: 12 digits (FR-FEE-014); bank references 12-22 letters or digits; cheques 6 digits. */
export const UTR = /^\d{12}$/;
export const BANK_REFERENCE = /^[A-Za-z0-9]{12,22}$/;
export const CHEQUE_NUMBER = /^\d{6}$/;

/** The mnemonic key of each mode at the counter (docs/17 §5.8). */
export const MODE_KEYS: Record<string, CounterMode> = {
  c: "cash",
  q: "cheque",
  b: "bank_transfer",
  u: "upi_qr",
};

/** True when a load failed because fees are off for the school (404 module_hidden). */
export function isFeesOff(loadable: Loadable<unknown>): boolean {
  return loadable.status === "error" && loadable.reason === "not_found";
}

export function useFeeStatus(enabled = true): Loadable<FeeStatus> {
  const api = useBffClient("staff");
  return useApiQuery(KEYS.status, () => unwrap(api.GET("/api/v1/fees/status")), { enabled });
}

export function useHeads(enabled = true): Loadable<Head[]> {
  const api = useBffClient("staff");
  return useApiQuery(KEYS.heads, () => unwrap(api.GET("/api/v1/fees/heads")), { enabled });
}

export function useAccount(id: string | null): Loadable<Account> {
  const api = useBffClient("staff");
  return useApiQuery(
    KEYS.account(id ?? "none"),
    () =>
      unwrap(
        api.GET("/api/v1/fees/accounts/{account_id}", {
          params: { path: { account_id: id ?? "" } },
        }),
      ),
    { enabled: id !== null },
  );
}

/** Download a CSV through the API client (so a step-up prompt can run first), never a link. */
export function saveText(filename: string, text: string, type = "text/csv;charset=utf-8"): void {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

/** Print views are API pages with their own strict CSP, opened through the BFF. */
export function printHref(path: string): string {
  return `/bff${path}`;
}
