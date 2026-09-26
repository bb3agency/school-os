"use client";

import type { ApiClient, Problem } from "@schoolos/api-client";
import {
  useMutation,
  useQuery,
  useQueryClient,
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

export function toLoadable<T>(query: {
  isPending: boolean;
  isError: boolean;
  error: unknown;
  data: T | undefined;
}): Loadable<T> {
  if (query.isPending) return loading;
  if (query.isError) {
    if (query.error instanceof NotAvailableError) return unavailable;
    // Navigating to sign-in or step-up: keep showing the loading state.
    if (query.error instanceof AuthRedirectError) return loading;
    const reason = reasonOf(query.error);
    return reason ? { status: "error", reason } : loadError;
  }
  return ready(query.data as T);
}

export interface ApiQueryOptions {
  /** Skip the request (e.g. no permission, or a filter is incomplete). */
  enabled?: boolean;
}

/** Query through the BFF, exposed as a Loadable for the view components. */
export function useApiQuery<T>(
  queryKey: QueryKey,
  load: () => Promise<T>,
  options: ApiQueryOptions = {},
): Loadable<T> {
  const query = useQuery({
    queryKey,
    queryFn: load,
    enabled: options.enabled ?? true,
    retry: (count, error) =>
      count < 1 &&
      !(error instanceof NotAvailableError) &&
      !(error instanceof AuthRedirectError) &&
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
