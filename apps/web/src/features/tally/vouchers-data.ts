"use client";

import { useApiQuery, unwrap, useBffClient } from "@/lib/bff/query";
import type { Loadable } from "@/lib/loadable";

/**
 * Tally voucher export and ledger-vs-Tally check (FM-03; FR-TALLY-011..030). SchoolOS never
 * writes to Tally: the screen downloads an XML file that the accountant imports by hand.
 *
 * LOCAL WRAPPER: these routes are new in wave F2 and not yet in the generated client
 * (`@schoolos/api-client`). The types below mirror `app/tally/vouchers_schemas.py`; after the
 * lead regenerates the client, swap `loose(api)` for the typed `api` and the interfaces for
 * `components["schemas"][...]`.
 */

export const CONFIGURE = "tally.configure";
export const REPORT = "fee.report";
export const FINANCE_READ = "finance.read";

export type PartyMode = "student" | "receivable";
export type ReceiptMode = "cash" | "cheque" | "bank_transfer" | "upi_qr" | "razorpay";
export type VoucherKind = "receipt" | "cancellation" | "refund" | "demand";

export interface VoucherSettings {
  configured: boolean;
  party_mode: PartyMode;
  receivable_ledger: string;
  modes: { mode: ReceiptMode; ledger: string | null }[];
  heads: { head_id: string; code: string; name: string; active: boolean; ledger: string | null }[];
  other_income_ledger: string | null;
  include_demand: boolean;
  company: string | null;
  links_available: boolean;
  format_verified: boolean;
  version: number;
  updated_at: string | null;
}

export interface VoucherSettingsIn {
  party_mode: PartyMode;
  receivable_ledger: string;
  mode_ledgers: Partial<Record<ReceiptMode, string>>;
  head_ledgers: Record<string, string>;
  other_income_ledger: string | null;
  include_demand: boolean;
  company: string | null;
}

export interface ExportRequest {
  period_from: string;
  period_to: string;
  include_exported: boolean;
}

export interface VoucherPreview {
  period_from: string;
  period_to: string;
  vouchers: number;
  new_vouchers: number;
  already_exported: number;
  by_kind: { kind: VoucherKind; count: number; new: number }[];
  cash_in: string;
  cash_out: string;
  open_days: string[];
  missing: { field: string; label: string }[];
  students_on_receivable: number;
  ready: boolean;
}

export interface VoucherExport {
  id: string;
  period_from: string;
  period_to: string;
  include_exported: boolean;
  include_demand: boolean;
  party_mode: PartyMode;
  vouchers: number;
  new_vouchers: number;
  cash_total: string;
  created_by_name: string | null;
  created_at: string;
}

export interface LedgerCheck {
  party_mode: PartyMode;
  as_of: string | null;
  compared: number;
  matching: number;
  differing: number;
  tally_total: string;
  schoolos_total: string;
  not_exported: number;
  rows: {
    ledger_name: string;
    state: "matches" | "differs" | "missing_in_tally" | "not_in_schoolos";
    tally_balance: string | null;
    schoolos_balance: string | null;
    difference: string;
    students: number;
  }[];
}

type Result<T> = Promise<{ data?: T; error?: unknown; response: Response }>;
interface LooseInit {
  body?: unknown;
  params?: { query?: Record<string, string | boolean> };
  headers?: Record<string, string>;
  parseAs?: "json" | "text" | "blob";
}
interface LooseClient {
  GET<T>(path: string, init?: LooseInit): Result<T>;
  PUT<T>(path: string, init?: LooseInit): Result<T>;
  POST<T>(path: string, init?: LooseInit): Result<T>;
}

/** The BFF client without path typing (new routes; see the note above). */
export function loose(api: ReturnType<typeof useBffClient>): LooseClient {
  return api as unknown as LooseClient;
}

const BASE = "/api/v1/tally/vouchers";

export const VOUCHER_KEYS = {
  all: ["staff", "tally", "vouchers"] as const,
  settings: ["staff", "tally", "vouchers", "settings"] as const,
  exports: ["staff", "tally", "vouchers", "exports"] as const,
  check: ["staff", "tally", "vouchers", "check"] as const,
};

export function useVoucherSettings(enabled: boolean): Loadable<VoucherSettings> {
  const api = useBffClient("staff");
  return useApiQuery(
    VOUCHER_KEYS.settings,
    () => unwrap(loose(api).GET<VoucherSettings>(`${BASE}/settings`)),
    { enabled },
  );
}

export function useVoucherExports(enabled: boolean): Loadable<VoucherExport[]> {
  const api = useBffClient("staff");
  return useApiQuery(
    VOUCHER_KEYS.exports,
    () => unwrap(loose(api).GET<VoucherExport[]>(`${BASE}/exports`)),
    { enabled },
  );
}

export function useLedgerCheck(enabled: boolean): Loadable<LedgerCheck> {
  const api = useBffClient("staff");
  return useApiQuery(
    VOUCHER_KEYS.check,
    () => unwrap(loose(api).GET<LedgerCheck>(`${BASE}/check`)),
    { enabled },
  );
}

export async function saveSettings(
  api: ReturnType<typeof useBffClient>,
  body: VoucherSettingsIn,
  version: number,
): Promise<VoucherSettings> {
  return unwrap(
    loose(api).PUT<VoucherSettings>(`${BASE}/settings`, {
      body,
      ...(version > 0 ? { headers: { "If-Match": `W/"${version}"` } } : {}),
    }),
  );
}

export async function previewExport(
  api: ReturnType<typeof useBffClient>,
  body: ExportRequest,
): Promise<VoucherPreview> {
  return unwrap(
    loose(api).GET<VoucherPreview>(`${BASE}/preview`, {
      params: {
        query: {
          period_from: body.period_from,
          period_to: body.period_to,
          include_exported: body.include_exported,
        },
      },
    }),
  );
}

export async function downloadExport(
  api: ReturnType<typeof useBffClient>,
  body: ExportRequest,
): Promise<{ name: string; text: string }> {
  const text = await unwrap(loose(api).POST<string>(`${BASE}/export`, { body, parseAs: "text" }));
  return { name: `tally-vouchers-${body.period_from}-to-${body.period_to}.xml`, text };
}

/** Field paths from the API ("mode_ledgers.cash", "head_ledgers.<id>") → form field ids. */
export function fieldId(path: string): string {
  return `tally-voucher-${path.replace(/[^a-z0-9]+/gi, "-")}`;
}
