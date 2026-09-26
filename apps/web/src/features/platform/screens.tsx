"use client";

import { useTranslations } from "next-intl";
import { Alert } from "@/components/ui/Alert";
import { unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import { DashboardView } from "./DashboardView";
import { SchoolsView } from "./SchoolsView";

/** Platform admin panel screens wired to /api/v1/platform/* through the BFF (C14). */

/** FR-PLT-001: GET /platform/dashboard. KPIs show "—" while loading or unavailable. */
export function DashboardScreen() {
  const api = useBffClient("operator");
  const tc = useTranslations("common");
  const kpis = useApiQuery(["operator", "dashboard"], () =>
    unwrap(api.GET("/api/v1/platform/dashboard")),
  );
  const notice =
    kpis.status === "error" ? (
      <Alert tone="danger" title={tc("loadErrorTitle")}>
        {tc("loadErrorBody")}
      </Alert>
    ) : kpis.status === "unavailable" ? (
      <Alert tone="info" title={tc("notAvailableYetTitle")}>
        {tc("notAvailableYetBody")}
      </Alert>
    ) : null;
  return <DashboardView kpis={kpis.status === "ready" ? kpis.data : null} notice={notice} />;
}

/** FR-PLT-001..005: GET /platform/tenants?q= (tenant metadata only, never student data). */
export function SchoolsScreen({ query = "" }: { query?: string }) {
  const api = useBffClient("operator");
  const q = query.trim();
  const schools = useApiQuery(
    ["operator", "tenants", q],
    async () =>
      (
        await unwrap(
          api.GET("/api/v1/platform/tenants", {
            params: { query: { limit: 100, ...(q ? { q } : {}) } },
          }),
        )
      ).data,
  );
  return <SchoolsView schools={schools} query={query} />;
}
