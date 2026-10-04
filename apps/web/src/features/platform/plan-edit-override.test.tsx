import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
  CSRF,
  installBffStub,
  page,
  problem,
  uninstallBffStub,
  type BffStub,
} from "@/test/bff-stub";
import { formatDate } from "@/lib/format";
import { messages, renderWithIntl } from "@/test/render";
import { PlansScreen, SubscriptionsScreen } from "./BillingViews";
import { SchoolDetailScreen } from "./SchoolDetailView";

/**
 * Operator screens for two built API actions (owner decision 2026-10-04; docs/16 §5.6, §5.7):
 * edit a draft plan (PATCH /platform/plans/{id}, FR-PLT-010) and set or clear a school's
 * negotiated price (PUT/DELETE /platform/subscriptions/{id}/price-override, FR-PLT-013).
 * Both need a permission with step-up MFA (`platform.plans.manage`,
 * `platform.subscriptions.manage`); neither is a two-person action, and neither route takes
 * If-Match or Idempotency-Key. Synthetic data only.
 */

const T = "0192f3a4-0000-7000-8000-000000000001";
const OP = "0192f3a4-0000-7000-8000-0000000000f1";
const DRAFT_ID = "0192f3a4-0000-7000-8000-00000000a003";
const pm = messages.en.platform;
const cm = messages.en.common;
const em = messages.en.errors;

const ALL = [
  "platform.tenants.read",
  "platform.plans.manage",
  "platform.subscriptions.read",
  "platform.subscriptions.manage",
  "platform.invoices.read",
];
const me = (permissions: string[]) => ({
  operator_id: OP,
  roles: ["billing_admin"],
  permissions,
  step_up_fresh: true,
});

const PLAN = (id: string, code: string, status: string, version = 1, extra = {}) => ({
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
  one_time_fee_inr: "15000.00",
  description: "Synthetic plan wording.",
  row_version: 3,
  ...extra,
});

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
  price_override_inr: null as string | null,
  override_reason: null as string | null,
  ai_bundle_id: null,
  ai_bundle_from: null,
  version: 2,
};

const DETAIL = (sub: typeof SUB) => ({
  tenant_id: T,
  school_name: "Sri Saraswati High School",
  code: "sshs",
  tier: "shared",
  boards: ["CBSE"],
  tenant_status: "active",
  tenant_status_reason: null,
  subscription_status: sub.status,
  subscription: sub,
  plan_code: "standard",
  deployment_status: "healthy",
  app_version: null,
  last_heartbeat_at: null,
  created_at: "2026-06-01T04:30:00Z",
  counts: { users: 12, active_memberships: 10, academic_years: 1, sections: 24 },
  open_tickets: 0,
  flag_overrides: {},
  invoices: [],
  offboard_requested_at: null,
  offboard_approved_at: null,
});

let stub: BffStub;
let permissions: string[];
beforeEach(() => {
  permissions = ALL;
  stub = installBffStub("operator");
  stub.routes["GET /bff/api/v1/platform/me"] = () => Response.json(me(permissions));
  stub.routes["GET /bff/api/v1/platform/tenants"] = () =>
    page([
      {
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
      },
    ]);
  stub.routes["GET /bff/api/v1/platform/plans"] = () =>
    page([
      PLAN("0192f3a4-0000-7000-8000-00000000a001", "standard", "published"),
      PLAN("0192f3a4-0000-7000-8000-00000000a002", "premium", "retired"),
      PLAN(DRAFT_ID, "premium", "draft", 2),
    ]);
  stub.routes["GET /bff/api/v1/platform/ai-bundles"] = () => page([]);
});
afterEach(uninstallBffStub);

const bodyOf = (key: string, index = 0) =>
  JSON.parse(stub.callsTo(key)[index]?.body ?? "null") as Record<string, unknown>;

const PATCH_DRAFT = `PATCH /bff/api/v1/platform/plans/${DRAFT_ID}`;
const OVERRIDE = `/bff/api/v1/platform/subscriptions/${SUB.id}/price-override`;

