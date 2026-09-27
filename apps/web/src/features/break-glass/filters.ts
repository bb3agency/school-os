import type { components } from "@schoolos/api-client";

/** Break-glass types and the URL status filter (plain module: server pages call it). */

export type Grant = components["schemas"]["GrantOut"];
export type GrantStatus = Grant["status"];

type Exhaustive<T extends string, L extends readonly T[]> = [T] extends [L[number]] ? L : never;
const statuses = [
  "requested",
  "active",
  "approved",
  "expired",
  "revoked",
  "denied",
] as const satisfies readonly GrantStatus[];
export const GRANT_STATUSES: Exhaustive<GrantStatus, typeof statuses> = statuses;

/** Status filter from the URL; anything else shows every request. */
export function grantStatusFilter(value: string | string[] | undefined): GrantStatus | null {
  const first = Array.isArray(value) ? value[0] : value;
  return first && (GRANT_STATUSES as readonly string[]).includes(first)
    ? (first as GrantStatus)
    : null;
}
