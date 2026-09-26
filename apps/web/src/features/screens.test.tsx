import { screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { forgetSessionInfo } from "@/lib/bff/session-client";
import { setNavigateForTesting } from "@/lib/bff/query";
import { messages, renderWithIntl } from "@/test/render";
import { DashboardScreen, SchoolsScreen } from "./platform/screens";
import { AuditScreen, StructureScreen, toIsoDate, UsersScreen } from "./school/screens";

type Route = (url: URL, request: Request) => Response | Promise<Response>;
let routes: Record<string, Route>;
let seen: URL[];
const navigate = vi.fn();

const page = (data: unknown[]) => Response.json({ data, next_cursor: null });

beforeEach(() => {
  forgetSessionInfo();
  seen = [];
  routes = {};
  navigate.mockReset();
  setNavigateForTesting(navigate);
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: Request) => {
      const url = new URL(input.url);
      seen.push(url);
      const route = routes[url.pathname];
      return route ? route(url, input) : Response.json({ code: "not_found" }, { status: 404 });
    }),
  );
});

afterEach(() => {
  setNavigateForTesting(undefined);
  vi.unstubAllGlobals();
});

describe("school screens wired to the BFF (US-202, US-102, FR-AUD-005)", () => {
  it("structure: shows rows, 'not available yet' for 404 and a plain error for 500", async () => {
    routes["/bff/api/v1/academic-years"] = () =>
      page([
        {
          id: "y1",
          label: "2026-27",
          starts_on: "2026-06-01",
          ends_on: "2027-04-30",
          is_current: true,
        },
      ]);
    routes["/bff/api/v1/sections"] = () =>
      Response.json({ code: "internal_error" }, { status: 500 });
    renderWithIntl(<StructureScreen />, "te");

    expect(await screen.findByText("2026-27")).toBeInTheDocument();
    expect(await screen.findByText(messages.te.common.notAvailableYetTitle)).toBeInTheDocument();
    expect(await screen.findByText(messages.te.common.loadErrorTitle)).toBeInTheDocument();
    expect(seen.map((u) => u.pathname).sort()).toEqual(
      expect.arrayContaining([
        "/bff/api/v1/academic-years",
        "/bff/api/v1/classes",
        "/bff/api/v1/sections",
      ]),
    );
  });

  it("users: renders the list", async () => {
    routes["/bff/api/v1/users"] = () =>
      page([
        {
          id: "u1",
          display_name: "Lakshmi K",
          login: "lakshmi",
          roles: ["office_staff"],
          scope_summary: null,
          status: "active",
          last_sign_in_at: null,
        },
      ]);
    renderWithIntl(<UsersScreen />);
    expect(await screen.findByText("Lakshmi K")).toBeInTheDocument();
    expect(seen.find((u) => u.pathname === "/bff/api/v1/users")?.searchParams.get("limit")).toBe(
      "200",
    );
  });

  it("users: step-up required sends the user to re-authenticate", async () => {
    routes["/bff/api/v1/users"] = () =>
      Response.json(
        {
          code: "step_up_required",
          step_up_url: "/bff/auth/step-up?next=%2Fen%2Fsettings%2Fusers",
        },
        { status: 428 },
      );
    renderWithIntl(<UsersScreen />);
    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith("/bff/auth/step-up?next=%2Fen%2Fsettings%2Fusers"),
    );
    expect(screen.getByRole("status")).toBeInTheDocument();
  });

  it("session ended: sends the user to sign in", async () => {
    routes["/bff/api/v1/users"] = () => Response.json({ code: "unauthenticated" }, { status: 401 });
    renderWithIntl(<UsersScreen />);
    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith(expect.stringMatching(/^\/bff\/auth\/login\?next=/)),
    );
  });

  it("audit: passes filters, converting DD/MM/YYYY dates", async () => {
    routes["/bff/api/v1/audit/events"] = () => page([]);
    renderWithIntl(
      <AuditScreen
        filters={{ actor: " clerk ", action: "", from: "01/06/2026", to: "31/02/2026" }}
      />,
    );
    expect(await screen.findByText(messages.en.school.audit.emptyTitle)).toBeInTheDocument();
    const url = seen.find((u) => u.pathname === "/bff/api/v1/audit/events");
    expect(url?.searchParams.get("actor")).toBe("clerk");
    expect(url?.searchParams.get("from")).toBe("2026-06-01");
    expect(url?.searchParams.has("to")).toBe(false);
    expect(url?.searchParams.has("action")).toBe(false);
  });

  it("toIsoDate only accepts real dates", () => {
    expect(toIsoDate("29/02/2028")).toBe("2028-02-29");
    expect(toIsoDate("29/02/2027")).toBeUndefined();
    expect(toIsoDate("2026-06-01")).toBeUndefined();
    expect(toIsoDate(undefined)).toBeUndefined();
  });
});

describe("platform screens wired to the BFF (FR-PLT-001)", () => {
  it("dashboard: shows KPIs from the API", async () => {
    routes["/bff/api/v1/platform/dashboard"] = () =>
      Response.json({
        mrr_inr: "125000.00",
        arr_inr: "1500000.00",
        active_schools: 12,
        trial_schools: 3,
        past_due_subscriptions: 1,
        fleet_healthy: 4,
        fleet_total: 5,
        ai_spend_month_inr: "3200.50",
      });
    renderWithIntl(<DashboardScreen />);
    const kpis = screen.getByRole("region", { name: messages.en.platform.dashboard.kpisLabel });
    await waitFor(() => expect(within(kpis).queryAllByText("—")).toHaveLength(0));
    expect(within(kpis).getByText("12")).toBeInTheDocument();
  });

  it("dashboard: says 'not available yet' while the API route does not exist", async () => {
    renderWithIntl(<DashboardScreen />, "te");
    expect(await screen.findByText(messages.te.common.notAvailableYetTitle)).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
      messages.te.platform.dashboard.title,
    );
  });

  it("schools: searches with q and lists tenant metadata", async () => {
    routes["/bff/api/v1/platform/tenants"] = () =>
      page([
        {
          id: "0192f3a4-0000-7000-8000-000000000001",
          name: "Sri Saraswati High School",
          code: "SSHS",
          status: "active",
          plan_key: "standard",
          plan_name: "Standard",
          deployment_mode: "shared",
          region: "ap-south-1",
          created_at: "2026-06-01T04:30:00Z",
        },
      ]);
    renderWithIntl(<SchoolsScreen query=" saraswati " />);
    expect(await screen.findByText("Sri Saraswati High School")).toBeInTheDocument();
    const url = seen.find((u) => u.pathname === "/bff/api/v1/platform/tenants");
    expect(url?.searchParams.get("q")).toBe("saraswati");
  });
});
