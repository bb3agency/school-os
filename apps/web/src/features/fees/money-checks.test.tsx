import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { installBffStub, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { intlErrors, messages, renderWithIntl } from "@/test/render";
import { me, STUDENT } from "@/test/school-fixtures";
import { CounterScreen } from "./CounterScreen";
import {
  COLLECT,
  FINANCE_READ,
  MANAGE,
  REFUND_REQUEST,
  type Account,
  type DailyReport,
  type DueLine,
  type FeeStatus,
  type Receipt,
} from "./data";
import { ReceiptDetailScreen, setReceiptPollDelayForTesting } from "./ReceiptDetailScreen";
import { DailyScreen } from "./ReportsScreens";

/**
 * Round 2 web audit, fees (XF-*): money requests the API would refuse are not offered, and a
 * confirmation shows the totals it acts on. Synthetic names and amounts only.
 */

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => "/fees/daily",
    useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
    useSearchParams: () => new URLSearchParams(),
    useParams: () => ({ locale: "en" }),
  };
});

const en = messages.en.fees;
const re = (text: string) => new RegExp(text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
const ACCOUNT = "0192f3a4-0000-7000-8000-00000000f001";
const HEAD = "0192f3a4-0000-7000-8000-00000000f101";
const RECEIPT = "0192f3a4-0000-7000-8000-00000000f201";
const MEMBER = "0192f3a4-0000-7000-8000-0000000000b9";

const STATUS: FeeStatus = {
  enabled: true,
  today: "2026-10-17",
  financial_year: "2026-27",
  day_closed: false,
  paper_size: "A5",
};

function receipt(overrides: Partial<Receipt> = {}): Receipt {
  return {
    id: RECEIPT,
    receipt_number: "RCT/2026-27/000123",
    financial_year: "2026-27",
    receipt_date: "2026-10-17",
    amount: "6100.00",
    amount_in_words: "Rupees Six Thousand One Hundred Only",
    mode: "cash",
    mode_details: {},
    utr: null,
    status: "issued",
    account_id: ACCOUNT,
    student_id: STUDENT,
    student_name: "Synthetica Fee Student",
    class_section: "Class IX A",
    admission_no: "F-0001",
    collected_by: MEMBER,
    collected_by_name: "Synthetic Clerk",
    issued_at: "2026-10-17T05:00:00Z",
    lines: [],
    content_sha256: "0".repeat(64),
    pdf_status: "ready",
    cancelled_on: null,
    cancellation: null,
    refunded: "0.00",
    refunds: [],
    version: 1,
    ...overrides,
  };
}

type Refund = Receipt["refunds"][number];
function refund(overrides: Partial<Refund>): Refund {
  return {
    id: "0192f3a4-0000-7000-8000-00000000f401",
    receipt_id: RECEIPT,
    receipt_number: "RCT/2026-27/000123",
    account_id: ACCOUNT,
    amount: "1000.00",
    mode: "cash",
    reason: "Paid twice by the parent",
    status: "pending",
    requested_by: MEMBER,
    requested_by_name: "Synthetic Clerk",
    requested_at: "2026-10-17T06:00:00Z",
    decided_by: null,
    decided_by_name: null,
    decided_at: null,
    decision_note: null,
    voucher_number: null,
    voucher_date: null,
    version: 1,
    ...overrides,
  };
}

let stub: BffStub;
beforeEach(() => {
  stub = installBffStub("staff");
});
afterEach(() => {
  uninstallBffStub();
  expect(intlErrors).toEqual([]);
});

function signedIn(permissions: string[]) {
  stub.routes["GET /bff/api/v1/me"] = () =>
    Response.json(me(permissions, { roles: ["office_staff"] }));
}

describe("receipt requests the API would refuse are not offered (XF-01, FR-FEE-012, FR-FEE-013)", () => {
  it("FR-FEE-013 XF-01: a pending refund holds its amount; no second refund or cancellation for it", async () => {
    signedIn([COLLECT, REFUND_REQUEST]);
    stub.routes[`GET /bff/api/v1/fees/receipts/${RECEIPT}`] = () =>
      Response.json(receipt({ refunds: [refund({ amount: "6100.00" })] }));
    const user = userEvent.setup();
    renderWithIntl(<ReceiptDetailScreen receiptId={RECEIPT} />);
    await user.click(await screen.findByRole("button", { name: messages.en.ui.more }));
    expect(screen.getByRole("menuitem", { name: en.receipt.downloadPdf })).toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: en.receipt.requestRefund })).toBeNull();
    expect(screen.queryByRole("menuitem", { name: en.receipt.requestCancel })).toBeNull();
  });

  it("FR-FEE-013 XF-01: a refund above what is left after live refunds is refused in the form", async () => {
    signedIn([COLLECT, REFUND_REQUEST]);
    stub.routes[`GET /bff/api/v1/fees/receipts/${RECEIPT}`] = () =>
      Response.json(
        receipt({
          refunds: [
            refund({ amount: "1000.00", status: "pending" }),
            refund({ id: "0192f3a4-0000-7000-8000-00000000f402", status: "rejected" }),
          ],
        }),
      );
    stub.routes[`POST /bff/api/v1/fees/receipts/${RECEIPT}/refunds`] = () =>
      Response.json(refund({}), { status: 201 });
    const user = userEvent.setup();
    renderWithIntl(<ReceiptDetailScreen receiptId={RECEIPT} />);
    await user.click(await screen.findByRole("button", { name: messages.en.ui.more }));
    await user.click(screen.getByRole("menuitem", { name: en.receipt.requestRefund }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText(re(en.receipt.refundAmount)), "5100.01");
    await user.type(
      within(dialog).getByLabelText(re(en.receipt.reason)),
      "Paid twice by the parent",
    );
    await user.click(within(dialog).getByRole("button", { name: en.receipt.requestRefund }));
    expect(
      await within(dialog).findByText(messages.en.validation.refundAboveLeft),
    ).toBeInTheDocument();
    expect(stub.callsTo(`POST /bff/api/v1/fees/receipts/${RECEIPT}/refunds`)).toHaveLength(0);
    const amount = within(dialog).getByLabelText(re(en.receipt.refundAmount));
    await user.clear(amount);
    await user.type(amount, "5100");
    await user.click(within(dialog).getByRole("button", { name: en.receipt.requestRefund }));
    await waitFor(() =>
      expect(stub.callsTo(`POST /bff/api/v1/fees/receipts/${RECEIPT}/refunds`)).toHaveLength(1),
    );
  });

  it("FR-FEE-012 XF-01: an online receipt is cancelled only as a duplicate entry", async () => {
    signedIn([COLLECT]);
    stub.routes[`GET /bff/api/v1/fees/receipts/${RECEIPT}`] = () =>
      Response.json(receipt({ mode: "razorpay", collected_by: null, collected_by_name: null }));
    const user = userEvent.setup();
    renderWithIntl(<ReceiptDetailScreen receiptId={RECEIPT} />);
    await user.click(await screen.findByRole("button", { name: messages.en.ui.more }));
    await user.click(screen.getByRole("menuitem", { name: en.receipt.requestCancel }));
    const dialog = await screen.findByRole("dialog");
    const select = within(dialog).getByLabelText(re(en.receipt.reasonCode));
    const options = within(select).getAllByRole("option");
    expect(options.map((o) => o.getAttribute("value"))).toEqual(["duplicate_entry"]);
  });
});

