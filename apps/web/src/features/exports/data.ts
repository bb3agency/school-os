"use client";

import { pollHeaders } from "@/lib/bff/poll";
import { useQuery, type Query } from "@tanstack/react-query";
import { DQ_KEYS } from "@/features/findings/data";
import { runPollDelay } from "@/features/findings/RunChecks";
import { AuthRedirectError } from "@/lib/bff/fetch";
import {
  ApiError,
  isTransientError,
  NotAvailableError,
  toLoadable,
  unwrap,
  useBffClient,
} from "@/lib/bff/query";
import { StepUpCancelledError } from "@/lib/bff/step-up";
import type { Loadable } from "@/lib/loadable";
import { isExportBusy, type Export, type ExportProfile, type RequestedBy } from "./types";
import { checkedFileUrl } from "@/lib/open-file-url";

/** Query-key roots; creating an export invalidates `all`. */
export const EXPORT_KEYS = {
  all: ["staff", "exports"],
  list: (view: RequestedBy, cursor: string | undefined) =>
    ["staff", "exports", "list", view, cursor ?? null] as const,
  one: (id: string) => ["staff", "exports", "one", id] as const,
  profiles: ["staff", "export-profiles"],
} as const;

/**
 * Pause before asking again while an export is queued or running: the same back-off as the
 * data-quality run (2 s, 3 s, 4.5 s … at most 15 s; slow office links). Tests shorten it.
 */
let pollDelay: (attempt: number) => number = runPollDelay;
export function setExportPollDelayForTesting(delay: ((attempt: number) => number) | null): void {
  pollDelay = delay ?? runPollDelay;
}

/**
 * Next poll for data holding exports: a delay while any of them is being made, `false` once
 * every one is ready, failed or expired (or the last request failed).
 */
export function exportRefetchInterval(
  exports: readonly Pick<Export, "status">[] | undefined,
  attempt: number,
  failed = false,
): number | false {
  if (failed || !exports) return false;
  return exports.some((item) => isExportBusy(item.status)) ? pollDelay(attempt) : false;
}

function interval<T>(pick: (data: T) => readonly Pick<Export, "status">[]) {
  return (query: Query<T, Error, T, readonly unknown[]>) => {
    const data = query.state.data;
    return exportRefetchInterval(
      data === undefined ? undefined : pick(data),
      query.state.dataUpdateCount,
      // A transient failure keeps polling: the screen still shows the old state (XO-07).
      query.state.status === "error" && !isTransientError(query.state.error),
    );
  };
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

export interface ExportPage {
  data: Export[];
  next_cursor: string | null;
}

/**
 * GET /exports?requested_by=me|all, newest first. Polls while an export on the page is being
 * made; a hidden tab does not poll (refetchIntervalInBackground: false).
 */
export function useExportList(
  view: RequestedBy,
  cursor: string | undefined,
  enabled: boolean,
): Loadable<ExportPage> {
  const api = useBffClient("staff");
  const query = useQuery({
    queryKey: EXPORT_KEYS.list(view, cursor),
    queryFn: (context) =>
      unwrap(
        api.GET("/api/v1/exports", {
          headers: pollHeaders(context),
          params: { query: { requested_by: view, limit: 25, ...(cursor ? { cursor } : {}) } },
        }),
      ),
    enabled,
    refetchInterval: interval<ExportPage>((page) => page.data),
    refetchIntervalInBackground: false,
    retry,
  });
  return toLoadable(query);
}

/** GET /exports/{id}; polls while it is queued or running (not in a hidden tab). */
export function useExport(exportId: string): Loadable<Export> {
  const api = useBffClient("staff");
  const query = useQuery({
    queryKey: EXPORT_KEYS.one(exportId),
    queryFn: (context) =>
      unwrap(
        api.GET("/api/v1/exports/{export_id}", {
          headers: pollHeaders(context),
          params: { path: { export_id: exportId } },
        }),
      ),
    refetchInterval: interval<Export>((one) => [one]),
    refetchIntervalInBackground: false,
    retry,
  });
  return toLoadable(query);
}

/** GET /export-profiles (needs export.board or export.portal). */
export function useExportProfiles(enabled: boolean) {
  const api = useBffClient("staff");
  return useQuery({
    queryKey: EXPORT_KEYS.profiles,
    queryFn: () => unwrap(api.GET("/api/v1/export-profiles")),
    enabled,
    staleTime: 10 * 60_000,
    retry: false,
  });
}

/** GET /attributes (labels and classification; needs student.read_basic), shared cache. */
export function useExportAttributes(enabled: boolean) {
  const api = useBffClient("staff");
  return useQuery({
    queryKey: DQ_KEYS.attributes,
    queryFn: () => unwrap(api.GET("/api/v1/attributes")),
    enabled,
    staleTime: 10 * 60_000,
    retry: false,
  });
}

export function profileName(
  profiles: readonly ExportProfile[] | undefined,
  key: string | null,
  locale: string,
): string | null {
  if (!key) return null;
  const found = profiles?.find((item) => item.key === key);
  if (!found) return key;
  return locale === "te" && found.label_te ? found.label_te : found.label_en;
}

/**
 * Open a short-lived download link. The presigned URL is used once, right away: never kept in
 * state, storage or logs. Tests replace the opener (jsdom cannot navigate).
 */
let openDownload: (url: string) => void = (url) => window.location.assign(url);
export function setDownloadOpenerForTesting(opener: ((url: string) => void) | null): void {
  openDownload = opener ?? ((url) => window.location.assign(url));
}
export function startDownload(url: string): void {
  // Only an https link (WS-03); anything else is the caller's usual error.
  openDownload(checkedFileUrl(url));
}
