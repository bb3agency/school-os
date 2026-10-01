import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import DataExportPage from "@/app/[locale]/(school)/settings/data-export/page";
import RetentionPage from "@/app/[locale]/(school)/settings/retention/page";
import { notificationHref } from "@/features/notifications/data";
import { SuspendedBanner } from "@/features/school-status/SuspendedBanner";
import { installBffStub, page, problem, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { intlErrors, messages, renderWithIntl } from "@/test/render";
import { me } from "@/test/school-fixtures";
import {
  retentionRules,
  retentionSchema,
  sameRetention,
  setAdminDownloadOpenerForTesting,
  setAdminPollDelayForTesting,
  type RetentionCategory,
  type TenantExport,
} from "./data";
import { DataExportScreen } from "./DataExportScreen";
import { RetentionScreen } from "./RetentionScreen";

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => "/settings/data-export",
    useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
    useSearchParams: () => new URLSearchParams(),
    useParams: () => ({ locale: "en" }),
  };
});

const en = messages.en.admin;
const te = messages.te.admin;
const EXPORT_ALL = "tenant.export_all";
const SENSITIVE = "student.read_sensitive";
const MANAGE = "tenant.settings.manage";
const EXPORT_ID = "0192f3a4-0000-7000-8000-00000000e001";
// A presigned URL is never shown on the page or kept (SEC-008).
const SIGNED = "https://s3.synthetic.test/sos-files/t/x/tenant-export/e.zip?X-Amz-Signature=abc";

function exportRow(overrides: Partial<TenantExport> = {}): TenantExport {
  return {
    id: EXPORT_ID,
    status: "ready",
    include_sensitive: false,
    error_code: null,
    created_at: "2026-09-29T04:30:00Z",
    started_at: "2026-09-29T04:30:05Z",
    finished_at: "2026-09-29T04:31:00Z",
    expires_at: "2026-09-30T04:31:00Z",
    size_bytes: 2_500_000,
    counts: {
      tables: { students: 412, student_values: 9000 },
      documents: 37,
      document_bytes: 2_000_000,
      audit_events: 1500,
    },
    requested_by: { membership_id: "0192f3a4-0000-7000-8000-0000000000b1", display_name: null },
    own: true,
    can_download: true,
    ...overrides,
  };
}

let stub: BffStub;
let opened: string[];
let rows: TenantExport[];

beforeEach(() => {
  stub = installBffStub("staff");
  opened = [];
  rows = [];
  setAdminDownloadOpenerForTesting((url) => opened.push(url));
  setAdminPollDelayForTesting(() => 20);
  stub.routes["GET /bff/api/v1/admin/tenant-export"] = () => page(rows);
});
afterEach(() => {
  uninstallBffStub();
  setAdminDownloadOpenerForTesting(null);
  setAdminPollDelayForTesting(null);
  expect(intlErrors).toEqual([]);
});

function signedIn(permissions: string[], overrides: Parameters<typeof me>[1] = {}) {
  stub.routes["GET /bff/api/v1/me"] = () =>
    Response.json(me(permissions, { roles: ["owner"], ...overrides }));
}