/* ---------------------------------------------------------------- edit a draft plan */

describe("edit a draft plan (FR-PLT-010, docs/16 §5.6)", () => {
  async function openEdit() {
    const user = userEvent.setup();
    renderWithIntl(<PlansScreen />);
    const row = await screen.findByRole("row", { name: /v2/ });
    await user.click(within(row).getByRole("button", { name: pm.plans.edit }));
    const dialog = screen.getByRole("dialog", {
      name: pm.plans.editTitle.replace("{name}", "Premium").replace("{version}", "2"),
    });
    return { user, dialog };
  }

  it("only draft plans offer Edit; published and retired plans are frozen", async () => {
    renderWithIntl(<PlansScreen />);
    const draft = await screen.findByRole("row", { name: /v2/ });
    expect(within(draft).getByRole("button", { name: pm.plans.edit })).toBeVisible();
    for (const row of screen.getAllByRole("row").filter((r) => r !== draft)) {
      expect(within(row).queryByRole("button", { name: pm.plans.edit })).toBeNull();
    }
  });

  it("is hidden from an operator without platform.plans.manage (UX only; the API checks)", async () => {
    permissions = ["platform.tenants.read", "platform.subscriptions.read"];
    renderWithIntl(<PlansScreen />);
    expect(await screen.findByRole("row", { name: /v2/ })).toBeVisible();
    expect(screen.queryByRole("button", { name: pm.plans.edit })).toBeNull();
  });

  it("prefills the draft, shows what cannot change, and patches the edited fields with CSRF", async () => {
    stub.routes[PATCH_DRAFT] = () =>
      Response.json(PLAN(DRAFT_ID, "premium", "draft", 2, { name: "Premium 2027" }));
    const { user, dialog } = await openEdit();
    // Fixed once created: code, deployment, period, pricing, GST, SAC (a new plan changes them).
    expect(within(dialog).getByText(pm.plans.fixedFieldsNote)).toBeVisible();
    expect(within(dialog).queryByLabelText(pm.plans.code)).toBeNull();
    expect(within(dialog).queryByLabelText(pm.plans.gstRate)).toBeNull();
    // Flat plan: no per-student price.
    expect(within(dialog).queryByLabelText(pm.plans.perStudentPrice)).toBeNull();
    expect(within(dialog).getByText(cm.stepUpNote)).toBeVisible();

    const name = within(dialog).getByLabelText(pm.plans.colName);
    expect(name).toHaveValue("Premium");
    await user.clear(name);
    await user.type(name, "Premium 2027");
    const price = within(dialog).getByLabelText(pm.plans.basePrice);
    expect(price).toHaveValue("4999.00");
    await user.clear(price);
    await user.type(price, "5499.50");
    await user.clear(within(dialog).getByLabelText(pm.plans.planDescription));
    await user.click(within(dialog).getByRole("button", { name: pm.plans.saveChanges }));

    await waitFor(() => expect(dialog).not.toHaveAttribute("open"));
    const [call] = stub.callsTo(PATCH_DRAFT);
    expect(call?.headers.get("x-csrf-token")).toBe(CSRF);
    // If-Match carries the draft's edit counter (row_version), not the catalogue version;
    // the route takes no Idempotency-Key.
    expect(call?.headers.get("if-match")).toBe('"3"');
    expect(call?.headers.get("idempotency-key")).toBeNull();
    expect(bodyOf(PATCH_DRAFT)).toEqual({
      name: "Premium 2027",
      base_price_inr: "5499.50",
      included_students: null,
      trial_days: 30,
      one_time_fee_inr: "15000.00",
      description: null,
      limits: {
        students: 1500,
        staff_users: null,
        storage_gb: null,
        documents: null,
        ai_tokens_month: null,
        ai_budget_inr: null,
      },
    });
    // The list reloads after the change.
    await waitFor(() =>
      expect(stub.callsTo("GET /bff/api/v1/platform/plans").length).toBeGreaterThan(1),
    );
  });

  it("checks amounts before sending (two decimals, rupees)", async () => {
    const { user, dialog } = await openEdit();
    const price = within(dialog).getByLabelText(pm.plans.basePrice);
    await user.clear(price);
    await user.type(price, "4999.999");
    await user.click(within(dialog).getByRole("button", { name: pm.plans.saveChanges }));
    expect(price).toHaveAttribute("aria-invalid", "true");
    expect(price).toHaveAccessibleDescription(
      `${pm.plans.priceHint} ${messages.en.validation.invalidAmount}`,
    );
    expect(stub.callsTo(PATCH_DRAFT)).toHaveLength(0);
  });

  it("a per-student draft needs its per-student price", async () => {
    stub.routes["GET /bff/api/v1/platform/plans"] = () =>
      page([
        PLAN(DRAFT_ID, "premium", "draft", 2, {
          pricing_model: "per_student",
          per_student_price_inr: "40.00",
          included_students: 100,
        }),
      ]);
    stub.routes[PATCH_DRAFT] = () => Response.json(PLAN(DRAFT_ID, "premium", "draft", 2));
    const { user, dialog } = await openEdit();
    const perStudent = within(dialog).getByLabelText(pm.plans.perStudentPrice);
    expect(perStudent).toHaveValue("40.00");
    await user.clear(perStudent);
    await user.click(within(dialog).getByRole("button", { name: pm.plans.saveChanges }));
    expect(perStudent).toHaveAttribute("aria-invalid", "true");
    expect(stub.callsTo(PATCH_DRAFT)).toHaveLength(0);
    await user.type(perStudent, "45");
    await user.click(within(dialog).getByRole("button", { name: pm.plans.saveChanges }));
    await waitFor(() => expect(stub.callsTo(PATCH_DRAFT)).toHaveLength(1));
    expect(bodyOf(PATCH_DRAFT)).toMatchObject({
      per_student_price_inr: "45",
      included_students: 100,
    });
  });

  it("409 plan_published: someone published it meanwhile, says to make a new version", async () => {
    stub.routes[PATCH_DRAFT] = () => problem(409, "plan_published");
    const { user, dialog } = await openEdit();
    await user.click(within(dialog).getByRole("button", { name: pm.plans.saveChanges }));
    expect(await within(dialog).findByText(em.api.plan_published.title)).toBeVisible();
    expect(within(dialog).getByText(em.api.plan_published.body)).toBeVisible();
    expect(within(dialog).getByText(/req_test/)).toBeInTheDocument();
  });

  it("422 field errors land on the field", async () => {
    stub.routes[PATCH_DRAFT] = () =>
      problem(422, "validation_error", {
        errors: [{ field: "base_price_inr", code: "invalid", message_key: "errors.invalid" }],
      });
    const { user, dialog } = await openEdit();
    await user.click(within(dialog).getByRole("button", { name: pm.plans.saveChanges }));
    const price = within(dialog).getByLabelText(pm.plans.basePrice);
    await waitFor(() => expect(price).toHaveAttribute("aria-invalid", "true"));
    expect(price).toHaveAccessibleDescription(`${pm.plans.priceHint} ${em.field.invalid}`);
  });

  it("412 stale: says someone changed it first, reloads the plan, and a reopened form is current", async () => {
    let name = "Premium";
    let rowVersion = 3;
    stub.routes["GET /bff/api/v1/platform/plans"] = () =>
      page([PLAN(DRAFT_ID, "premium", "draft", 2, { name, row_version: rowVersion })]);
    stub.routes[PATCH_DRAFT] = () => problem(412, "precondition_failed");
    const { user, dialog } = await openEdit();
    // Meanwhile another operator renamed the draft.
    name = "Premium (renamed)";
    rowVersion = 4;
    const before = stub.callsTo("GET /bff/api/v1/platform/plans").length;
    await user.click(within(dialog).getByRole("button", { name: pm.plans.saveChanges }));
    expect(await within(dialog).findByText(em.api.precondition_failed.title)).toBeVisible();
    expect(within(dialog).getByText(em.api.precondition_failed.body)).toBeVisible();
    await waitFor(() =>
      expect(stub.callsTo("GET /bff/api/v1/platform/plans").length).toBeGreaterThan(before),
    );
    // Close and edit again: the form shows the other operator's change and its version.
    await user.keyboard("{Escape}");
    await waitFor(() => expect(dialog).not.toHaveAttribute("open"));
    const row = await screen.findByRole("row", { name: /Premium \(renamed\)/ });
    stub.routes[PATCH_DRAFT] = () =>
      Response.json(PLAN(DRAFT_ID, "premium", "draft", 2, { name, row_version: 5 }));
    await user.click(within(row).getByRole("button", { name: pm.plans.edit }));
    const again = screen.getByRole("dialog", {
      name: pm.plans.editTitle.replace("{name}", name).replace("{version}", "2"),
    });
    expect(within(again).getByLabelText(pm.plans.colName)).toHaveValue(name);
    await user.click(within(again).getByRole("button", { name: pm.plans.saveChanges }));
    await waitFor(() => expect(stub.callsTo(PATCH_DRAFT)).toHaveLength(2));
    expect(stub.callsTo(PATCH_DRAFT)[1]?.headers.get("if-match")).toBe('"4"');
  });

  it("403 says the role is missing", async () => {
    stub.routes[PATCH_DRAFT] = () => problem(403, "forbidden");
    const { user, dialog } = await openEdit();
    await user.click(within(dialog).getByRole("button", { name: pm.plans.saveChanges }));
    expect(await within(dialog).findByText(em.api.forbidden.title)).toBeVisible();
  });

  it("428 step-up leaves for re-authentication", async () => {
    stub.routes[PATCH_DRAFT] = () =>
      problem(428, "step_up_required", {
        step_up_url: "/bff/auth/platform/step-up?next=%2Fplatform%2Fplans",
      });
    const { user, dialog } = await openEdit();
    await user.click(within(dialog).getByRole("button", { name: pm.plans.saveChanges }));
    await waitFor(() =>
      expect(stub.navigate).toHaveBeenCalledWith(
        "/bff/auth/platform/step-up?next=%2Fplatform%2Fplans",
      ),
    );
  });

  it("Escape closes the dialog and focus returns to Edit", async () => {
    const { user, dialog } = await openEdit();
    await user.keyboard("{Escape}");
    await waitFor(() => expect(dialog).not.toHaveAttribute("open"));
    const row = screen.getByRole("row", { name: /v2/ });
    await waitFor(() =>
      expect(within(row).getByRole("button", { name: pm.plans.edit })).toHaveFocus(),
    );
  });
});

