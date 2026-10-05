import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ChooseSchoolView } from "@/features/auth/ChooseSchoolView";
import { CheckInvitationsButton } from "@/features/auth/CheckInvitationsButton";
import { PendingInvitations } from "@/features/auth/PendingInvitations";
import {
  CSRF,
  installBffStub,
  page,
  problem,
  uninstallBffStub,
  type BffStub,
} from "@/test/bff-stub";
import { me } from "@/test/records-fixtures";
import { intlErrors, messages, renderWithIntl } from "@/test/render";
import { AnnouncementBanner, resetDismissedAnnouncements } from "./AnnouncementBanner";
import { BillingScreen } from "./BillingView";
import { HomeScreen } from "./HomeView";
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

describe("school home (dashboard)", () => {
  const IMPORT = {
    id: "0192f3a4-0000-7000-8000-00000000b001",
    status: "committed",
    source: "admission_register",
    row_count: 412,
    error_count: 0,
    created_at: "2026-09-20T05:00:00Z",
    created_by: "0192f3a4-0000-7000-8000-00000000c001",
    committed_at: "2026-09-20T05:10:00Z",
    reverted_at: null,
  };

  it("shows only numbers the API returns, with work waiting and clear actions", async () => {
    stub.routes["GET /bff/api/v1/me"] = () =>
      Response.json(
        me(["dq.findings.read", "student.identity_change.approve", "import.run", "kb.ask"]),
      );
    stub.routes["GET /bff/api/v1/dq/summary"] = () =>
      Response.json({
        blockers: 12,
        warnings: 30,
        students_with_blockers: 9,
        by_rule: [],
        by_severity: {},
        last_run: null,
        profile_key: null,
      });
    stub.routes["GET /bff/api/v1/change-requests"] = () =>
      Response.json({ data: [{}, {}, {}], next_cursor: null });
    stub.routes["GET /bff/api/v1/imports"] = () => page([IMPORT]);
    renderWithIntl(<HomeScreen />);

    expect(await screen.findByText(/Hello, Office Clerk/)).toBeVisible();
    const kpis = screen.getByRole("region", { name: sm.home.kpi.label });
    expect(
      await within(kpis).findByRole("group", { name: sm.home.kpi.blockers }),
    ).toHaveTextContent("12");
    expect(within(kpis).getByRole("group", { name: sm.home.kpi.warnings })).toHaveTextContent("30");
    expect(within(kpis).getByRole("group", { name: sm.home.changesWaiting })).toHaveTextContent(
      "3",
    );
    expect(await screen.findByText("3 changes are waiting for approval.")).toBeVisible();
    expect(screen.getByRole("link", { name: sm.home.work.checksAction })).toHaveAttribute(
      "href",
      "/findings",
    );
    expect(await screen.findByRole("link", { name: "Admission register" })).toHaveAttribute(
      "href",
      `/imports/${IMPORT.id}`,
    );
    // Only pending requests are counted, from one page.
    const call = stub.callsTo("GET /bff/api/v1/change-requests")[0];
    expect(call?.url.searchParams.get("status")).toBe("pending");
    // The school has imports: no getting-started steps.
    expect(screen.queryByRole("list", { name: sm.home.steps.label })).toBeNull();
    expect(intlErrors).toEqual([]);
  });

  it("without those permissions: no numbers and no API calls, only the getting-started steps", async () => {
    stub.routes["GET /bff/api/v1/me"] = () => Response.json(me(["student.read_basic"]));
    renderWithIntl(<HomeScreen />, "te");
    expect(
      await screen.findByRole("list", { name: messages.te.school.home.steps.label }),
    ).toBeVisible();
    expect(screen.queryByRole("region", { name: messages.te.school.home.kpi.label })).toBeNull();
    expect(stub.callsTo("GET /bff/api/v1/dq/summary")).toHaveLength(0);
    expect(stub.callsTo("GET /bff/api/v1/change-requests")).toHaveLength(0);
    expect(stub.callsTo("GET /bff/api/v1/imports")).toHaveLength(0);
    expect(intlErrors).toEqual([]);
  });

  it("more pending requests than one page reads as '200+', and a failed load shows '—'", async () => {
    stub.routes["GET /bff/api/v1/me"] = () =>
      Response.json(me(["dq.findings.read", "student.identity_change.request"]));
    stub.routes["GET /bff/api/v1/dq/summary"] = () => problem(500, "internal_error");
    stub.routes["GET /bff/api/v1/change-requests"] = () =>
      Response.json({ data: Array.from({ length: 200 }, () => ({})), next_cursor: "next" });
    renderWithIntl(<HomeScreen />);
    const kpis = await screen.findByRole("region", { name: sm.home.kpi.label });
    await waitFor(() =>
      expect(within(kpis).getByRole("group", { name: sm.home.changesWaiting })).toHaveTextContent(
        "200+",
      ),
    );
    await waitFor(() =>
      expect(within(kpis).getByRole("group", { name: sm.home.kpi.blockers })).toHaveTextContent(
        "—",
      ),
    );
  });
});

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

  it("never shows AI tokens to the school; the AI answers card replaces them (ADR-0038)", async () => {
    stub.routes["GET /bff/api/v1/tenant/billing"] = () =>
      Response.json({
        available: true,
        plan_code: "standard",
        plan_name: "Standard",
        tier: "shared",
        billing_period: "monthly",
        status: "active",
        current_period_start: "2026-09-01",
        current_period_end: "2026-09-30",
        trial_ends_at: null,
        past_due_since: null,
        grace_ends_on: null,
        cancel_at_period_end: false,
        usage_date: "2026-09-25",
        usage: [
          { metric: "students", used: "1210", limit: "1500", percent: 80 },
          { metric: "ai_tokens_month", used: "420000", limit: "1000000", percent: 42 },
        ],
        amount_due_inr: "0.00",
      });
    stub.routes["GET /bff/api/v1/tenant/billing/invoices"] = () => page([]);
    renderWithIntl(<BillingScreen />);
    expect(await screen.findByText("1,210 of 1,500", { selector: "span" })).toBeInTheDocument();
    expect(screen.queryByText(/AI tokens/)).toBeNull();
    expect(screen.queryByText(/420,000/)).toBeNull();
    expect(screen.getAllByRole("meter")).toHaveLength(1);
  });

  describe("AI answers card (ADR-0038)", () => {
    const base = {
      available: true,
      plan_code: "shared",
      plan_name: "Shared",
      tier: "shared",
      billing_period: "monthly",
      status: "active",
      current_period_start: "2026-10-01",
      current_period_end: "2026-10-31",
      trial_ends_at: null,
      past_due_since: null,
      grace_ends_on: null,
      cancel_at_period_end: false,
      usage_date: null,
      usage: [],
      amount_due_inr: "0.00",
    };
    const bundle = {
      code: "ai-standard",
      name: "Standard",
      included_answers: 1000,
      price_inr: "1499.00",
      overage_rate_inr: "1.50",
      counts_from: "2026-10-01",
      month_start: "2026-10-01",
      answers_used: 412,
      answers_counted_to: "2026-10-03",
    };
    const ai = sm.billing.ai;

    function serve(body: unknown) {
      stub.routes["GET /bff/api/v1/tenant/billing"] = () => Response.json(body);
      stub.routes["GET /bff/api/v1/tenant/billing/invoices"] = () => page([]);
    }

    it("shows the bundle, included answers, this month's meter and the prices", async () => {
      serve({ ...base, ai_bundle: bundle });
      renderWithIntl(<BillingScreen />);
      const card = (await screen.findByRole("heading", { name: ai.title })).closest("section");
      expect(card).not.toBeNull();
      const scope = within(card as HTMLElement);
      expect(scope.getByText("Standard bundle")).toBeInTheDocument();
      expect(scope.getByText("1,000 a month")).toBeInTheDocument();
      expect(scope.getByText("₹1,499.00 a month plus GST")).toBeInTheDocument();
      expect(scope.getByText("₹1.50 plus GST")).toBeInTheDocument();
      const meter = scope.getByRole("meter", { name: ai.thisMonth });
      expect(meter).toHaveAttribute("value", "412");
      expect(meter).toHaveAttribute("max", "1000");
      expect(scope.getByText("412 of 1,000", { selector: "span" })).toBeInTheDocument();
      expect(scope.getByText(/^Counted up to/)).toBeInTheDocument();
      expect(scope.queryByText(ai.none)).toBeNull();
    });

    it("before the bundle counts: no meter, says when counting starts", async () => {
      serve({
        ...base,
        ai_bundle: {
          ...bundle,
          counts_from: "2026-11-01",
          answers_used: null,
          answers_counted_to: null,
        },
      });
      renderWithIntl(<BillingScreen />);
      const card = (await screen.findByRole("heading", { name: ai.title })).closest("section");
      const scope = within(card as HTMLElement);
      expect(scope.queryByRole("meter")).toBeNull();
      expect(scope.getByText(/^Answers count against this bundle from/)).toBeInTheDocument();
    });

    it("no bundle: plain text telling the school how to get one", async () => {
      serve({ ...base, ai_bundle: null });
      renderWithIntl(<BillingScreen />);
      expect(await screen.findByText(ai.none)).toBeInTheDocument();
      expect(ai.none).toBe("No AI answer bundle. Ask SchoolOS support to add one.");
      expect(screen.queryByRole("meter")).toBeNull();
    });

    it("dedicated hosts (no billing data): no AI answers card", async () => {
      serve({ available: false });
      renderWithIntl(<BillingScreen />);
      expect(await screen.findByText(sm.billing.planUnavailable)).toBeInTheDocument();
      expect(screen.queryByRole("heading", { name: ai.title })).toBeNull();
    });
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
      <ChooseSchoolView schools={schools} next="/settings/users" navigate={navigate} />,
    );
    const list = screen.getByRole("list", { name: messages.en.chooseSchool.listLabel });
    const [open, suspended] = within(list).getAllByRole("button");
    expect(suspended).toBeDisabled();
    expect(within(list).getByText(messages.en.chooseSchool.suspendedHint)).toBeVisible();
    await user.tab();
    expect(open).toHaveFocus();
    await user.keyboard("{Enter}");
    await waitFor(() => expect(navigate).toHaveBeenCalledWith("/settings/users"));
    const [call] = stub.callsTo("POST /bff/auth/active-tenant");
    expect(call?.headers.get("x-csrf-token")).toBe(CSRF);
    expect(JSON.parse(call?.body ?? "{}")).toEqual({ tenant_id: T });
  });

  it("explains when the school cannot be opened", async () => {
    stub.routes["POST /bff/auth/active-tenant"] = () => problem(403, "tenant_not_available");
    const navigate = vi.fn();
    const user = userEvent.setup();
    renderWithIntl(<ChooseSchoolView schools={schools} next="/" navigate={navigate} />, "te");
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
    await waitFor(() => expect(navigate).toHaveBeenCalledWith("/choose-school"));
    expect(
      stub.callsTo("POST /bff/api/v1/me/accept-invitations")[0]?.headers.get("x-csrf-token"),
    ).toBe(CSRF);
  });

  it("invitations to an existing account: accept goes to the picker, decline removes it (DL-09)", async () => {
    const A = "0192f3a4-0000-7000-8000-00000000c001";
    const B = "0192f3a4-0000-7000-8000-00000000c002";
    stub.routes["GET /bff/api/v1/me/invitations"] = () =>
      Response.json({
        data: [
          {
            membership_id: A,
            tenant_id: T,
            school_name: "Synthetic Hill School",
            roles: ["teacher"],
            invited_at: "2026-10-01T04:30:00Z",
            expires_at: "2026-10-31T04:30:00Z",
          },
          {
            membership_id: B,
            tenant_id: T,
            school_name: "Synthetic Lake School",
            roles: ["teacher"],
            invited_at: "2026-10-01T04:30:00Z",
            expires_at: "2026-10-31T04:30:00Z",
          },
        ],
      });
    stub.routes[`POST /bff/api/v1/me/invitations/${B}/decline`] = () =>
      Response.json({ tenant_id: T, membership_id: B, status: "removed" });
    stub.routes[`POST /bff/api/v1/me/invitations/${A}/accept`] = () =>
      Response.json({ tenant_id: T, membership_id: A, status: "active" });
    const navigate = vi.fn();
    const user = userEvent.setup();
    renderWithIntl(<PendingInvitations navigate={navigate} />);
    await user.click(
      await screen.findByRole("button", {
        name: "Decline the invitation from Synthetic Lake School",
      }),
    );
    await waitFor(() => expect(screen.queryByText("Synthetic Lake School")).toBeNull());
    expect(navigate).not.toHaveBeenCalled();
    await user.click(
      screen.getByRole("button", { name: "Accept the invitation from Synthetic Hill School" }),
    );
    await waitFor(() => expect(navigate).toHaveBeenCalledWith("/choose-school"));
    expect(
      stub.callsTo(`POST /bff/api/v1/me/invitations/${A}/accept`)[0]?.headers.get("x-csrf-token"),
    ).toBe(CSRF);
  });

  it("no open invitations: nothing is shown (DL-09)", async () => {
    stub.routes["GET /bff/api/v1/me/invitations"] = () => Response.json({ data: [] });
    const { container } = renderWithIntl(<PendingInvitations navigate={vi.fn()} />);
    await waitFor(() => expect(stub.callsTo("GET /bff/api/v1/me/invitations")).toHaveLength(1));
    expect(container).toBeEmptyDOMElement();
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