describe("data export screen (US-1201 AC1, FR-ADM-001)", () => {
  it("explains that only the owner can export, without calling the export API", async () => {
    signedIn(["student.read_basic"], { roles: ["principal"] });
    renderWithIntl(<DataExportScreen />);
    expect(await screen.findByText(en.dataExport.ownerOnlyTitle)).toBeInTheDocument();
    expect(stub.callsTo("GET /bff/api/v1/admin/tenant-export")).toHaveLength(0);
    expect(screen.queryByRole("button", { name: en.dataExport.requestButton })).toBeNull();
  });

  it("requests a masked export with an Idempotency-Key and shows it being made", async () => {
    signedIn([EXPORT_ALL]);
    stub.routes["POST /bff/api/v1/admin/tenant-export"] = () => {
      rows = [exportRow({ status: "queued", can_download: false, size_bytes: null, counts: null })];
      return Response.json(rows[0], { status: 202 });
    };
    renderWithIntl(<DataExportScreen />);
    const button = await screen.findByRole("button", { name: en.dataExport.requestButton });
    // Without student.read_sensitive there is no choice: restricted details stay masked.
    expect(screen.queryByRole("checkbox")).toBeNull();
    expect(screen.getByText(en.dataExport.sensitiveNotAllowed)).toBeInTheDocument();
    expect(screen.getByText(messages.en.common.stepUpNote)).toBeInTheDocument();
    await userEvent.click(button);
    await waitFor(() =>
      expect(stub.callsTo("POST /bff/api/v1/admin/tenant-export")).toHaveLength(1),
    );
    const call = stub.callsTo("POST /bff/api/v1/admin/tenant-export")[0];
    expect(JSON.parse(call?.body ?? "{}")).toEqual({ include_sensitive: false });
    expect(call?.headers.get("Idempotency-Key")).toMatch(/^[0-9a-f-]{36}$/);
    expect(await screen.findByText(en.dataExport.requested)).toBeInTheDocument();
    expect(await screen.findByText(en.dataExport.queuedTitle)).toBeInTheDocument();
    // One export at a time: the button waits for it.
    expect(screen.getByRole("button", { name: en.dataExport.requestButton })).toBeDisabled();
  });

  it("includes restricted details only when asked, by someone allowed to see them", async () => {
    signedIn([EXPORT_ALL, SENSITIVE]);
    stub.routes["POST /bff/api/v1/admin/tenant-export"] = () =>
      Response.json(exportRow({ status: "queued", include_sensitive: true }), { status: 202 });
    renderWithIntl(<DataExportScreen />);
    const box = await screen.findByRole("checkbox", { name: en.dataExport.sensitiveLabel });
    expect(box).not.toBeChecked();
    await userEvent.click(box);
    await userEvent.click(screen.getByRole("button", { name: en.dataExport.requestButton }));
    await waitFor(() =>
      expect(stub.callsTo("POST /bff/api/v1/admin/tenant-export")).toHaveLength(1),
    );
    expect(JSON.parse(stub.callsTo("POST /bff/api/v1/admin/tenant-export")[0]?.body ?? "")).toEqual(
      { include_sensitive: true },
    );
  });

  it("downloads a ready export through a short-lived link that never reaches the page", async () => {
    signedIn([EXPORT_ALL]);
    rows = [
      exportRow(),
      exportRow({
        id: "0192f3a4-0000-7000-8000-00000000e000",
        status: "expired",
        can_download: false,
        own: false,
        size_bytes: null,
      }),
    ];
    stub.routes[`GET /bff/api/v1/admin/tenant-export/${EXPORT_ID}/download-url`] = () =>
      Response.json({
        url: SIGNED,
        expires_at: "2026-09-29T05:00:00Z",
        filename: "schoolos-export-20260929-0192f3a4.zip",
        content_type: "application/zip",
        size_bytes: 2_500_000,
      });
    renderWithIntl(<DataExportScreen />);
    const latest = await screen.findByRole("region", { name: en.dataExport.latestTitle });
    expect(within(latest).getByText(en.dataExport.readyTitle)).toBeInTheDocument();
    expect(within(latest).getByText("412")).toBeInTheDocument();
    const history = screen.getByRole("region", { name: en.dataExport.historyTable });
    expect(within(history).getByText(en.dataExport.status.expired)).toBeInTheDocument();
    expect(within(history).getByText(en.dataExport.formerStaff)).toBeInTheDocument();
    const buttons = screen.getAllByRole("button", { name: /Download ZIP/ });
    await userEvent.click(buttons[0]!);
    await waitFor(() => expect(opened).toEqual([SIGNED]));
    expect(document.body.innerHTML).not.toContain("X-Amz-Signature");
  });

  it("explains a refused request and a failed or deleted export", async () => {
    signedIn([EXPORT_ALL]);
    rows = [exportRow({ status: "failed", error_code: "too_large", can_download: false })];
    stub.routes["POST /bff/api/v1/admin/tenant-export"] = () =>
      problem(409, "tenant_export_in_progress");
    renderWithIntl(<DataExportScreen />);
    expect(await screen.findByText(en.dataExport.failedTitle)).toBeInTheDocument();
    expect(screen.getByText(en.dataExport.failure.too_large)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: en.dataExport.requestButton }));
    expect(await screen.findByText(en.errors.tenant_export_in_progress.title)).toBeInTheDocument();
  });

  it("the page renders under its own title, and in Telugu with no missing messages", async () => {
    signedIn([EXPORT_ALL, SENSITIVE]);
    rows = [exportRow({ status: "running", can_download: false })];
    const { unmount } = renderWithIntl(<DataExportPage />);
    expect(
      await screen.findByRole("heading", { level: 1, name: en.dataExport.title }),
    ).toBeInTheDocument();
    unmount();
    renderWithIntl(<DataExportScreen />, "te");
    expect(
      await screen.findByRole("heading", { level: 1, name: te.dataExport.title }),
    ).toBeInTheDocument();
    // The alert title and the status pill say the same word in Telugu.
    expect(await screen.findAllByText(te.dataExport.runningTitle)).not.toHaveLength(0);
    expect(
      screen.getByRole("checkbox", { name: te.dataExport.sensitiveLabel }),
    ).toBeInTheDocument();
  });

  it("links the ready notification and the suspended-school banner to the export", async () => {
    expect(notificationHref({ resource_type: "tenant_export", resource_id: EXPORT_ID })).toBe(
      "/settings/data-export",
    );
    stub.routes["GET /bff/api/v1/me"] = () =>
      Response.json(
        me(["tenant.billing.read", EXPORT_ALL], { roles: ["owner"], tenant_status: "suspended" }),
      );
    renderWithIntl(<SuspendedBanner />);
    const link = await screen.findByRole("link", { name: en.suspended.exportLink });
    expect(link.getAttribute("href")).toMatch(/\/settings\/data-export$/);
  });
});

