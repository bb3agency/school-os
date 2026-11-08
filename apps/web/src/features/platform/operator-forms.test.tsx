import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { installBffStub, page, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { messages, renderWithIntl } from "@/test/render";
import { AnnouncementEditor } from "./AnnouncementEditor";
import { InvoicesScreen, PlansScreen } from "./BillingViews";
import { filterHref, type ActiveFilter } from "./FilterCard";
import { ProvisionSchoolForm } from "./ProvisionSchoolForm";
import { SchoolsView } from "./SchoolsView";
import { ready } from "@/lib/loadable";

/**
 * D75 (docs/17 §4.3, §4.4; NFR-A11Y-001): the operator console forms on the standard
 * patterns. A failed submit moves focus to an `ErrorSummary` linking each field; the
 * provisioning wizard shows `Steps`; edit dialogs say whether changes are not saved yet (the
 * `SaveBar` status); operator lists show the URL filters as `FilterChips`. Synthetic data only.
 */

const push = vi.hoisted(() => vi.fn());
vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => "/platform",
    useRouter: () => ({ push, replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
    useSearchParams: () => new URLSearchParams(),
    useParams: () => ({ locale: "en" }),
  };
});

const T = "0192f3a4-0000-7000-8000-000000000001";
const DRAFT_ID = "0192f3a4-0000-7000-8000-00000000a003";
const pm = messages.en.platform;
const summaryTitle = messages.en.ui.errorSummary.title;
const bar = messages.en.ui.saveBar;

const PLAN = {
  id: DRAFT_ID,
  code: "premium",
  name: "Premium",
  version: 2,
  tier: "shared",
  status: "draft",
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
  published_at: null,
  created_at: "2026-06-01T00:00:00Z",
  one_time_fee_inr: "0.00",
  description: null,
  row_version: 3,
};

let stub: BffStub;
beforeEach(() => {
  push.mockReset();
  stub = installBffStub("operator");
  stub.routes["GET /bff/api/v1/platform/me"] = () =>
    Response.json({
      operator_id: "0192f3a4-0000-7000-8000-0000000000f1",
      roles: ["billing_admin"],
      permissions: ["platform.tenants.read", "platform.plans.manage", "platform.invoices.read"],
      step_up_fresh: true,
    });
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
  stub.routes["GET /bff/api/v1/platform/plans"] = () => page([PLAN]);
  stub.routes["GET /bff/api/v1/platform/ai-bundles"] = () => page([]);
  stub.routes["GET /bff/api/v1/platform/invoices"] = () => page([]);
});
afterEach(uninstallBffStub);

