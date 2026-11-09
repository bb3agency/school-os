import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { installBffStub, problem, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { intlErrors, messages, renderWithIntl } from "@/test/render";
import { me } from "@/test/school-fixtures";
import type { FeeStatus } from "./data";
import type { Ageing, CollectionRegister, Series } from "./period";
import { FeeReportsScreen } from "./PeriodReports";

/**
 * FM-04 fee reports (FR-FEE-061..074): finance.read sees the reports; fee.report downloads them
 * through the API client (step-up like other fee downloads); cancelled receipts are listed
 * apart; the year-end tab shows the receipt number check. Synthetic names and amounts only.
 */

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => "/fees/reports/collections",
    useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
    useSearchParams: () => new URLSearchParams(),
    useParams: () => ({ locale: "en" }),
  };
});

const en = messages.en.fees.periodReports;
const STATUS: FeeStatus = {
  enabled: true,
  today: "2026-11-09",
  financial_year: "2026-27",
  day_closed: false,
  paper_size: "A5",
};
const COLLECTIONS: CollectionRegister = {
  date_from: "2026-11-01",
  date_to: "2026-11-09",
  by_day: [
    {
      date: "2026-11-02",
      receipts: 2,
      gross: "1500.00",
      cancelled: "300.00",
      refunded: "0.00",
      net: "1200.00",
    },
  ],
  by_head: [
    {
      head_id: "0192f3a4-0000-7000-8000-00000000f001",
      head_code: "TUI",
      head_name: "Tuition fee",
      collected: "1500.00",
      cancelled: "300.00",
      refunded: "0.00",
      net: "1200.00",
    },
  ],
  by_mode: [
    {
      mode: "cash",
      receipts: 2,
      gross: "1500.00",
      cancelled: "300.00",
      refunded: "0.00",
      net: "1200.00",
    },
  ],
  by_collector: [{ membership_id: null, name: "Synthetica Clerk", receipts: 2, gross: "1500.00" }],
  receipts: [],
  receipts_count: 2,
  truncated: false,
  cancellations: [
    {
      cancellation_id: "0192f3a4-0000-7000-8000-00000000f101",
      receipt_id: "0192f3a4-0000-7000-8000-00000000f201",
      receipt_number: "RCT/2026-27/000042",
      receipt_date: "2026-11-02",
      cancelled_on: "2026-11-02",
      amount: "300.00",
      mode: "cash",
      reason_code: "wrong_amount",
      requested_by_name: "Synthetica Clerk",
      approved_by_name: "Synthetica Principal",
    },
  ],
  refunds: [],
  receipts_total: "1500.00",
  cancelled_total: "300.00",
  refunded_total: "0.00",
  net_total: "1200.00",
};
const AGEING: Ageing = {
  as_of: "2026-11-09",
  bands: [
    { from_days: 0, to_days: 30 },
    { from_days: 31, to_days: 90 },
    { from_days: 91, to_days: null },
  ],
  rows: [
    {
      student_id: "0192f3a4-0000-7000-8000-00000000f301",
      display_name: "Synthetica Owing Student",
      admission_no: "F-0301",
      class_section: "IX-A",
      bands: ["4500.00", "0.00", "0.00"],
      earlier_years: "4800.00",
      overdue: "9300.00",
      oldest_due_on: "2025-07-15",
    },
  ],
  truncated: false,
  by_class: [
    {
      class_section: "IX-A",
      students: 1,
      bands: ["4500.00", "0.00", "0.00"],
      earlier_years: "4800.00",
      overdue: "9300.00",
    },
  ],
  totals: ["4500.00", "0.00", "0.00"],
  earlier_years_total: "4800.00",
  overdue_total: "9300.00",
  not_yet_due_total: "4500.00",
};
const SERIES: Series = {
  financial_year: "2026-27",
  gap_free: true,
  series: [
    {
      series: "receipt",
      financial_year: "2026-27",
      first_number: "RCT/2026-27/000001",
      last_number: "RCT/2026-27/000042",
      used: 42,
      cancelled: 1,
      counter: 42,
      missing_count: 0,
      missing: [],
    },
  ],
};

let stub: BffStub;

beforeEach(() => {
  stub = installBffStub("staff");
  stub.routes["GET /bff/api/v1/fees/status"] = () => Response.json(STATUS);
  stub.routes["GET /bff/api/v1/fees/reports/collections"] = () => Response.json(COLLECTIONS);
  stub.routes["GET /bff/api/v1/fees/reports/ageing"] = () => Response.json(AGEING);
  stub.routes["GET /bff/api/v1/fees/reports/receipt-series"] = () => Response.json(SERIES);
  stub.routes["GET /bff/api/v1/fees/reports/collections/download"] = () =>
    new Response("number,date\n", {
      headers: {
        "content-type": "text/csv",
        "content-disposition": 'attachment; filename="fee-collections.csv"',
      },
    });
});
afterEach(() => {
  uninstallBffStub();
  expect(intlErrors).toEqual([]);
});

