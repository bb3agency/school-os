import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { installBffStub, page, problem, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { messages, renderWithIntl } from "@/test/render";
import { SubscriptionsScreen } from "./BillingViews";
import { HeartbeatPill } from "./HeartbeatPill";
import { CertInClock } from "./incidents/IncidentsScreen";
import { BreakGlassScreen } from "./OperationsViews";
import { PlatformTicketScreen } from "./SupportScreens";

/**
 * Operator console audit (2026-11-03, sector F): creating POSTs send an Idempotency-Key so a
 * retry after a lost response does not add a second reply or break-glass request (docs/09 §2,
 * docs/16 §20), and the AI-bundle refusal on an annual plan reads in plain words instead of
 * "this changed while you were working". Synthetic data only.
 */

const T = "0192f3a4-0000-7000-8000-000000000001";
const OP = "0192f3a4-0000-7000-8000-0000000000f1";
const pm = messages.en.platform;
const api = messages.en.errors.api;

const ME = {
  operator_id: OP,
  roles: ["platform_owner"],
  permissions: [
    "platform.tenants.read",
    "platform.subscriptions.read",
    "platform.subscriptions.manage",
    "platform.support.read",
    "platform.support.manage",
    "platform.breakglass.request",
  ],
  step_up_fresh: true,
};

const TENANT_SUMMARY = {
  tenant_id: T,
  school_name: "Sri Saraswati High School",
  code: "sshs",
  tier: "shared",
  tenant_status: "active",
  subscription_status: "active",
  plan_code: "standard",
  deployment_status: "healthy",
  app_version: null,
  last_heartbeat_at: null,
  created_at: "2026-06-01T04:30:00Z",
};

const PLAN = (id: string, code: string, period: "monthly" | "annual") => ({
  id,
  code,
  name: code,
  version: 1,
  tier: "shared",
  status: "published",
  billing_period: period,
  pricing_model: "flat",
  base_price_inr: "4999.00",
  per_student_price_inr: null,
  included_students: null,
  gst_rate: "18.00",
  sac_code: "998314",
  trial_days: 30,
  limits: {},
  features: {},
  published_at: "2026-06-01T00:00:00Z",
  created_at: "2026-06-01T00:00:00Z",
  one_time_fee_inr: "0.00",
  description: null,
});
const MONTHLY = "0192f3a4-0000-7000-8000-00000000a001";
const ANNUAL = "0192f3a4-0000-7000-8000-00000000a002";

const SUB = {
  id: "0192f3a4-0000-7000-8000-00000000b001",
  tenant_id: T,
  billing_account_id: "0192f3a4-0000-7000-8000-00000000b002",
  plan_id: MONTHLY,
  pending_plan_id: null,
  status: "active",
  current_period_start: "2026-11-01",
  current_period_end: "2026-11-30",
  trial_ends_at: null,
  past_due_since: null,
  grace_ends_on: null,
  cancel_at_period_end: false,
  cancelled_at: null,
  price_override_inr: null,
  ai_bundle_id: "0192f3a4-0000-7000-8000-00000000d001",
  ai_bundle_from: "2026-12-01",
  version: 2,
};

let stub: BffStub;
beforeEach(() => {
  stub = installBffStub("operator");
  stub.routes["GET /bff/api/v1/platform/me"] = () => Response.json(ME);
  stub.routes["GET /bff/api/v1/platform/tenants"] = () => page([TENANT_SUMMARY]);
  stub.routes["GET /bff/api/v1/platform/plans"] = () =>
    page([PLAN(MONTHLY, "standard", "monthly"), PLAN(ANNUAL, "yearly", "annual")]);
  stub.routes["GET /bff/api/v1/platform/ai-bundles"] = () => page([]);
});
afterEach(uninstallBffStub);

describe("operator console: idempotent creates and plain-language refusals", () => {
  it("FR-PLT-027: a ticket reply sends an Idempotency-Key", async () => {
    const ticket = {
      id: "0192f3a4-0000-7000-8000-00000000f201",
      tenant_id: T,
      number: "SUP-000042",
      ticket_no: 42,
      subject: "Import stuck",
      category: "import",
      channel: "portal",
      priority: "p2",
      status: "open",
      assigned_to: null,
      personal_data_flagged: false,
      first_response_due_at: "2026-09-26T08:30:00Z",
      resolution_due_at: "2026-09-28T08:30:00Z",
      first_responded_at: null,
      resolved_at: null,
      closed_at: null,
      created_at: "2026-09-26T04:30:00Z",
      updated_at: "2026-09-26T04:30:00Z",
      version: 1,
      messages: [],
    };
    const route = `POST /bff/api/v1/platform/support/tickets/${ticket.id}/messages`;
    stub.routes[`GET /bff/api/v1/platform/support/tickets/${ticket.id}`] = () =>
      Response.json(ticket);
    stub.routes[route] = () => Response.json(ticket, { status: 201 });
    const user = userEvent.setup();
    renderWithIntl(<PlatformTicketScreen ticketId={ticket.id} />);
    await user.type(await screen.findByLabelText(pm.support.reply), "Checking the worker logs.");
    await user.click(screen.getByRole("button", { name: pm.support.send }));
    await waitFor(() => expect(stub.callsTo(route)).toHaveLength(1));
    expect(stub.callsTo(route)[0]?.headers.get("idempotency-key")).toBeTruthy();
  });

  it("FR-OPS-004: a break-glass request sends an Idempotency-Key", async () => {
    const route = "POST /bff/api/v1/platform/break-glass-requests";
    stub.routes["GET /bff/api/v1/platform/break-glass-requests"] = () => page([]);
    stub.routes[route] = () =>
      Response.json({ id: "0192f3a4-0000-7000-8000-00000000f102" }, { status: 201 });
    const user = userEvent.setup();
    renderWithIntl(<BreakGlassScreen />);
    await user.click(await screen.findByRole("button", { name: pm.breakGlass.request }));
    const dialog = screen.getByRole("dialog", { name: pm.breakGlass.requestTitle });
    await user.selectOptions(within(dialog).getByLabelText(pm.breakGlass.colSchool), T);
    await user.type(
      within(dialog).getByLabelText(pm.breakGlass.colReason),
      "The school asked for help with an import that failed",
    );
    await user.click(within(dialog).getByRole("button", { name: pm.breakGlass.request }));
    await waitFor(() => expect(stub.callsTo(route)).toHaveLength(1));
    expect(stub.callsTo(route)[0]?.headers.get("idempotency-key")).toBeTruthy();
  });

  it("FR-PLT-013: a plan change refused for the AI bundle (409 ai_bundle_needs_monthly_plan) says why", async () => {
    stub.routes["GET /bff/api/v1/platform/subscriptions"] = () => page([SUB]);
    stub.routes[`POST /bff/api/v1/platform/subscriptions/${SUB.id}/change-plan`] = () =>
      problem(409, "ai_bundle_needs_monthly_plan");
    const user = userEvent.setup();
    renderWithIntl(<SubscriptionsScreen />);
    await user.click(await screen.findByRole("button", { name: pm.subscriptions.changePlan }));
    const dialog = screen.getByRole("dialog", { name: pm.subscriptions.changePlanTitle });
    await user.selectOptions(within(dialog).getByLabelText(pm.subscriptions.newPlan), ANNUAL);
    await user.click(within(dialog).getByRole("button", { name: pm.subscriptions.changePlan }));
    expect(
      await within(dialog).findByText(api.ai_bundle_needs_monthly_plan.title),
    ).toBeInTheDocument();
    expect(within(dialog).queryByText(api.conflict.title)).not.toBeInTheDocument();
  });
});

describe("incident clocks", () => {
  it("FR-DPDP-030: the CERT-In clock moves on while the page stays open", () => {
    vi.useFakeTimers({ toFake: ["Date", "setInterval", "clearInterval"] });
    try {
      vi.setSystemTime(new Date("2026-11-03T10:00:00Z"));
      renderWithIntl(
        <CertInClock
          incident={{
            cert_in_required: true,
            cert_in_reported_at: null,
            cert_in_due_at: "2026-11-03T15:30:00Z",
          }}
        />,
      );
      expect(screen.getByText(/due in 5 h/)).toBeInTheDocument();
      act(() => {
        vi.advanceTimersByTime(6 * 60 * 60_000);
      });
      expect(screen.getByText(/overdue by/)).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("fleet heartbeat age", () => {
  it("FR-PLT-025: the age keeps counting while the page stays open", () => {
    vi.useFakeTimers({ toFake: ["Date", "setInterval", "clearInterval"] });
    try {
      vi.setSystemTime(new Date("2026-11-03T10:00:00Z"));
      renderWithIntl(<HeartbeatPill at="2026-11-03T09:55:00Z" status="healthy" />);
      expect(screen.getByText(/5 min/)).toBeInTheDocument();
      act(() => {
        vi.advanceTimersByTime(30 * 60_000);
      });
      expect(screen.getByText(/35 min/)).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });
});
