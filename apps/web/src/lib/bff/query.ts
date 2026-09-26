"use client";

import type { ApiClient, Problem } from "@schoolos/api-client";
import { useQuery, type QueryKey } from "@tanstack/react-query";
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

/** The API does not have this endpoint yet (404/405/501 while the backend is built). */
export class NotAvailableError extends Error {
  constructor() {
    super("not available yet");
    this.name = "NotAvailableError";
  }
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string | undefined,
  ) {
    super(`API error ${status}`);
    this.name = "ApiError";
  }
}

const NOT_AVAILABLE = new Set([404, 405, 501]);

type ClientResult<T> = { data?: T; error?: unknown; response: Response };

/** openapi-fetch result → data, or a typed error the screens can show. */
export async function unwrap<T>(pending: Promise<ClientResult<T>>): Promise<T> {
  const { data, error, response } = await pending;
  if (NOT_AVAILABLE.has(response.status)) throw new NotAvailableError();
  if (error !== undefined || data === undefined) {
    throw new ApiError(response.status, (error as Partial<Problem> | undefined)?.code);
  }
  return data;
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
};

function reasonOf(error: unknown): LoadErrorReason | undefined {
  if (!(error instanceof ApiError)) return undefined;
  if (error.code && error.code in REASONS) return REASONS[error.code];
  return error.status === 403 ? "forbidden" : undefined;
}

/** Query through the BFF, exposed as a Loadable for the view components. */
export function useApiQuery<T>(queryKey: QueryKey, load: () => Promise<T>): Loadable<T> {
  const query = useQuery({
    queryKey,
    queryFn: load,
    retry: (count, error) =>
      count < 1 &&
      !(error instanceof NotAvailableError) &&
      !(error instanceof AuthRedirectError) &&
      !(error instanceof ApiError && error.status < 500),
  });
  if (query.isPending) return loading;
  if (query.isError) {
    if (query.error instanceof NotAvailableError) return unavailable;
    // Navigating to sign-in or step-up: keep showing the loading state.
    if (query.error instanceof AuthRedirectError) return loading;
    const reason = reasonOf(query.error);
    return reason ? { status: "error", reason } : loadError;
  }
  return ready(query.data);
}
