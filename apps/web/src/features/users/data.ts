"use client";

import { useQuery } from "@tanstack/react-query";
import { AuthRedirectError } from "@/lib/bff/fetch";
import { ApiError, NotAvailableError, toLoadable, unwrap, useBffClient } from "@/lib/bff/query";
import { StepUpCancelledError } from "@/lib/bff/step-up";
import type { Loadable } from "@/lib/loadable";
import type { StaffRole, StaffUser } from "./types";

/** Query-key roots; every change to a user invalidates `all`. */
export const USER_KEYS = {
  all: ["staff", "users"],
  list: (cursor: string | undefined) => ["staff", "users", "list", cursor ?? null] as const,
  one: (id: string) => ["staff", "users", "one", id] as const,
  roles: ["staff", "roles"],
} as const;

/** The list asks for the API's largest page: most schools have fewer staff than this. */
export const USER_PAGE_SIZE = 200;

function retry(count: number, error: unknown): boolean {
  return (
    count < 1 &&
    !(error instanceof NotAvailableError) &&
    !(error instanceof AuthRedirectError) &&
    !(error instanceof StepUpCancelledError) &&
    !(error instanceof ApiError && error.status < 500)
  );
}

export interface UserPage {
  data: StaffUser[];
  next_cursor: string | null;
}

/** GET /users (needs `user.manage`), in invitation order. */
export function useUserList(cursor: string | undefined, enabled: boolean): Loadable<UserPage> {
  const api = useBffClient("staff");
  const query = useQuery({
    queryKey: USER_KEYS.list(cursor),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/users", {
          params: { query: { limit: USER_PAGE_SIZE, ...(cursor ? { cursor } : {}) } },
        }),
      ),
    enabled,
    retry,
  });
  return toLoadable(query);
}

/** GET /users/{id} (needs `user.manage`). */
export function useUser(userId: string): Loadable<StaffUser> {
  const api = useBffClient("staff");
  const query = useQuery({
    queryKey: USER_KEYS.one(userId),
    queryFn: () =>
      unwrap(api.GET("/api/v1/users/{user_id}", { params: { path: { user_id: userId } } })),
    retry,
  });
  return toLoadable(query);
}

/**
 * GET /roles (needs `user.manage`): the school's roles with their names in both languages and
 * their permissions. The break-glass support role is never listed. One page is enough (a
 * school has nine built-in roles and a few of its own).
 */
export function useRoles(enabled: boolean) {
  const api = useBffClient("staff");
  return useQuery({
    queryKey: USER_KEYS.roles,
    queryFn: async () =>
      (await unwrap(api.GET("/api/v1/roles", { params: { query: { limit: 200 } } }))).data,
    enabled,
    staleTime: 5 * 60_000,
    retry: false,
  });
}

/** The role's name in the reader's language, or null when the role is not in the list. */
export function roleName(
  roles: readonly StaffRole[] | undefined,
  key: string,
  locale: string,
): string | null {
  const found = roles?.find((role) => role.key === key);
  if (!found) return null;
  return locale === "te" && found.name_te ? found.name_te : found.name_en;
}
