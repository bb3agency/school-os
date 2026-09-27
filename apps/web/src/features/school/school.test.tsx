import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ChooseSchoolView } from "@/features/auth/ChooseSchoolView";
import { CheckInvitationsButton } from "@/features/auth/CheckInvitationsButton";
import {
  CSRF,
  installBffStub,
  page,
  problem,
  uninstallBffStub,
  type BffStub,
} from "@/test/bff-stub";
import { messages, renderWithIntl } from "@/test/render";
import { AnnouncementBanner, resetDismissedAnnouncements } from "./AnnouncementBanner";
import { BillingScreen } from "./BillingView";
import { SupportScreen, SupportTicketScreen } from "./SupportScreens";

/** School-side screens (US-1204, FR-PLT-026, FR-PLT-027, FR-IAM-013). Synthetic data only. */

const T = "0192f3a4-0000-7000-8000-000000000001";
const sm = messages.en.school;
let stub: BffStub;

beforeEach(() => {
  stub = installBffStub("staff");
  resetDismissedAnnouncements();
});
afterEach(uninstallBffStub);

describe("Plan and billing (FR-PLT-030)", () => {
  it("shows the plan, usage against limits and invoices", async () => {
    stub.routes["GET /bff/api/v1/tenant/billing"] = () =>
      Response.json({
        available: true,
        plan_code: "standard",
        plan_name: "Standard",
        tier: "shared",
        billing_period: "monthly",
        status: "past_due",
        current_period_start: "2026-09-01",
        current_period_end: "2026-09-30",
        trial_ends_at: null,
        past_due_since: "2026-09-16",
        grace_ends_on: "2026-09-30",
        cancel_at_period_end: false,
        usage_date: "2026-09-25",
        usage: [
          { metric: "students", used: "1210", limit: "1500", percent: 80 },
          { metric: "storage_gb", used: "3.25", limit: null, percent: null },
          { metric: "ai_budget_inr", used: "812.50", limit: "1000", percent: 81 },
        ],
        amount_due_inr: "5898.82",
      });
    stub.routes["GET /bff/api/v1/tenant/billing/invoices"] = () =>
      page([
        {
          invoice_id: "0192f3a4-0000-7000-8000-00000000c001",
          invoice_number: "SOS/2026-27/000123",
          period_start: "2026-09-01",
          period_end: "2026-09-30",
          issue_date: "2026-09-01",
          due_date: "2026-09-15",
          total_inr: "5898.82",
          amount_due_inr: "5898.82",
          status: "issued",
        },
      ]);
    renderWithIntl(<BillingScreen />);
    expect(await screen.findByText("Standard")).toBeInTheDocument();
    expect(screen.getByText(sm.billing.pastDueTitle)).toBeInTheDocument();
    expect(screen.getByText("1,210 of 1,500", { selector: "span" })).toBeInTheDocument();
    expect(screen.getByText("3.25 GB (no limit)")).toBeInTheDocument();
    expect(screen.getByText("₹812.50 of ₹1,000.00", { selector: "span" })).toBeInTheDocument();
    expect(screen.getAllByRole("meter")).toHaveLength(2);
    expect(await screen.findByText("SOS/2026-27/000123")).toBeInTheDocument();
  });

  it("dedicated hosts: says invoices come by email", async () => {
    stub.routes["GET /bff/api/v1/tenant/billing"] = () => Response.json({ available: false });
    stub.routes["GET /bff/api/v1/tenant/billing/invoices"] = () => page([]);
    renderWithIntl(<BillingScreen />, "te");
    expect(await screen.findByText(messages.te.school.billing.planUnavailable)).toBeInTheDocument();
  });

  it("explains a missing permission (403)", async () => {
    stub.routes["GET /bff/api/v1/tenant/billing"] = () => problem(403, "forbidden");
    stub.routes["GET /bff/api/v1/tenant/billing/invoices"] = () => problem(403, "forbidden");
    renderWithIntl(<BillingScreen />);
    expect((await screen.findAllByText(messages.en.errors.load.forbidden)).length).toBeGreaterThan(
      0,
    );
  });
});

