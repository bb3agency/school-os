import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setDownloadOpenerForTesting } from "@/features/exports/data";
import { installBffStub, page, problem, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { messages, renderWithIntl } from "@/test/render";
import { InvoicesScreen } from "./BillingViews";
import { DashboardView } from "./DashboardView";
import { HeartbeatPill } from "./HeartbeatPill";
import { FlagsScreen } from "./OperationsViews";
import { SchoolDetailScreen } from "./SchoolDetailView";
import { SchoolsView } from "./SchoolsView";
import { ready } from "@/lib/loadable";

/**
 * New UX of the platform UI pass (FR-PLT-001, FR-PLT-002, FR-PLT-015..019, FR-PLT-022,
 * FR-PLT-023, NFR-A11Y-001, NFR-I18N-001): attention list, filter empty state, provisioning
 * timeline, invoice PDF download, flag switch with confirmation, heartbeat age. Synthetic
 * data only.
 */

const T = "0192f3a4-0000-7000-8000-000000000001";
const pm = messages.en.platform;
const cm = messages.en.common;

const ME = {
  operator_id: "0192f3a4-0000-7000-8000-0000000000f1",
  roles: ["platform_owner"],
  permissions: [
    "platform.tenants.read",
    "platform.tenants.provision",
    "platform.invoices.read",
    "platform.invoices.manage",
    "platform.flags.read",
    "platform.flags.manage",
    "platform.subscriptions.read",
    "platform.audit.read",
  ],
  step_up_fresh: true,
};

const INVOICE = {
  id: "0192f3a4-0000-7000-8000-00000000c001",
  tenant_id: T,
  subscription_id: "0192f3a4-0000-7000-8000-00000000b001",
  invoice_number: "SOS/2026-27/000123",
  financial_year: "2026-27",
  status: "issued",
  period_start: "2026-09-01",
  period_end: "2026-09-30",
  issue_date: "2026-09-01",
  due_date: "2026-09-15",
  supplier_legal_name: "SchoolOS",
  supplier_gstin: "37AAAAA0000A1Z5",
  supplier_state_code: "37",
  recipient_legal_name: "Sample Education Society",
  recipient_gstin: null,
  place_of_supply_state_code: "37",
  tax_type: "cgst_sgst",
  taxable_value_inr: "4999.00",
  cgst_inr: "449.91",
  sgst_inr: "449.91",
  igst_inr: "0.00",
  total_inr: "5898.82",
  amount_paid_inr: "0.00",
  tds_inr: "0.00",
  balance_due_inr: "5898.82",
  notes: null,
  void_reason: null,
  version: 1,
};

let stub: BffStub;
beforeEach(() => {
  stub = installBffStub("operator");
  stub.routes["GET /bff/api/v1/platform/me"] = () => Response.json(ME);
  stub.routes["GET /bff/api/v1/platform/tenants"] = () => page([]);
  stub.routes["GET /bff/api/v1/platform/plans"] = () => page([]);
});
afterEach(() => {
  uninstallBffStub();
  setDownloadOpenerForTesting(null);
});

describe("platform dashboard: needs-attention list (FR-PLT-001)", () => {
  it("lists only non-zero numbers from the API, each linking to its filtered list", () => {
    renderWithIntl(
      <DashboardView
        kpis={{
          schools_by_status: { active: 12, suspended: 0, provisioning: 1 },
          fleet_by_status: { healthy: 4, unreachable: 1 },
          past_due_count: 2,
          past_due_amount_inr: "9998.00",
          tickets_sla_breached: 0,
          trials_ending_14d: null,
        }}
      />,
    );
    const card = screen.getByRole("region", { name: pm.dashboard.attention.title });
    const items = within(card).getAllByRole("listitem");
    expect(items).toHaveLength(3);
    expect(within(card).getByText("1 deployment is degraded or unreachable")).toBeVisible();
    expect(within(card).getByText("2 subscriptions are past due")).toBeVisible();
    expect(within(card).getByText("1 school is still being set up")).toBeVisible();
    expect(
      within(card).getByRole("link", { name: pm.dashboard.attention.openFleet }),
    ).toHaveAttribute("href", "/en/platform/fleet?status=unreachable");
    // The fleet tile shows its share as a ring with a text value.
    expect(
      screen.getByRole("progressbar", { name: pm.dashboard.fleetHealthyShare }),
    ).toHaveAttribute("aria-valuetext", "80%");
  });

  it("says so when nothing needs attention", () => {
    renderWithIntl(<DashboardView kpis={{ schools_by_status: { active: 3 } }} />, "te");
    expect(screen.getByText(messages.te.platform.dashboard.attention.noneTitle)).toBeVisible();
  });
});

describe("schools: filter bar (FR-PLT-001)", () => {
  it("a filtered empty list says no school matches and offers to clear the filters", () => {
    renderWithIntl(<SchoolsView schools={ready([])} filters={{ q: "zzz" }} />);
    expect(screen.getByRole("search")).toBeInTheDocument();
    expect(screen.getByText(pm.schools.noMatchTitle)).toBeVisible();
    expect(screen.getByRole("link", { name: pm.schools.clearFilters })).toHaveAttribute(
      "href",
      "/en/platform/schools",
    );
  });

  it("shows workflow pills and tier tags with text", () => {
    renderWithIntl(
      <SchoolsView
        schools={ready([
          {
            tenant_id: T,
            school_name: "Sample Model School",
            code: "sms",
            tier: "dedicated",
            tenant_status: "provisioning",
            subscription_status: null,
            plan_code: null,
            deployment_status: "provisioning",
            app_version: null,
            last_heartbeat_at: null,
            created_at: "2026-06-01T04:30:00Z",
          },
        ])}
      />,
    );
    const table = screen.getByRole("table");
    expect(
      within(table).getAllByText(messages.en.status.school.provisioning).length,
    ).toBeGreaterThan(0);
    expect(within(table).getByText(messages.en.deploymentMode.dedicated)).toBeVisible();
  });
});

describe("school detail: provisioning timeline (FR-PLT-002)", () => {
  it("shows the steps, where it stopped, and Resume provisioning in the same card", async () => {
    stub.routes[`GET /bff/api/v1/platform/tenants/${T}`] = () =>
      Response.json({
        tenant_id: T,
        school_name: "Sample Model School",
        code: "sms",
        tier: "shared",
        boards: [],
        tenant_status: "provisioning",
        tenant_status_reason: null,
        subscription_status: null,
        subscription: null,
        plan_code: null,
        deployment_status: "provisioning",
        app_version: null,
        last_heartbeat_at: null,
        created_at: "2026-06-01T04:30:00Z",
        counts: null,
        open_tickets: 0,
        flag_overrides: {},
        invoices: [],
        offboard_requested_at: null,
        offboard_approved_at: null,
        provisioning: {
          state: "failed",
          failed_step: "owner_invite",
          last_error: "unexpected_error",
          attempts: 2,
          in_progress: false,
          resumable: true,
          updated_at: "2026-09-26T04:30:00Z",
        },
      });
    renderWithIntl(<SchoolDetailScreen schoolId={T} tab="overview" />);
    const sd = pm.schoolDetail.provisioning;
    const card = await screen.findByRole("region", { name: sd.state.failed });
    const steps = within(card).getByRole("list", { name: sd.timelineLabel });
    const items = within(steps).getAllByRole("listitem");
    expect(items).toHaveLength(3);
    expect(items[0]).toHaveTextContent(`${sd.stepStatus.done} ${sd.timeline.register}`);
    expect(items[1]).toHaveTextContent(`${sd.stepStatus.done} ${sd.timeline.initialise}`);
    expect(items[2]).toHaveTextContent(sd.stepStatus.stopped);
    expect(within(items[2] as HTMLElement).getByText(sd.stoppedHere)).toBeVisible();
    expect(within(card).getByRole("button", { name: sd.resume })).toBeEnabled();
  });
});

describe("invoices: PDF download and GST split (FR-PLT-015..019)", () => {
  beforeEach(() => {
    stub.routes["GET /bff/api/v1/platform/invoices"] = () => page([INVOICE]);
  });

  it("downloads the PDF of a numbered invoice through a short-lived link", async () => {
    const opened = vi.fn();
    setDownloadOpenerForTesting(opened);
    stub.routes[`GET /bff/api/v1/platform/invoices/${INVOICE.id}/download-url`] = () =>
      Response.json({
        url: "https://s3.example/invoice.pdf?sig=synthetic",
        expires_at: "2026-09-29T10:05:00Z",
        filename: "SOS-2026-27-000123.pdf",
        content_type: "application/pdf",
        sha256: "0".repeat(64),
      });
    const user = userEvent.setup();
    renderWithIntl(<InvoicesScreen />);
    expect(await screen.findByText("CGST ₹449.91 + SGST ₹449.91")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Download PDF SOS/2026-27/000123" }));
    await waitFor(() =>
      expect(opened).toHaveBeenCalledWith("https://s3.example/invoice.pdf?sig=synthetic"),
    );
  });

  it("explains a PDF that is still being prepared (409 invoice_pdf_pending)", async () => {
    stub.routes[`GET /bff/api/v1/platform/invoices/${INVOICE.id}/download-url`] = () =>
      problem(409, "invoice_pdf_pending");
    const user = userEvent.setup();
    renderWithIntl(<InvoicesScreen />, "te");
    await user.click(await screen.findByRole("button", { name: /SOS\/2026-27\/000123/ }));
    expect(
      await screen.findByText(messages.te.platform.invoices.downloadErrors.invoice_pdf_pending),
    ).toBeVisible();
  });
});

describe("feature flags: switch with confirmation (FR-PLT-022)", () => {
  const FLAG = {
    key: "ask.citations_v2",
    tenant_id: null,
    enabled: false,
    rollout_percent: 25,
    description: "New citation chips",
    updated_at: "2026-09-01T00:00:00Z",
  };

  it("flipping the switch changes nothing until confirmed, then keeps the other settings", async () => {
    stub.routes["GET /bff/api/v1/platform/flags"] = () => page([FLAG]);
    stub.routes["PUT /bff/api/v1/platform/flags/ask.citations_v2"] = () => Response.json({});
    const user = userEvent.setup();
    renderWithIntl(<FlagsScreen />);
    const toggle = await screen.findByRole("switch", { name: /ask\.citations_v2/ });
    expect(toggle).toHaveAttribute("aria-checked", "false");
    await user.click(toggle);
    const dialog = screen.getByRole("dialog", {
      name: pm.flags.turnOnTitle.replace("{key}", FLAG.key),
    });
    expect(stub.callsTo("PUT /bff/api/v1/platform/flags/ask.citations_v2")).toHaveLength(0);
    await user.keyboard("{Escape}");
    expect(stub.callsTo("PUT /bff/api/v1/platform/flags/ask.citations_v2")).toHaveLength(0);

    await user.click(toggle);
    expect(within(dialog).getByText(cm.stepUpNote)).toBeVisible();
    await user.click(within(dialog).getByRole("button", { name: pm.flags.turnOn }));
    await waitFor(() =>
      expect(
        JSON.parse(stub.callsTo("PUT /bff/api/v1/platform/flags/ask.citations_v2")[0]?.body ?? ""),
      ).toEqual({ enabled: true, description: "New citation chips", rollout_percent: 25 }),
    );
    await waitFor(() => expect(dialog).not.toHaveAttribute("open"));
  });

  it("shows a plain On/Off pill without platform.flags.manage", async () => {
    stub.routes["GET /bff/api/v1/platform/me"] = () =>
      Response.json({ ...ME, permissions: ["platform.flags.read"] });
    stub.routes["GET /bff/api/v1/platform/flags"] = () => page([FLAG]);
    renderWithIntl(<FlagsScreen />);
    await waitFor(() => expect(screen.queryByRole("switch")).toBeNull());
    expect(await screen.findByText(pm.flags.stateOff)).toBeVisible();
  });
});

describe("fleet: heartbeat age (FR-PLT-023)", () => {
  it("says how long ago the host reported, from the API timestamp", () => {
    const now = Date.parse("2026-09-29T10:00:00Z");
    renderWithIntl(
      <>
        <HeartbeatPill at="2026-09-29T09:48:00Z" status="healthy" now={now} />
        <HeartbeatPill at={null} status="provisioning" now={now} />
      </>,
    );
    expect(screen.getByText(/^12 min.? ago$/)).toBeVisible();
    expect(screen.getByText(pm.fleet.heartbeatNever)).toBeVisible();
  });
});