/* ------------------------------------------------------------ negotiated price */

describe("negotiated price per school (FR-PLT-013, docs/16 §5.7)", () => {
  let current: typeof SUB;
  beforeEach(() => {
    current = { ...SUB };
    stub.routes[`GET /bff/api/v1/platform/tenants/${T}`] = () => Response.json(DETAIL(current));
    stub.routes[`PUT ${OVERRIDE}`] = async (request) => {
      const body = (await request.json()) as { price_override_inr: string; reason: string };
      current = {
        ...current,
        price_override_inr: body.price_override_inr,
        override_reason: body.reason,
      };
      return Response.json(current);
    };
    stub.routes[`DELETE ${OVERRIDE}`] = () => {
      current = { ...current, price_override_inr: null, override_reason: null };
      return Response.json(current);
    };
  });

  const renderTab = () => renderWithIntl(<SchoolDetailScreen schoolId={T} tab="subscription" />);

  it("sets a price with a reason; it shows on the subscription afterwards", async () => {
    const user = userEvent.setup();
    renderTab();
    await user.click(await screen.findByRole("button", { name: pm.subscriptions.setOverride }));
    const dialog = screen.getByRole("dialog", { name: pm.subscriptions.setOverrideTitle });
    expect(within(dialog).getByText(pm.subscriptions.overrideConsequence)).toBeVisible();
    expect(within(dialog).getByText(cm.stepUpNote)).toBeVisible();
    await user.type(within(dialog).getByLabelText(pm.subscriptions.overrideAmount), "3999.00");
    await user.type(
      within(dialog).getByLabelText(cm.reason),
      "Pilot school, price agreed in writing (ref SS/2026/3)",
    );
    await user.click(within(dialog).getByRole("button", { name: pm.subscriptions.setOverride }));
    await waitFor(() => expect(dialog).not.toHaveAttribute("open"));
    const [call] = stub.callsTo(`PUT ${OVERRIDE}`);
    expect(call?.headers.get("x-csrf-token")).toBe(CSRF);
    expect(bodyOf(`PUT ${OVERRIDE}`)).toEqual({
      price_override_inr: "3999.00",
      reason: "Pilot school, price agreed in writing (ref SS/2026/3)",
    });
    expect(await screen.findByText("₹3,999.00")).toBeVisible();
    // The reason shows under the price (docs/16 §5.3).
    expect(screen.getByText("Pilot school, price agreed in writing (ref SS/2026/3)")).toBeVisible();
    // An existing price can be changed or removed.
    expect(screen.getByRole("button", { name: pm.subscriptions.changeOverride })).toBeVisible();
    expect(screen.getByRole("button", { name: pm.subscriptions.removeOverride })).toBeVisible();
  });

  it("refuses a negative amount, more than two decimals and a short reason before sending", async () => {
    const user = userEvent.setup();
    renderTab();
    await user.click(await screen.findByRole("button", { name: pm.subscriptions.setOverride }));
    const dialog = screen.getByRole("dialog", { name: pm.subscriptions.setOverrideTitle });
    const amount = within(dialog).getByLabelText(pm.subscriptions.overrideAmount);
    const confirm = within(dialog).getByRole("button", { name: pm.subscriptions.setOverride });
    await user.type(amount, "-100");
    await user.type(within(dialog).getByLabelText(cm.reason), "short");
    await user.click(confirm);
    expect(amount).toHaveAttribute("aria-invalid", "true");
    expect(amount).toHaveAccessibleDescription(
      `${pm.subscriptions.overrideAmountHint} ${messages.en.validation.invalidAmount}`,
    );
    expect(within(dialog).getByLabelText(cm.reason)).toHaveAttribute("aria-invalid", "true");
    await user.clear(amount);
    await user.type(amount, "3999.555");
    await user.click(confirm);
    expect(amount).toHaveAttribute("aria-invalid", "true");
    expect(stub.callsTo(`PUT ${OVERRIDE}`)).toHaveLength(0);
  });

  it("accepts ₹0, for example a free pilot (owner decision 2026-10-04)", async () => {
    const user = userEvent.setup();
    renderTab();
    await user.click(await screen.findByRole("button", { name: pm.subscriptions.setOverride }));
    const dialog = screen.getByRole("dialog", { name: pm.subscriptions.setOverrideTitle });
    await user.type(within(dialog).getByLabelText(pm.subscriptions.overrideAmount), "0.00");
    await user.type(within(dialog).getByLabelText(cm.reason), "Free pilot for the 2026-27 year");
    await user.click(within(dialog).getByRole("button", { name: pm.subscriptions.setOverride }));
    await waitFor(() => expect(dialog).not.toHaveAttribute("open"));
    // Money (numeric(14,2), >= 0) takes the decimal string as typed.
    expect(bodyOf(`PUT ${OVERRIDE}`)).toEqual({
      price_override_inr: "0.00",
      reason: "Free pilot for the 2026-27 year",
    });
    expect(await screen.findByText("₹0.00")).toBeVisible();
  });

  it("removes the price after confirmation; focus returns when cancelled", async () => {
    current = { ...current, price_override_inr: "3999.00" };
    const user = userEvent.setup();
    renderTab();
    expect(await screen.findByText("₹3,999.00")).toBeVisible();
    const remove = screen.getByRole("button", { name: pm.subscriptions.removeOverride });
    await user.click(remove);
    let dialog = screen.getByRole("dialog", { name: pm.subscriptions.removeOverrideTitle });
    await user.click(within(dialog).getByRole("button", { name: cm.cancel }));
    await waitFor(() => expect(dialog).not.toHaveAttribute("open"));
    await waitFor(() => expect(remove).toHaveFocus());
    expect(stub.callsTo(`DELETE ${OVERRIDE}`)).toHaveLength(0);

    await user.click(remove);
    dialog = screen.getByRole("dialog", { name: pm.subscriptions.removeOverrideTitle });
    await user.click(within(dialog).getByRole("button", { name: pm.subscriptions.removeOverride }));
    await waitFor(() => expect(stub.callsTo(`DELETE ${OVERRIDE}`)).toHaveLength(1));
    expect(stub.callsTo(`DELETE ${OVERRIDE}`)[0]?.headers.get("x-csrf-token")).toBe(CSRF);
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: pm.subscriptions.removeOverride })).toBeNull(),
    );
    expect(screen.queryByText("₹3,999.00")).toBeNull();
  });

  it("an operator without platform.subscriptions.manage sees the price but no actions", async () => {
    permissions = ["platform.tenants.read", "platform.subscriptions.read"];
    current = {
      ...current,
      price_override_inr: "3999.00",
      override_reason: "Agreed with the society in writing",
    };
    renderTab();
    expect(await screen.findByText("₹3,999.00")).toBeVisible();
    expect(screen.getByText("Agreed with the society in writing")).toBeVisible();
    expect(screen.queryByRole("button", { name: pm.subscriptions.setOverride })).toBeNull();
    expect(screen.queryByRole("button", { name: pm.subscriptions.changeOverride })).toBeNull();
    expect(screen.queryByRole("button", { name: pm.subscriptions.removeOverride })).toBeNull();
  });

  it("428 step-up on set leaves for re-authentication", async () => {
    stub.routes[`PUT ${OVERRIDE}`] = () =>
      problem(428, "step_up_required", { step_up_url: "/bff/auth/platform/step-up?next=%2F" });
    const user = userEvent.setup();
    renderTab();
    await user.click(await screen.findByRole("button", { name: pm.subscriptions.setOverride }));
    const dialog = screen.getByRole("dialog", { name: pm.subscriptions.setOverrideTitle });
    await user.type(within(dialog).getByLabelText(pm.subscriptions.overrideAmount), "3999");
    await user.type(within(dialog).getByLabelText(cm.reason), "Agreed in writing with the society");
    await user.click(within(dialog).getByRole("button", { name: pm.subscriptions.setOverride }));
    await waitFor(() =>
      expect(stub.navigate).toHaveBeenCalledWith("/bff/auth/platform/step-up?next=%2F"),
    );
  });

  it("403 on remove says the role is missing", async () => {
    current = { ...current, price_override_inr: "3999.00" };
    stub.routes[`DELETE ${OVERRIDE}`] = () => problem(403, "forbidden");
    const user = userEvent.setup();
    renderTab();
    await user.click(await screen.findByRole("button", { name: pm.subscriptions.removeOverride }));
    const dialog = screen.getByRole("dialog", { name: pm.subscriptions.removeOverrideTitle });
    await user.click(within(dialog).getByRole("button", { name: pm.subscriptions.removeOverride }));
    expect(await within(dialog).findByText(em.api.forbidden.title)).toBeVisible();
  });

  it("the subscriptions list shows a negotiated price under the plan", async () => {
    stub.routes["GET /bff/api/v1/platform/subscriptions"] = () =>
      page([{ ...SUB, price_override_inr: "3999.00" }]);
    renderWithIntl(<SubscriptionsScreen />);
    expect(
      await screen.findByText(pm.subscriptions.overrideValue.replace("{amount}", "₹3,999.00")),
    ).toBeVisible();
  });
});

