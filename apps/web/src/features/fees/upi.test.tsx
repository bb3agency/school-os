import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { installBffStub, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { intlErrors, messages, renderWithIntl } from "@/test/render";
import { me } from "@/test/school-fixtures";
import { ReconcileScreen } from "./ReconcileScreen";
import { UpiCounterHelp } from "./UpiCounter";
import { UpiSettingsScreen } from "./UpiSettingsScreen";
import {
  confirmable,
  initialSelection,
  type StatementImport,
  type StatementLine,
  type UpiExceptions,
  type UpiReport,
  type UpiSettings,
} from "./upi";

/**
 * UPI and bank statements (R2-2E; US-2109; FR-FEE-014..017; docs/17 §5.8). Synthetic payees,
 * UTRs and amounts only. SchoolOS never handles money: the QR is the school's own, and a
 * confirmation never edits a receipt.
 */

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => "/fees/reconcile",
    useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
    useSearchParams: () => new URLSearchParams(),
    useParams: () => ({ locale: "en" }),
  };
});

const en = messages.en.fees;
const STATEMENT = "0192f3a4-0000-7000-8000-00000000e001";
const RECEIPT = "0192f3a4-0000-7000-8000-00000000e101";
const L1 = "0192f3a4-0000-7000-8000-00000000e201";
const L2 = "0192f3a4-0000-7000-8000-00000000e202";
const L3 = "0192f3a4-0000-7000-8000-00000000e203";
const MEMBER = "0192f3a4-0000-7000-8000-0000000000b9";
const STATUS = {
  enabled: true,
  today: "2026-10-18",
  financial_year: "2026-27",
  day_closed: false,
  paper_size: "A5",
};
const SETTINGS: UpiSettings = {
  configured: true,
  payee_name: "Synthetic Public School",
  vpa: "synthetic.school@sbi",
  confirm_within_days: 7,
  upi_uri: "upi://pay?pa=synthetic.school@sbi&pn=Synthetic%20Public%20School&cu=INR",
  version: 2,
};
const REF = {
  id: RECEIPT,
  receipt_number: "RCT/2026-27/000045",
  receipt_date: "2026-10-17",
  amount: "1500.00",
  status: "issued",
  utr: "629112345678",
  student_name: "Synthetica Fee Student",
  class_section: "Class IX A",
};

function line(overrides: Partial<StatementLine>): StatementLine {
  return {
    id: L1,
    import_id: STATEMENT,
    line_no: 6,
    value_date: "2026-10-17",
    amount: "1500.00",
    ref_kind: "utr",
    utr_or_ref: "629112345678",
    narration: "BY TRANSFER-UPI/CR/••••5678/SYNTHETICA PARENT/SYNB/••••@oksbi/Fees--",
    narration_erased: false,
    match_status: "proposed",
    match_kind: "utr_exact",
    receipt: REF,
    exception: null,
    conflict_receipt: null,
    ignore_reason: null,
    decided_by_name: null,
    decided_at: null,
    version: 1,
    ...overrides,
  };
}

const LINES: StatementLine[] = [
  line({}),
  line({
    id: L2,
    line_no: 7,
    amount: "2200.00",
    ref_kind: "reference",
    utr_or_ref: "SYNBN26291123456",
    match_kind: "amount_date",
    receipt: { ...REF, id: "0192f3a4-0000-7000-8000-00000000e102", amount: "2200.00" },
  }),
  line({
    id: L3,
    line_no: 8,
    amount: "1400.00",
    utr_or_ref: "629187654321",
    match_status: "unmatched",
    match_kind: null,
    receipt: null,
    exception: "amount_mismatch",
    conflict_receipt: { ...REF, utr: "629187654321" },
  }),
];

const IMPORT: StatementImport = {
  id: STATEMENT,
  preset: "sbi",
  preset_version: 1,
  file_kind: "xlsx",
  period_from: "2026-10-15",
  period_to: "2026-10-17",
  line_count: 4,
  credit_count: 3,
  duplicate_count: 0,
  proposed_count: 2,
  imported_by: MEMBER,
  imported_by_name: "Synthetic Accountant",
  imported_at: "2026-10-18T04:00:00Z",
  file_deleted_at: "2026-10-18T04:00:00Z",
};

