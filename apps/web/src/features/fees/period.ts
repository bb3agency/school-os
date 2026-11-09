"use client";

import type { components } from "@schoolos/api-client";
import { unwrap } from "@/lib/bff/query";

/**
 * Accountant and management reports (FM-04; FR-FEE-061..074): collections by period, demand,
 * collection and balance (DCB), registers, ageing and the year-end pack. Views need
 * `finance.read`; downloads need `fee.report` with step-up and are fetched through the API
 * client (never a link), so a stale sign-in gets the step-up prompt first.
 */

type S = components["schemas"];
export type Money = string;
export type CollectionRegister = S["CollectionRegisterOut"];
export type DcbRow = S["DcbRow"];
export type Dcb = S["DcbOut"];
export type Registers = S["RegistersOut"];
export type Ageing = S["AgeingOut"];
export type Series = S["SeriesOut"];

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

type FileResult = { data?: Blob; error?: unknown; response: Response };

/**
 * A report file (CSV, XLSX or ZIP) from a typed `api.GET(..., { parseAs: "blob" })`, with the
 * name the API gave it.
 */
export async function reportFile(
  pending: Promise<FileResult>,
  fallbackName: string,
): Promise<{ name: string; blob: Blob }> {
  let disposition = "";
  const blob = await unwrap(
    pending.then((result) => {
      disposition = result.response.headers.get("content-disposition") ?? "";
      return result;
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