describe("day close confirms the totals it closes (XF-02, FR-FEE-019)", () => {
  function report(overrides: Partial<DailyReport> = {}): DailyReport {
    return {
      date: "2026-10-17",
      by_mode: [],
      by_collector: [],
      receipts: [],
      cancellations: [],
      refunds: [],
      receipts_total: "0.00",
      cancelled_total: "0.00",
      refunded_total: "0.00",
      net_total: "0.00",
      cash_to_hand_over: "0.00",
      cheque_to_hand_over: "0.00",
      closed: null,
      ...overrides,
    };
  }

  it("FR-FEE-019 XF-02: opening Close day reloads the report, so a receipt taken since shows", async () => {
    signedIn([FINANCE_READ, MANAGE]);
    stub.routes["GET /bff/api/v1/fees/status"] = () => Response.json(STATUS);
    let calls = 0;
    stub.routes["GET /bff/api/v1/fees/reports/daily"] = () => {
      calls += 1;
      return Response.json(
        calls === 1
          ? report()
          : report({
              receipts: [
                {
                  id: RECEIPT,
                  receipt_number: "RCT/2026-27/000123",
                  amount: "6100.00",
                  mode: "cash",
                  status: "issued",
                  student_name: "Synthetica Fee Student",
                  class_section: "Class IX A",
                  collected_by_name: "Synthetic Clerk",
                },
              ],
              receipts_total: "6100.00",
              net_total: "6100.00",
              cash_to_hand_over: "6100.00",
            }),
      );
    };
    const user = userEvent.setup();
    renderWithIntl(<DailyScreen />);
    await user.click(await screen.findByRole("button", { name: en.daily.close }));
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByText(/1 receipt, net ₹6,100\.00/)).toBeInTheDocument();
    expect(calls).toBe(2);
  });
});

