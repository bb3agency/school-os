import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { installBffStub, page, problem, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { messages, renderWithIntl } from "@/test/render";
import { DashboardView } from "../DashboardView";
import {
  clockFromMinutes,
  reopenTellPending,
  reopenToldSchema,
  type IncidentClock,
  type IncidentDetail,
} from "./data";
import { IncidentDetailScreen } from "./IncidentDetailScreen";
import { IncidentsScreen } from "./IncidentsScreen";

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => "/platform/incidents",
    useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
    useSearchParams: () => new URLSearchParams(),
    useParams: () => ({ locale: "en" }),
  };
});

/**
 * Incident follow-ups in the operator console (D49; FR-DPDP-031, FR-DPDP-032): the clock alert
 * tile and banner, and recording that a dedicated-host school was told of a reopen. Synthetic
 * data only; tenant codes, never student data.
 */
const im = messages.en.platform.incidents;
const NOW = new Date("2026-10-21T06:00:00Z");
const INC = "0192f3a4-0000-7000-8000-00000000e001";
const INC2 = "0192f3a4-0000-7000-8000-00000000e002";
const OP = "0192f3a4-0000-7000-8000-0000000000f1";
const T1 = "0192f3a4-0000-7000-8000-000000000001";
let stub: BffStub;

const CLOCKS: IncidentClock[] = [
  {
    incident_id: INC,
    severity: "P1",
    clock: "cert_in",
    stage: "overdue",
    due_at: "2026-10-21T04:00:00Z",
    minutes_left: -125,
    tenant_id: null,
    tenant_code: null,
  },
  {
    incident_id: INC2,
    severity: "P3",
    clock: "school_notice",
    stage: "due_soon",
    due_at: "2026-10-21T06:40:00Z",
    minutes_left: 40,
    tenant_id: T1,
    tenant_code: "svvn-kakinada",
  },
];

function detail(mode: string, extra: Partial<IncidentDetail> = {}): IncidentDetail {
  return {
    id: INC,
    opened_at: "2026-10-20T04:00:00Z",
    severity: "P2",
    status: "open",
    summary: "Contact details of about 300 parents in 1 school",
    cert_in_required: false,
    cert_in_due_at: null,
    cert_in_reported_at: null,
    lead_operator_id: OP,
    created_by: OP,
    created_at: "2026-10-20T04:05:00Z",
    confirmed_by: OP,
    first_confirmed_at: "2026-10-20T05:00:00Z",
    second_confirmed_by: OP,
    confirmed_at: "2026-10-20T05:10:00Z",
    confirm_by: null,
    schools_count: 1,
    schools_notified: 1,
    reopen_count: 1,
    last_reopened_at: "2026-10-21T05:00:00Z",
    close_blocked_by: [],
    version: 7,
    schools: [
      {
        tenant_id: T1,
        school_name: "Sri Venkateswara Vidya Niketan English Medium High School, Kakinada",
        tenant_code: "svvn-kakinada",
        mode,
        categories: ["contact"],
        approx_people: 300,
        window_from: null,
        window_to: null,
        school_notified_at: "2026-10-20T06:00:00Z",
        pulled_at: null,
        notice_due_at: null,
        reopen_pulled: 0,
        reopen_pulled_at: null,
      },
    ],
    ...extra,
  };
}

function setPermissions(permissions: readonly string[]) {
  stub.routes["GET /bff/api/v1/platform/me"] = () =>
    Response.json({ operator_id: OP, roles: ["platform_owner"], permissions, step_up_fresh: true });
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
  stub = installBffStub("operator");
  stub.routes["GET /bff/api/v1/platform/tenants"] = () => page([]);
});
afterEach(() => {
  uninstallBffStub();
  vi.useRealTimers();
});

describe("incident clock alerts in the console (D49 part 2; FR-DPDP-031)", () => {
  it("words the API's minutes left", () => {
    expect(clockFromMinutes({ stage: "overdue", minutes_left: -5 })).toEqual({
      state: "overdue",
      hours: 1,
    });
    expect(clockFromMinutes({ stage: "overdue", minutes_left: -125 })).toEqual({
      state: "overdue",
      hours: 2,
    });
    expect(clockFromMinutes({ stage: "due_soon", minutes_left: 40 })).toEqual({
      state: "dueSoon",
    });
    expect(clockFromMinutes({ stage: "due_soon", minutes_left: 119 })).toEqual({
      state: "due",
      hours: 1,
    });
  });

  it("the dashboard shows each alerting clock with a link to its incident", () => {
    renderWithIntl(<DashboardView kpis={{ incident_clocks: CLOCKS }} />);
    const tile = screen.getByRole("region", { name: im.clockAlerts.label });
    expect(within(tile).getByText("2 incident clocks are due soon or overdue")).toBeInTheDocument();
    const items = within(tile).getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(within(items[0]!).getByText(im.clockAlerts.certIn)).toBeInTheDocument();
    expect(within(items[0]!).getByText(/overdue by 2 h/)).toBeInTheDocument();
    expect(within(items[0]!).getByRole("link", { name: im.clockAlerts.open })).toHaveAttribute(
      "href",
      `/platform/incidents/${INC}`,
    );
    expect(within(items[1]!).getByText("School notice: svvn-kakinada")).toBeInTheDocument();
    expect(within(items[1]!).getByText(/due within the hour/)).toBeInTheDocument();
  });

  it("no tile without the incident permission (null) or when nothing is alerting", () => {
    const { unmount } = renderWithIntl(
      <DashboardView kpis={{ incident_clocks: null, schools_by_status: { active: 2 } }} />,
    );
    expect(screen.queryByRole("region", { name: im.clockAlerts.label })).toBeNull();
    unmount();
    renderWithIntl(<DashboardView kpis={{ incident_clocks: [] }} />, "te");
    expect(
      screen.queryByRole("region", { name: messages.te.platform.incidents.clockAlerts.label }),
    ).toBeNull();
  });

  it("the incidents list shows the same alerts as a banner", async () => {
    setPermissions(["platform.incidents.read"]);
    stub.routes["GET /bff/api/v1/platform/incidents"] = () => page([]);
    stub.routes["GET /bff/api/v1/platform/incidents/clocks"] = () => Response.json(CLOCKS);
    renderWithIntl(<IncidentsScreen />);
    const banner = await screen.findByRole("region", { name: im.clockAlerts.label });
    expect(within(banner).getAllByRole("listitem")).toHaveLength(2);
  });

  it("an operator without the incident role never asks for the clocks", async () => {
    setPermissions(["platform.tenants.read"]);
    renderWithIntl(<IncidentsScreen />);
    expect(await screen.findByText(im.noPermissionTitle)).toBeInTheDocument();
    expect(stub.callsTo("GET /bff/api/v1/platform/incidents/clocks")).toEqual([]);
  });
});

