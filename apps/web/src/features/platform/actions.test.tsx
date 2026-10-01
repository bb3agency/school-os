import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  CSRF,
  installBffStub,
  page,
  problem,
  uninstallBffStub,
  type BffStub,
} from "@/test/bff-stub";
import { messages, renderWithIntl } from "@/test/render";
import { AnnouncementsScreen } from "./AnnouncementsView";
import { InvoicesScreen, PlansScreen, SubscriptionsScreen } from "./BillingViews";
import { BreakGlassScreen, FleetScreen, FlagsScreen, PlatformAuditScreen } from "./OperationsViews";
import { OperatorsScreen } from "./OperatorsView";
import { SchoolDetailScreen } from "./SchoolDetailView";
import { PlatformTicketScreen } from "./SupportScreens";

/**
 * Control-plane actions against the generated API shapes (FR-PLT-001..030): confirm
 * dialogs, reasons, step-up (428), two-person (409 same_operator), Idempotency-Key and CSRF
 * on writes, one-time secrets. Synthetic data only.
 */

const T = "0192f3a4-0000-7000-8000-000000000001";
const OP = "0192f3a4-0000-7000-8000-0000000000f1";
const pm = messages.en.platform;
const cm = messages.en.common;

const ME = {
  operator_id: OP,
  roles: ["platform_owner"],
  permissions: [
    "platform.tenants.read",
    "platform.tenants.provision",
    "platform.tenants.suspend",
    "platform.tenants.offboard",
    "platform.plans.manage",
    "platform.subscriptions.read",
    "platform.subscriptions.manage",
    "platform.invoices.read",
    "platform.invoices.manage",
    "platform.flags.read",
    "platform.flags.manage",
    "platform.usage.read",
    "platform.fleet.read",
    "platform.fleet.manage",
    "platform.announcements.manage",
    "platform.support.read",
    "platform.support.manage",
    "platform.breakglass.request",
    "platform.breakglass.emergency",
    "platform.operators.manage",
    "platform.audit.read",
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

const SUB = {
  id: "0192f3a4-0000-7000-8000-00000000b001",
  tenant_id: T,
  billing_account_id: "0192f3a4-0000-7000-8000-00000000b002",
  plan_id: "0192f3a4-0000-7000-8000-00000000a001",
  pending_plan_id: null,
  status: "active",
  current_period_start: "2026-09-01",
  current_period_end: "2026-09-30",
  trial_ends_at: null,
  past_due_since: null,
  grace_ends_on: null,
  cancel_at_period_end: false,
  cancelled_at: null,
  price_override_inr: null,
  version: 2,
};

const PLAN = (id: string, code: string, status: string, version = 1) => ({
  id,
  code,
  name: code === "standard" ? "Standard" : "Premium",
  version,
  tier: "shared",
  status,
  billing_period: "monthly",
  pricing_model: "flat",
  base_price_inr: "4999.00",
  per_student_price_inr: null,
  included_students: null,
  gst_rate: "18.00",
  sac_code: "998314",
  trial_days: 30,
  limits: { students: 1500 },
  features: {},
  published_at: status === "draft" ? null : "2026-06-01T00:00:00Z",
  created_at: "2026-06-01T00:00:00Z",
  one_time_fee_inr: code === "standard" ? "15000.00" : "0.00",
  description: code === "standard" ? "Synthetic shared plan wording." : null,
});

const BUNDLE = (id: string, name: string, answers: number, price: string) => ({
  id,
  code: `ai-${name.toLowerCase()}`,
  version: 1,
  name,
  included_answers: answers,
  price_inr: price,
  overage_rate_inr: "1.50",
  status: "published",
  published_at: "2026-10-01T00:00:00Z",
});
const BUNDLES = [
  BUNDLE("0192f3a4-0000-7000-8000-00000000d001", "Lite", 300, "699.00"),
  BUNDLE("0192f3a4-0000-7000-8000-00000000d002", "Standard", 1000, "1499.00"),
  BUNDLE("0192f3a4-0000-7000-8000-00000000d003", "High", 3000, "3499.00"),
];

const DETAIL = {
  tenant_id: T,
  school_name: "Sri Saraswati High School",
  code: "sshs",
  tier: "shared",
  boards: ["CBSE"],
  tenant_status: "active",
  tenant_status_reason: null,
  subscription_status: "active",
  subscription: SUB,
  plan_code: "standard",
  deployment_status: "healthy",
  app_version: null,
  last_heartbeat_at: null,
  created_at: "2026-06-01T04:30:00Z",
  counts: { users: 12, active_memberships: 10, academic_years: 1, sections: 24 },
  open_tickets: 1,
  flag_overrides: {},
  invoices: [],
  offboard_requested_at: null,
  offboard_approved_at: null,
};

const INVOICE = {
  id: "0192f3a4-0000-7000-8000-00000000c001",
  tenant_id: T,
  subscription_id: SUB.id,
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
  stub.routes["GET /bff/api/v1/platform/tenants"] = () => page([TENANT_SUMMARY]);
  stub.routes["GET /bff/api/v1/platform/plans"] = () =>
    page([
      PLAN("0192f3a4-0000-7000-8000-00000000a001", "standard", "published"),
      PLAN("0192f3a4-0000-7000-8000-00000000a002", "premium", "published"),
      PLAN("0192f3a4-0000-7000-8000-00000000a003", "premium", "draft", 2),
    ]);
  stub.routes["GET /bff/api/v1/platform/ai-bundles"] = () => page(BUNDLES);
});
afterEach(uninstallBffStub);

const bodyOf = (key: string, index = 0) =>
  JSON.parse(stub.callsTo(key)[index]?.body ?? "null") as Record<string, unknown>;

describe("school detail actions (FR-PLT-004..005, SEC-027)", () => {
  beforeEach(() => {
    stub.routes[`GET /bff/api/v1/platform/tenants/${T}`] = () => Response.json(DETAIL);
  });

  it("suspend needs a reason of 10+ characters and posts it with CSRF", async () => {
    stub.routes[`POST /bff/api/v1/platform/tenants/${T}/suspend`] = () =>
      Response.json({ ...DETAIL, tenant_status: "suspended" });
    const user = userEvent.setup();
    renderWithIntl(<SchoolDetailScreen schoolId={T} tab="overview" />);
    await user.click(await screen.findByRole("button", { name: pm.schoolDetail.suspend }));
    const dialog = screen.getByRole("dialog", { name: pm.schoolDetail.suspendDialogTitle });
    const reason = within(dialog).getByLabelText(cm.reason);
    await user.type(reason, "abuse");
    await user.click(within(dialog).getByRole("button", { name: pm.schoolDetail.suspendConfirm }));
    expect(reason).toHaveAttribute("aria-invalid", "true");
    expect(reason).toHaveAccessibleDescription(
      `${cm.reasonHint} ${messages.en.validation.reasonTooShort}`,
    );
    expect(stub.callsTo(`POST /bff/api/v1/platform/tenants/${T}/suspend`)).toHaveLength(0);

    await user.type(reason, " reported by the school in writing");
    await user.click(within(dialog).getByRole("button", { name: pm.schoolDetail.suspendConfirm }));
    await waitFor(() => expect(dialog).not.toHaveAttribute("open"));
    const [call] = stub.callsTo(`POST /bff/api/v1/platform/tenants/${T}/suspend`);
    expect(call?.headers.get("x-csrf-token")).toBe(CSRF);
    expect(JSON.parse(call?.body ?? "{}")).toEqual({
      reason: "abuse reported by the school in writing",
    });
  });

  it("the second offboarding step by the same operator explains the two-person rule (409)", async () => {
    stub.routes[`GET /bff/api/v1/platform/tenants/${T}`] = () =>
      Response.json({ ...DETAIL, offboard_requested_at: "2026-09-20T04:30:00Z" });
    stub.routes[`POST /bff/api/v1/platform/tenants/${T}/offboarding:approve`] = () =>
      problem(409, "same_operator");
    const user = userEvent.setup();
    renderWithIntl(<SchoolDetailScreen schoolId={T} tab="overview" />, "te");
    expect(
      await screen.findByText(messages.te.platform.schoolDetail.offboardPendingTitle),
    ).toBeVisible();
    await user.click(
      screen.getByRole("button", { name: messages.te.platform.schoolDetail.approveOffboard }),
    );
    const dialog = screen.getByRole("dialog");
    await user.click(
      within(dialog).getByRole("button", {
        name: messages.te.platform.schoolDetail.approveOffboard,
      }),
    );
    expect(
      await within(dialog).findByText(messages.te.errors.api.same_operator.title),
    ).toBeInTheDocument();
    expect(within(dialog).getByText(/req_test/)).toBeInTheDocument();
  });

  it("step-up required (428) leaves for re-authentication", async () => {
    stub.routes[`POST /bff/api/v1/platform/tenants/${T}/offboarding`] = () =>
      problem(428, "step_up_required", {
        step_up_url: "/bff/auth/platform/step-up?next=%2Fplatform",
      });
    const user = userEvent.setup();
    renderWithIntl(<SchoolDetailScreen schoolId={T} tab="overview" />);
    await user.click(await screen.findByRole("button", { name: pm.schoolDetail.offboard }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText(pm.schoolDetail.twoPersonNote)).toBeVisible();
    await user.type(
      within(dialog).getByLabelText(pm.schoolDetail.offboardReason),
      "Written request from the society, ref SS/2026/14",
    );
    await user.click(within(dialog).getByRole("button", { name: pm.schoolDetail.offboardConfirm }));
    await waitFor(() =>
      expect(stub.navigate).toHaveBeenCalledWith("/bff/auth/platform/step-up?next=%2Fplatform"),
    );
  });

  it("billing account: GSTIN must start with the state code (client-side, like the API)", async () => {
    stub.routes[`GET /bff/api/v1/platform/tenants/${T}/billing-account`] = () =>
      problem(404, "not_found");
    const user = userEvent.setup();
    renderWithIntl(<SchoolDetailScreen schoolId={T} tab="overview" />);
    expect(await screen.findByText(pm.billingAccount.none)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: cm.edit }));
    const dialog = screen.getByRole("dialog", { name: pm.billingAccount.editTitle });
    const ba = pm.billingAccount;
    // Fields that are not under test are pasted: typing ~70 characters one key event at a
    // time made this test take 2 s on its own and hit the 5 s timeout when the suite ran
    // under load. The GSTIN, which is under test, is still typed key by key.
    const fill = async (label: string, value: string) => {
      await user.click(within(dialog).getByLabelText(label));
      await user.paste(value);
    };
    await fill(ba.legalName, "Sample Education Society");
    await user.type(within(dialog).getByLabelText(ba.gstin), "36abcde1234f1z5");
    await fill(ba.billingEmail, "accounts@example.org");
    await fill(ba.addressLine1, "1 Main Road");
    await fill(ba.city, "Guntur");
    await fill(ba.postalCode, "522001");
    await user.click(within(dialog).getByRole("button", { name: cm.save }));
    expect(within(dialog).getByLabelText(ba.gstin)).toHaveAccessibleDescription(
      expect.stringContaining(messages.en.validation.gstinStateMismatch),
    );
    await waitFor(() => expect(within(dialog).getByLabelText(ba.gstin)).toHaveFocus());
    expect(stub.callsTo(`PUT /bff/api/v1/platform/tenants/${T}/billing-account`)).toHaveLength(0);

    stub.routes[`PUT /bff/api/v1/platform/tenants/${T}/billing-account`] = () => Response.json({});
    await user.clear(within(dialog).getByLabelText(ba.gstin));
    await user.type(within(dialog).getByLabelText(ba.gstin), "37abcde1234f1z5");
    await user.click(within(dialog).getByRole("button", { name: cm.save }));
    await waitFor(() =>
      expect(stub.callsTo(`PUT /bff/api/v1/platform/tenants/${T}/billing-account`)).toHaveLength(1),
    );
    expect(bodyOf(`PUT /bff/api/v1/platform/tenants/${T}/billing-account`)).toMatchObject({
      gstin: "37ABCDE1234F1Z5",
      state_code: "37",
      postal_code: "522001",
      pan: null,
    });
  });
});

