"use client";

import type { ApiClient, Problem } from "@schoolos/api-client";
import {
  useMutation,
  useQuery,
  useQueryClient,
  type QueryFunctionContext,
  type QueryKey,
  type UseMutationResult,
} from "@tanstack/react-query";
import { useLocale } from "next-intl";
import { useMemo } from "react";
import {
  loadError,
  loading,
  ready,
  unavailable,
  type Loadable,
  type LoadErrorReason,
} from "@/lib/loadable";
import { AuthRedirectError, createBffClient } from "./fetch";
import type { Navigate, SessionKind } from "./session-client";
import { StepUpCancelledError } from "./step-up";

/** The API does not offer this endpoint on this deployment (405/501). */
export class NotAvailableError extends Error {
  constructor() {
    super("not available yet");
    this.name = "NotAvailableError";
  }
}

/** A problem+json answer from the API (or the BFF). `problem` never holds tokens. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string | undefined,
    readonly problem: Partial<Problem> = {},
  ) {
    super(`API error ${status}`);
    this.name = "ApiError";
  }
}

const NOT_AVAILABLE = new Set([405, 501]);

type ClientResult<T> = { data?: T; error?: unknown; response: Response };

function asProblem(error: unknown): Partial<Problem> {
  return error && typeof error === "object" ? (error as Partial<Problem>) : {};
}

/** openapi-fetch result → data, or a typed error the screens can show. */
export async function unwrap<T>(pending: Promise<ClientResult<T>>): Promise<T> {
  const { data, error, response } = await pending;
  if (NOT_AVAILABLE.has(response.status)) throw new NotAvailableError();
  if (!response.ok || error !== undefined) {
    const problem = asProblem(error);
    throw new ApiError(response.status, problem.code, problem);
  }
  // 204 No Content (e.g. DELETE) has no body: openapi-fetch returns `{}` or undefined.
  return (data ?? ({} as T)) as T;
}

/** A list body that is not an array (absent, `{}` from a 204, a proxy error page) reads as empty. */
export function asList<T>(body: readonly T[] | null | undefined): T[] {
  return Array.isArray(body) ? [...body] : [];
}

/** Most pages `listAll` follows (200 rows each at the API's cap: 5,000 rows). */
export const LIST_ALL_MAX_PAGES = 25;

/**
 * Every page of a cursor-paged list, for small catalogues a picker must show in full (the
 * school's years, classes and sections; FR-TEN-010). One page stops at the API's cap of 200
 * rows, and sections of every year are listed, so a large school passes it within a few
 * years; the rows after it were silently missing from every section picker.
 */
export async function listAll<T>(
  load: (cursor: string | undefined) => Promise<{ data?: T[] | null; next_cursor?: string | null }>,
  maxPages: number = LIST_ALL_MAX_PAGES,
): Promise<T[]> {
  const rows: T[] = [];
  let cursor: string | undefined;
  for (let page = 0; page < maxPages; page += 1) {
    const body = await load(cursor);
    rows.push(...asList(body.data));
    if (!body.next_cursor) break;
    cursor = body.next_cursor;
  }
  return rows;
}

let navigateOverride: Navigate | undefined;
/** Tests only: capture sign-in/step-up navigation instead of leaving the page. */
export function setNavigateForTesting(navigate: Navigate | undefined): void {
  navigateOverride = navigate;
}

export function useBffClient(kind: SessionKind): ApiClient {
  const locale = useLocale();
  return useMemo(
    () =>
      createBffClient({
        kind,
        locale,
        ...(navigateOverride ? { navigate: navigateOverride } : {}),
      }),
    [kind, locale],
  );
}

const REASONS: Record<string, LoadErrorReason> = {
  active_tenant_required: "active_tenant_required",
  mfa_required: "mfa_required",
  tenant_suspended: "tenant_suspended",
  no_membership: "forbidden",
  forbidden: "forbidden",
  wrong_session: "forbidden",
  not_found: "not_found",
};

export function reasonOf(error: unknown): LoadErrorReason | undefined {
  if (!(error instanceof ApiError)) return undefined;
  if (error.code && error.code in REASONS) return REASONS[error.code];
  if (error.status === 403) return "forbidden";
  if (error.status === 404) return "not_found";
  return undefined;
}

