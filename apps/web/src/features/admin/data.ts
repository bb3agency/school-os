"use client";

import type { components } from "@schoolos/api-client";
import { useQuery, type Query } from "@tanstack/react-query";
import { z } from "zod";
import { AuthRedirectError } from "@/lib/bff/fetch";
import { ApiError, NotAvailableError, toLoadable, unwrap, useBffClient } from "@/lib/bff/query";
import { StepUpCancelledError } from "@/lib/bff/step-up";
import type { Loadable } from "@/lib/loadable";

/**
 * School admin console data (US-1201): the school's full data export (FR-ADM-001:
 * `tenant.export_all`, the owner; requesting and downloading need a fresh MFA sign-in) and
 * retention settings (FR-ADM-002: `tenant.settings.manage`; changes need step-up and If-Match).
 * The API checks every permission; these values only decide what the screens offer.
 */

export const EXPORT_ALL = "tenant.export_all";
export const READ_SENSITIVE = "student.read_sensitive";
export const SETTINGS_MANAGE = "tenant.settings.manage";

export type TenantExport = components["schemas"]["TenantExportOut"];
export type TenantExportStatus = TenantExport["status"];
export type RetentionSettings = components["schemas"]["RetentionOut"];
export type RetentionCategory = components["schemas"]["RetentionCategoryOut"];

export const ADMIN_KEYS = {
  exports: ["staff", "admin", "tenant-exports"] as const,
  exportList: (cursor: string | undefined) =>
    ["staff", "admin", "tenant-exports", "list", cursor ?? null] as const,
  retention: ["staff", "admin", "retention"] as const,
} as const;

/** Categories in the order the screen lists them (unknown ones follow, as the API sends them). */
export const CATEGORY_ORDER = [
  "import_raw_files",
  "exports",
  "notifications_read",
  "tenant_exports",
  "kb_queries",
  "audit_events",
] as const;

export function orderedCategories(categories: readonly RetentionCategory[]): RetentionCategory[] {
  const rank = (key: string) => {
    const index = (CATEGORY_ORDER as readonly string[]).indexOf(key);
    return index === -1 ? CATEGORY_ORDER.length : index;
  };
  return [...categories].sort((a, b) => rank(a.key) - rank(b.key));
}

export function isExportBusy(status: TenantExportStatus): boolean {
  return status === "queued" || status === "running";
}

/** `W/"3"` for If-Match (the API's ETag format; 0 until the settings were first changed). */
export function ifMatch(version: number): string {
  return `W/"${version}"`;
}

/**
 * Pause before asking again while an export is being made: 2 s, 3 s, 4.5 s … at most 15 s
 * (slow office links). Tests shorten it.
 */
let pollDelay: (attempt: number) => number = (attempt) =>
  Math.min(2000 * 1.5 ** Math.max(attempt - 1, 0), 15_000);
export function setAdminPollDelayForTesting(delay: ((attempt: number) => number) | null): void {
  pollDelay = delay ?? ((attempt) => Math.min(2000 * 1.5 ** Math.max(attempt - 1, 0), 15_000));
}

export function exportRefetchInterval(
  rows: readonly Pick<TenantExport, "status">[] | undefined,
  attempt: number,
  failed = false,
): number | false {
  if (failed || !rows) return false;
  return rows.some((row) => isExportBusy(row.status)) ? pollDelay(attempt) : false;
}

function retry(count: number, error: unknown): boolean {
  return (
    count < 1 &&
    !(error instanceof NotAvailableError) &&
    !(error instanceof AuthRedirectError) &&
    !(error instanceof StepUpCancelledError) &&
    !(error instanceof ApiError && error.status < 500)
  );
}

export interface TenantExportPage {
  data: TenantExport[];
  next_cursor: string | null;
}

/** GET /admin/tenant-export, newest first; polls while one is queued or running. */
export function useTenantExports(
  cursor: string | undefined,
  enabled: boolean,
): Loadable<TenantExportPage> {
  const api = useBffClient("staff");
  const query = useQuery({
    queryKey: ADMIN_KEYS.exportList(cursor),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/admin/tenant-export", {
          params: { query: { limit: 20, ...(cursor ? { cursor } : {}) } },
        }),
      ),
    enabled,
    refetchInterval: (q: Query<TenantExportPage, Error, TenantExportPage, readonly unknown[]>) =>
      exportRefetchInterval(
        q.state.data?.data,
        q.state.dataUpdateCount,
        q.state.status === "error",
      ),
    refetchIntervalInBackground: false,
    retry,
  });
  return toLoadable(query);
}

/** GET /admin/retention (with the settings version for If-Match). */
export function useRetention(enabled: boolean): Loadable<RetentionSettings> {
  const api = useBffClient("staff");
  const query = useQuery({
    queryKey: ADMIN_KEYS.retention,
    queryFn: () => unwrap(api.GET("/api/v1/admin/retention")),
    enabled,
    retry,
  });
  return toLoadable(query);
}

/**
 * One field per configurable category: a whole number of days within the category's bounds.
 * Built from the loaded settings so the form checks exactly what the API will; the API still
 * validates every value.
 */
export function retentionSchema(categories: readonly RetentionCategory[]) {
  const shape: Record<string, z.ZodType<number>> = {};
  for (const category of categories) {
    if (!category.configurable) continue;
    shape[category.key] = z
      .string()
      .trim()
      .min(1, { error: "required" })
      .refine(
        (value) =>
          /^\d+$/.test(value) &&
          Number(value) >= category.min_days &&
          Number(value) <= category.max_days,
        { error: "invalidNumber" },
      )
      .transform(Number) as unknown as z.ZodType<number>;
  }
  return z.object(shape);
}

/** The PUT body: every configurable category's days (a category left out means its default). */
export function retentionRules(
  categories: readonly RetentionCategory[],
  values: Record<string, number>,
): Record<string, number> {
  const rules: Record<string, number> = {};
  for (const category of categories) {
    if (!category.configurable) continue;
    const days = values[category.key];
    if (typeof days === "number" && days !== category.default_days) rules[category.key] = days;
  }
  return rules;
}

/** True when the form's values equal what is saved (nothing to send). */
export function sameRetention(
  categories: readonly RetentionCategory[],
  values: Record<string, number>,
): boolean {
  return categories.every((c) => !c.configurable || values[c.key] === c.days);
}

/**
 * Open a short-lived download link at once. The presigned URL is never kept in state,
 * storage or logs. Tests replace the opener (jsdom cannot navigate).
 */
let openDownload: (url: string) => void = (url) => window.location.assign(url);
export function setAdminDownloadOpenerForTesting(opener: ((url: string) => void) | null): void {
  openDownload = opener ?? ((url) => window.location.assign(url));
}
export function startDownload(url: string): void {
  openDownload(url);
}