let stub: BffStub;

beforeEach(() => {
  stub = installBffStub("staff");
  stub.routes["GET /bff/api/v1/fees/status"] = () => Response.json(STATUS);
});
afterEach(() => {
  uninstallBffStub();
  expect(intlErrors).toEqual([]);
});

function signedIn(permissions: string[]) {
  stub.routes["GET /bff/api/v1/me"] = () =>
    Response.json(me(permissions, { roles: ["accountant"] }));
}

describe("proposal selection (FR-FEE-016)", () => {
  it("ticks exact UTR matches only; amount-and-date matches wait for a person", () => {
    expect([...initialSelection(LINES)]).toEqual([L1]);
    expect(confirmable(LINES).map((l) => l.id)).toEqual([L1, L2]);
  });
});

describe("UPI settings (US-2109 AC1, FR-FEE-015)", () => {
  it("saves the payee name and UPI ID with If-Match and previews the static QR", async () => {
    signedIn(["fee.manage"]);
    stub.routes["GET /bff/api/v1/fees/upi-settings"] = () => Response.json(SETTINGS);
    stub.routes["PUT /bff/api/v1/fees/upi-settings"] = () =>
      Response.json({ ...SETTINGS, version: 3 });
    const user = userEvent.setup();
    renderWithIntl(<UpiSettingsScreen />);
    const vpa = await screen.findByLabelText(en.upi.vpa);
    expect(vpa).toHaveValue("synthetic.school@sbi");
    const qr = screen.getByRole("img", { name: /Synthetic Public School/ });
    expect(qr.getAttribute("src")).toMatch(/^\/bff\/api\/v1\/fees\/upi-settings\/qr\.svg/);
    expect(screen.getByText(en.upi.moneyTitle)).toBeInTheDocument();
    await user.clear(vpa);
    await user.type(vpa, "not a upi id");
    await user.click(screen.getByRole("button", { name: en.upi.save }));
    expect(await screen.findByText(en.upi.errors.vpa)).toBeInTheDocument();
    expect(stub.callsTo("PUT /bff/api/v1/fees/upi-settings")).toHaveLength(0);
    await user.clear(vpa);
    await user.type(vpa, "school.fees@hdfcbank");
    await user.keyboard("{Enter}");
    expect(await screen.findByText(en.upi.saved)).toBeInTheDocument();
    const put = stub.callsTo("PUT /bff/api/v1/fees/upi-settings")[0];
    expect(put?.headers.get("If-Match")).toBe('W/"2"');
    expect(JSON.parse(put?.body ?? "{}")).toEqual({
      payee_name: "Synthetic Public School",
      vpa: "school.fees@hdfcbank",
      confirm_within_days: 7,
    });
  });

  it("shows the details read-only to the counter", async () => {
    signedIn(["fee.collect"]);
    stub.routes["GET /bff/api/v1/fees/upi-settings"] = () => Response.json(SETTINGS);
    renderWithIntl(<UpiSettingsScreen />);
    expect(await screen.findByText("synthetic.school@sbi", { selector: "dd" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: en.upi.save })).not.toBeInTheDocument();
  });

  it("explains who can see it, without calling the API, to a teacher", async () => {
    signedIn(["student.read_basic"]);
    renderWithIntl(<UpiSettingsScreen />);
    expect(await screen.findByText(en.noAccessTitle)).toBeInTheDocument();
    expect(stub.callsTo("GET /bff/api/v1/fees/upi-settings")).toHaveLength(0);
  });
});

