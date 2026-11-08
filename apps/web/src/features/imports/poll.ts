"use client";

import { useQuery, type QueryFunctionContext, type QueryKey } from "@tanstack/react-query";
import { AuthRedirectError } from "@/lib/bff/fetch";
import { ApiError, NotAvailableError, pollFailed, toLoadable } from "@/lib/bff/query";
import type { Loadable } from "@/lib/loadable";

/** How often a screen asks again while a worker is busy (slow connections: not too often). */
export const POLL_MS = 3000;

/**
 * Like useApiQuery, but asks again every `interval(data)` ms while that returns a number
 * (e.g. while an import is being read or checked). Same retry rules as useApiQuery. `load`
 * gets the query function's context for pollHeaders, so the re-reads are passive (XB-07); a
 * transient failure keeps polling, any other stops it (XO-08).
 */
export function usePolledQuery<T>(
  queryKey: QueryKey,
  load: (context: QueryFunctionContext) => Promise<T>,
  interval: (data: T | undefined) => number | false,
  options: { enabled?: boolean } = {},
): Loadable<T> {
  const query = useQuery({
    queryKey,
    queryFn: (context) => load(context),
    enabled: options.enabled ?? true,
    refetchInterval: (current) =>
      pollFailed(current.state) ? false : interval(current.state.data),
    // A slow office PC in another tab should not keep polling.
    refetchIntervalInBackground: false,
    retry: (count, error) =>
      count < 1 &&
      !(error instanceof NotAvailableError) &&
      !(error instanceof AuthRedirectError) &&
      !(error instanceof ApiError && error.status < 500),
  });
  return toLoadable(query);
}
