"use client";

import type { components } from "@schoolos/api-client";
import { unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import type { Loadable } from "@/lib/loadable";

/**
 * UPI and bank statements (R2-2E; ADR-0042 decision 9; US-2109; docs/17 §5.8). SchoolOS never
 * receives money: the QR is the school's own static UPI QR (its UPI ID and payee name, no
 * amount), a UPI receipt is issued at the counter with the UTR, and a person later confirms it
 * from the bank statement. Statement lines are personal financial data (C2): they stay on these
 * screens, never in a URL (ids only) and never in browser storage.
 */

export const RECONCILE = "fee.reconcile";
export const UPI_READERS = ["fee.collect", "fee.manage", "finance.read", RECONCILE] as const;
export const QR_PATH = "/bff/api/v1/fees/upi-settings/qr.svg";
export const VPA = /^[A-Za-z0-9][A-Za-z0-9._-]{1,63}@[A-Za-z][A-Za-z0-9]{1,63}$/;
export const STATEMENT_MAX_BYTES = 2 * 1024 * 1024;

type S = components["schemas"];
export type UpiSettings = S["UpiSettingsOut"];
export type UtrCheck = S["UtrCheckOut"];
export type Preset = S["StatementPresetOut"];
export type StatementImport = S["StatementImportOut"];
export type StatementDetail = S["StatementDetailOut"];
export type StatementLine = S["StatementLineOut"];
export type UpiExceptions = S["UpiExceptionsOut"];
export type UpiReport = S["UpiReportOut"];
export type IgnoreReason = S["IgnoreIn"]["reason"];
export const IGNORE_REASONS: readonly IgnoreReason[] = [
  "not_a_fee",
  "recorded_elsewhere",
  "duplicate_credit",
  "other",
];

export const UPI_KEYS = {
  settings: ["staff", "fees", "upi", "settings"] as const,
  presets: ["staff", "fees", "upi", "presets"] as const,
  statements: ["staff", "fees", "upi", "statements"] as const,
  statement: (id: string) => ["staff", "fees", "upi", "statement", id] as const,
  exceptions: ["staff", "fees", "upi", "exceptions"] as const,
  report: ["staff", "fees", "upi", "report"] as const,
} as const;

export function useUpiSettings(enabled = true): Loadable<UpiSettings> {
  const api = useBffClient("staff");
  return useApiQuery(UPI_KEYS.settings, () => unwrap(api.GET("/api/v1/fees/upi-settings")), {
    enabled,
  });
}

/**
 * Lines a person may tick in "Confirm N matches": proposals only. An exact UTR match is ticked
 * from the start; an amount-and-date match is shown but left for the person to tick.
 */
export function initialSelection(lines: readonly StatementLine[]): Set<string> {
  return new Set(
    lines
      .filter((l) => l.match_status === "proposed" && l.match_kind === "utr_exact")
      .map((l) => l.id),
  );
}

export function confirmable(lines: readonly StatementLine[]): StatementLine[] {
  return lines.filter((l) => l.match_status === "proposed");
}