describe("own allocation at the counter stays within each line (XF-04, FR-FEE-010)", () => {
  function line(overrides: Partial<DueLine>): DueLine {
    return {
      instalment_no: 1,
      label: "Term 1",
      due_on: "2026-09-17",
      head_id: HEAD,
      head_code: "TUI",
      head_name: "Tuition fee",
      collectable: true,
      charged: "3000.00",
      concession: "0.00",
      late_fee: "0.00",
      adjustment: "0.00",
      paid: "0.00",
      due: "3000.00",
      overdue: true,
      ...overrides,
    };
  }
  const account: Account = {
    id: ACCOUNT,
    student_id: STUDENT,
    display_name: "Synthetica Fee Student",
    admission_no: "F-0001",
    class_section: "Class IX A",
    academic_year_id: "0192f3a4-0000-7000-8000-00000000f301",
    plan_id: "0192f3a4-0000-7000-8000-00000000f302",
    plan_name: "Class IX 2026-27",
    category_code: null,
    status: "open",
    lines: [line({}), line({ instalment_no: 2, label: "Term 2", overdue: false })],
    totals: {
      charged: "6000.00",
      concessions: "0.00",
      late_fees: "0.00",
      adjustments: "0.00",
      paid: "0.00",
      refunded: "0.00",
      balance: "6000.00",
      overdue: "3000.00",
      collectable: "6000.00",
    },
    pending_requests: 0,
    version: 1,
  };

  it("FR-FEE-010 XF-04: an amount above a line's due is refused before saving", async () => {
    signedIn([COLLECT]);
    stub.routes["GET /bff/api/v1/fees/status"] = () => Response.json(STATUS);
    stub.routes[`GET /bff/api/v1/fees/accounts/${ACCOUNT}`] = () => Response.json(account);
    stub.routes["POST /bff/api/v1/fees/receipts"] = () => Response.json(receipt(), { status: 201 });
    const user = userEvent.setup();
    renderWithIntl(<CounterScreen initialAccountId={ACCOUNT} />);
    const amount = await screen.findByLabelText(re(en.counter.amount));
    await user.clear(amount);
    await user.type(amount, "4000");
    await user.click(screen.getByRole("button", { name: en.counter.changeAllocation }));
    const term1 = screen.getByLabelText(/^Term 1/);
    const term2 = screen.getByLabelText(/^Term 2/);
    await user.clear(term1);
    await user.type(term1, "3500");
    await user.clear(term2);
    await user.type(term2, "500");
    await user.click(screen.getByRole("button", { name: re(en.counter.save) }));
    expect(
      (await screen.findAllByText(en.counter.errors.allocationAboveDue)).length,
    ).toBeGreaterThan(0);
    expect(stub.callsTo("POST /bff/api/v1/fees/receipts")).toHaveLength(0);
  });
});

describe("the receipt page reads the receipt again while its PDF is made (XF-07, FR-FEE-011)", () => {
  it("FR-FEE-011 XF-07: polls a queued PDF passively until ready, then stops", async () => {
    setReceiptPollDelayForTesting(() => 20);
    try {
      signedIn([COLLECT]);
      let reads = 0;
      stub.routes[`GET /bff/api/v1/fees/receipts/${RECEIPT}`] = () =>
        Response.json(receipt({ pdf_status: (reads += 1) < 3 ? "queued" : "ready" }));
      const user = userEvent.setup();
      renderWithIntl(<ReceiptDetailScreen receiptId={RECEIPT} />);
      expect(await screen.findByText(en.receipt.pdfPreparing)).toBeInTheDocument();
      await waitFor(() => expect(screen.queryByText(en.receipt.pdfPreparing)).toBeNull());
      const calls = stub.callsTo(`GET /bff/api/v1/fees/receipts/${RECEIPT}`);
      // The first read is the clerk opening the page; the polls do not keep the session awake.
      expect(calls[0]?.headers.get("x-sos-passive")).toBeNull();
      expect(calls.length).toBeGreaterThan(1);
      expect(calls.slice(1).every((c) => c.headers.get("x-sos-passive") === "1")).toBe(true);
      await user.click(screen.getByRole("button", { name: messages.en.ui.more }));
      expect(screen.getByRole("menuitem", { name: en.receipt.downloadPdf })).not.toHaveAttribute(
        "aria-disabled",
        "true",
      );
      const settled = stub.callsTo(`GET /bff/api/v1/fees/receipts/${RECEIPT}`).length;
      await new Promise((resolve) => setTimeout(resolve, 100));
      expect(stub.callsTo(`GET /bff/api/v1/fees/receipts/${RECEIPT}`)).toHaveLength(settled);
    } finally {
      setReceiptPollDelayForTesting(null);
    }
  });
});