function signedIn(permissions: string[]) {
  stub.routes["GET /bff/api/v1/me"] = () =>
    Response.json(me(permissions, { roles: ["accountant"] }));
}

describe("Fee reports (FR-FEE-061..074)", () => {
  it("shows the period's collections with cancelled receipts apart", async () => {
    signedIn(["finance.read"]);
    renderWithIntl(<FeeReportsScreen tab="collections" />);
    expect(await screen.findByText("RCT/2026-27/000042")).toBeInTheDocument();
    expect(screen.getAllByText("₹1,500.00").length).toBeGreaterThan(0);
    expect(screen.getAllByText("₹1,200.00").length).toBeGreaterThan(0);
    expect(screen.getByText(en.cancelledReceiptsHint)).toBeInTheDocument();
    expect(screen.getByText("Tuition fee")).toBeInTheDocument();
    const call = stub.callsTo("GET /bff/api/v1/fees/reports/collections")[0];
    expect(call?.url.searchParams.get("from")).toBe("2026-11-01");
    expect(call?.url.searchParams.get("to")).toBe("2026-11-09");
    // No downloads without fee.report.
    expect(screen.queryByRole("button", { name: en.downloads })).toBeNull();
  });

  it("explains who can see them, without calling the API", async () => {
    signedIn(["fee.collect"]);
    renderWithIntl(<FeeReportsScreen tab="collections" />);
    expect(await screen.findByText(messages.en.fees.noAccessTitle)).toBeInTheDocument();
    expect(stub.callsTo("GET /bff/api/v1/fees/reports/collections")).toHaveLength(0);
  });

  it("says fees are off (404 module_hidden)", async () => {
    signedIn(["finance.read"]);
    stub.routes["GET /bff/api/v1/fees/status"] = () => problem(404, "module_hidden");
    renderWithIntl(<FeeReportsScreen tab="collections" />);
    expect(await screen.findByText(messages.en.fees.off.title)).toBeInTheDocument();
  });

  it("downloads the register through the API client with fee.report", async () => {
    signedIn(["finance.read", "fee.report"]);
    const createObjectURL = vi.fn(() => "blob:csv");
    Object.assign(URL, { createObjectURL, revokeObjectURL: vi.fn() });
    const user = userEvent.setup();
    renderWithIntl(<FeeReportsScreen tab="collections" />);
    await screen.findByText("RCT/2026-27/000042");
    await user.click(screen.getByRole("button", { name: en.downloads }));
    await user.click(await screen.findByRole("menuitem", { name: en.files.receipts.csv }));
    await waitFor(() =>
      expect(stub.callsTo("GET /bff/api/v1/fees/reports/collections/download")).toHaveLength(1),
    );
    const call = stub.callsTo("GET /bff/api/v1/fees/reports/collections/download")[0];
    expect(call?.url.searchParams.get("part")).toBe("receipts");
    expect(call?.url.searchParams.get("format")).toBe("csv");
    await waitFor(() => expect(createObjectURL).toHaveBeenCalledTimes(1));
  });

  it("shows the step-up prompt when a download needs a fresh sign-in (428)", async () => {
    signedIn(["finance.read", "fee.report"]);
    stub.routes["GET /bff/api/v1/fees/reports/collections/download"] = () =>
      problem(428, "step_up_required", { step_up_url: "/bff/auth/step-up" });
    const user = userEvent.setup();
    renderWithIntl(<FeeReportsScreen tab="collections" />);
    await screen.findByText("RCT/2026-27/000042");
    await user.click(screen.getByRole("button", { name: en.downloads }));
    await user.click(await screen.findByRole("menuitem", { name: en.files.receipts.csv }));
    await waitFor(() => expect(stub.navigate).toHaveBeenCalled());
  });

  it("shows ageing bands with earlier years in their own column", async () => {
    signedIn(["finance.read"]);
    renderWithIntl(<FeeReportsScreen tab="ageing" />);
    expect(await screen.findByText("Synthetica Owing Student")).toBeInTheDocument();
    expect(screen.getAllByText("0 to 30 days").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Over 90 days").length).toBeGreaterThan(0);
    expect(screen.getAllByText("₹4,800.00").length).toBeGreaterThan(0);
  });

  it("shows the receipt number check and the year-end pack", async () => {
    signedIn(["finance.read", "fee.report"]);
    renderWithIntl(<FeeReportsScreen tab="year-end" />);
    expect(await screen.findByText(en.gapFreeTitle)).toBeInTheDocument();
    expect(screen.getByText("RCT/2026-27/000042")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: en.pack })).toBeInTheDocument();
    const call = stub.callsTo("GET /bff/api/v1/fees/reports/receipt-series")[0];
    expect(call?.url.searchParams.get("financial_year")).toBe("2026-27");
  });
});