describe("school provisioning state and resume (FR-PLT-002, docs/16 §5.4)", () => {
  const RUN = {
    state: "failed",
    failed_step: "initialise",
    last_error: "unexpected_error",
    attempts: 2,
    in_progress: false,
    resumable: true,
    updated_at: "2026-09-27T04:30:00Z",
  };
  const PROVISIONING = {
    ...DETAIL,
    tenant_status: "provisioning",
    subscription_status: "trialing",
    provisioning: RUN,
  };
  const RESUME = `POST /bff/api/v1/platform/tenants/${T}/provisioning:resume`;
  const ACTIVATE = `POST /bff/api/v1/platform/tenants/${T}/activate`;
  const sd = pm.schoolDetail;

  beforeEach(() => {
    stub.routes[`GET /bff/api/v1/platform/tenants/${T}`] = () => Response.json(PROVISIONING);
  });

  it("explains a failed run in plain language: step, error code for support, attempts", async () => {
    renderWithIntl(<SchoolDetailScreen schoolId={T} tab="overview" />);
    const box = within(await screen.findByRole("region", { name: sd.provisioning.state.failed }));
    expect(
      box.getByText(sd.provisioning.failedAt.replace("{step}", sd.provisioning.steps.initialise)),
    ).toBeVisible();
    expect(
      box.getByText(sd.provisioning.errorCode.replace("{code}", "unexpected_error")),
    ).toBeVisible();
    expect(box.getByText(sd.provisioning.attempts.replace("{count, number}", "2"))).toBeVisible();
    expect(box.getByText(sd.provisioning.resumeHint)).toBeVisible();
    expect(screen.getByRole("button", { name: sd.provisioning.resume })).toBeEnabled();
    expect(screen.getByText(sd.provisioning.label.failed)).toBeVisible();
  });

  it("resume confirms first, then posts with CSRF and reloads the school", async () => {
    let calls = 0;
    stub.routes[`GET /bff/api/v1/platform/tenants/${T}`] = () => {
      calls += 1;
      return Response.json(
        calls === 1
          ? PROVISIONING
          : {
              ...PROVISIONING,
              provisioning: {
                ...RUN,
                state: "completed",
                failed_step: null,
                last_error: null,
                resumable: false,
              },
            },
      );
    };
    stub.routes[RESUME] = () =>
      Response.json({
        tenant_id: T,
        deployment_id: "0192f3a4-0000-7000-8000-00000000d001",
        subscription_id: SUB.id,
        billing_account_id: SUB.billing_account_id,
        tier: "shared",
        tenant_status: "provisioning",
        owner_invite: "created",
      });
    const user = userEvent.setup();
    renderWithIntl(<SchoolDetailScreen schoolId={T} tab="overview" />);
    await user.click(await screen.findByRole("button", { name: sd.provisioning.resume }));
    const dialog = screen.getByRole("dialog", { name: sd.provisioning.resumeTitle });
    expect(dialog).toHaveAccessibleDescription(sd.provisioning.resumeBody);
    expect(within(dialog).getByText(cm.stepUpNote)).toBeVisible();
    expect(stub.callsTo(RESUME)).toHaveLength(0);
    await user.click(within(dialog).getByRole("button", { name: sd.provisioning.resume }));
    await waitFor(() => expect(dialog).not.toHaveAttribute("open"));
    const [call] = stub.callsTo(RESUME);
    expect(call?.headers.get("x-csrf-token")).toBe(CSRF);
    expect(await screen.findByText(sd.provisioning.state.completed)).toBeVisible();
    expect(screen.queryByRole("button", { name: sd.provisioning.resume })).toBeNull();
  });

  it.each([
    ["provisioning_in_progress", 409],
    ["resume_needs_request", 409],
    ["provisioning_failed", 503],
  ] as const)("resume refused with %s is explained in Telugu", async (code, status) => {
    stub.routes[RESUME] = () => problem(status, code);
    const user = userEvent.setup();
    renderWithIntl(<SchoolDetailScreen schoolId={T} tab="overview" />, "te");
    const tsd = messages.te.platform.schoolDetail;
    await user.click(await screen.findByRole("button", { name: tsd.provisioning.resume }));
    const dialog = screen.getByRole("dialog", { name: tsd.provisioning.resumeTitle });
    await user.click(within(dialog).getByRole("button", { name: tsd.provisioning.resume }));
    expect(await within(dialog).findByText(messages.te.errors.api[code].title)).toBeVisible();
    expect(within(dialog).getByText(messages.te.errors.api[code].body)).toBeVisible();
    expect(within(dialog).getByText(/req_test/)).toBeInTheDocument();
  });

  it("step-up required (428) on resume leaves for re-authentication", async () => {
    stub.routes[RESUME] = () =>
      problem(428, "step_up_required", {
        step_up_url: "/bff/auth/platform/step-up?next=%2Fplatform",
      });
    const user = userEvent.setup();
    renderWithIntl(<SchoolDetailScreen schoolId={T} tab="overview" />);
    await user.click(await screen.findByRole("button", { name: sd.provisioning.resume }));
    const dialog = screen.getByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: sd.provisioning.resume }));
    await waitFor(() =>
      expect(stub.navigate).toHaveBeenCalledWith("/bff/auth/platform/step-up?next=%2Fplatform"),
    );
  });

  it("a run another request holds says so and offers no resume", async () => {
    stub.routes[`GET /bff/api/v1/platform/tenants/${T}`] = () =>
      Response.json({
        ...PROVISIONING,
        provisioning: {
          ...RUN,
          state: "registered",
          failed_step: null,
          last_error: null,
          in_progress: true,
          resumable: false,
        },
      });
    renderWithIntl(<SchoolDetailScreen schoolId={T} tab="overview" />);
    expect(await screen.findByText(sd.provisioning.state.registered)).toBeVisible();
    expect(screen.getByText(sd.provisioning.inProgress)).toBeVisible();
    expect(screen.queryByRole("button", { name: sd.provisioning.resume })).toBeNull();
  });

  it("resume is hidden without platform.tenants.provision (UX only; the API checks)", async () => {
    stub.routes["GET /bff/api/v1/platform/me"] = () =>
      Response.json({ ...ME, permissions: ["platform.tenants.read"] });
    renderWithIntl(<SchoolDetailScreen schoolId={T} tab="overview" />);
    expect(await screen.findByText(sd.provisioning.state.failed)).toBeVisible();
    expect(screen.queryByRole("button", { name: sd.provisioning.resume })).toBeNull();
    expect(screen.queryByRole("button", { name: sd.activate })).toBeNull();
  });

  it("go live before setup finishes warns in the dialog and explains 409 provisioning_incomplete", async () => {
    stub.routes[ACTIVATE] = () => problem(409, "provisioning_incomplete");
    const user = userEvent.setup();
    renderWithIntl(<SchoolDetailScreen schoolId={T} tab="overview" />);
    await user.click(await screen.findByRole("button", { name: sd.activate }));
    const dialog = screen.getByRole("dialog", { name: sd.activateTitle });
    expect(within(dialog).getByText(sd.provisioning.activateIncomplete)).toBeVisible();
    await user.click(within(dialog).getByRole("button", { name: sd.activate }));
    expect(
      await within(dialog).findByText(messages.en.errors.api.provisioning_incomplete.title),
    ).toBeVisible();
    expect(
      within(dialog).getByText(messages.en.errors.api.provisioning_incomplete.body),
    ).toBeVisible();
    expect(stub.callsTo(ACTIVATE)).toHaveLength(1);
  });

  it("a finished setup shows no warning and go live has no incomplete note", async () => {
    stub.routes[`GET /bff/api/v1/platform/tenants/${T}`] = () =>
      Response.json({
        ...PROVISIONING,
        provisioning: {
          ...RUN,
          state: "completed",
          failed_step: null,
          last_error: null,
          resumable: false,
        },
      });
    const user = userEvent.setup();
    renderWithIntl(<SchoolDetailScreen schoolId={T} tab="overview" />);
    expect(await screen.findByText(sd.provisioning.state.completed)).toBeVisible();
    expect(screen.queryByText(sd.provisioning.state.failed)).toBeNull();
    await user.click(screen.getByRole("button", { name: sd.activate }));
    const dialog = screen.getByRole("dialog", { name: sd.activateTitle });
    expect(within(dialog).queryByText(sd.provisioning.activateIncomplete)).toBeNull();
  });
});