const CATEGORIES: RetentionCategory[] = [
  {
    key: "audit_events",
    days: 395,
    default_days: 395,
    min_days: 395,
    max_days: 395,
    configurable: false,
    enforced: false,
    is_default: true,
  },
  {
    key: "import_raw_files",
    days: 90,
    default_days: 90,
    min_days: 7,
    max_days: 90,
    configurable: true,
    enforced: true,
    is_default: true,
  },
  {
    key: "exports",
    days: 3,
    default_days: 7,
    min_days: 1,
    max_days: 7,
    configurable: true,
    enforced: true,
    is_default: false,
  },
  {
    key: "notifications_read",
    days: 90,
    default_days: 90,
    min_days: 30,
    max_days: 90,
    configurable: true,
    enforced: true,
    is_default: true,
  },
];

function retention(version = 2) {
  return {
    categories: CATEGORIES,
    version,
    updated_at: "2026-09-28T10:00:00Z",
    updated_by: {
      membership_id: "0192f3a4-0000-7000-8000-0000000000b1",
      display_name: "Test Owner",
    },
  };
}

describe("retention settings (FR-ADM-002)", () => {
  it("says who may change retention, without calling the API", async () => {
    signedIn(["student.read_basic"], { roles: ["office_admin"] });
    renderWithIntl(<RetentionScreen />);
    expect(await screen.findByText(en.retention.notAllowedTitle)).toBeInTheDocument();
    expect(stub.callsTo("GET /bff/api/v1/admin/retention")).toHaveLength(0);
  });

  it("lists editable and fixed periods, and saves only differences with If-Match", async () => {
    signedIn([MANAGE]);
    stub.routes["GET /bff/api/v1/admin/retention"] = () => Response.json(retention());
    stub.routes["PUT /bff/api/v1/admin/retention"] = () => Response.json(retention(3));
    renderWithIntl(<RetentionScreen />);
    const raw = await screen.findByLabelText(en.retention.categories.import_raw_files.name);
    expect(raw).toHaveValue("90");
    expect(screen.getByLabelText(en.retention.categories.exports.name)).toHaveValue("3");
    expect(screen.getByText(en.retention.changed)).toBeInTheDocument();
    const fixed = screen.getByRole("region", { name: en.retention.fixedTitle });
    expect(within(fixed).getByText(en.retention.categories.audit_events.name)).toBeInTheDocument();
    expect(within(fixed).getByText("395 days")).toBeInTheDocument();
    expect(within(fixed).getByText(en.retention.notEnforced)).toBeInTheDocument();
    expect(screen.queryByLabelText(en.retention.categories.audit_events.name)).toBeNull();
    await userEvent.clear(raw);
    await userEvent.type(raw, "30");
    await userEvent.click(screen.getByRole("button", { name: en.retention.save }));
    await waitFor(() => expect(stub.callsTo("PUT /bff/api/v1/admin/retention")).toHaveLength(1));
    const call = stub.callsTo("PUT /bff/api/v1/admin/retention")[0];
    expect(call?.headers.get("If-Match")).toBe('W/"2"');
    expect(JSON.parse(call?.body ?? "{}")).toEqual({ rules: { import_raw_files: 30, exports: 3 } });
    expect(await screen.findByText(en.retention.outcome.saved)).toBeInTheDocument();
  });

  it("checks the bounds before sending and sends nothing when nothing changed", async () => {
    signedIn([MANAGE]);
    stub.routes["GET /bff/api/v1/admin/retention"] = () => Response.json(retention());
    renderWithIntl(<RetentionScreen />);
    await userEvent.click(await screen.findByRole("button", { name: en.retention.save }));
    expect(await screen.findByText(en.retention.outcome.unchanged)).toBeInTheDocument();
    const read = screen.getByLabelText(en.retention.categories.notifications_read.name);
    await userEvent.clear(read);
    await userEvent.type(read, "120");
    await userEvent.click(screen.getByRole("button", { name: en.retention.save }));
    expect(await screen.findByText(messages.en.validation.invalidNumber)).toBeInTheDocument();
    expect(read).toHaveAttribute("aria-invalid", "true");
    expect(stub.callsTo("PUT /bff/api/v1/admin/retention")).toHaveLength(0);
  });

  it("on 412 offers the latest settings; the defaults can be restored", async () => {
    signedIn([MANAGE]);
    let version = 2;
    stub.routes["GET /bff/api/v1/admin/retention"] = () => Response.json(retention(version));
    stub.routes["PUT /bff/api/v1/admin/retention"] = (request) =>
      request.headers.get("If-Match") === 'W/"2"'
        ? problem(412, "precondition_failed")
        : Response.json(retention(version + 1));
    renderWithIntl(<RetentionScreen />);
    const raw = await screen.findByLabelText(en.retention.categories.import_raw_files.name);
    await userEvent.clear(raw);
    await userEvent.type(raw, "45");
    await userEvent.click(screen.getByRole("button", { name: en.retention.save }));
    expect(await screen.findByText(en.errors.precondition_failed.title)).toBeInTheDocument();
    version = 5;
    await userEvent.click(screen.getByRole("button", { name: en.retention.reload }));
    await userEvent.click(
      await screen.findByRole("button", { name: en.retention.restoreDefaults }),
    );
    await waitFor(() =>
      expect(
        stub.callsTo("PUT /bff/api/v1/admin/retention").some((c) => c.body === '{"rules":{}}'),
      ).toBe(true),
    );
    expect(await screen.findByText(en.retention.outcome.defaults)).toBeInTheDocument();
  });

  it("the page renders under its own title, and in Telugu with no missing messages", async () => {
    signedIn([MANAGE]);
    stub.routes["GET /bff/api/v1/admin/retention"] = () => Response.json(retention());
    const { unmount } = renderWithIntl(<RetentionPage />);
    expect(
      await screen.findByRole("heading", { level: 1, name: en.retention.title }),
    ).toBeInTheDocument();
    unmount();
    renderWithIntl(<RetentionScreen />, "te");
    expect(
      await screen.findByLabelText(te.retention.categories.import_raw_files.name),
    ).toBeInTheDocument();
    expect(screen.getByText("395 రోజులు")).toBeInTheDocument();
  });

  it("builds the form schema and the PUT body from the loaded categories", () => {
    const schema = retentionSchema(CATEGORIES);
    const parsed = schema.parse({ import_raw_files: "30", exports: "7", notifications_read: "90" });
    expect(retentionRules(CATEGORIES, parsed)).toEqual({ import_raw_files: 30 });
    expect(
      sameRetention(CATEGORIES, { import_raw_files: 90, exports: 3, notifications_read: 90 }),
    ).toBe(true);
    expect(
      schema.safeParse({ import_raw_files: "6", exports: "7", notifications_read: "90" }).success,
    ).toBe(false);
    expect("audit_events" in schema.shape).toBe(false);
  });
});