/**
 * A failure that says nothing about the data already shown (server trouble, timeout, rate
 * limit, offline): the last good answer stays on screen. Sign-in, step-up, no access, a
 * suspended school, a record that is gone and a route this deployment lacks are not.
 * Pollers keep asking after a transient failure (the screen still shows the old state).
 */
export function isTransientError(error: unknown): boolean {
  if (error instanceof ApiError) {
    return error.status >= 500 || error.status === 408 || error.status === 429;
  }
  return !(
    error instanceof NotAvailableError ||
    error instanceof AuthRedirectError ||
    error instanceof StepUpCancelledError
  );
}

interface PollState {
  status: string;
  error: unknown;
}

/**
 * A status poll stops on this failure (XO-08, as XO-07): a transient one keeps it going (the
 * screen still shows the last answer and the job may still be running); any other (no access,
 * gone, signed out, a route this deployment lacks) stops it. Pass the query's `state`.
 */
export function pollFailed(state: PollState): boolean {
  return state.status === "error" && !isTransientError(state.error);
}

export function toLoadable<T>(query: {
  isPending: boolean;
  isError: boolean;
  error: unknown;
  data: T | undefined;
}): Loadable<T> {
  if (query.isPending) return loading;
  if (query.isError) {
    // A background refetch or poll failed: keep what is shown (XO-07).
    if (query.data !== undefined && isTransientError(query.error)) {
      return { status: "ready", data: query.data, refreshFailed: true };
    }
    if (query.error instanceof NotAvailableError) return unavailable;
    // Navigating to sign-in or step-up: keep showing the loading state.
    if (query.error instanceof AuthRedirectError) return loading;
    const reason = reasonOf(query.error);
    return reason ? { status: "error", reason } : loadError;
  }
  return ready(query.data as T);
}

export interface ApiQueryOptions<T = unknown> {
  /** Skip the request (e.g. no permission, or a filter is incomplete). */
  enabled?: boolean;
  /**
   * Ask again every this many milliseconds while the screen is open (live counts, e.g. a
   * WhatsApp broadcast that is still sending); `false` or omitted = no polling. A function
   * decides from the last answer (stop once the broadcast is done).
   */
  refetchInterval?: number | false | ((data: T | undefined) => number | false);
}

/**
 * Query through the BFF, exposed as a Loadable for the view components. `load` gets the query
 * function's context: a polling caller passes it to pollHeaders (lib/bff/poll.ts), so the
 * automatic re-reads are passive (XB-07).
 */
export function useApiQuery<T>(
  queryKey: QueryKey,
  load: (context: QueryFunctionContext) => Promise<T>,
  options: ApiQueryOptions<T> = {},
): Loadable<T> {
  const interval = options.refetchInterval;
  const query = useQuery({
    queryKey,
    queryFn: (context) => load(context),
    enabled: options.enabled ?? true,
    // Only callers that ask for polling set it (other observers of the same key keep theirs).
    ...(interval === undefined
      ? {}
      : {
          refetchInterval: (current: { state: PollState & { data: T | undefined } }) =>
            pollFailed(current.state)
              ? false
              : typeof interval === "function"
                ? interval(current.state.data)
                : interval,
        }),
    retry: (count, error) =>
      count < 1 &&
      !(error instanceof NotAvailableError) &&
      !(error instanceof AuthRedirectError) &&
      !(error instanceof StepUpCancelledError) &&
      !(error instanceof ApiError && error.status < 500),
  });
  return toLoadable(query);
}

/** A fresh Idempotency-Key for one user intent (docs/09 §2). */
export function newIdempotencyKey(): string {
  return crypto.randomUUID();
}

/**
 * Mutation through the BFF. On success the listed query keys (prefixes) are refetched.
 * Errors stay on the mutation for <ApiErrorAlert>; 401/428 navigate away (AuthRedirectError).
 */
export function useApiMutation<TInput, TResult>(
  run: (input: TInput) => Promise<TResult>,
  options: { invalidate?: readonly QueryKey[]; onSuccess?: (result: TResult) => void } = {},
): UseMutationResult<TResult, unknown, TInput> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: run,
    onSuccess: async (result) => {
      options.onSuccess?.(result);
      await Promise.all(
        (options.invalidate ?? []).map((queryKey) => queryClient.invalidateQueries({ queryKey })),
      );
    },
  });
}
