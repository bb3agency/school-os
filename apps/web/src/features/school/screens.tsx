"use client";

import { useTranslations } from "next-intl";
import { Pager, usePagedList } from "@/features/students/paging";
import { unwrap, useBffClient } from "@/lib/bff/query";
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
  const t = useTranslations("school.audit");
  const query = { limit: 100, ...auditQuery(filters) };
  // Every page (docs/09 §2): one day of a busy school is more than 100 events.
  const { rows, pager } = usePagedList(["staff", "audit-events", query], (cursor) =>
    unwrap(
      api.GET("/api/v1/audit/events", {
        params: { query: { ...query, ...(cursor ? { cursor } : {}) } },
      }),
    ),
  );
  // The pager sits in the table's footer, only when there is another page.
  const paged = Boolean(pager.onPrevious || pager.onNext);
  return (
    <AuditView
      events={rows}
      filters={filters}
      footer={paged ? <Pager label={t("pagesLabel")} {...pager} /> : undefined}
    />
  );
}
