"use client";

import { unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import { AuditView } from "./AuditView";

/**
 * School console screens wired to the API through the BFF (TanStack Query). The views
 * stay presentational; these components only load data.
 */

export interface AuditFilters {
  actor?: string | undefined;
  action?: string | undefined;
  from?: string | undefined;
  to?: string | undefined;
}

/** DD/MM/YYYY (what users type) → YYYY-MM-DD (API); anything else is ignored. */
export function toIsoDate(value: string | undefined): string | undefined {
  const match = /^(\d{2})\/(\d{2})\/(\d{4})$/.exec(value?.trim() ?? "");
  if (!match) return undefined;
  const [, day, month, year] = match;
  const iso = `${year}-${month}-${day}`;
  const date = new Date(`${iso}T00:00:00Z`);
  return !Number.isNaN(date.getTime()) && date.toISOString().startsWith(iso) ? iso : undefined;
}

/** FR-AUD-005: GET /audit/events with the URL's filters. */
export function AuditScreen({ filters = {} }: { filters?: AuditFilters }) {
  const api = useBffClient("staff");
  const query = {
    limit: 100,
    ...(filters.actor?.trim() ? { actor: filters.actor.trim() } : {}),
    ...(filters.action?.trim() ? { action: filters.action.trim() } : {}),
    ...(toIsoDate(filters.from) ? { from: toIsoDate(filters.from) as string } : {}),
    ...(toIsoDate(filters.to) ? { to: toIsoDate(filters.to) as string } : {}),
  };
  const events = useApiQuery(
    ["staff", "audit-events", query],
    async () => (await unwrap(api.GET("/api/v1/audit/events", { params: { query } }))).data,
  );
  return <AuditView events={events} filters={filters} />;
}