describe("plans, subscriptions and invoices (FR-PLT-010..019)", () => {
  it("publishes a draft plan version", async () => {
    stub.routes["POST /bff/api/v1/platform/plans/0192f3a4-0000-7000-8000-00000000a003/publish"] =
      () => Response.json(PLAN("0192f3a4-0000-7000-8000-00000000a003", "premium", "published", 2));
    const user = userEvent.setup();
    renderWithIntl(<PlansScreen />);
    await user.click(await screen.findByRole("button", { name: pm.plans.publish }));
    const dialog = screen.getByRole("dialog", { name: "Publish Premium version 2?" });
    await user.click(within(dialog).getByRole("button", { name: pm.plans.publish }));
    await waitFor(() =>
      expect(
        stub.callsTo(
          "POST /bff/api/v1/platform/plans/0192f3a4-0000-7000-8000-00000000a003/publish",
        ),
      ).toHaveLength(1),
    );
  });

  it("changes a subscription's plan to another published plan of the same tier", async () => {
    stub.routes["GET /bff/api/v1/platform/subscriptions"] = () => page([SUB]);
    stub.routes[`POST /bff/api/v1/platform/subscriptions/${SUB.id}/change-plan`] = () =>
      Response.json({ ...SUB, pending_plan_id: "0192f3a4-0000-7000-8000-00000000a002" });
    const user = userEvent.setup();
    renderWithIntl(<SubscriptionsScreen />);
    expect((await screen.findAllByText("Sri Saraswati High School (sshs)")).length).toBeGreaterThan(
      0,
    );
    await user.click(screen.getByRole("button", { name: pm.subscriptions.changePlan }));
    const dialog = screen.getByRole("dialog", { name: pm.subscriptions.changePlanTitle });
    const select = within(dialog).getByLabelText(pm.subscriptions.newPlan);
    // The current plan is disabled; drafts are not offered.
    expect(
      within(select)
        .getAllByRole("option")
        .map((o) => o.textContent),
    ).toEqual([cm.chooseOne, "Standard (standard v1)", "Premium (premium v1)"]);
    await user.selectOptions(select, "0192f3a4-0000-7000-8000-00000000a002");
    await user.click(within(dialog).getByRole("button", { name: pm.subscriptions.changePlan }));
    await waitFor(() =>
      expect(bodyOf(`POST /bff/api/v1/platform/subscriptions/${SUB.id}/change-plan`)).toEqual({
        plan_id: "0192f3a4-0000-7000-8000-00000000a002",
      }),
    );
  });

  it("plans show the one-time fee, the wording and the AI answer bundles (ADR-0038)", async () => {
    renderWithIntl(<PlansScreen />);
    expect(await screen.findByText("₹15,000.00")).toBeInTheDocument();
    expect(screen.getByText("Synthetic shared plan wording.")).toBeInTheDocument();
    const bundles = await screen.findByRole("table", { name: pm.plans.bundlesTitle });
    const standard = within(bundles).getByRole("row", { name: /Standard/ });
    expect(within(standard).getByText("1,000")).toBeInTheDocument();
    expect(within(standard).getByText("₹1,499.00")).toBeInTheDocument();
    expect(within(standard).getByText("₹1.50")).toBeInTheDocument();
    // Answers, never tokens or "unlimited", for schools' bundles.
    expect(bundles.textContent?.toLowerCase()).not.toMatch(/unlimited|token/);
  });

  it("a new plan sends its one-time fee and description", async () => {
    stub.routes["POST /bff/api/v1/platform/plans"] = () =>
      Response.json(PLAN("0192f3a4-0000-7000-8000-00000000a009", "standard", "draft"), {
        status: 201,
      });
    const user = userEvent.setup();
    renderWithIntl(<PlansScreen />);
    await user.click(await screen.findByRole("button", { name: pm.plans.newPlan }));
    const dialog = screen.getByRole("dialog", { name: pm.plans.newPlanTitle });
    await user.type(within(dialog).getByLabelText(pm.plans.code), "shared-pilot");
    await user.type(within(dialog).getByLabelText(pm.plans.colName), "Shared pilot");
    await user.type(within(dialog).getByLabelText(pm.plans.basePrice), "4999.00");
    await user.type(within(dialog).getByLabelText(pm.plans.oneTimeFee), "15000.00");
    await user.type(within(dialog).getByLabelText(pm.plans.planDescription), "Pilot wording.");
    await user.click(within(dialog).getByRole("button", { name: pm.plans.saveDraft }));
    await waitFor(() => expect(stub.callsTo("POST /bff/api/v1/platform/plans")).toHaveLength(1));
    expect(bodyOf("POST /bff/api/v1/platform/plans")).toMatchObject({
      code: "shared-pilot",
      base_price_inr: "4999.00",
      one_time_fee_inr: "15000.00",
      description: "Pilot wording.",
    });
  });

  it("chooses an AI answer bundle for a subscription and shows it (ADR-0038)", async () => {
    stub.routes["GET /bff/api/v1/platform/subscriptions"] = () =>
      page([
        {
          ...SUB,
          ai_bundle_id: "0192f3a4-0000-7000-8000-00000000d001",
          ai_bundle_from: "2026-11-01",
        },
      ]);
    stub.routes[`PUT /bff/api/v1/platform/subscriptions/${SUB.id}/ai-bundle`] = () =>
      Response.json({
        ...SUB,
        ai_bundle_id: "0192f3a4-0000-7000-8000-00000000d002",
        ai_bundle_from: "2026-11-01",
      });
    const user = userEvent.setup();
    renderWithIntl(<SubscriptionsScreen />);
    expect(await screen.findByText("Lite: 300 answers a month")).toBeInTheDocument();
    expect(screen.getByText("One-time fee ₹15,000.00 on the first invoice")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: pm.subscriptions.removeAiBundle })).toBeVisible();
    await user.click(screen.getByRole("button", { name: pm.subscriptions.chooseAiBundle }));
    const dialog = screen.getByRole("dialog", { name: pm.subscriptions.chooseAiBundleTitle });
    const select = within(dialog).getByLabelText(pm.subscriptions.aiBundle);
    expect(
      within(select)
        .getAllByRole("option")
        .map((o) => o.textContent),
    ).toEqual([
      cm.chooseOne,
      "Lite: 300 answers, ₹699.00 a month, ₹1.50 each extra answer",
      "Standard: 1,000 answers, ₹1,499.00 a month, ₹1.50 each extra answer",
      "High: 3,000 answers, ₹3,499.00 a month, ₹1.50 each extra answer",
    ]);
    await user.selectOptions(select, "0192f3a4-0000-7000-8000-00000000d002");
    await user.click(within(dialog).getByRole("button", { name: pm.subscriptions.chooseAiBundle }));
    await waitFor(() =>
      expect(bodyOf(`PUT /bff/api/v1/platform/subscriptions/${SUB.id}/ai-bundle`)).toEqual({
        ai_bundle_id: "0192f3a4-0000-7000-8000-00000000d002",
      }),
    );
  });

  it("removes the AI answer bundle after confirmation", async () => {
    stub.routes["GET /bff/api/v1/platform/subscriptions"] = () =>
      page([
        {
          ...SUB,
          ai_bundle_id: "0192f3a4-0000-7000-8000-00000000d003",
          ai_bundle_from: "2026-11-01",
        },
      ]);
    stub.routes[`DELETE /bff/api/v1/platform/subscriptions/${SUB.id}/ai-bundle`] = () =>
      Response.json({ ...SUB, ai_bundle_id: null, ai_bundle_from: null });
    const user = userEvent.setup();
    renderWithIntl(<SubscriptionsScreen />);
    await user.click(await screen.findByRole("button", { name: pm.subscriptions.removeAiBundle }));
    const dialog = screen.getByRole("dialog", { name: pm.subscriptions.removeAiBundleTitle });
    await user.click(within(dialog).getByRole("button", { name: pm.subscriptions.removeAiBundle }));
    await waitFor(() =>
      expect(
        stub.callsTo(`DELETE /bff/api/v1/platform/subscriptions/${SUB.id}/ai-bundle`),
      ).toHaveLength(1),
    );
  });

  it("offers no AI bundle on an annual plan", async () => {
    stub.routes["GET /bff/api/v1/platform/plans"] = () =>
      page([
        {
          ...PLAN("0192f3a4-0000-7000-8000-00000000a001", "standard", "published"),
          billing_period: "annual",
        },
      ]);
    stub.routes["GET /bff/api/v1/platform/subscriptions"] = () => page([SUB]);
    renderWithIntl(<SubscriptionsScreen />);
    expect(
      await screen.findByRole("button", { name: pm.subscriptions.changePlan }),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: pm.subscriptions.chooseAiBundle }),
      ).not.toBeInTheDocument(),
    );
  });

  it("shows invoice numbers and records a payment with TDS and an Idempotency-Key", async () => {
    stub.routes["GET /bff/api/v1/platform/invoices"] = () =>
      page([
        INVOICE,
        {
          ...INVOICE,
          id: "0192f3a4-0000-7000-8000-00000000c002",
          status: "draft",
          invoice_number: null,
        },
      ]);
    stub.routes[`POST /bff/api/v1/platform/invoices/${INVOICE.id}/payments`] = () =>
      Response.json({}, { status: 201 });
    const user = userEvent.setup();
    renderWithIntl(<InvoicesScreen />);
    expect(
      await screen.findByText("SOS/2026-27/000123", { selector: "span.font-mono" }),
    ).toBeVisible();
    expect(screen.getAllByText(pm.invoices.draftNumber).length).toBeGreaterThan(0);
    await user.click(screen.getByRole("button", { name: pm.invoices.recordPayment }));
    const dialog = screen.getByRole("dialog", { name: pm.invoices.recordPaymentTitle });
    const amount = within(dialog).getByLabelText(pm.invoices.amount);
    expect(amount).toHaveValue("5898.82");
    await user.clear(amount);
    await user.type(amount, "5399.00");
    await user.type(within(dialog).getByLabelText(pm.invoices.tds), "499.82");
    await user.type(within(dialog).getByLabelText(pm.invoices.receivedOn), "2026-09-10");
    await user.type(within(dialog).getByLabelText(pm.invoices.reference), "UTR 123");
    await user.click(within(dialog).getByRole("button", { name: pm.invoices.recordPayment }));
    expect(within(dialog).getByLabelText(pm.invoices.reference)).toHaveAttribute(
      "aria-invalid",
      "true",
    );
    await user.clear(within(dialog).getByLabelText(pm.invoices.reference));
    await user.type(within(dialog).getByLabelText(pm.invoices.reference), "UTR123456");
    await user.click(within(dialog).getByRole("button", { name: pm.invoices.recordPayment }));
    await waitFor(() =>
      expect(
        stub.callsTo(`POST /bff/api/v1/platform/invoices/${INVOICE.id}/payments`),
      ).toHaveLength(1),
    );
    const [call] = stub.callsTo(`POST /bff/api/v1/platform/invoices/${INVOICE.id}/payments`);
    expect(call?.headers.get("idempotency-key")).toMatch(/^[0-9a-f-]{36}$/);
    expect(JSON.parse(call?.body ?? "{}")).toEqual({
      method: "bank_transfer",
      amount_inr: "5399.00",
      tds_inr: "499.82",
      received_on: "2026-09-10",
      reference: "UTR123456",
      notes: null,
    });
  });
});