describe("announcements banner (FR-PLT-026)", () => {
  const item = {
    id: "0192f3a4-0000-7000-8000-00000000a501",
    title_en: "Maintenance on Sunday",
    title_te: "ఆదివారం నిర్వహణ",
    body_en: "SchoolOS is unavailable from 06:00 to 07:00.",
    body_te: "06:00 నుండి 07:00 వరకు SchoolOS అందుబాటులో ఉండదు.",
    severity: "maintenance",
    starts_at: "2026-09-26T00:00:00Z",
    ends_at: "2026-09-27T01:30:00Z",
  };

  it("shows the Telugu text in Telugu and hides it for the session when dismissed", async () => {
    stub.routes["GET /bff/api/v1/announcements"] = () => Response.json([item]);
    const user = userEvent.setup();
    const { unmount } = renderWithIntl(<AnnouncementBanner />, "te");
    expect(await screen.findByText("ఆదివారం నిర్వహణ")).toBeInTheDocument();
    expect(screen.queryByText("Maintenance on Sunday")).toBeNull();
    await user.click(screen.getByRole("button", { name: /దాచండి/ }));
    expect(screen.queryByText("ఆదివారం నిర్వహణ")).toBeNull();
    unmount();

    renderWithIntl(<AnnouncementBanner />, "en");
    await waitFor(() =>
      expect(stub.callsTo("GET /bff/api/v1/announcements").length).toBeGreaterThan(0),
    );
    expect(screen.queryByText("Maintenance on Sunday")).toBeNull();
  });

  it("stays silent when the API fails (banners never block work)", async () => {
    stub.routes["GET /bff/api/v1/announcements"] = () => problem(500, "internal_error");
    const { container } = renderWithIntl(<AnnouncementBanner />);
    await waitFor(() => expect(stub.callsTo("GET /bff/api/v1/announcements")).toHaveLength(1));
    expect(container).toBeEmptyDOMElement();
  });
});

describe("support (FR-PLT-027)", () => {
  it("opens a ticket with an Idempotency-Key, with the student-data warning visible", async () => {
    stub.routes["GET /bff/api/v1/support/tickets"] = () => page([]);
    stub.routes["POST /bff/api/v1/support/tickets"] = () =>
      Response.json(
        { id: "0192f3a4-0000-7000-8000-00000000f201", number: "SUP-000042" },
        { status: 201 },
      );
    const user = userEvent.setup();
    renderWithIntl(<SupportScreen />);
    expect(screen.getByText(messages.en.support.piiWarningTitle)).toBeVisible();
    await user.click(screen.getByRole("button", { name: sm.support.send }));
    expect(screen.getByLabelText(sm.support.subject)).toHaveAttribute("aria-invalid", "true");
    await user.type(screen.getByLabelText(sm.support.subject), "Import stuck");
    await user.selectOptions(screen.getByLabelText(sm.support.category), "import");
    await user.type(screen.getByLabelText(sm.support.message), "Batch 3 does not finish.");
    await user.click(screen.getByRole("button", { name: sm.support.send }));
    expect(await screen.findByText("Ticket SUP-000042 is open")).toBeInTheDocument();
    const [call] = stub.callsTo("POST /bff/api/v1/support/tickets");
    expect(call?.headers.get("x-csrf-token")).toBe(CSRF);
    expect(call?.headers.get("idempotency-key")).toMatch(/^[0-9a-f-]{36}$/);
    expect(JSON.parse(call?.body ?? "{}")).toEqual({
      subject: "Import stuck",
      category: "import",
      priority: "p3",
      body: "Batch 3 does not finish.",
    });
    expect(screen.getByLabelText(sm.support.subject)).toHaveValue("");
  });

  it("shows the thread and posts a reply", async () => {
    const id = "0192f3a4-0000-7000-8000-00000000f201";
    const ticket = {
      id,
      tenant_id: T,
      number: "SUP-000042",
      ticket_no: 42,
      subject: "Import stuck",
      category: "import",
      channel: "portal",
      priority: "p3",
      status: "waiting_on_school",
      assigned_to: null,
      personal_data_flagged: false,
      first_response_due_at: "2026-09-26T08:30:00Z",
      resolution_due_at: "2026-09-28T08:30:00Z",
      first_responded_at: "2026-09-26T05:00:00Z",
      resolved_at: null,
      closed_at: null,
      created_at: "2026-09-26T04:30:00Z",
      updated_at: "2026-09-26T05:00:00Z",
      version: 2,
      messages: [
        {
          id: "0192f3a4-0000-7000-8000-00000000f302",
          author_type: "operator",
          author_id: null,
          body: "Which batch number?",
          internal_note: false,
          created_at: "2026-09-26T05:00:00Z",
        },
      ],
    };
    stub.routes[`GET /bff/api/v1/support/tickets/${id}`] = () => Response.json(ticket);
    stub.routes[`POST /bff/api/v1/support/tickets/${id}/messages`] = () => Response.json(ticket);
    const user = userEvent.setup();
    renderWithIntl(<SupportTicketScreen ticketId={id} />);
    expect(await screen.findByText("Which batch number?")).toBeInTheDocument();
    expect(screen.getByText(messages.en.support.authorOperator)).toBeInTheDocument();
    await user.type(screen.getByLabelText(sm.support.reply), "Batch 3.");
    await user.click(screen.getByRole("button", { name: sm.support.send }));
    await waitFor(() =>
      expect(
        JSON.parse(
          stub.callsTo(`POST /bff/api/v1/support/tickets/${id}/messages`)[0]?.body ?? "{}",
        ),
      ).toEqual({
        body: "Batch 3.",
      }),
    );
  });

  it("another school's ticket answers 404: 'not found'", async () => {
    const id = "0192f3a4-0000-7000-8000-00000000f299";
    renderWithIntl(<SupportTicketScreen ticketId={id} />, "te");
    expect(await screen.findByText(messages.te.errors.load.not_found)).toBeInTheDocument();
  });
});

