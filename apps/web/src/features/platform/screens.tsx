"use client";

import { useTranslations } from "next-intl";
import { Alert } from "@/components/ui/Alert";
import { unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import { DashboardView } from "./DashboardView";
import { PK, useCan } from "./data";
import { SchoolsView, type SchoolFilters } from "./SchoolsView";

/** Platform admin panel screens wired to /api/v1/platform/* through the BFF (C14). */

/** FR-PLT-001 / docs/16 §5.1: GET /platform/dashboard. */
export function DashboardScreen() {
  const api = useBffClient("operator");
  const tc = useTranslations("common");
  const te = useTranslations("errors");
  const kpis = useApiQuery(PK.dashboard, () => unwrap(api.GET("/api/v1/platform/dashboard")));
  const notice =
    kpis.status === "error" ? (
      <Alert tone="danger" title={tc("loadErrorTitle")}>
        {kpis.reason ? te(`load.${kpis.reason}`) : tc("loadErrorBody")}
      </Alert>
    ) : kpis.status === "unavailable" ? (
      <Alert tone="info" title={tc("notAvailableYetTitle")}>
        {tc("notAvailableYetBody")}
      </Alert>
    ) : null;
  return <DashboardView kpis={kpis.status === "ready" ? kpis.data : null} notice={notice} />;
}

/** FR-PLT-001..005: GET /platform/tenants with the URL's filters. */
export function SchoolsScreen({ filters = {} }: { filters?: SchoolFilters }) {
  const api = useBffClient("operator");
  const can = useCan();
  const q = filters.q?.trim() ?? "";
  const query = {
    limit: 200,
    ...(q ? { q } : {}),
    ...(filters.status ? { status: filters.status } : {}),
    ...(filters.tier ? { tier: filters.tier } : {}),
    ...(filters.trialEnding ? { trial_ending: true } : {}),
    ...(filters.pastDue ? { past_due: true } : {}),
  };
  const schools = useApiQuery(
    [...PK.tenants, "list", query],
    async () => (await unwrap(api.GET("/api/v1/platform/tenants", { params: { query } }))).data,
  );
  return (
    <SchoolsView
      schools={schools}
      filters={filters}
      canProvision={can("platform.tenants.provision")}
    />
  );
}
