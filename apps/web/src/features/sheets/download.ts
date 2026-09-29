"use client";

import { createBffFetch } from "@/lib/bff/fetch";
import { ApiError } from "@/lib/bff/query";

/**
 * Sheet downloads (FR-IMP-009, FR-DOC-011). The API answers with the file itself (CSV with a
 * BOM or XLSX, `Content-Disposition: attachment`), so the page fetches it through the BFF and
 * hands the bytes to the browser as a download. A 428 `step_up_required` goes through the
 * page's step-up handler ("Confirm it's you" in a small window, ADR-0018) and the request is
 * sent once more; a cancelled prompt rejects with `StepUpCancelledError`. Nothing is kept in
 * browser storage.
 */

export type SheetFormat = "csv" | "xlsx";

type Saver = (blob: Blob, filename: string) => void;

function saveWithLink(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1_000);
}

let saver: Saver = saveWithLink;

/** Tests capture the file instead of clicking a link. */
export function setSheetSaverForTesting(value: Saver | undefined): void {
  saver = value ?? saveWithLink;
}

/** `attachment; filename="import-0192f3a4-sheet.csv"` → a safe file name (ASCII only). */
export function downloadName(disposition: string | null, fallback: string): string {
  const match = /filename="?([^";]+)"?/i.exec(disposition ?? "");
  const name = (match?.[1] ?? "").replace(/[^\w.-]/g, "_").replace(/^\.+/, "");
  return name || fallback;
}

export interface SheetDownload {
  /** API path under /api/v1 (e.g. `/api/v1/imports/<id>/sheet/export?format=csv`). */
  path: string;
  method: "GET" | "POST";
  body?: unknown;
  locale: string;
  fallbackName: string;
}

/** Fetch the file through the BFF (with step-up) and save it; returns the file name. */
export async function downloadSheet({
  path,
  method,
  body,
  locale,
  fallbackName,
}: SheetDownload): Promise<string> {
  const send = createBffFetch({ kind: "staff", locale });
  const headers: Record<string, string> = {
    accept: "text/csv, application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  };
  if (body !== undefined) headers["content-type"] = "application/json";
  const response = await send(
    new Request(`${window.location.origin}/bff${path}`, {
      method,
      headers,
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
    }),
  );
  if (!response.ok) {
    const problem = (await response.json().catch(() => ({}))) as { code?: string };
    throw new ApiError(response.status, problem.code, problem);
  }
  const filename = downloadName(response.headers.get("content-disposition"), fallbackName);
  saver(await response.blob(), filename);
  return filename;
}
