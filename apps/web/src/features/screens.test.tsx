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

// Fixtures in the generated API shapes (apps/api/openapi.json). Synthetic data only.
const STAMP = {
  version: 1,
  created_at: "2026-06-01T04:30:00Z",
  updated_at: "2026-06-01T04:30:00Z",
};
const YEAR = {
  id: "0192f3a4-0000-7000-8000-0000000000a1",
  label: "2026-27",
  starts_on: "2026-06-01",
  ends_on: "2027-04-30",
  is_current: true,
  ...STAMP,
};
const CLASS_6 = {
  id: "0192f3a4-0000-7000-8000-0000000000c6",
  code: "6",
  display_en: "Class 6",
  display_te: "6వ తరగతి",
  sort_order: 6,
  ...STAMP,
};
const section = (name: string) => ({
  id: `0192f3a4-0000-7000-8000-0000000000${name.toLowerCase()}${name.toLowerCase()}`.slice(0, 36),
  academic_year_id: YEAR.id,
  class_id: CLASS_6.id,
  name,
  class_teacher_membership_id: null,
  ...STAMP,
});

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
  it("structure: classes in Telugu, sections with their class, section counts", async () => {
    routes["/bff/api/v1/academic-years"] = () => page([YEAR]);
    routes["/bff/api/v1/classes"] = () => page([CLASS_6]);
    routes["/bff/api/v1/sections"] = () => page([section("A"), section("B")]);
    renderWithIntl(<StructureScreen />, "te");

    expect(await screen.findByText("2026-27")).toBeInTheDocument();
    const classes = await screen.findByRole("region", {
      name: new RegExp(`^${messages.te.school.structure.classes.title}\\.`),
    });
    expect(within(classes).getByText("6వ తరగతి")).toBeInTheDocument();
    expect(within(classes).getByText("2")).toBeInTheDocument();
    const sections = await screen.findByRole("region", {
      name: new RegExp(`^${messages.te.school.structure.sections.title}\\.`),
    });
    expect(within(sections).getAllByText("6వ తరగతి")).toHaveLength(2);
  });

  it("structure: 'not available yet' for 404 and a plain error for 500", async () => {
    routes["/bff/api/v1/academic-years"] = () => page([YEAR]);
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
          id: "0192f3a4-0000-7000-8000-0000000000d1",
          membership_id: "0192f3a4-0000-7000-8000-0000000000e1",
          display_name: "Lakshmi K",
          email: null,
          preferred_language: "te",
          status: "suspended",
          expires_at: null,
          roles: ["office_staff", "custom_librarian"],
          scopes: [
            { type: "class", ref: CLASS_6.id },
            { type: "section", ref: "s1" },
            { type: "section", ref: "s2" },
          ],
          last_login_at: null,
          created_at: "2026-06-01T04:30:00Z",
          version: 3,
        },
      ]);
    renderWithIntl(<UsersScreen />);
    expect(await screen.findByText("Lakshmi K")).toBeInTheDocument();
    expect(screen.getByText("Office staff and custom_librarian")).toBeInTheDocument();
    expect(screen.getByText("1 class and 2 sections")).toBeInTheDocument();
    expect(screen.getByText(messages.en.status.member.suspended)).toBeInTheDocument();
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

  it("explains 'choose a school first' (409 active_tenant_required) and no access (403)", async () => {
    routes["/bff/api/v1/users"] = () =>
      Response.json({ code: "active_tenant_required" }, { status: 409 });
    renderWithIntl(<UsersScreen />);
    expect(
      await screen.findByText(messages.en.errors.load.active_tenant_required),
    ).toBeInTheDocument();
  });

  it("explains a missing permission (403)", async () => {
    routes["/bff/api/v1/users"] = () => Response.json({ code: "forbidden" }, { status: 403 });
    renderWithIntl(<UsersScreen />, "te");
    expect(await screen.findByText(messages.te.errors.load.forbidden)).toBeInTheDocument();
  });

  it("session ended: sends the user to sign in", async () => {
    routes["/bff/api/v1/users"] = () => Response.json({ code: "unauthenticated" }, { status: 401 });
    renderWithIntl(<UsersScreen />);
    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith(expect.stringMatching(/^\/bff\/auth\/login\?next=/)),
    );
  });

  it("audit: passes filters, converting DD/MM/YYYY dates", async () => {
    routes["/bff/api/v1/audit/events"] = () =>
      page([
        {
          id: "0192f3a4-0000-7000-8000-0000000000f1",
          seq: 7,
          occurred_at: "2026-09-26T04:30:00Z",
          actor_type: "user",
          actor_id: "0192f3a4-0000-7000-8000-0000000000d1",
          action: "tenant.class.created",
          resource_type: "class",
          resource_id: CLASS_6.id,
          summary: { fields: ["code", "display_en"], count: 1 },
          request_id: "req_1",
        },
      ]);
    renderWithIntl(
      <AuditScreen
        filters={{ actor: " clerk ", action: "", from: "01/06/2026", to: "31/02/2026" }}
      />,
    );
    expect(await screen.findByText("fields: code, display_en; count: 1")).toBeInTheDocument();
    expect(screen.getByText("Staff user · 0192f3a4")).toBeInTheDocument();
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
