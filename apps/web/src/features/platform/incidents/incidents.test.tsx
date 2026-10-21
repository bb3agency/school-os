import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { installBffStub, page, problem, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { messages, renderWithIntl } from "@/test/render";
import { confirmationOf, incidentSummary, type Incident, type IncidentDetail } from "./data";
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
 * Operator incident records (ADR-0050; FR-DPDP-030, FR-DPDP-031; SEC-029 two-person). The
 * clock is pinned. Synthetic data only; summaries hold categories and counts.
 */
const im = messages.en.platform.incidents;
const NOW = new Date("2026-10-21T06:00:00Z");
const INC = "0192f3a4-0000-7000-8000-00000000e001";
const OP = "0192f3a4-0000-7000-8000-0000000000f1";
const OTHER = "0192f3a4-0000-7000-8000-0000000000f2";
const T1 = "0192f3a4-0000-7000-8000-000000000001";
let stub: BffStub;

function incident(extra: Partial<Incident> = {}): Incident {
  return {
    id: INC,
    opened_at: "2026-10-21T04:00:00Z",
    severity: "P2",
    status: "open",
    summary: "Contact details of about 300 parents in 2 schools",
    cert_in_required: true,
    cert_in_due_at: "2026-10-21T10:00:00Z",
    cert_in_reported_at: null,
    lead_operator_id: OP,
    created_by: OP,
    created_at: "2026-10-21T04:05:00Z",
    confirmed_by: null,
    first_confirmed_at: null,
    second_confirmed_by: null,
    confirmed_at: null,
    confirm_by: null,
    schools_count: 2,
    schools_notified: 1,
    version: 5,
    ...extra,
  };
}

function detail(extra: Partial<IncidentDetail> = {}): IncidentDetail {
  return {
    ...incident(),
    schools: [
      {
        tenant_id: T1,
        school_name: "Sri Venkateswara Vidya Niketan English Medium High School, Kakinada",
        tenant_code: "svvn-kakinada",
        mode: "shared",
        categories: ["contact"],
        approx_people: 300,
        window_from: null,
        window_to: null,
        school_notified_at: null,
        pulled_at: null,
        notice_due_at: null,
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

describe("summary: categories and counts only (FR-DPDP-030)", () => {
  it("refuses long digit runs and e-mail addresses, accepts counts", () => {
    expect(incidentSummary.safeParse("Phones like 9876543210 leaked").success).toBe(false);
    expect(incidentSummary.safeParse("Mail to parent@example.com exposed").success).toBe(false);
    expect(incidentSummary.safeParse("Contact details of about 300 parents").success).toBe(true);
  });

  it("the form explains why before anything is sent", async () => {
    setPermissions(["platform.incidents.read", "platform.incidents.manage"]);
    stub.routes["GET /bff/api/v1/platform/incidents"] = () => page([]);
    const user = userEvent.setup();
    renderWithIntl(<IncidentsScreen />);
    await user.click(await screen.findByRole("button", { name: im.open }));
    const dialog = screen.getByRole("dialog", { name: im.openTitle });
    await user.selectOptions(within(dialog).getByLabelText(im.severityLabel), "P1");
    await user.type(
      within(dialog).getByLabelText(im.summaryLabel),
      "Parent phone 9876543210 seen by another school",
    );
    await user.click(within(dialog).getByRole("button", { name: im.open }));
    expect(
      await within(dialog).findByText(messages.en.validation.summaryPersonalData),
    ).toBeInTheDocument();
    expect(stub.callsTo("POST /bff/api/v1/platform/incidents")).toEqual([]);
  });
});

describe("incident list", () => {
  it("shows the CERT-In clock and how many schools were told", async () => {
    setPermissions(["platform.incidents.read"]);
    stub.routes["GET /bff/api/v1/platform/incidents"] = () => page([incident()]);
    renderWithIntl(<IncidentsScreen />);
    expect(await screen.findByText("Report due in 4 h")).toBeInTheDocument();
    expect(screen.getByText("1 of 2 told")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: im.open })).toBeNull();
  });

  it("an operator without the incident role is told so", async () => {
    setPermissions(["platform.tenants.read"]);
    renderWithIntl(<IncidentsScreen />);
    expect(await screen.findByText(im.noPermissionTitle)).toBeInTheDocument();
  });
});

describe("two-person confirmation (SEC-029)", () => {
  it("knows who confirmed first", () => {
    const waiting = incident({ confirmed_by: OP, confirm_by: "2026-10-22T06:00:00Z" });
    expect(confirmationOf(waiting, OP)).toEqual({
      state: "waiting",
      mine: true,
      by: "2026-10-22T06:00:00Z",
    });
    expect(confirmationOf(waiting, OTHER)).toMatchObject({ state: "waiting", mine: false });
    expect(confirmationOf(incident(), OP)).toEqual({ state: "none" });
  });

  it("after my confirmation it waits for a second operator, with no second button for me", async () => {
    setPermissions(["platform.incidents.read", "platform.incidents.manage"]);
    stub.routes[`GET /bff/api/v1/platform/incidents/${INC}`] = () =>
      Response.json(
        detail({
          confirmed_by: OP,
          first_confirmed_at: "2026-10-21T05:00:00Z",
          confirm_by: "2026-10-22T05:00:00Z",
        }),
      );
    renderWithIntl(<IncidentDetailScreen incidentId={INC} />);
    expect(
      await screen.findByText(/Waiting for a second operator, by 22\/10\/2026/),
    ).toBeInTheDocument();
    expect(screen.getByText(im.youConfirmed)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: im.confirmSecond })).toBeNull();
  });

  it("another operator confirms second; a refusal is explained", async () => {
    setPermissions(["platform.incidents.read", "platform.incidents.manage"]);
    stub.routes[`GET /bff/api/v1/platform/incidents/${INC}`] = () =>
      Response.json(detail({ confirmed_by: OTHER, confirm_by: "2026-10-22T05:00:00Z" }));
    stub.routes[`POST /bff/api/v1/platform/incidents/${INC}/confirm`] = () =>
      problem(409, "same_operator");
    const user = userEvent.setup();
    renderWithIntl(<IncidentDetailScreen incidentId={INC} />);
    await user.click(await screen.findByRole("button", { name: im.confirmSecond }));
    const dialog = screen.getByRole("dialog", { name: im.confirmSecondTitle });
    await user.click(within(dialog).getByRole("button", { name: im.confirmSecond }));
    await waitFor(() =>
      expect(stub.callsTo(`POST /bff/api/v1/platform/incidents/${INC}/confirm`)).toHaveLength(1),
    );
    expect(
      await within(dialog).findByText(messages.en.errors.api.same_operator.title),
    ).toBeInTheDocument();
  });

  it("the CERT-In report is recorded with If-Match", async () => {
    setPermissions(["platform.incidents.read", "platform.incidents.manage"]);
    stub.routes[`GET /bff/api/v1/platform/incidents/${INC}`] = () => Response.json(detail());
    stub.routes[`PATCH /bff/api/v1/platform/incidents/${INC}`] = () =>
      Response.json(detail({ cert_in_reported_at: "2026-10-21T05:30:00Z", version: 6 }));
    const user = userEvent.setup();
    renderWithIntl(<IncidentDetailScreen incidentId={INC} />);
    await user.click(await screen.findByRole("button", { name: im.recordReport }));
    const dialog = screen.getByRole("dialog", { name: im.recordReportTitle });
    await user.click(within(dialog).getByRole("button", { name: im.recordReport }));
    await waitFor(() =>
      expect(stub.callsTo(`PATCH /bff/api/v1/platform/incidents/${INC}`)).toHaveLength(1),
    );
    const call = stub.callsTo(`PATCH /bff/api/v1/platform/incidents/${INC}`)[0];
    expect(call?.headers.get("if-match")).toBe('"5"');
    expect(JSON.parse(call?.body ?? "{}")).toHaveProperty("cert_in_reported_at");
  });
});
