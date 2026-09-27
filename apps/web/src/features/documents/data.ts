"use client";

import { useQuery, type Query } from "@tanstack/react-query";
import { runPollDelay } from "@/features/findings/RunChecks";
import { AuthRedirectError } from "@/lib/bff/fetch";
import { ApiError, NotAvailableError, toLoadable, unwrap, useBffClient } from "@/lib/bff/query";
import { StepUpCancelledError } from "@/lib/bff/step-up";
import type { Loadable } from "@/lib/loadable";
import type { DocumentListFilters } from "./filters";
import {
  isVersionBusy,
  type DocumentDetail,
  type DocumentRow,
  type DocumentVersion,
} from "./types";

/** Query-key roots; uploads, ACL changes and deletes invalidate `all`. */
export const DOCUMENT_KEYS = {
  all: ["staff", "documents"],
  list: (filters: DocumentListFilters, cursor: string | undefined) =>
    ["staff", "documents", "list", filters, cursor ?? null] as const,
  one: (id: string) => ["staff", "documents", "one", id] as const,
  roles: ["staff", "documents", "roles"],
  members: ["staff", "documents", "members"],
} as const;

/**
 * Pause before asking again while a version is being checked: 2 s, 3 s, 4.5 s … at most 15 s
 * (slow office links). Tests shorten it.
 */
let pollDelay: (attempt: number) => number = runPollDelay;
export function setDocumentPollDelayForTesting(delay: ((attempt: number) => number) | null): void {
  pollDelay = delay ?? runPollDelay;
}

type WithVersion = { current_version: Pick<DocumentVersion, "status"> | null };

/** Next poll: a delay while any current version is still being checked, else `false`. */
export function documentRefetchInterval(
  rows: readonly WithVersion[] | undefined,
  attempt: number,
  failed = false,
): number | false {
  if (failed || !rows) return false;
  return rows.some((row) => row.current_version && isVersionBusy(row.current_version.status))
    ? pollDelay(attempt)
    : false;
}

function interval<T>(pick: (data: T) => readonly WithVersion[]) {
  return (query: Query<T, Error, T, readonly unknown[]>) => {
    const data = query.state.data;
    return documentRefetchInterval(
      data === undefined ? undefined : pick(data),
      query.state.dataUpdateCount,
      query.state.status === "error",
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

export interface DocumentPage {
  data: DocumentRow[];
  next_cursor: string | null;
}

/** GET /documents (filtered by ACL and scopes in the API), newest first. */
export function useDocumentList(
  filters: DocumentListFilters,
  cursor: string | undefined,
  enabled: boolean,
): Loadable<DocumentPage> {
  const api = useBffClient("staff");
  const query = useQuery({
    queryKey: DOCUMENT_KEYS.list(filters, cursor),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/documents", {
          params: {
            query: {
              limit: 25,
              ...(cursor ? { cursor } : {}),
              ...(filters.purpose ? { purpose: filters.purpose } : {}),
              ...(filters.docType ? { doc_type: filters.docType } : {}),
              ...(filters.status ? { status: filters.status } : {}),
            },
          },
        }),
      ),
    enabled,
    refetchInterval: interval<DocumentPage>((page) => page.data),
    refetchIntervalInBackground: false,
    retry,
  });
  return toLoadable(query);
}

/** GET /documents/{id} with its versions; polls while a version is being checked. */
export function useDocument(documentId: string, enabled: boolean): Loadable<DocumentDetail> {
  const api = useBffClient("staff");
  const query = useQuery({
    queryKey: DOCUMENT_KEYS.one(documentId),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/documents/{document_id}", {
          params: { path: { document_id: documentId } },
        }),
      ),
    enabled,
    refetchInterval: interval<DocumentDetail>((one) => [
      ...one.versions.map((version) => ({ current_version: version })),
    ]),
    refetchIntervalInBackground: false,
    retry,
  });
  return toLoadable(query);
}

const PAGE = { limit: 200 } as const;

/** GET /roles (needs user.manage): custom roles and their names. */
export function useSchoolRoles(enabled: boolean) {
  const api = useBffClient("staff");
  return useQuery({
    queryKey: DOCUMENT_KEYS.roles,
    queryFn: async () => (await unwrap(api.GET("/api/v1/roles", { params: { query: PAGE } }))).data,
    enabled,
    staleTime: 10 * 60_000,
    retry: false,
  });
}

/** GET /users (needs user.manage): staff names for "who can see it". */
export function useSchoolMembers(enabled: boolean) {
  const api = useBffClient("staff");
  return useQuery({
    queryKey: DOCUMENT_KEYS.members,
    queryFn: async () => (await unwrap(api.GET("/api/v1/users", { params: { query: PAGE } }))).data,
    enabled,
    staleTime: 5 * 60_000,
    retry: false,
  });
}

/**
 * Open a short-lived download link. The presigned URL (≤ 5 minutes, always an attachment) is
 * used once, right away: never kept in state, storage or logs. Tests replace the opener
 * (jsdom cannot navigate).
 */
let openDownload: (url: string) => void = (url) => window.location.assign(url);
export function setDocumentDownloadOpenerForTesting(opener: ((url: string) => void) | null): void {
  openDownload = opener ?? ((url) => window.location.assign(url));
}
export function startDocumentDownload(url: string): void {
  openDownload(url);
}