describe("a dedicated school told of a reopen by phone (D49; FR-DPDP-032)", () => {
  it("is pending only for a dedicated school behind the reopen count", () => {
    expect(reopenTellPending({ mode: "dedicated", reopen_pulled: 0 }, { reopen_count: 1 })).toBe(
      true,
    );
    expect(reopenTellPending({ mode: "dedicated", reopen_pulled: 1 }, { reopen_count: 1 })).toBe(
      false,
    );
    expect(reopenTellPending({ mode: "shared", reopen_pulled: 0 }, { reopen_count: 1 })).toBe(
      false,
    );
  });

  it("the note follows the summary rule", () => {
    expect(reopenToldSchema.safeParse({ note: "Told on 9876543210" }).success).toBe(false);
    expect(reopenToldSchema.safeParse({ note: "Mail to head@school.example" }).success).toBe(false);
    expect(reopenToldSchema.safeParse({ note: "Principal told by phone" }).success).toBe(true);
  });

  it("records it from the school's row with the reopen count", async () => {
    setPermissions(["platform.incidents.read", "platform.incidents.manage"]);
    stub.routes[`GET /bff/api/v1/platform/incidents/${INC}`] = () =>
      Response.json(detail("dedicated"));
    stub.routes[`POST /bff/api/v1/platform/incidents/${INC}/reopen:told`] = () =>
      Response.json(detail("dedicated", {}));
    const user = userEvent.setup();
    renderWithIntl(<IncidentDetailScreen incidentId={INC} />);
    await user.click(await screen.findByRole("button", { name: im.reopenTold }));
    const dialog = screen.getByRole("dialog");
    await user.type(within(dialog).getByLabelText(im.reopenToldNote), "Principal told by phone");
    await user.click(within(dialog).getByRole("button", { name: im.reopenTold }));
    await waitFor(() =>
      expect(stub.callsTo(`POST /bff/api/v1/platform/incidents/${INC}/reopen:told`)).toHaveLength(
        1,
      ),
    );
    const call = stub.callsTo(`POST /bff/api/v1/platform/incidents/${INC}/reopen:told`)[0];
    expect(JSON.parse(call?.body ?? "{}")).toEqual({
      tenant_id: T1,
      reopen_seq: 1,
      note: "Principal told by phone",
    });
  });

  it("a refusal is explained", async () => {
    setPermissions(["platform.incidents.read", "platform.incidents.manage"]);
    stub.routes[`GET /bff/api/v1/platform/incidents/${INC}`] = () =>
      Response.json(detail("dedicated"));
    stub.routes[`POST /bff/api/v1/platform/incidents/${INC}/reopen:told`] = () =>
      problem(409, "reopen_changed");
    const user = userEvent.setup();
    renderWithIntl(<IncidentDetailScreen incidentId={INC} />);
    await user.click(await screen.findByRole("button", { name: im.reopenTold }));
    const dialog = screen.getByRole("dialog");
    await user.type(within(dialog).getByLabelText(im.reopenToldNote), "Principal told by phone");
    await user.click(within(dialog).getByRole("button", { name: im.reopenTold }));
    expect(await within(dialog).findByText(im.errors.reopen_changed.title)).toBeInTheDocument();
  });

  it("no action for a shared school, a received reopen or a reader", async () => {
    setPermissions(["platform.incidents.read", "platform.incidents.manage"]);
    stub.routes[`GET /bff/api/v1/platform/incidents/${INC}`] = () =>
      Response.json(detail("shared"));
    const { unmount } = renderWithIntl(<IncidentDetailScreen incidentId={INC} />);
    expect(await screen.findByText(im.reopenNotPulled)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: im.reopenTold })).toBeNull();
    unmount();
    setPermissions(["platform.incidents.read"]);
    stub.routes[`GET /bff/api/v1/platform/incidents/${INC}`] = () =>
      Response.json(detail("dedicated"));
    renderWithIntl(<IncidentDetailScreen incidentId={INC} />);
    expect(await screen.findByText(im.reopenNotPulled)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: im.reopenTold })).toBeNull();
  });
});
