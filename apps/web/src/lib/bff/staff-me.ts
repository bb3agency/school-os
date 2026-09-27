"use client";

import type { Me } from "@schoolos/api-client";
import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { useCallback } from "react";
import { unwrap, useBffClient } from "./query";

/** Query key of GET /me for school staff (shared by every screen that checks permissions). */
export const STAFF_ME_KEY = ["staff", "me"] as const;

/**
 * GET /me for school staff: roles, effective permissions, `membership_id` and `tenant_status`.
 * One cached query per page (60 s), shared by every screen and the suspended banner.
 */
export function useStaffMeQuery(): UseQueryResult<Me> {
  const api = useBffClient("staff");
  return useQuery({
    queryKey: STAFF_ME_KEY,
    queryFn: () => unwrap(api.GET("/api/v1/me")),
    staleTime: 60_000,
    retry: false,
  });
}

/**
 * The signed-in member in the active school (GET /me). `undefined` while loading or when /me
 * cannot be read (e.g. 403 tenant_suspended for most roles of a suspended school).
 */
export function useStaffMe(): Me | undefined {
  return useStaffMeQuery().data;
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
