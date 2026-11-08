"use client";

import { useTranslations } from "next-intl";
import { Alert } from "@/components/ui/Alert";
import { Pager, usePagedList } from "@/features/students/paging";
import { unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import { DashboardView } from "./DashboardView";
import { PK, useCan } from "./data";
import { INCIDENT_KEYS } from "./incidents/data";
import { SchoolsView, type SchoolFilters } from "./SchoolsView";

/** Platform admin panel screens wired to /api/v1/platform/* through the BFF (C14). */

/** FR-PLT-001 / docs/16 §5.1: GET /platform/dashboard. */
export function DashboardScreen() {
  const api = useBffClient("operator");
  const tc = useTranslations("common");
  const te = useTranslations("errors");
  const kpis = useApiQuery(PK.dashboard, () => unwrap(api.GET("/api/v1/platform/dashboard")));
  // WP-05 (D49 part 2): the dashboard's incident clocks are a snapshot; while it stays open
  // they come from the same list the incidents screen asks for every minute, so "due in 2 h"
  // moves on. Only for operators whose dashboard has the clocks (`platform.incidents.read`).
  const hasClocks = kpis.status === "ready" && Array.isArray(kpis.data.incident_clocks);
  const clocks = useApiQuery(
    INCIDENT_KEYS.clocks,
    () => unwrap(api.GET("/api/v1/platform/incidents/clocks")),
    { enabled: hasClocks, refetchInterval: 60_000 },
  );
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
  return (
    <DashboardView
      kpis={kpis.status === "ready" ? kpis.data : null}
      notice={notice}
      {...(hasClocks && clocks.status === "ready" ? { liveClocks: clocks.data } : {})}
    />
  );
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
  const t = useTranslations("platform.schools");
  const { rows: schools, pager } = usePagedList([...PK.tenants, "list", query], (cursor) =>
    unwrap(
      api.GET("/api/v1/platform/tenants", {
        params: { query: { ...query, ...(cursor ? { cursor } : {}) } },
      }),
    ),
  );
  return (
    <div className="space-y-4">
      <SchoolsView
        schools={schools}
        filters={filters}
        canProvision={can("platform.tenants.provision")}
      />
      <Pager label={t("pagesLabel")} {...pager} />
    </div>
  );
}
