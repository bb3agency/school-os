"use client";

import type { Me } from "@schoolos/api-client";
import { useQuery } from "@tanstack/react-query";
import { useCallback } from "react";
import { unwrap, useBffClient } from "./query";

/** Query key of GET /me for school staff (shared by every screen that checks permissions). */
export const STAFF_ME_KEY = ["staff", "me"] as const;

/**
 * The signed-in member in the active school (GET /me): roles, effective permissions and
 * `membership_id`. `undefined` while loading or when /me cannot be read (e.g. 403
 * tenant_suspended for most roles of a suspended school).
 */
export function useStaffMe(): Me | undefined {
  const api = useBffClient("staff");
  const query = useQuery({
    queryKey: STAFF_ME_KEY,
    queryFn: () => unwrap(api.GET("/api/v1/me")),
    staleTime: 60_000,
    retry: false,
  });
  return query.data;
}

/**
 * `can(permission)` or `can([a, b])` (any of them) from the effective permissions. Hides
 * actions until /me has loaded; hiding is UX only, the API checks every call.
 */
export function useStaffCan(): (permission: string | readonly string[]) => boolean {
  const me = useStaffMe();
  return useCallback(
    (permission: string | readonly string[]) => {
      if (!me) return false;
      const wanted = typeof permission === "string" ? [permission] : permission;
      return wanted.some((item) => me.permissions.includes(item));
    },
    [me],
  );
}