describe("counter UTR help (FR-FEE-014)", () => {
  it("checks a 12-digit UTR in a POST body and links the receipt already holding it", async () => {
    signedIn(["fee.collect"]);
    stub.routes["GET /bff/api/v1/fees/upi-settings"] = () => Response.json(SETTINGS);
    stub.routes["POST /bff/api/v1/fees/upi/utr-check"] = () =>
      Response.json({
        state: "in_use",
        receipt_id: RECEIPT,
        receipt_number: "RCT/2026-27/000045",
        seen_in_bank: true,
      });
    const user = userEvent.setup();
    renderWithIntl(<UpiCounterHelp utr="629112345678" />);
    expect(await screen.findByRole("link", { name: "RCT/2026-27/000045" })).toBeInTheDocument();
    expect(screen.getByText(en.counter.upi.seenInBank)).toBeInTheDocument();
    const call = stub.callsTo("POST /bff/api/v1/fees/upi/utr-check")[0];
    expect(call?.url.search).toBe("");
    expect(JSON.parse(call?.body ?? "{}")).toEqual({ utr: "629112345678" });
    // The QR stays folded so the counter fits the screen; one press shows it.
    const toggle = screen.getByRole("button", { name: en.counter.upi.showQr });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    await user.click(toggle);
    expect(screen.getByRole("img", { name: /Synthetic Public School/ })).toBeInTheDocument();
  });

  it("does not check a UTR that is not 12 digits", async () => {
    signedIn(["fee.collect"]);
    stub.routes["GET /bff/api/v1/fees/upi-settings"] = () =>
      Response.json({ ...SETTINGS, configured: false, payee_name: null, vpa: null, upi_uri: null });
    renderWithIntl(<UpiCounterHelp utr="12345" />);
    expect(await screen.findByText(en.counter.upi.noQr)).toBeInTheDocument();
    expect(stub.callsTo("POST /bff/api/v1/fees/upi/utr-check")).toHaveLength(0);
  });
});