/* ------------------------------------------------- billing suspension of a subscription */

describe("suspend a subscription for non-payment (FR-PLT-014, docs/16 §5.7, §9)", () => {
  const SUSPEND = `POST /bff/api/v1/platform/subscriptions/${SUB.id}/suspend`;
  const PAST_DUE = {
    ...SUB,
    status: "past_due",
    past_due_since: "2026-09-01",
    grace_ends_on: "2026-09-16",
  } as unknown as typeof SUB;
  let current: typeof SUB;
  beforeEach(() => {
    current = { ...PAST_DUE };
    stub.routes[`GET /bff/api/v1/platform/tenants/${T}`] = () => Response.json(DETAIL(current));
    stub.routes[SUSPEND] = () => {
      current = { ...current, status: "suspended" };
      return Response.json(current);
    };
  });
  const renderTab = () => renderWithIntl(<SchoolDetailScreen schoolId={T} tab="subscription" />);
  const reasonText = "Two invoices unpaid after reminders";

  async function openSuspend() {
    const user = userEvent.setup();
    renderTab();
    await user.click(await screen.findByRole("button", { name: pm.subscriptions.suspend }));
    const dialog = screen.getByRole("dialog", { name: pm.subscriptions.suspendTitle });
    return { user, dialog };
  }

  it("a past-due subscription is suspended with a reason; a shared school is suspended too", async () => {
    const { user, dialog } = await openSuspend();
    // A billing suspension, told apart from the school suspension, with the grace end date.
    expect(
      within(dialog).getByText(
        pm.subscriptions.suspendBody.replace("{date}", formatDate("2026-09-16") ?? ""),
      ),
    ).toBeVisible();
    expect(
      await within(dialog).findByText(pm.subscriptions.suspendSharedConsequence),
    ).toBeVisible();
    expect(within(dialog).getByText(cm.stepUpNote)).toBeVisible();
    // Only a platform owner can approve a suspension inside a board-exam window.
    expect(within(dialog).queryByLabelText(pm.subscriptions.examWindowOverride)).toBeNull();
    const confirm = within(dialog).getByRole("button", { name: pm.subscriptions.suspend });
    await user.click(confirm);
    expect(within(dialog).getByLabelText(cm.reason)).toHaveAttribute("aria-invalid", "true");
    expect(stub.callsTo(SUSPEND)).toHaveLength(0);
    await user.type(within(dialog).getByLabelText(cm.reason), reasonText);
    await user.click(confirm);
    await waitFor(() => expect(dialog).not.toHaveAttribute("open"));
    const [call] = stub.callsTo(SUSPEND);
    expect(call?.headers.get("x-csrf-token")).toBe(CSRF);
    expect(bodyOf(SUSPEND)).toEqual({ reason: reasonText, exam_window_override: false });
    // Suspended: reactivation is offered instead.
    expect(await screen.findByRole("button", { name: pm.subscriptions.reactivate })).toBeVisible();
    expect(screen.queryByRole("button", { name: pm.subscriptions.suspend })).toBeNull();
  });

  it("a dedicated school's dialog says only the subscription changes", async () => {
    stub.routes["GET /bff/api/v1/platform/plans"] = () =>
      page([PLAN(SUB.plan_id, "standard", "published", 1, { tier: "dedicated" })]);
    const { dialog } = await openSuspend();
    expect(
      await within(dialog).findByText(pm.subscriptions.suspendDedicatedConsequence),
    ).toBeVisible();
    expect(within(dialog).queryByText(pm.subscriptions.suspendSharedConsequence)).toBeNull();
  });

  it("a platform owner can approve a suspension inside a board-exam window", async () => {
    stub.routes["GET /bff/api/v1/platform/me"] = () =>
      Response.json({ ...me(ALL), roles: ["platform_owner"] });
    const { user, dialog } = await openSuspend();
    await user.click(await within(dialog).findByLabelText(pm.subscriptions.examWindowOverride));
    await user.type(within(dialog).getByLabelText(cm.reason), reasonText);
    await user.click(within(dialog).getByRole("button", { name: pm.subscriptions.suspend }));
    await waitFor(() => expect(stub.callsTo(SUSPEND)).toHaveLength(1));
    expect(bodyOf(SUSPEND)).toMatchObject({ exam_window_override: true });
  });

  it.each(["active", "trial", "suspended"])(
    "a %s subscription offers no suspension",
    async (status) => {
      current = { ...SUB, status };
      renderTab();
      expect(
        await screen.findByRole("button", {
          name: status === "suspended" ? pm.subscriptions.reactivate : pm.subscriptions.cancel,
        }),
      ).toBeVisible();
      expect(screen.queryByRole("button", { name: pm.subscriptions.suspend })).toBeNull();
    },
  );

  it("is hidden from an operator without platform.subscriptions.manage", async () => {
    permissions = ["platform.tenants.read", "platform.subscriptions.read"];
    renderTab();
    expect(await screen.findByText(messages.en.status.subscription.past_due)).toBeVisible();
    expect(screen.queryByRole("button", { name: pm.subscriptions.suspend })).toBeNull();
  });

  it.each([
    ["grace_not_over", em.api.grace_not_over.title],
    ["exam_window", em.api.exam_window.title],
    ["invalid_state", em.api.invalid_state.title],
  ])("409 %s is explained in plain words", async (code, title) => {
    stub.routes[SUSPEND] = () => problem(409, code);
    const { user, dialog } = await openSuspend();
    await user.type(within(dialog).getByLabelText(cm.reason), reasonText);
    await user.click(within(dialog).getByRole("button", { name: pm.subscriptions.suspend }));
    expect(await within(dialog).findByText(title)).toBeVisible();
  });

  it("428 step-up leaves for re-authentication", async () => {
    stub.routes[SUSPEND] = () =>
      problem(428, "step_up_required", { step_up_url: "/bff/auth/platform/step-up?next=%2F" });
    const { user, dialog } = await openSuspend();
    await user.type(within(dialog).getByLabelText(cm.reason), reasonText);
    await user.click(within(dialog).getByRole("button", { name: pm.subscriptions.suspend }));
    await waitFor(() =>
      expect(stub.navigate).toHaveBeenCalledWith("/bff/auth/platform/step-up?next=%2F"),
    );
  });

  it("reactivation says overdue invoices come first and explains 409 overdue_invoices", async () => {
    current = { ...SUB, status: "suspended" };
    stub.routes[`POST /bff/api/v1/platform/subscriptions/${SUB.id}/reactivate`] = () =>
      problem(409, "overdue_invoices");
    const user = userEvent.setup();
    renderTab();
    await user.click(await screen.findByRole("button", { name: pm.subscriptions.reactivate }));
    const dialog = screen.getByRole("dialog", { name: pm.subscriptions.reactivateTitle });
    expect(within(dialog).getByText(pm.subscriptions.reactivateBody)).toBeVisible();
    await user.click(within(dialog).getByRole("button", { name: pm.subscriptions.reactivate }));
    expect(await within(dialog).findByText(em.api.overdue_invoices.title)).toBeVisible();
  });

  it("the school suspension points to the subscription for non-payment", async () => {
    permissions = [...ALL, "platform.tenants.suspend"];
    const user = userEvent.setup();
    renderWithIntl(<SchoolDetailScreen schoolId={T} tab="overview" />);
    await user.click(await screen.findByRole("button", { name: pm.schoolDetail.suspend }));
    const dialog = screen.getByRole("dialog", { name: pm.schoolDetail.suspendDialogTitle });
    expect(within(dialog).getByText(pm.schoolDetail.suspendDialogBody)).toBeVisible();
    expect(pm.schoolDetail.suspendDialogBody).toMatch(/non-payment/);
  });
});
