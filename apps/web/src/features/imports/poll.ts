"use client";

import { useQuery, type QueryKey } from "@tanstack/react-query";
import { AuthRedirectError } from "@/lib/bff/fetch";
import { ApiError, NotAvailableError, toLoadable } from "@/lib/bff/query";
import type { Loadable } from "@/lib/loadable";

/** How often a screen asks again while a worker is busy (slow connections: not too often). */
export const POLL_MS = 3000;

/**
 * Like useApiQuery, but asks again every `interval(data)` ms while that returns a number
 * (e.g. while an import is being read or checked). Same retry rules as useApiQuery.
 */
export function usePolledQuery<T>(
  queryKey: QueryKey,
  load: () => Promise<T>,
  interval: (data: T | undefined) => number | false,
  options: { enabled?: boolean } = {},
): Loadable<T> {
  const query = useQuery({
    queryKey,
    queryFn: load,
    enabled: options.enabled ?? true,
    refetchInterval: (current) => interval(current.state.data),
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
