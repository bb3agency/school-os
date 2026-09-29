import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type * as ExportsData from "@/features/exports/data";
import { CSRF, installBffStub, problem, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { messages, renderWithIntl } from "@/test/render";
import { SchoolDetailScreen } from "./SchoolDetailView";

/**
 * Offboarding progress on the school detail (FR-PLT-005, docs/16 §5.5.1): state, deadline,
 * steps, rows per category, the export gate (step-up, CSRF, reference rules), dedicated
 * teardown, certificate download; English and Telugu. Synthetic data only.
 */

const download = vi.hoisted(() => vi.fn());
vi.mock("@/features/exports/data", async (importOriginal) => {
  const actual = await importOriginal<typeof ExportsData>();
  return { ...actual, startDownload: download };
});

const T = "0192f3a4-0000-7000-8000-000000000001";
const OP = "0192f3a4-0000-7000-8000-0000000000f1";
const om = messages.en.platform.schoolDetail.offboarding;
const tm = messages.te.platform.schoolDetail.offboarding;

const ME = {
  operator_id: OP,
  roles: ["platform_owner"],
  permissions: ["platform.tenants.read", "platform.tenants.offboard"],
  step_up_fresh: true,
};

const RUN = {
  tenant_id: T,
  tier: "shared",
  state: "awaiting_export",
  approved_at: "2026-09-01T04:30:00Z",
  deadline_at: "2026-10-01T04:30:00Z",
  overdue: false,
  due_soon: false,
  export_basis: null,
  export_reference: null,
  export_confirmed_at: null,
  kms_deletion_reference: null,
  host_teardown_reference: null,
  teardown_confirmed_at: null,
  inventory: null,
  objects_before: null,
  remaining: null,
  objects_deleted: null,
  profiles_cleared: null,
  keys_destroyed: null,
  deletion_started_at: null,
  data_deleted_at: null,
  keys_destroyed_at: null,
  completed_at: null,
  audit_delete_after: null,
  audit_deleted_at: null,
  failed_step: null,
  last_error: null,
  attempts: 0,
  in_progress: false,
  certificate: null,
};

const DETAIL = {
  tenant_id: T,
  school_name: "Synthetic Public School",
  code: "sps",
  tier: "shared",
  boards: ["SSC"],
  tenant_status: "offboarding",
  tenant_status_reason: "offboarding",
  subscription_status: "active",
  subscription: null,
  plan_code: "standard",
  deployment_status: "healthy",
  app_version: null,
  last_heartbeat_at: null,
  created_at: "2026-06-01T04:30:00Z",
  counts: null,
  open_tickets: 0,
  flag_overrides: {},
  invoices: [],
  offboard_requested_at: "2026-08-31T04:30:00Z",
  offboard_approved_at: "2026-09-01T04:30:00Z",
  offboarding: RUN,
};

const COMPLETED = {
  ...RUN,
  state: "completed",
  export_basis: "school_confirmed",
  export_reference: "Letter 2026/09/02",
  export_confirmed_at: "2026-09-02T04:30:00Z",
  inventory: { students: 2412, documents: 88, identity: 41 },
  objects_before: 350,
  remaining: {},
  keys_destroyed: 2,
  deletion_started_at: "2026-09-02T05:00:00Z",
  data_deleted_at: "2026-09-02T05:10:00Z",
  keys_destroyed_at: "2026-09-02T05:11:00Z",
  completed_at: "2026-09-02T05:15:00Z",
  audit_delete_after: "2027-09-03T05:15:00Z",
  certificate: {
    id: "0192f3a4-0000-7000-8000-00000000d001",
    issued_at: "2026-09-02T05:15:00Z",
    template_version: "v0",
    content_sha256: "a".repeat(64),
    pdf_sha256: "b".repeat(64),
    size_bytes: 12345,
  },
};

let stub: BffStub;
beforeEach(() => {
  download.mockReset();
  stub = installBffStub("operator");
  stub.routes["GET /bff/api/v1/platform/me"] = () => Response.json(ME);
  stub.routes[`GET /bff/api/v1/platform/tenants/${T}`] = () => Response.json(DETAIL);
});
afterEach(uninstallBffStub);

const CONFIRM = `POST /bff/api/v1/platform/tenants/${T}/offboarding:confirm-export`;

describe("offboarding progress (FR-PLT-005)", () => {
  it("shows the export gate and confirms the export with a reference, CSRF and step-up", async () => {
    stub.routes[CONFIRM] = () => Response.json({ ...RUN, state: "scheduled" });
    const user = userEvent.setup();
    renderWithIntl(<SchoolDetailScreen schoolId={T} tab="overview" />);
    expect(await screen.findByText(om.state.awaiting_export)).toBeVisible();
    expect(screen.getByText(om.exportGateHint)).toBeVisible();
    expect(screen.getByRole("list", { name: om.timelineLabel })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: om.confirmExport }));
    const dialog = screen.getByRole("dialog", { name: om.confirmExportTitle });
    const reference = within(dialog).getByLabelText(om.reference);
    await user.selectOptions(within(dialog).getByLabelText(om.basis), "school_confirmed");
    await user.type(reference, "Ravi's letter!");
    await user.click(within(dialog).getByRole("button", { name: om.confirmExport }));
    expect(reference).toHaveAttribute("aria-invalid", "true");
    expect(stub.callsTo(CONFIRM)).toHaveLength(0);

    await user.clear(reference);
    await user.type(reference, "Letter 2026/09/02");
    await user.click(within(dialog).getByRole("button", { name: om.confirmExport }));
    await waitFor(() => expect(stub.callsTo(CONFIRM)).toHaveLength(1));
    const [call] = stub.callsTo(CONFIRM);
    expect(call?.headers.get("x-csrf-token")).toBe(CSRF);
    expect(JSON.parse(call?.body ?? "{}")).toEqual({
      basis: "school_confirmed",
      reference: "Letter 2026/09/02",
    });
  });

  it("a second confirmation explains the conflict in Telugu", async () => {
    stub.routes[CONFIRM] = () => problem(409, "export_already_confirmed");
    const user = userEvent.setup();
    renderWithIntl(<SchoolDetailScreen schoolId={T} tab="overview" />, "te");
    await user.click(await screen.findByRole("button", { name: tm.confirmExport }));
    const dialog = screen.getByRole("dialog");
    await user.selectOptions(within(dialog).getByLabelText(tm.basis), "delivered_by_us");
    await user.type(within(dialog).getByLabelText(tm.reference), "TKT-1042");
    await user.click(within(dialog).getByRole("button", { name: tm.confirmExport }));
    expect(
      await within(dialog).findByText(messages.te.errors.api.export_already_confirmed.title),
    ).toBeInTheDocument();
  });

  it("a completed deletion shows counts, the kept audit log and downloads the certificate", async () => {
    stub.routes[`GET /bff/api/v1/platform/tenants/${T}`] = () =>
      Response.json({ ...DETAIL, tenant_status: "deleted", offboarding: COMPLETED });
    stub.routes[`GET /bff/api/v1/platform/tenants/${T}/deletion-certificate/download-url`] = () =>
      Response.json({
        url: "https://files.synthetic.test/cert.pdf",
        expires_at: "2026-09-02T05:20:00Z",
        filename: "certificate-of-deletion-sps.pdf",
        content_type: "application/pdf",
        size_bytes: 12345,
        sha256: "b".repeat(64),
      });
    const user = userEvent.setup();
    renderWithIntl(<SchoolDetailScreen schoolId={T} tab="overview" />);
    expect(await screen.findByText(om.state.completed)).toBeVisible();
    const table = screen.getByRole("table", { name: om.inventoryTitle });
    const students = within(table).getByRole("row", { name: new RegExp(om.categories.students) });
    expect(students).toHaveTextContent("2,412");
    expect(screen.queryByRole("button", { name: om.confirmExport })).not.toBeInTheDocument();
    expect(screen.getByText(/2027/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: om.downloadCertificate }));
    await waitFor(() =>
      expect(download).toHaveBeenCalledWith("https://files.synthetic.test/cert.pdf"),
    );
  });

  it("a failed step shows the step and its code; overdue is flagged", async () => {
    stub.routes[`GET /bff/api/v1/platform/tenants/${T}`] = () =>
      Response.json({
        ...DETAIL,
        offboarding: {
          ...RUN,
          state: "deleting",
          overdue: true,
          export_basis: "delivered_by_us",
          export_reference: "R8-7",
          export_confirmed_at: "2026-09-02T04:30:00Z",
          failed_step: "verify",
          last_error: "data_remaining",
          remaining: { "sis.students": 2, files: 0 },
          attempts: 3,
        },
      });
    renderWithIntl(<SchoolDetailScreen schoolId={T} tab="overview" />);
    expect(await screen.findByText(om.state.deleting)).toBeVisible();
    expect(screen.getByText(om.overdue)).toBeVisible();
    expect(
      screen.getByText(om.failedAt.replace("{step}", om.failedSteps.verify)),
    ).toBeInTheDocument();
    expect(screen.getByText(/data_remaining/)).toBeInTheDocument();
    expect(screen.getByText("sis.students")).toBeInTheDocument();
  });

  it("a dedicated school records the host teardown references", async () => {
    const TEARDOWN = `POST /bff/api/v1/platform/tenants/${T}/offboarding:confirm-teardown`;
    stub.routes[`GET /bff/api/v1/platform/tenants/${T}`] = () =>
      Response.json({
        ...DETAIL,
        tier: "dedicated",
        offboarding: {
          ...RUN,
          tier: "dedicated",
          state: "scheduled",
          export_basis: "school_confirmed",
          export_reference: "Letter 12",
          export_confirmed_at: "2026-09-02T04:30:00Z",
        },
      });
    stub.routes[TEARDOWN] = () => Response.json({ ...RUN, state: "keys_destroyed" });
    const user = userEvent.setup();
    renderWithIntl(<SchoolDetailScreen schoolId={T} tab="overview" />);
    await user.click(await screen.findByRole("button", { name: om.confirmTeardown }));
    const dialog = screen.getByRole("dialog", { name: om.confirmTeardownTitle });
    await user.type(within(dialog).getByLabelText(om.kmsReference), "tf-run-101");
    await user.type(within(dialog).getByLabelText(om.hostReference), "tf-run-102");
    await user.click(within(dialog).getByRole("button", { name: om.confirmTeardown }));
    await waitFor(() => expect(stub.callsTo(TEARDOWN)).toHaveLength(1));
    expect(JSON.parse(stub.callsTo(TEARDOWN)[0]?.body ?? "{}")).toEqual({
      kms_deletion_reference: "tf-run-101",
      host_teardown_reference: "tf-run-102",
    });
  });

  it("an operator without the offboard permission sees progress but no actions", async () => {
    stub.routes["GET /bff/api/v1/platform/me"] = () =>
      Response.json({ ...ME, roles: ["platform_viewer"], permissions: ["platform.tenants.read"] });
    renderWithIntl(<SchoolDetailScreen schoolId={T} tab="overview" />);
    expect(await screen.findByText(om.state.awaiting_export)).toBeVisible();
    expect(screen.queryByRole("button", { name: om.confirmExport })).not.toBeInTheDocument();
  });
});
