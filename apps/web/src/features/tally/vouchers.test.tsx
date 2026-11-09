import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import TallyVouchersPage from "@/app/[locale]/(school)/settings/tally/vouchers/page";
import { installBffStub, problem, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { intlErrors, messages, renderWithIntl } from "@/test/render";
import { me } from "@/test/school-fixtures";
import { TallyVouchersScreen } from "./TallyVouchersScreen";
import {
  CONFIGURE,
  FINANCE_READ,
  REPORT,
  fieldId,
  type LedgerCheck,
  type VoucherPreview,
  type VoucherSettings,
} from "./vouchers-data";

/**
 * FM-03 Tally voucher export (FR-TALLY-011..030): ledger mapping, the file for closed days and
 * the ledger-vs-Tally check. SchoolOS never writes to Tally. Synthetic names and amounts only.
 */

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => "/settings/tally/vouchers",
    useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
    useSearchParams: () => new URLSearchParams(),
    useParams: () => ({ locale: "en" }),
  };
});

const en = messages.en.tally.vouchers;
const HEAD = "0192f3a4-0000-7000-8000-00000000f001";
const BASE = "/bff/api/v1/tally/vouchers";

function settings(overrides: Partial<VoucherSettings> = {}): VoucherSettings {
  return {
    configured: true,
    party_mode: "student",
    receivable_ledger: "Fee receivable",
    modes: [
      { mode: "cash", ledger: "Cash" },
      { mode: "cheque", ledger: null },
      { mode: "bank_transfer", ledger: null },
      { mode: "upi_qr", ledger: null },
      { mode: "razorpay", ledger: null },
    ],
    heads: [{ head_id: HEAD, code: "TUI", name: "Tuition fee", active: true, ledger: null }],
    other_income_ledger: null,
    include_demand: false,
    company: null,
    links_available: false,
    format_verified: false,
    version: 2,
    updated_at: "2026-11-05T05:00:00Z",
    ...overrides,
  };
}

function preview(overrides: Partial<VoucherPreview> = {}): VoucherPreview {
  return {
    period_from: "2026-11-05",
    period_to: "2026-11-05",
    vouchers: 2,
    new_vouchers: 2,
    already_exported: 0,
    by_kind: [
      { kind: "receipt", count: 2, new: 2 },
      { kind: "cancellation", count: 0, new: 0 },
      { kind: "refund", count: 0, new: 0 },
      { kind: "demand", count: 0, new: 0 },
    ],
    cash_in: "2500.00",
    cash_out: "0.00",
    open_days: [],
    missing: [],
    students_on_receivable: 2,
    ready: true,
    ...overrides,
  };
}

let stub: BffStub;

beforeEach(() => {
  stub = installBffStub("staff");
  stub.routes[`GET ${BASE}/settings`] = () => Response.json(settings());
  stub.routes[`GET ${BASE}/exports`] = () => Response.json([]);
});
afterEach(() => {
  uninstallBffStub();
  expect(intlErrors).toEqual([]);
});

function signedIn(permissions: string[]) {
  stub.routes["GET /bff/api/v1/me"] = () =>
    Response.json(me(permissions, { roles: ["accountant"] }));
}

