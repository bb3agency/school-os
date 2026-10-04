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
    // The route takes neither If-Match nor Idempotency-Key.
    expect(call?.headers.get("if-match")).toBeNull();
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
      const body = (await request.json()) as { price_override_inr: string };
      current = { ...current, price_override_inr: body.price_override_inr };
      return Response.json(current);
    };
    stub.routes[`DELETE ${OVERRIDE}`] = () => {
      current = { ...current, price_override_inr: null };
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
    // An existing price can be changed or removed.
    expect(screen.getByRole("button", { name: pm.subscriptions.changeOverride })).toBeVisible();
    expect(screen.getByRole("button", { name: pm.subscriptions.removeOverride })).toBeVisible();
  });

  it("refuses zero, more than two decimals and a short reason before sending", async () => {
    const user = userEvent.setup();
    renderTab();
    await user.click(await screen.findByRole("button", { name: pm.subscriptions.setOverride }));
    const dialog = screen.getByRole("dialog", { name: pm.subscriptions.setOverrideTitle });
    const amount = within(dialog).getByLabelText(pm.subscriptions.overrideAmount);
    const confirm = within(dialog).getByRole("button", { name: pm.subscriptions.setOverride });
    await user.type(amount, "0");
    await user.type(within(dialog).getByLabelText(cm.reason), "short");
    await user.click(confirm);
    expect(amount).toHaveAttribute("aria-invalid", "true");
    expect(amount).toHaveAccessibleDescription(
      `${pm.subscriptions.overrideAmountHint} ${messages.en.validation.invalidPositiveAmount}`,
    );
    expect(within(dialog).getByLabelText(cm.reason)).toHaveAttribute("aria-invalid", "true");
    await user.clear(amount);
    await user.type(amount, "3999.555");
    await user.click(confirm);
    expect(amount).toHaveAttribute("aria-invalid", "true");
    expect(stub.callsTo(`PUT ${OVERRIDE}`)).toHaveLength(0);
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
    current = { ...current, price_override_inr: "3999.00" };
    renderTab();
    expect(await screen.findByText("₹3,999.00")).toBeVisible();
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
