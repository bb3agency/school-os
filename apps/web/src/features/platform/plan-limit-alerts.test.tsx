import { screen } from "@testing-library/react";
import type { PlatformDashboard } from "@schoolos/api-client";
import { describe, expect, it } from "vitest";
import { loading, ready } from "@/lib/loadable";
import { messages, renderWithIntl } from "@/test/render";
import { DashboardView } from "./DashboardView";
import { PlanLimitAlerts, planLimitAlertSchema, type PlanLimitAlert } from "./PlanLimitAlerts";

/**
 * Plan-limit crossings for operators (docs/16 §5.10, §19 Q16; FR-PLT-021, D57): the usage page
 * list and the dashboard tile. Counts and metric codes only. Synthetic data only.
 */
const l = messages.en.platform.usage.limitAlerts;
const d = messages.en.platform.dashboard;
const A = "0192f3a4-0000-7000-8000-00000000000a";
const B = "0192f3a4-0000-7000-8000-00000000000b";
const names: Record<string, string> = { [A]: "Synthetic School A", [B]: "Synthetic School B" };
const nameOf = (id: string) => names[id] ?? id;
const row = (
  tenant_id: string,
  metric: string,
  threshold: 80 | 100,
  used: string,
  limit: string,
): PlanLimitAlert => ({
  tenant_id,
  metric,
  threshold,
  period_start: "2026-11-01",
  usage_value: used,
  limit_value: limit,
  crossed_at: "2026-11-02T20:00:00Z",
});

describe("PlanLimitAlerts (FR-PLT-021, D57)", () => {
  it("lists crossings, limit reached first, with the metric and the counts", () => {
    renderWithIntl(
      <PlanLimitAlerts
        alerts={ready<readonly PlanLimitAlert[]>([
          row(A, "students", 80, "812.00", "1000.00"),
          row(B, "storage_gb", 100, "20.50", "20.00"),
        ])}
        schoolName={nameOf}
      />,
    );
    expect(screen.getByRole("heading", { level: 2, name: l.title })).toBeInTheDocument();
    const items = screen.getAllByRole("listitem");
    expect(items[0]).toHaveTextContent("Synthetic School B");
    expect(items[0]).toHaveTextContent(l.reached);
    expect(items[0]).toHaveTextContent("Storage (GB)");
    expect(items[0]).toHaveTextContent("20.5 of 20");
    expect(items[1]).toHaveTextContent("Synthetic School A");
    expect(items[1]).toHaveTextContent(l.near);
    expect(items[1]).toHaveTextContent("812 of 1,000");
  });

  it("none: says so; nothing while loading", () => {
    const { unmount } = renderWithIntl(
      <PlanLimitAlerts alerts={ready<readonly PlanLimitAlert[]>([])} schoolName={nameOf} />,
    );
    expect(screen.getByText(l.none)).toBeInTheDocument();
    unmount();
    renderWithIntl(<PlanLimitAlerts alerts={loading} schoolName={nameOf} />);
    expect(screen.queryByRole("heading", { name: l.title })).toBeNull();
  });

  it("the local schema matches the API shape and refuses other thresholds", () => {
    expect(planLimitAlertSchema.safeParse(row(A, "documents", 80, "9", "10")).success).toBe(true);
    expect(
      planLimitAlertSchema.safeParse({ ...row(A, "documents", 80, "9", "10"), threshold: 50 })
        .success,
    ).toBe(false);
  });
});

describe("DashboardView plan-limit tile (D57)", () => {
  const base = { incident_clocks: null } as unknown as PlatformDashboard;

  it("shows schools at the limit, those near it, and a needs-attention link", () => {
    renderWithIntl(<DashboardView kpis={{ ...base, plan_limit_alerts: { "80": 3, "100": 2 } }} />);
    expect(screen.getByText(d.planLimits)).toBeInTheDocument();
    expect(screen.getByText("3 more at 80% this billing period")).toBeInTheDocument();
    expect(
      screen.getByText("5 schools are at 80% or more of a plan limit this billing period"),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: d.attention.openUsage })).toHaveAttribute(
      "href",
      expect.stringContaining("/platform/usage"),
    );
  });

  it("no tile without platform.usage.read (the API sends null)", () => {
    renderWithIntl(<DashboardView kpis={{ ...base, plan_limit_alerts: null }} />);
    expect(screen.queryByText(d.planLimits)).toBeNull();
    expect(screen.queryByRole("link", { name: d.attention.openUsage })).toBeNull();
  });
});
