"use client";

import type { AiBundle, OperatorMe, Plan, TenantSummary } from "@schoolos/api-client";
import { useInfiniteQuery, useQuery, type QueryKey } from "@tanstack/react-query";
import { useMemo } from "react";
import { toLoadable, unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import type { Loadable } from "@/lib/loadable";

/** Query-key roots for the control plane; mutations invalidate by these prefixes. */
export const PK = {
  me: ["operator", "me"],
  dashboard: ["operator", "dashboard"],
  tenants: ["operator", "tenants"],
  tenant: (id: string) => ["operator", "tenants", id] as const,
  plans: ["operator", "plans"],
  aiBundles: ["operator", "ai-bundles"],
  subscriptions: ["operator", "subscriptions"],
  invoices: ["operator", "invoices"],
  usage: ["operator", "usage"],
  flags: ["operator", "flags"],
  deployments: ["operator", "deployments"],
  versions: ["operator", "fleet-versions"],
  announcements: ["operator", "announcements"],
  tickets: ["operator", "tickets"],
  breakGlass: ["operator", "break-glass"],
  operators: ["operator", "operators"],
  audit: ["operator", "audit"],
} as const;

/** Rows per page of the cursor-paged operator lists (the API caps `limit` at 200). */
export const LIST_PAGE_SIZE = 50;

export interface PagedList<T> {
  state: Loadable<readonly T[]>;
  hasMore: boolean;
  loadingMore: boolean;
  loadMore: () => void;
}

/**
 * A cursor-paged operator list (`{ data, next_cursor }`): the first page, then "Show more"
 * follows `next_cursor` (audit 2026-10-06 R-14: deployments, announcements and break-glass
 * requests were unpaged or cut off at 200 rows).
 */
export function usePagedList<T>(
  queryKey: QueryKey,
  load: (cursor: string | null) => Promise<{ data: T[]; next_cursor?: string | null }>,
): PagedList<T> {
  const list = useInfiniteQuery({
    queryKey,
    queryFn: ({ pageParam }) => load(pageParam),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor ?? null,
    retry: false,
  });
  const rows = list.data?.pages.flatMap((one) => one.data) ?? [];
  return {
    state: toLoadable({ ...list, data: rows as readonly T[] }),
    hasMore: list.hasNextPage,
    loadingMore: list.isFetchingNextPage,
    loadMore: () => void list.fetchNextPage(),
  };
}

/** The signed-in operator's roles and effective permissions (GET /platform/me). */
export function useOperatorMe(): OperatorMe | undefined {
  const api = useBffClient("operator");
  const query = useQuery({
    queryKey: PK.me,
    queryFn: () => unwrap(api.GET("/api/v1/platform/me")),
    staleTime: 60_000,
    retry: false,
  });
  return query.data;
}

/**
 * Hide actions the operator cannot take (UX only; the API checks every call). While
 * /platform/me is loading, actions stay visible; the server answers 403 if not allowed.
 */
export function useCan(): (permission: string) => boolean {
  const me = useOperatorMe();
  return useMemo(
    () => (permission: string) => (me ? me.permissions.includes(permission) : true),
    [me],
  );
}

/**
 * School names for screens whose rows carry only `tenant_id` (subscriptions, invoices,
 * usage, tickets, break-glass). Metadata only: name and code, never student data.
 */
export function useSchoolDirectory(): {
  schools: readonly TenantSummary[];
  nameOf: (tenantId: string | null | undefined) => string;
} {
  const api = useBffClient("operator");
  const query = useQuery({
    queryKey: [...PK.tenants, "directory"],
    queryFn: async () =>
      (await unwrap(api.GET("/api/v1/platform/tenants", { params: { query: { limit: 200 } } })))
        .data,
    staleTime: 60_000,
    retry: false,
  });
  return useMemo(() => {
    const schools = query.data ?? [];
    const byId = new Map(schools.map((school) => [school.tenant_id, school]));
    return {
      schools,
      nameOf: (tenantId) => {
        if (!tenantId) return "";
        const school = byId.get(tenantId);
        return school ? `${school.school_name} (${school.code})` : tenantId.slice(0, 8);
      },
    };
  }, [query.data]);
}

/** Plans (all statuses) for pickers and plan names. */
export function usePlanDirectory(): {
  plans: readonly Plan[];
  nameOf: (planId: string | null | undefined) => string;
} {
  const api = useBffClient("operator");
  const query = useQuery({
    queryKey: [...PK.plans, "directory"],
    queryFn: async () => (await unwrap(api.GET("/api/v1/platform/plans"))).data,
    staleTime: 60_000,
    retry: false,
  });
  return useMemo(() => {
    const plans = query.data ?? [];
    const byId = new Map(plans.map((plan) => [plan.id, plan]));
    return {
      plans,
      nameOf: (planId) => {
        if (!planId) return "";
        const plan = byId.get(planId);
        return plan ? planLabel(plan) : planId.slice(0, 8);
      },
    };
  }, [query.data]);
}

/** AI answer bundles (ADR-0038): the plans screen, the subscription picker and names. */
export function useAiBundles(): Loadable<AiBundle[]> {
  const api = useBffClient("operator");
  return useApiQuery(
    PK.aiBundles,
    async () => (await unwrap(api.GET("/api/v1/platform/ai-bundles"))).data,
  );
}

/** The loaded value, or ``fallback`` while loading or on error. */
export function readyOr<T>(state: Loadable<T>, fallback: T): T {
  return state.status === "ready" ? state.data : fallback;
}

export function planLabel(plan: Pick<Plan, "name" | "code" | "version">): string {
  return `${plan.name} (${plan.code} v${plan.version})`;
}

/** `W/"3"`-style ETag for If-Match from a resource's `version`. */
export function ifMatch(version: number): string {
  return `"${version}"`;
}
