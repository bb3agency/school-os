"use client";

import { useQuery } from "@tanstack/react-query";
import type { components } from "@schoolos/api-client";
import { AuthRedirectError } from "@/lib/bff/fetch";
import { ApiError, NotAvailableError, unwrap, useBffClient } from "@/lib/bff/query";

/**
 * Ask usage (R2.4; FR-KB-040, FR-KB-041, US-2501, US-2502; docs/17 §5.8 "Ask usage"):
 * `GET /knowledge/usage` gives everyone with `kb.ask` this month's state (`ok`, `near_limit`,
 * `search_only` with its reason) and, only to `tenant.billing.read` holders, the billable
 * answers used and the bundle's included answers. Numbers are never shown without them (the
 * API sends null); tokens and money never.
 */

export type Usage = components["schemas"]["UsageOut"];
export type UsageReason = NonNullable<Usage["search_only_reason"]>;

export const USAGE_KEY = ["staff", "knowledge", "usage"] as const;

const retry = (count: number, error: unknown) =>
  count < 1 &&
  !(error instanceof NotAvailableError) &&
  !(error instanceof AuthRedirectError) &&
  !(error instanceof ApiError && error.status < 500);

/** GET /knowledge/usage (kb.ask). Refetched after each answer (see ChatScreen). */
export function useAskUsage(enabled = true) {
  const api = useBffClient("staff");
  return useQuery({
    queryKey: USAGE_KEY,
    queryFn: (): Promise<Usage> => unwrap(api.GET("/api/v1/knowledge/usage")),
    enabled,
    retry,
    staleTime: 30_000,
  });
}

/** The numbers are the caller's to see (`tenant.billing.read`): the API sent them. */
export function showsNumbers(usage: Usage | undefined): usage is Usage & { answers_used: number } {
  return typeof usage?.answers_used === "number";
}

/** Share of the included answers used, 0-100 (rounded down); null without a bundle. */
export function usedPercent(usage: Usage): number | null {
  if (usage.answers_used == null || usage.included_answers == null) return null;
  return Math.min(100, Math.floor((usage.answers_used / usage.included_answers) * 100));
}

export type UsageBanner =
  { kind: "search_only"; reason: UsageReason } | { kind: "near_limit" } | null;

/**
 * The banner above the composer (FR-KB-041): search-only answers for everyone, with the
 * reason; "near your monthly answers" only for billing readers (others see no numbers and no
 * warning until answers are actually limited, US-2501 AC2).
 */
export function bannerOf(usage: Usage | undefined): UsageBanner {
  if (!usage) return null;
  if (usage.state === "search_only") {
    return { kind: "search_only", reason: usage.search_only_reason ?? "ai_unavailable" };
  }
  if (usage.state === "near_limit" && showsNumbers(usage)) return { kind: "near_limit" };
  return null;
}

/** Key a dismissal by month and banner, so a new month or a new state shows it again. */
export function bannerKey(usage: Usage, banner: NonNullable<UsageBanner>): string {
  return `${usage.month}:${banner.kind}:${banner.kind === "search_only" ? banner.reason : ""}`;
}