describe("provision wizard on Steps and ErrorSummary (FR-PLT-001, D75)", () => {
  it("a failed Next moves focus to the summary, whose links point at the fields", async () => {
    const user = userEvent.setup({ delay: null });
    renderWithIntl(<ProvisionSchoolForm />, "en");
    const steps = screen.getByRole("list", { name: pm.provision.stepsLabel });
    expect(within(steps).getAllByRole("listitem")).toHaveLength(6);
    expect(within(steps).getByRole("listitem", { current: "step" })).toHaveTextContent(
      pm.provision.steps.school,
    );

    await user.click(screen.getByRole("button", { name: messages.en.common.next }));
    const summary = screen.getByRole("alert", { name: "2 fields need attention" });
    expect(summary).toHaveFocus();
    const name = screen.getByLabelText(pm.provision.fields.schoolName);
    const links = within(summary).getAllByRole("link");
    expect(links[0]).toHaveAttribute("href", `#${name.id}`);
    // A link moves focus to its field.
    await user.click(links[0] as HTMLElement);
    expect(name).toHaveFocus();
  });

  it("finished steps say Done; the next step's heading takes focus", async () => {
    const user = userEvent.setup({ delay: null });
    renderWithIntl(<ProvisionSchoolForm />, "en");
    await user.type(screen.getByLabelText(pm.provision.fields.schoolName), "Sample Public School");
    await user.type(screen.getByLabelText(pm.provision.fields.code), "sample-public");
    await user.click(screen.getByRole("button", { name: messages.en.common.next }));
    expect(
      screen.getByRole("heading", { level: 2, name: pm.provision.steps.deployment }),
    ).toHaveFocus();
    const steps = screen.getByRole("list", { name: pm.provision.stepsLabel });
    const [first] = within(steps).getAllByRole("listitem");
    expect(first).toHaveTextContent(messages.en.ui.steps.done);
    expect(within(steps).getByRole("listitem", { current: "step" })).toHaveTextContent(
      pm.provision.steps.deployment,
    );
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

describe("plan dialogs (FR-PLT-010, D75)", () => {
  async function openEdit() {
    const user = userEvent.setup();
    renderWithIntl(<PlansScreen />);
    const row = await screen.findByRole("row", { name: /v2/ });
    await user.click(within(row).getByRole("button", { name: pm.plans.edit }));
    const dialog = screen.getByRole("dialog", {
      name: pm.plans.editTitle.replace("{name}", "Premium").replace("{version}", "2"),
    });
    return { user, dialog: within(dialog) };
  }

  it("the edit dialog says whether changes are not saved yet", async () => {
    const { user, dialog } = await openEdit();
    expect(dialog.getByText(bar.noChanges)).toBeInTheDocument();
    await user.type(dialog.getByLabelText(pm.plans.colName), " 2027");
    expect(await dialog.findByText(bar.unsaved)).toBeInTheDocument();
    await user.clear(dialog.getByLabelText(pm.plans.colName));
    await user.type(dialog.getByLabelText(pm.plans.colName), "Premium");
    expect(await dialog.findByText(bar.noChanges)).toBeInTheDocument();
  });

  it("a failed save moves focus to the error summary inside the dialog", async () => {
    const { user, dialog } = await openEdit();
    const name = dialog.getByLabelText(pm.plans.colName);
    await user.clear(name);
    await user.click(dialog.getByRole("button", { name: pm.plans.saveChanges }));
    const summary = await dialog.findByRole("alert", { name: summaryTitle });
    await waitFor(() => expect(summary).toHaveFocus());
    expect(
      within(summary).getByRole("link", { name: messages.en.validation.required }),
    ).toHaveAttribute("href", `#${name.id}`);
    expect(stub.callsTo(`PATCH /bff/api/v1/platform/plans/${DRAFT_ID}`)).toHaveLength(0);
  });
});

describe("new announcement form (FR-PLT-026, D75)", () => {
  it("a failed submit moves focus to the error summary linking the title", async () => {
    const user = userEvent.setup({ delay: null });
    renderWithIntl(<AnnouncementEditor />, "en");
    await user.click(screen.getByRole("button", { name: pm.announcements.save }));
    const summary = screen.getByRole("alert", { name: summaryTitle });
    await waitFor(() => expect(summary).toHaveFocus());
    const title = screen.getByLabelText(pm.announcements.titleLabel);
    expect(within(summary).getAllByRole("link")[0]).toHaveAttribute("href", `#${title.id}`);
    expect(title).toHaveAttribute("aria-invalid", "true");
  });
});

describe("operator lists show URL filters as chips (D75, docs/17 §4.3)", () => {
  const active: ActiveFilter[] = [
    { param: "status", raw: "paid", label: "Status", value: "Paid" },
    { param: "fy", raw: "2026-27", label: "Financial year", value: "2026-27" },
  ];

  it("removing a chip keeps the other filters", () => {
    expect(filterHref("/platform/invoices", active, "status")).toBe(
      "/platform/invoices?fy=2026-27",
    );
    expect(filterHref("/platform/invoices", active, "fy")).toBe("/platform/invoices?status=paid");
    expect(filterHref("/platform/invoices", [])).toBe("/platform/invoices");
  });

  it("invoices: one chip per filter; removing one opens the list without it", async () => {
    const user = userEvent.setup();
    renderWithIntl(
      <InvoicesScreen filters={{ status: "paid", financialYear: "2026-27", tenantId: T }} />,
    );
    const status = messages.en.status.invoice.paid;
    const remove = screen.getByRole("button", {
      name: `Remove filter ${pm.invoices.colStatus}: ${status}`,
    });
    expect(
      await screen.findByRole("button", {
        name: `Remove filter ${pm.invoices.colSchool}: Sri Saraswati High School (sshs)`,
      }),
    ).toBeInTheDocument();
    await user.click(remove);
    expect(push).toHaveBeenCalledTimes(1);
    expect(String(push.mock.calls[0]?.[0])).toContain(`/platform/invoices?fy=2026-27&school=${T}`);
    await user.click(screen.getByRole("button", { name: messages.en.ui.filters.clearAll }));
    expect(String(push.mock.calls[1]?.[0])).toMatch(/\/platform\/invoices$/);
  });

  it("schools: chips for the search, status and the check boxes", () => {
    renderWithIntl(
      <SchoolsView
        schools={ready([])}
        filters={{ q: "saraswati", status: "active", pastDue: true }}
      />,
    );
    const chips = screen.getByRole("list", { name: messages.en.ui.filters.active });
    expect(within(chips).getAllByRole("listitem")).toHaveLength(3);
    expect(
      screen.getByRole("button", {
        name: `Remove filter ${pm.schools.filterPastDue}: ${messages.en.common.yes}`,
      }),
    ).toBeInTheDocument();
  });

  it("an unfiltered list shows no chips", () => {
    renderWithIntl(<SchoolsView schools={ready([])} />);
    expect(screen.queryByRole("list", { name: messages.en.ui.filters.active })).toBeNull();
  });
});