describe("fleet, flags, audit, operators, announcements, break-glass, support", () => {
  it("rotating a heartbeat key shows it once with copy and a stored confirmation", async () => {
    const deployment = {
      id: "0192f3a4-0000-7000-8000-00000000d001",
      tenant_id: T,
      tenant_code: "sshs",
      school_name: "Sri Saraswati High School",
      tenant_status: "active",
      mode: "dedicated",
      region: "ap-south-1",
      backup_region: "ap-south-2",
      host_ref: null,
      hostname: null,
      custom_domain: "office.sshs.edu.in",
      app_version: "2026.09.1",
      target_version: null,
      last_heartbeat_at: "2026-09-26T04:30:00Z",
      status: "healthy",
      heartbeat_key_id: "hk_1",
      heartbeat_next_key_id: null,
      version: 3,
    };
    stub.routes["GET /bff/api/v1/platform/deployments"] = () => page([deployment]);
    stub.routes["GET /bff/api/v1/platform/fleet/versions"] = () =>
      Response.json([{ version: "2026.09.1", deployments: 1 }]);
    stub.routes[`POST /bff/api/v1/platform/deployments/${deployment.id}/heartbeat-key:rotate`] =
      () =>
        Response.json({
          deployment_id: deployment.id,
          heartbeat_key: "hb-synthetic-rotated",
          heartbeat_key_id: "hk_2",
        });
    const user = userEvent.setup();
    const writeText = vi.fn(async () => undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    renderWithIntl(<FleetScreen />);
    await user.click(await screen.findByRole("button", { name: pm.fleet.rotateKey }));
    const dialog = screen.getByRole("dialog", { name: pm.fleet.rotateKeyTitle });
    await user.click(within(dialog).getByRole("button", { name: pm.fleet.rotateKey }));
    const key = await within(dialog).findByLabelText(pm.fleet.heartbeatKey);
    expect(key).toHaveValue("hb-synthetic-rotated");
    await user.click(within(dialog).getByRole("button", { name: cm.secret.copy }));
    expect(writeText).toHaveBeenCalledWith("hb-synthetic-rotated");
    const done = within(dialog).getByRole("button", { name: cm.done });
    expect(done).toBeDisabled();
    await user.click(within(dialog).getByLabelText(cm.secret.stored));
    await user.click(done);
    await waitFor(() => expect(dialog).not.toHaveAttribute("open"));
    expect(screen.queryByDisplayValue("hb-synthetic-rotated")).toBeNull();
  });

  it("creates a flag with a percentage rollout", async () => {
    stub.routes["GET /bff/api/v1/platform/flags"] = () => page([]);
    stub.routes["PUT /bff/api/v1/platform/flags/ask.citations_v2"] = () => Response.json({});
    const user = userEvent.setup();
    renderWithIntl(<FlagsScreen />);
    await user.click(await screen.findByRole("button", { name: pm.flags.newFlag }));
    const dialog = screen.getByRole("dialog");
    await user.type(within(dialog).getByLabelText(pm.flags.colKey), "ask.citations_v2");
    await user.click(within(dialog).getByLabelText(pm.flags.enabledLabel));
    await user.type(within(dialog).getByLabelText(pm.flags.colRollout), "25");
    await user.click(within(dialog).getByRole("button", { name: cm.save }));
    await waitFor(() =>
      expect(bodyOf("PUT /bff/api/v1/platform/flags/ask.citations_v2")).toEqual({
        enabled: true,
        description: null,
        rollout_percent: 25,
      }),
    );
  });

  it("audit: verifies the chain and downloads CSV with Accept: text/csv", async () => {
    stub.routes["GET /bff/api/v1/platform/audit/events"] = (request) =>
      request.headers.get("accept") === "text/csv"
        ? new Response("seq,action\n1,tenant.provisioned\n", {
            headers: { "content-type": "text/csv" },
          })
        : page([]);
    stub.routes["POST /bff/api/v1/platform/audit/verify"] = () =>
      Response.json(
        {
          ok: false,
          checked: 41,
          first_bad_seq: 17,
          reason: "hash",
          job_id: "0192f3a4-0000-7000-8000-00000000e001",
        },
        { status: 202 },
      );
    const createObjectURL = vi.fn(() => "blob:csv");
    Object.assign(URL, { createObjectURL, revokeObjectURL: vi.fn() });
    const user = userEvent.setup();
    renderWithIntl(
      <PlatformAuditScreen filters={{ action: "tenant.provisioned", from: "2026-09-01" }} />,
    );
    await user.click(screen.getByRole("button", { name: pm.audit.verify }));
    expect(await screen.findByText(pm.audit.verifyFailed)).toBeInTheDocument();
    expect(
      screen.getByText(
        "The first bad entry is number 17. Report this to the platform owner at once.",
      ),
    ).toBeVisible();

    await user.click(screen.getByRole("button", { name: pm.audit.exportCsv }));
    await waitFor(() => expect(createObjectURL).toHaveBeenCalled());
    const csv = stub.calls.find((c) => c.headers.get("accept") === "text/csv");
    expect(csv?.url.searchParams.get("action")).toBe("tenant.provisioned");
    expect(csv?.url.searchParams.get("from")).toBe("2026-09-01T00:00:00+05:30");
  });

  it("operators: invite needs at least one role and sends an Idempotency-Key", async () => {
    stub.routes["GET /bff/api/v1/platform/operators"] = () => page([]);
    stub.routes["POST /bff/api/v1/platform/operators"] = () => Response.json({}, { status: 201 });
    const user = userEvent.setup();
    renderWithIntl(<OperatorsScreen />);
    await user.click(screen.getByRole("button", { name: pm.operators.invite }));
    const dialog = screen.getByRole("dialog", { name: pm.operators.inviteDialogTitle });
    await user.type(within(dialog).getByLabelText(pm.operators.inviteName), "Ravi Kumar");
    await user.type(
      within(dialog).getByLabelText(pm.operators.inviteEmail),
      "ravi@schoolos.example",
    );
    await user.type(within(dialog).getByLabelText(pm.operators.inviteSubject), "op-ravi");
    await user.click(within(dialog).getByRole("button", { name: pm.operators.inviteSend }));
    expect(within(dialog).getByText(messages.en.validation.chooseRole)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("checkbox", { name: /Support agent/ }));
    await user.click(within(dialog).getByRole("button", { name: pm.operators.inviteSend }));
    await waitFor(() =>
      expect(stub.callsTo("POST /bff/api/v1/platform/operators")).toHaveLength(1),
    );
    const [call] = stub.callsTo("POST /bff/api/v1/platform/operators");
    expect(call?.headers.get("idempotency-key")).toBeTruthy();
    expect(JSON.parse(call?.body ?? "{}")).toEqual({
      display_name: "Ravi Kumar",
      email: "ravi@schoolos.example",
      idp_subject: "op-ravi",
      roles: ["support_agent"],
    });
  });

  it("announcements: both languages required; tier audience and IST times sent as UTC", async () => {
    stub.routes["GET /bff/api/v1/platform/announcements"] = () => page([]);
    stub.routes["POST /bff/api/v1/platform/announcements"] = () =>
      Response.json({}, { status: 201 });
    const user = userEvent.setup();
    // Telugu switched on explicitly (ADR-0036): the Telugu texts are asked for only then.
    renderWithIntl(<AnnouncementsScreen />, { telugu: true });
    const a = pm.announcements;
    const enPanel = screen.getByRole("tabpanel", { name: a.english });
    await user.type(within(enPanel).getByLabelText(a.titleLabel), "Maintenance on Sunday");
    await user.type(
      within(enPanel).getByLabelText(a.bodyLabel),
      "SchoolOS is unavailable 06:00–07:00.",
    );
    await user.click(screen.getByRole("button", { name: a.save }));
    expect(
      await screen.findByText(messages.en.validation.bothLanguages, { selector: "div" }),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: a.telugu }));
    const te = screen.getByRole("tabpanel", { name: a.telugu });
    await user.type(within(te).getByLabelText(a.titleLabel), "ఆదివారం నిర్వహణ");
    await user.type(
      within(te).getByLabelText(a.bodyLabel),
      "06:00–07:00 వరకు SchoolOS అందుబాటులో ఉండదు.",
    );
    await user.selectOptions(screen.getByLabelText(a.audience), "dedicated");
    await user.selectOptions(screen.getByLabelText(a.severity), "maintenance");
    await user.type(screen.getByLabelText(a.startsAt), "2026-09-27T06:00");
    await user.type(screen.getByLabelText(a.endsAt), "2026-09-27T07:00");
    await user.click(screen.getByRole("button", { name: a.save }));
    await waitFor(() =>
      expect(stub.callsTo("POST /bff/api/v1/platform/announcements")).toHaveLength(1),
    );
    expect(bodyOf("POST /bff/api/v1/platform/announcements")).toMatchObject({
      title_te: "ఆదివారం నిర్వహణ",
      audience: "tier",
      audience_tier: "dedicated",
      audience_tenant_ids: [],
      severity: "maintenance",
      starts_at: "2026-09-27T00:30:00.000Z",
      ends_at: "2026-09-27T01:30:00.000Z",
    });
  });

  it("announcements with Telugu switched off: English only; the API's Telugu fields get the English text (ADR-0036)", async () => {
    stub.routes["GET /bff/api/v1/platform/announcements"] = () => page([]);
    stub.routes["POST /bff/api/v1/platform/announcements"] = () =>
      Response.json({}, { status: 201 });
    const user = userEvent.setup();
    const { container } = renderWithIntl(<AnnouncementsScreen />);
    const a = pm.announcements;
    expect(screen.queryByRole("tab", { name: a.telugu })).toBeNull();
    expect(screen.queryByText(a.bothLanguagesHint)).toBeNull();
    expect(container.textContent ?? "").not.toMatch(/Telugu|[\u0C00-\u0C7F]/);
    await user.click(screen.getByRole("button", { name: a.save }));
    expect(await screen.findAllByText(messages.en.validation.required)).not.toHaveLength(0);
    expect(screen.queryByText(messages.en.validation.bothLanguages)).toBeNull();
    await user.type(screen.getByLabelText(a.titleLabel), "Maintenance on Sunday");
    await user.type(screen.getByLabelText(a.bodyLabel), "SchoolOS is unavailable 06:00–07:00.");
    await user.type(screen.getByLabelText(a.startsAt), "2026-09-27T06:00");
    await user.type(screen.getByLabelText(a.endsAt), "2026-09-27T07:00");
    await user.click(screen.getByRole("button", { name: a.save }));
    await waitFor(() =>
      expect(stub.callsTo("POST /bff/api/v1/platform/announcements")).toHaveLength(1),
    );
    expect(bodyOf("POST /bff/api/v1/platform/announcements")).toMatchObject({
      title_en: "Maintenance on Sunday",
      title_te: "Maintenance on Sunday",
      body_en: "SchoolOS is unavailable 06:00–07:00.",
      body_te: "SchoolOS is unavailable 06:00–07:00.",
    });
  });

  it("break-glass: the requester cannot give the second emergency confirmation (409)", async () => {
    stub.routes["GET /bff/api/v1/platform/break-glass-requests"] = () =>
      page([
        {
          id: "0192f3a4-0000-7000-8000-00000000f101",
          tenant_id: T,
          requested_by: OP,
          reason_code: "security_incident",
          reason: "Suspected account compromise reported by principal",
          scope: { access: "read" },
          duration_minutes: 60,
          emergency: true,
          emergency_confirmed_by_1: OP,
          emergency_confirmed_by_2: null,
          status: "requested",
          created_at: "2026-09-26T04:30:00Z",
        },
      ]);
    stub.routes[
      "POST /bff/api/v1/platform/break-glass-requests/0192f3a4-0000-7000-8000-00000000f101/emergency-confirm"
    ] = () => problem(409, "same_operator");
    const user = userEvent.setup();
    renderWithIntl(<BreakGlassScreen />);
    expect(await screen.findByText("1 of 2 confirmations")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: pm.breakGlass.confirmEmergency }));
    const dialog = screen.getByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: pm.breakGlass.confirmEmergency }));
    expect(
      await within(dialog).findByText(messages.en.errors.api.same_operator.body),
    ).toBeVisible();
  });

  it("support: operator reply as internal note, with the student-data warning visible", async () => {
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
      messages: [
        {
          id: "0192f3a4-0000-7000-8000-00000000f301",
          author_type: "school_user",
          author_id: "0192f3a4-0000-7000-8000-0000000000d1",
          body: "Batch 3 does not finish.",
          internal_note: false,
          created_at: "2026-09-26T04:30:00Z",
        },
      ],
    };
    stub.routes[`GET /bff/api/v1/platform/support/tickets/${ticket.id}`] = () =>
      Response.json(ticket);
    stub.routes[`POST /bff/api/v1/platform/support/tickets/${ticket.id}/messages`] = () =>
      Response.json(ticket, { status: 201 });
    stub.routes[`PATCH /bff/api/v1/platform/support/tickets/${ticket.id}`] = () =>
      Response.json(ticket);
    const user = userEvent.setup();
    renderWithIntl(<PlatformTicketScreen ticketId={ticket.id} />);
    expect(await screen.findByText("Batch 3 does not finish.")).toBeInTheDocument();
    expect(screen.getByText(messages.en.support.piiWarningTitle)).toBeVisible();
    await user.type(screen.getByLabelText(pm.support.reply), "Checking the worker logs.");
    await user.click(screen.getByLabelText(pm.support.internalNote));
    await user.click(screen.getByRole("button", { name: pm.support.send }));
    await waitFor(() =>
      expect(bodyOf(`POST /bff/api/v1/platform/support/tickets/${ticket.id}/messages`)).toEqual({
        body: "Checking the worker logs.",
        internal_note: true,
      }),
    );
    await user.selectOptions(screen.getByLabelText(pm.support.assignee), "me");
    await user.selectOptions(screen.getByLabelText(pm.support.colStatus), "in_progress");
    await user.click(screen.getByRole("button", { name: cm.save }));
    await waitFor(() =>
      expect(stub.callsTo(`PATCH /bff/api/v1/platform/support/tickets/${ticket.id}`)).toHaveLength(
        1,
      ),
    );
    const [patch] = stub.callsTo(`PATCH /bff/api/v1/platform/support/tickets/${ticket.id}`);
    expect(patch?.headers.get("if-match")).toBe('"1"');
    expect(JSON.parse(patch?.body ?? "{}")).toEqual({
      status: "in_progress",
      priority: "p2",
      assigned_to: OP,
      personal_data_flagged: false,
    });
  });
});
