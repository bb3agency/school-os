"use client";

import type { components } from "@schoolos/api-client";
import { useApiQuery, unwrap, useBffClient } from "@/lib/bff/query";
import type { Loadable } from "@/lib/loadable";

/**
 * Tally voucher export and ledger-vs-Tally check (FM-03; FR-TALLY-011..030). SchoolOS never
 * writes to Tally: the screen downloads an XML file that the accountant imports by hand.
 */

export const CONFIGURE = "tally.configure";
export const REPORT = "fee.report";
export const FINANCE_READ = "finance.read";

type S = components["schemas"];
export type PartyMode = S["VoucherSettingsOut"]["party_mode"];
export type ReceiptMode = S["ModeMappingOut"]["mode"];
export type VoucherKind = S["KindCount"]["kind"];
export type VoucherSettings = S["VoucherSettingsOut"];
export type VoucherSettingsIn = S["VoucherSettingsIn"];
export type ExportRequest = S["VoucherExportIn"];
export type VoucherPreview = S["VoucherPreviewOut"];
export type VoucherExport = S["VoucherExportOut"];
export type LedgerCheck = S["LedgerCheckOut"];

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
    () => unwrap(api.GET("/api/v1/tally/vouchers/settings")),
    { enabled },
  );
}

export function useVoucherExports(enabled: boolean): Loadable<VoucherExport[]> {
  const api = useBffClient("staff");
  return useApiQuery(
    VOUCHER_KEYS.exports,
    () => unwrap(api.GET("/api/v1/tally/vouchers/exports")),
    { enabled },
  );
}

export function useLedgerCheck(enabled: boolean): Loadable<LedgerCheck> {
  const api = useBffClient("staff");
  return useApiQuery(VOUCHER_KEYS.check, () => unwrap(api.GET("/api/v1/tally/vouchers/check")), {
    enabled,
  });
}

export async function saveSettings(
  api: ReturnType<typeof useBffClient>,
  body: VoucherSettingsIn,
  version: number,
): Promise<VoucherSettings> {
  return unwrap(
    api.PUT("/api/v1/tally/vouchers/settings", {
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
    api.GET("/api/v1/tally/vouchers/preview", {
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
  const text = await unwrap(api.POST("/api/v1/tally/vouchers/export", { body, parseAs: "text" }));
  return { name: `tally-vouchers-${body.period_from}-to-${body.period_to}.xml`, text };
}

/** Field paths from the API ("mode_ledgers.cash", "head_ledgers.<id>") → form field ids. */
export function fieldId(path: string): string {
  return `tally-voucher-${path.replace(/[^a-z0-9]+/gi, "-")}`;
}