describe("school picker (FR-IAM-013)", () => {
  const schools = [
    { tenant_id: T, name: "Sri Saraswati High School", code: "sshs", status: "active" },
    {
      tenant_id: "0192f3a4-0000-7000-8000-000000000002",
      name: "Vidya Nilayam",
      code: "vn",
      status: "suspended",
    },
  ];

  it("chooses a school with CSRF, then continues to next (keyboard only)", async () => {
    stub.routes["POST /bff/auth/active-tenant"] = () => Response.json({ authenticated: true });
    const navigate = vi.fn();
    const user = userEvent.setup();
    renderWithIntl(
      <ChooseSchoolView schools={schools} next="/en/settings/users" navigate={navigate} />,
    );
    const list = screen.getByRole("list", { name: messages.en.chooseSchool.listLabel });
    const [open, suspended] = within(list).getAllByRole("button");
    expect(suspended).toBeDisabled();
    expect(within(list).getByText(messages.en.chooseSchool.suspendedHint)).toBeVisible();
    await user.tab();
    expect(open).toHaveFocus();
    await user.keyboard("{Enter}");
    await waitFor(() => expect(navigate).toHaveBeenCalledWith("/en/settings/users"));
    const [call] = stub.callsTo("POST /bff/auth/active-tenant");
    expect(call?.headers.get("x-csrf-token")).toBe(CSRF);
    expect(JSON.parse(call?.body ?? "{}")).toEqual({ tenant_id: T });
  });

  it("explains when the school cannot be opened", async () => {
    stub.routes["POST /bff/auth/active-tenant"] = () => problem(403, "tenant_not_available");
    const navigate = vi.fn();
    const user = userEvent.setup();
    renderWithIntl(<ChooseSchoolView schools={schools} next="/te" navigate={navigate} />, "te");
    await user.click(within(screen.getByRole("list")).getAllByRole("button")[0] as HTMLElement);
    expect(await screen.findByText(messages.te.chooseSchool.errorNotAvailable)).toBeInTheDocument();
    expect(navigate).not.toHaveBeenCalled();
  });

  it("'no access yet': accepts a re-sent invitation, then goes to the picker", async () => {
    stub.routes["POST /bff/api/v1/me/accept-invitations"] = () => Response.json({ accepted: [T] });
    stub.routes["GET /bff/api/v1/me/schools"] = () => Response.json({ data: [schools[0]] });
    const navigate = vi.fn();
    const user = userEvent.setup();
    renderWithIntl(<CheckInvitationsButton navigate={navigate} />);
    await user.click(screen.getByRole("button", { name: messages.en.noAccess.checkAgain }));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith("/en/choose-school"));
    expect(
      stub.callsTo("POST /bff/api/v1/me/accept-invitations")[0]?.headers.get("x-csrf-token"),
    ).toBe(CSRF);
  });

  it("'no access yet': still nothing", async () => {
    stub.routes["POST /bff/api/v1/me/accept-invitations"] = () => Response.json({ accepted: [] });
    stub.routes["GET /bff/api/v1/me/schools"] = () => Response.json({ data: [] });
    const user = userEvent.setup();
    renderWithIntl(<CheckInvitationsButton navigate={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: messages.en.noAccess.checkAgain }));
    expect(await screen.findByText(messages.en.noAccess.stillNone)).toBeInTheDocument();
  });
});
