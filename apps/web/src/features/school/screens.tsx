"use client";

import { unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import { auditQuery, type AuditFilters } from "./audit-filters";
import { AuditView } from "./AuditView";

/**
 * School console screens wired to the API through the BFF (TanStack Query). The views
 * stay presentational; these components only load data.
 */

export { toIsoDate, type AuditFilters } from "./audit-filters";

/** FR-AUD-005: GET /audit/events with the URL's filters. */
export function AuditScreen({ filters = {} }: { filters?: AuditFilters }) {
  const api = useBffClient("staff");
  const query = { limit: 100, ...auditQuery(filters) };
  const events = useApiQuery(
    ["staff", "audit-events", query],
    async () => (await unwrap(api.GET("/api/v1/audit/events", { params: { query } }))).data,
  );
  return <AuditView events={events} filters={filters} />;
}