describe("Tally voucher export (FM-03)", () => {
  it("renders the page", async () => {
    signedIn([CONFIGURE, REPORT]);
    renderWithIntl(<TallyVouchersPage />);
    expect(await screen.findByRole("heading", { level: 1, name: en.title })).toBeInTheDocument();
  });

  it("explains who can export, without calling the API", async () => {
    signedIn(["student.read_basic"]);
    renderWithIntl(<TallyVouchersScreen />);
    expect(await screen.findByText(en.noAccessTitle)).toBeInTheDocument();
    expect(stub.callsTo(`GET ${BASE}/settings`)).toHaveLength(0);
  });

  it("says fees are off when the API answers 404", async () => {
    signedIn([CONFIGURE, REPORT]);
    stub.routes[`GET ${BASE}/settings`] = () => problem(404, "module_hidden");
    renderWithIntl(<TallyVouchersScreen />);
    expect(await screen.findByText(messages.en.fees.off.title)).toBeInTheDocument();
  });

  it("saves the mapping with If-Match and the typed ledger names (FR-TALLY-011)", async () => {
    signedIn([CONFIGURE, REPORT]);
    let sent: unknown;
    stub.routes[`PUT ${BASE}/settings`] = async (request) => {
      sent = await request.json();
      expect(request.headers.get("If-Match")).toBe('W/"2"');
      return Response.json(settings({ version: 3 }));
    };
    renderWithIntl(<TallyVouchersScreen />);
    const cheque = await screen.findByLabelText(
      en.mapping.modeLedger.replace("{mode}", en.modes.cheque),
    );
    await userEvent.type(cheque, "SBI Current A/c");
    await userEvent.click(screen.getByRole("button", { name: en.mapping.save }));
    expect(await screen.findByText(en.mapping.saved)).toBeInTheDocument();
    expect(sent).toMatchObject({
      party_mode: "student",
      receivable_ledger: "Fee receivable",
      mode_ledgers: { cash: "Cash", cheque: "SBI Current A/c" },
      include_demand: false,
    });
  });

  it("shows open days and missing ledgers, and downloads only when ready (FR-TALLY-016)", async () => {
    signedIn([CONFIGURE, REPORT]);
    stub.routes[`GET ${BASE}/preview`] = () =>
      Response.json(
        preview({
          open_days: ["2026-11-05"],
          missing: [{ field: "mode_ledgers.upi_qr", label: "UPI (school QR)" }],
          ready: false,
        }),
      );
    renderWithIntl(<TallyVouchersScreen />);
    const from = await screen.findByLabelText(en.export.from);
    fireEvent.change(from, { target: { value: "2026-11-05" } });
    await userEvent.click(screen.getByRole("button", { name: en.export.check }));
    expect(await screen.findByText(en.export.openDaysTitle)).toBeInTheDocument();
    expect(screen.getByText(en.export.missingTitle)).toBeInTheDocument();
    const link = screen.getByRole("link", {
      name: en.mapping.modeLedger.replace("{mode}", en.modes.upi_qr),
    });
    expect(link).toHaveAttribute("href", `#${fieldId("mode_ledgers.upi_qr")}`);
    expect(screen.getByRole("button", { name: en.export.download })).toBeDisabled();
    const query = stub.callsTo(`GET ${BASE}/preview`)[0]?.url.searchParams;
    expect(query?.get("period_from")).toBe("2026-11-05");
    expect(query?.get("period_to")).toBe("2026-11-05");
  });

  it("downloads the XML file through a POST (FR-TALLY-018)", async () => {
    signedIn([CONFIGURE, REPORT]);
    stub.routes[`GET ${BASE}/preview`] = () => Response.json(preview());
    stub.routes[`POST ${BASE}/export`] = () =>
      new Response("<?xml version='1.0' encoding='utf-8'?><ENVELOPE/>", {
        headers: { "content-type": "application/xml; charset=utf-8" },
      });
    const created = vi.fn(() => "blob:test");
    Object.assign(URL, { createObjectURL: created, revokeObjectURL: vi.fn() });
    renderWithIntl(<TallyVouchersScreen />);
    fireEvent.change(await screen.findByLabelText(en.export.from), {
      target: { value: "2026-11-05" },
    });
    await userEvent.click(screen.getByRole("button", { name: en.export.check }));
    await userEvent.click(await screen.findByRole("button", { name: en.export.download }));
    expect(await screen.findByText(en.export.done)).toBeInTheDocument();
    const [call] = stub.callsTo(`POST ${BASE}/export`);
    expect(JSON.parse(call?.body ?? "{}")).toEqual({
      period_from: "2026-11-05",
      period_to: "2026-11-05",
      include_exported: false,
    });
    expect(created).toHaveBeenCalled();
  });

  it("lists ledgers that differ from Tally (FR-TALLY-025)", async () => {
    signedIn([FINANCE_READ]);
    const check: LedgerCheck = {
      party_mode: "student",
      as_of: "2026-11-04",
      compared: 3,
      matching: 2,
      differing: 1,
      tally_total: "17000.00",
      schoolos_total: "16500.00",
      not_exported: 0,
      rows: [
        {
          ledger_name: "Synthetic Family Ledger 9A",
          state: "differs",
          tally_balance: "8500.00",
          schoolos_balance: "8000.00",
          difference: "500.00",
          students: 1,
        },
      ],
    };
    stub.routes[`GET ${BASE}/check`] = () => Response.json(check);
    stub.routes["GET /bff/api/v1/me"] = () =>
      Response.json(me([FINANCE_READ, REPORT], { roles: ["auditor_readonly"] }));
    renderWithIntl(<TallyVouchersScreen />);
    expect(await screen.findByText("Synthetic Family Ledger 9A")).toBeInTheDocument();
    expect(screen.getByText(en.check.states.differs)).toBeInTheDocument();
    await waitFor(() => expect(stub.callsTo(`GET ${BASE}/check`)).toHaveLength(1));
  });
});