describe("reconcile UPI (US-2109 AC2-AC3, FR-FEE-016/017)", () => {
  function routes() {
    stub.routes["GET /bff/api/v1/fees/bank-statement-presets"] = () =>
      Response.json([{ preset: "sbi", version: 1, name: "State Bank of India", verified: false }]);
    stub.routes["GET /bff/api/v1/fees/bank-statements"] = () => Response.json([IMPORT]);
    stub.routes[`GET /bff/api/v1/fees/bank-statements/${STATEMENT}`] = () =>
      Response.json({ statement: IMPORT, lines: LINES });
    stub.routes[`POST /bff/api/v1/fees/bank-statements/${STATEMENT}/confirm`] = () =>
      Response.json({ confirmed: 1, already_confirmed: 0, skipped: [] });
  }

  it("is only for fee.reconcile holders (no API call otherwise)", async () => {
    signedIn(["fee.collect"]);
    renderWithIntl(<ReconcileScreen />);
    expect(await screen.findByText(en.noAccessTitle)).toBeInTheDocument();
    expect(stub.callsTo("GET /bff/api/v1/fees/bank-statements")).toHaveLength(0);
  });

  it("reviews a statement and confirms only the ticked proposals in one step", async () => {
    signedIn(["fee.reconcile"]);
    routes();
    const user = userEvent.setup();
    renderWithIntl(<ReconcileScreen />);
    await user.click(await screen.findByRole("button", { name: en.reconcile.review }));
    const table = await screen.findByRole("region", { name: en.reconcile.linesCaption });
    const exact = within(table).getByRole("checkbox", { name: /row 6/ });
    const near = within(table).getByRole("checkbox", { name: /row 7/ });
    expect(exact).toBeChecked();
    expect(near).not.toBeChecked();
    // The mismatch is an exception next to its receipt, never a correction.
    expect(within(table).getByText(en.reconcile.exceptions.amount_mismatch)).toBeInTheDocument();
    expect(within(table).getByText(en.reconcile.notCorrected)).toBeInTheDocument();
    expect(within(table).queryByRole("checkbox", { name: /row 8/ })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Confirm 1 match" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Confirm 1 match" }));
    expect(await screen.findByText("1 receipt confirmed.")).toBeInTheDocument();
    const post = stub.callsTo(`POST /bff/api/v1/fees/bank-statements/${STATEMENT}/confirm`)[0];
    expect(JSON.parse(post?.body ?? "{}")).toEqual({ line_ids: [L1] });
  });

  it("lists UPI receipts not seen in the bank and credits that need a person", async () => {
    signedIn(["fee.reconcile"]);
    routes();
    const exceptions: UpiExceptions = {
      as_of: "2026-10-18",
      confirm_within_days: 7,
      unconfirmed_receipts: [
        {
          id: RECEIPT,
          receipt_number: "RCT/2026-27/000012",
          receipt_date: "2026-10-01",
          amount: "900.00",
          utr: "629100000012",
          student_name: "Synthetica Late Student",
          class_section: "Class X A",
          days_waiting: 17,
          proposed_line_id: null,
        },
      ],
      unconfirmed_count: 1,
      lines: [LINES[2] as StatementLine],
      lines_count: 1,
      proposed_count: 2,
    };
    const report: UpiReport = {
      date_from: "2026-09-18",
      date_to: "2026-10-18",
      rows: [],
      confirmed_count: 40,
      confirmed_total: "60000.00",
      unconfirmed_count: 3,
      unconfirmed_total: "4500.00",
      overdue_count: 1,
    };
    stub.routes["GET /bff/api/v1/fees/upi-exceptions"] = () => Response.json(exceptions);
    stub.routes["GET /bff/api/v1/fees/reports/upi"] = () => Response.json(report);
    stub.routes["POST /bff/api/v1/fees/bank-statements/rematch"] = () =>
      Response.json({ looked_at: 5, proposed: 2 });
    const user = userEvent.setup();
    renderWithIntl(<ReconcileScreen />);
    const tab = await screen.findByRole("tab", { name: en.reconcile.exceptionsTab });
    await user.click(tab);
    expect(await screen.findByText("RCT/2026-27/000012")).toBeInTheDocument();
    expect(screen.getByText("40")).toBeInTheDocument();
    expect(screen.getAllByText(en.reconcile.exceptions.amount_mismatch).length).toBeGreaterThan(0);
    await user.click(screen.getByRole("button", { name: en.reconcile.rematch }));
    expect(await screen.findByText("2 new matches proposed.")).toBeInTheDocument();
  });

  it("dismisses a credit with a reason code", async () => {
    signedIn(["fee.reconcile"]);
    routes();
    stub.routes[`POST /bff/api/v1/fees/bank-statement-lines/${L3}/dismiss`] = () =>
      Response.json({ ...LINES[2], match_status: "ignored", ignore_reason: "not_a_fee" });
    const user = userEvent.setup();
    renderWithIntl(<ReconcileScreen />);
    await user.click(await screen.findByRole("button", { name: en.reconcile.review }));
    const table = await screen.findByRole("region", { name: en.reconcile.linesCaption });
    const dismissButtons = within(table).getAllByRole("button", { name: en.reconcile.dismiss });
    await user.click(dismissButtons.at(-1) as HTMLElement);
    const dialog = await screen.findByRole("dialog");
    await user.selectOptions(
      within(dialog).getByLabelText(en.reconcile.dismissReason),
      "recorded_elsewhere",
    );
    await user.click(within(dialog).getByRole("button", { name: en.reconcile.dismiss }));
    await waitFor(() =>
      expect(stub.callsTo(`POST /bff/api/v1/fees/bank-statement-lines/${L3}/dismiss`)).toHaveLength(
        1,
      ),
    );
    const call = stub.callsTo(`POST /bff/api/v1/fees/bank-statement-lines/${L3}/dismiss`)[0];
    expect(JSON.parse(call?.body ?? "{}")).toEqual({ reason: "recorded_elsewhere" });
  });

  it("explains a statement the bank sent twice", async () => {
    signedIn(["fee.reconcile"]);
    routes();
    stub.routes["GET /bff/api/v1/fees/bank-statements"] = () => Response.json([]);
    renderWithIntl(<ReconcileScreen />);
    expect(await screen.findByText(en.reconcile.noStatements)).toBeInTheDocument();
    expect(en.errors.statement_already_imported.title).toBeTruthy();
  });
});
