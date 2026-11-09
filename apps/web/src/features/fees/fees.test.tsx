import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ALL_STRUCTURE_KEYS } from "@/features/academic-structure/data";
import { installBffStub, problem, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { intlErrors, messages, renderWithIntl, testQueryClient } from "@/test/render";
import { me, STUDENT } from "@/test/school-fixtures";
import { AccountScreen, adjustmentSchema, concessionSchema } from "./AccountScreen";
import { ApprovalsScreen } from "./ApprovalsScreen";
import { CounterScreen } from "./CounterScreen";
import {
  COLLECT,
  CONCESSION_APPROVE,
  defaultAllocation,
  FINANCE_READ,
  fromPaise,
  toPaise,
  type Account,
  type AccountSummary,
  type ApprovalItem,
  type Concession,
  type DueLine,
  type FeeStatus,
  type Receipt,
} from "./data";
import { ReceiptDetailScreen } from "./ReceiptDetailScreen";
import { ReceiptsScreen } from "./ReceiptsScreen";
import { SetupScreen } from "./SetupScreen";

/**
 * Fee screens (R2-1B; US-2102, US-2103..US-2106, US-2110; docs/17 §5.8). Synthetic names and
 * amounts only. Amounts are exact decimal strings; the screens add them as whole paise.
 */

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => "/fees/collect",
    useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
    useSearchParams: () => new URLSearchParams(),
    useParams: () => ({ locale: "en" }),
  };
});

const en = messages.en.fees;
/** A regular expression matching `text` literally (labels with brackets, ₹ and dots). */
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

function account(): Account {
  const lines = [
    line({}),
    line({
      instalment_no: 2,
      label: "Term 2",
      due_on: "2026-11-17",
      due: "3000.00",
      overdue: false,
    }),
    line({
      head_id: null,
      head_code: null,
      head_name: null,
      charged: "0.00",
      late_fee: "100.00",
      due: "100.00",
    }),
  ];
  return {
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
    lines,
    totals: {
      charged: "6000.00",
      concessions: "0.00",
      late_fees: "100.00",
      adjustments: "0.00",
      paid: "0.00",
      refunded: "0.00",
      balance: "6100.00",
      overdue: "3100.00",
      collectable: "6100.00",
    },
    pending_requests: 0,
    version: 1,
  };
}

const MATCH: AccountSummary = {
  account_id: ACCOUNT,
  student_id: STUDENT,
  display_name: "Synthetica Fee Student",
  admission_no: "F-0001",
  class_section: "Class IX A",
  academic_year_id: "0192f3a4-0000-7000-8000-00000000f301",
  balance: "6100.00",
  overdue: "3100.00",
  earlier_years_due: "0.00",
  status: "open",
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
    pdf_status: "queued",
    cancelled_on: null,
    cancellation: null,
    refunded: "0.00",
    refunds: [],
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

describe("exact money (FR-FEE-028)", () => {
  it("converts rupees to whole paise and back without floating point", () => {
    expect(toPaise("1250.5")).toBe(125050);
    expect(toPaise("0.10")).toBe(10);
    expect(toPaise("1,25,000.00")).toBe(12500000);
    expect(toPaise("12.345")).toBeNull();
    expect(toPaise("abc")).toBeNull();
    expect(fromPaise(125050)).toBe("1250.50");
    expect(fromPaise(-5)).toBe("-0.05");
    // 0.1 + 0.2 in paise is exact.
    expect(fromPaise((toPaise("0.1") ?? 0) + (toPaise("0.2") ?? 0))).toBe("0.30");
  });

  it("allocates oldest due first and skips lines that are not collectable", () => {
    const lines = [
      line({ due: "100.00" }),
      line({ instalment_no: 2, due: "300.00", collectable: false }),
      line({ instalment_no: 3, due: "200.00" }),
    ];
    const out = defaultAllocation(lines, 250_00);
    expect(out.map((x) => [x.line.instalment_no, x.paise])).toEqual([
      [1, 100_00],
      [3, 150_00],
    ]);
  });
});

describe("fee counter (US-2102; docs/17 §5.8)", () => {
  it("says fees are off while the school has not switched them on (404 module_hidden)", async () => {
    signedIn([COLLECT]);
    stub.routes["GET /bff/api/v1/fees/status"] = () => problem(404, "module_hidden");
    renderWithIntl(<CounterScreen />);
    expect(await screen.findByText(en.off.title)).toBeInTheDocument();
  });

  it("explains who can collect fees, without calling the API, to a teacher", async () => {
    signedIn(["student.read_basic"]);
    renderWithIntl(<CounterScreen />);
    expect(await screen.findByText(en.noAccessTitle)).toBeInTheDocument();
    expect(stub.callsTo("GET /bff/api/v1/fees/status")).toHaveLength(0);
  });

  it("finds a student by keyboard, pre-fills what is due and saves with one idempotency key", async () => {
    signedIn([COLLECT]);
    stub.routes["GET /bff/api/v1/fees/status"] = () => Response.json(STATUS);
    stub.routes["POST /bff/api/v1/fees/accounts/search"] = () => Response.json([MATCH]);
    stub.routes[`GET /bff/api/v1/fees/accounts/${ACCOUNT}`] = () => Response.json(account());
    stub.routes["POST /bff/api/v1/fees/receipts"] = () => Response.json(receipt(), { status: 201 });
    const user = userEvent.setup();
    renderWithIntl(<CounterScreen />);
    const search = await screen.findByRole("combobox", { name: en.counter.searchLabel });
    await waitFor(() => expect(search).toHaveFocus());
    await user.type(search, "F-0001");
    expect(
      await screen.findByRole("option", { name: /Synthetica Fee Student/ }),
    ).toBeInTheDocument();
    // The name never goes into the URL: the search is a POST body.
    const call = stub.callsTo("POST /bff/api/v1/fees/accounts/search").at(-1);
    expect(call?.url.search).toBe("");
    expect(JSON.parse(call?.body ?? "{}")).toEqual({ query: "F-0001" });
    await user.keyboard("{Enter}");
    const amount = await screen.findByLabelText(re(en.counter.amount));
    await waitFor(() => expect(amount).toHaveFocus());
    expect(amount).toHaveValue("6100.00");
    // Q chooses cheque from the mode group; its fields appear.
    await user.click(screen.getByRole("radio", { name: re(en.modes.cash) }));
    await user.keyboard("q");
    expect(screen.getByRole("radio", { name: re(en.modes.cheque) })).toBeChecked();
    await user.keyboard("c");
    expect(screen.getByRole("radio", { name: re(en.modes.cash) })).toBeChecked();
    await user.keyboard("{Control>}{Enter}{/Control}");
    expect(await screen.findByText("RCT/2026-27/000123")).toBeInTheDocument();
    const posts = stub.callsTo("POST /bff/api/v1/fees/receipts");
    expect(posts).toHaveLength(1);
    expect(posts[0]?.headers.get("Idempotency-Key")).toMatch(/^[0-9a-f-]{36}$/);
    expect(JSON.parse(posts[0]?.body ?? "{}")).toMatchObject({
      account_id: ACCOUNT,
      amount: "6100.00",
      mode: "cash",
      allocations: null,
    });
    expect(screen.getByRole("link", { name: re(en.counter.print) })).toHaveAttribute(
      "href",
      `/bff/api/v1/fees/receipts/${RECEIPT}/print`,
    );
    // N starts the next student.
    await user.keyboard("n");
    expect(
      await screen.findByRole("combobox", { name: en.counter.searchLabel }),
    ).toBeInTheDocument();
  });

  it("checks the UTR and refuses an amount above what is due before calling the API", async () => {
    signedIn([COLLECT]);
    stub.routes["GET /bff/api/v1/fees/status"] = () => Response.json(STATUS);
    stub.routes[`GET /bff/api/v1/fees/accounts/${ACCOUNT}`] = () => Response.json(account());
    const user = userEvent.setup();
    renderWithIntl(<CounterScreen initialAccountId={ACCOUNT} />);
    const amount = await screen.findByLabelText(re(en.counter.amount));
    await user.clear(amount);
    await user.type(amount, "7000");
    await user.click(screen.getByRole("radio", { name: re(en.modes.upi_qr) }));
    await user.type(screen.getByLabelText(re(en.counter.utr)), "123");
    await user.click(screen.getByRole("button", { name: re(en.counter.save) }));
    // The error summary takes focus and links each problem to its field; each field still
    // carries its own message.
    const summary = await screen.findByRole("alert", {
      name: messages.en.ui.errorSummary.title,
    });
    await waitFor(() => expect(summary).toHaveFocus());
    expect(
      within(summary).getByRole("link", { name: en.counter.errors.amountAboveDue }),
    ).toHaveAttribute("href", `#${amount.id}`);
    expect(within(summary).getByRole("link", { name: en.counter.errors.utr })).toBeInTheDocument();
    expect(amount).toHaveAccessibleDescription(
      expect.stringContaining(en.counter.errors.amountAboveDue),
    );
    expect(screen.getByLabelText(re(en.counter.utr))).toHaveAccessibleDescription(
      expect.stringContaining(en.counter.errors.utr),
    );
    expect(stub.callsTo("POST /bff/api/v1/fees/receipts")).toHaveLength(0);
  });

  it("shows the day is closed instead of the form", async () => {
    signedIn([COLLECT]);
    stub.routes["GET /bff/api/v1/fees/status"] = () =>
      Response.json({ ...STATUS, day_closed: true });
    renderWithIntl(<CounterScreen />);
    expect(await screen.findByText(en.counter.dayClosedTitle)).toBeInTheDocument();
    expect(screen.queryByRole("combobox")).toBeNull();
  });
});

describe("receipts list", () => {
  it("strikes through cancelled receipts and says when", async () => {
    signedIn([FINANCE_READ]);
    stub.routes["GET /bff/api/v1/fees/receipts"] = () =>
      Response.json({
        data: [
          {
            id: RECEIPT,
            receipt_number: "RCT/2026-27/000123",
            receipt_date: "2026-10-17",
            amount: "6100.00",
            mode: "cash",
            status: "cancelled",
            account_id: ACCOUNT,
            student_id: STUDENT,
            student_name: "Synthetica Fee Student",
            class_section: "Class IX A",
            collected_by: MEMBER,
            collected_by_name: "Synthetic Clerk",
            cancelled_on: "2026-10-18",
            cancellation_pending: false,
            refunded: "0.00",
          },
        ],
        next_cursor: null,
      });
    renderWithIntl(<ReceiptsScreen />);
    const link = await screen.findByRole("link", { name: "RCT/2026-27/000123" });
    expect(link.className).toContain("line-through");
    expect(screen.getByText("Cancelled on 18/10/2026")).toBeInTheDocument();
  });
});

describe("receipt page", () => {
  it("prints first and asks for a cancellation from More, with its consequence", async () => {
    signedIn([COLLECT]);
    stub.routes[`GET /bff/api/v1/fees/receipts/${RECEIPT}`] = () => Response.json(receipt());
    stub.routes[`POST /bff/api/v1/fees/receipts/${RECEIPT}/cancellation`] = () =>
      Response.json({}, { status: 201 });
    const user = userEvent.setup();
    renderWithIntl(<ReceiptDetailScreen receiptId={RECEIPT} />);
    expect(await screen.findByRole("link", { name: re(en.receipt.print) })).toHaveAttribute(
      "href",
      `/bff/api/v1/fees/receipts/${RECEIPT}/print`,
    );
    // No refund permission: no refund item; the PDF is not ready yet.
    await user.click(screen.getByRole("button", { name: messages.en.ui.more }));
    expect(screen.queryByRole("menuitem", { name: en.receipt.requestRefund })).toBeNull();
    expect(screen.getByRole("menuitem", { name: en.receipt.downloadPdf })).toHaveAttribute(
      "aria-disabled",
      "true",
    );
    await user.click(screen.getByRole("menuitem", { name: en.receipt.requestCancel }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(en.receipt.cancelConsequence)).toBeInTheDocument();
    await user.type(within(dialog).getByLabelText(re(en.receipt.reason)), "Typed the wrong amount");
    await user.click(within(dialog).getByRole("button", { name: en.receipt.requestCancel }));
    await waitFor(() =>
      expect(stub.callsTo(`POST /bff/api/v1/fees/receipts/${RECEIPT}/cancellation`)).toHaveLength(
        1,
      ),
    );
    const call = stub.callsTo(`POST /bff/api/v1/fees/receipts/${RECEIPT}/cancellation`)[0];
    expect(call?.headers.get("Idempotency-Key")).toBeTruthy();
  });

  it("SEC-010 WS-03: a receipt PDF link that is not https is refused with an error, never opened", async () => {
    signedIn([COLLECT]);
    stub.routes[`GET /bff/api/v1/fees/receipts/${RECEIPT}`] = () =>
      Response.json(receipt({ pdf_status: "ready" }));
    stub.routes[`GET /bff/api/v1/fees/receipts/${RECEIPT}/download-url`] = () =>
      Response.json({
        url: "javascript:alert(1)",
        expires_at: "2026-10-17T05:05:00Z",
        filename: "receipt.pdf",
      });
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign });
    const user = userEvent.setup();
    renderWithIntl(<ReceiptDetailScreen receiptId={RECEIPT} />);
    await user.click(await screen.findByRole("button", { name: messages.en.ui.more }));
    await user.click(screen.getByRole("menuitem", { name: en.receipt.downloadPdf }));
    expect(await screen.findByText("Something went wrong")).toBeInTheDocument();
    expect(assign).not.toHaveBeenCalled();
    vi.unstubAllGlobals();
  });

  it("opens a More dialog without a hidden trigger, returns focus to More and opens it again", async () => {
    signedIn([COLLECT]);
    stub.routes[`GET /bff/api/v1/fees/receipts/${RECEIPT}`] = () => Response.json(receipt());
    const user = userEvent.setup();
    renderWithIntl(<ReceiptDetailScreen receiptId={RECEIPT} />);
    const more = await screen.findByRole("button", { name: messages.en.ui.more });
    // Controlled open: no hidden trigger buttons sit in the page any more.
    expect(document.querySelector("button[hidden]")).toBeNull();
    for (let round = 0; round < 2; round += 1) {
      await user.click(more);
      await user.click(screen.getByRole("menuitem", { name: en.receipt.requestCancel }));
      const dialog = await screen.findByRole("dialog");
      expect(within(dialog).getByText(en.receipt.cancelConsequence)).toBeInTheDocument();
      await user.keyboard("{Escape}");
      await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
      await waitFor(() => expect(more).toHaveFocus());
    }
  });
});

describe("approvals inbox (D17: never one's own request)", () => {
  function item(overrides: Partial<ApprovalItem>): ApprovalItem {
    return {
      kind: "cancellation",
      id: "0192f3a4-0000-7000-8000-00000000f401",
      account_id: ACCOUNT,
      student_id: STUDENT,
      student_name: "Synthetica Fee Student",
      class_section: "Class IX A",
      amount: "6100.00",
      summary_code: "wrong_amount",
      reason: "Typed the wrong amount",
      requested_by: MEMBER,
      requested_by_name: "Synthetic Clerk",
      requested_at: "2026-10-17T05:00:00Z",
      receipt_id: RECEIPT,
      receipt_number: "RCT/2026-27/000123",
      mine: false,
      can_decide: true,
      version: 1,
      ...overrides,
    };
  }

  it("offers approve and reject to a checker and withdraw to the maker", async () => {
    signedIn([CONCESSION_APPROVE, "fee.receipt.cancel"]);
    stub.routes["GET /bff/api/v1/fees/approvals"] = () =>
      Response.json([
        item({}),
        item({ id: "0192f3a4-0000-7000-8000-00000000f402", mine: true, can_decide: false }),
      ]);
    stub.routes[
      "POST /bff/api/v1/fees/cancellations/0192f3a4-0000-7000-8000-00000000f401/approve"
    ] = () => Response.json({}, { status: 200 });
    const user = userEvent.setup();
    renderWithIntl(<ApprovalsScreen />);
    const approve = await screen.findAllByRole("button", { name: en.approvals.approve });
    expect(approve).toHaveLength(1);
    expect(screen.getByText(en.approvals.mine)).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: en.approvals.withdraw })).toHaveLength(1);
    await user.click(approve[0] as HTMLElement);
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(messages.en.common.stepUpNote)).toBeInTheDocument();
    expect(within(dialog).getByText(en.approvals.consequence.cancellation)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: en.approvals.approve }));
    await waitFor(() =>
      expect(
        stub.callsTo(
          "POST /bff/api/v1/fees/cancellations/0192f3a4-0000-7000-8000-00000000f401/approve",
        ),
      ).toHaveLength(1),
    );
  });
});

describe("fee problems the API returns have plain-language messages (FR-FEE-007..009)", () => {
  /** 409/422 problem codes of app/fees with no shared `errors.api.*` message. */
  const FEE_CODES = [
    "above_late_fee",
    "refund_first",
    "voucher_not_ready",
    "plan_not_draft",
    "plan_already_active",
    "plan_not_active",
    "too_many_heads",
    "too_many_students",
  ];
  /** Field codes (`errors.<code>`) of app/fees validation failures. */
  const FIELD_CODES = [
    "above_100_percent",
    "above_late_fee",
    "waiver_reduces_late_fee",
    "zero",
    "above_due",
    "concession_exceeds_due",
    "test_keys_in_prod",
    "allocations_do_not_add_up",
    "invalid_utr",
    "invalid_cheque_number",
    "invalid_reference",
    "not_for_mode",
    "original_method_only",
    "original_method_online_only",
    "in_future",
    "too_old",
    "invalid_range",
    // Razorpay refused the key id and secret at connect (WD-03, FR-PAY-003).
    "keys_rejected",
  ];

  it("has a title and body for every fee problem code, in en and te", () => {
    for (const locale of ["en", "te"] as const) {
      const errors = messages[locale].fees.errors as Record<
        string,
        { title?: string; body?: string }
      >;
      for (const code of FEE_CODES) {
        expect(typeof errors[code]?.title, `${locale}:${code}`).toBe("string");
        expect(typeof errors[code]?.body, `${locale}:${code}`).toBe("string");
      }
      const fields = messages[locale].errors.field as unknown as Record<string, unknown>;
      for (const code of FIELD_CODES) {
        expect(typeof fields[code], `${locale}:${code}`).toBe("string");
      }
    }
  });

  it("explains a waiver above the late fee left when approving (409 above_late_fee)", async () => {
    signedIn([CONCESSION_APPROVE]);
    const id = "0192f3a4-0000-7000-8000-00000000f403";
    stub.routes["GET /bff/api/v1/fees/approvals"] = () =>
      Response.json([
        {
          kind: "adjustment",
          id,
          account_id: ACCOUNT,
          student_id: STUDENT,
          student_name: "Synthetica Fee Student",
          class_section: "Class IX A",
          amount: "-100.00",
          summary_code: "late_fee_waiver",
          reason: "Waive the late fee",
          requested_by: MEMBER,
          requested_by_name: "Synthetic Clerk",
          requested_at: "2026-10-17T05:00:00Z",
          receipt_id: null,
          receipt_number: null,
          mine: false,
          can_decide: true,
          version: 1,
        } satisfies ApprovalItem,
      ]);
    stub.routes[`POST /bff/api/v1/fees/adjustments/${id}/approve`] = () =>
      problem(409, "above_late_fee");
    const user = userEvent.setup();
    renderWithIntl(<ApprovalsScreen />);
    await user.click(await screen.findByRole("button", { name: en.approvals.approve }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: en.approvals.approve }));
    const errors = en.errors as Record<string, { title: string } | undefined>;
    expect(
      await within(dialog).findByText(errors.above_late_fee?.title ?? "(no message)"),
    ).toBeInTheDocument();
  });
});

describe("own allocation at the counter (FR-FEE-010, FR-FEE-028)", () => {
  it("says the most each line takes in rupees with Indian grouping", async () => {
    signedIn([COLLECT]);
    stub.routes["GET /bff/api/v1/fees/status"] = () => Response.json(STATUS);
    stub.routes["POST /bff/api/v1/fees/accounts/search"] = () => Response.json([MATCH]);
    const big = account();
    big.lines = big.lines.map((l, i) => (i === 0 ? { ...l, due: "125000.00" } : l));
    big.totals = { ...big.totals, collectable: "128100.00" };
    stub.routes[`GET /bff/api/v1/fees/accounts/${ACCOUNT}`] = () => Response.json(big);
    const user = userEvent.setup();
    renderWithIntl(<CounterScreen />);
    const search = await screen.findByRole("combobox", { name: en.counter.searchLabel });
    await user.type(search, "F-0001");
    await screen.findByRole("option", { name: /Synthetica Fee Student/ });
    await user.keyboard("{Enter}");
    await user.click(await screen.findByRole("button", { name: en.counter.changeAllocation }));
    const upTo = (amount: string) => en.counter.upTo.replace("{amount}", amount);
    expect(await screen.findByText(upTo("₹1,25,000.00"))).toBeInTheDocument();
    expect(screen.queryByText(upTo("125000.00"))).not.toBeInTheDocument();
  });
});

describe("fee plans (FR-FEE-004)", () => {
  it("makes a new version with an Idempotency-Key, so a retry does not make a second draft", async () => {
    signedIn(["fee.manage"]);
    const PLAN = "0192f3a4-0000-7000-8000-00000000f302";
    stub.routes["GET /bff/api/v1/fees/status"] = () => Response.json(STATUS);
    stub.routes["GET /bff/api/v1/fees/heads"] = () => Response.json([]);
    stub.routes["GET /bff/api/v1/academic-years"] = () =>
      Response.json({ data: [], next_cursor: null });
    stub.routes["GET /bff/api/v1/classes"] = () => Response.json({ data: [], next_cursor: null });
    const plan = {
      id: PLAN,
      academic_year_id: "0192f3a4-0000-7000-8000-00000000f301",
      class_id: "0192f3a4-0000-7000-8000-00000000f303",
      category_code: null,
      plan_version: 1,
      status: "active",
      name: "Class IX 2026-27",
      late_fee_rule: {},
      previous_plan_id: null,
      lines: [],
      instalments: [],
      annual_total: "0.00",
      accounts: 0,
      version: 1,
    };
    stub.routes["GET /bff/api/v1/fees/plans"] = () => Response.json([plan]);
    stub.routes[`POST /bff/api/v1/fees/plans/${PLAN}/new-version`] = () =>
      Response.json({ ...plan, status: "draft", plan_version: 2 }, { status: 201 });
    const user = userEvent.setup();
    renderWithIntl(<SetupScreen />);
    await user.click(await screen.findByRole("button", { name: en.setup.newVersion }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: en.setup.newVersion }));
    await waitFor(() =>
      expect(stub.callsTo(`POST /bff/api/v1/fees/plans/${PLAN}/new-version`)).toHaveLength(1),
    );
    const [call] = stub.callsTo(`POST /bff/api/v1/fees/plans/${PLAN}/new-version`);
    expect(call?.headers.get("idempotency-key")).toBeTruthy();
  });
});

describe("account requests check what the API checks (FR-FEE-007, FR-FEE-009)", () => {
  type Parsed = {
    success: boolean;
    error?: { issues: readonly { path: readonly PropertyKey[]; message: string }[] };
  };
  const issues = (parsed: Parsed) =>
    parsed.success
      ? []
      : (parsed.error?.issues ?? []).map((i) => `${i.path.map(String).join(".")}:${i.message}`);
  const base = { concession_type: "merit", target: `1:${HEAD}`, reason: "Merit scholarship" };

  it("refuses a percent concession above 100", () => {
    const schema = concessionSchema();
    expect(issues(schema.safeParse({ ...base, basis: "percent", value: "101" }))).toEqual([
      "value:invalidPercent",
    ]);
    expect(issues(schema.safeParse({ ...base, basis: "percent", value: "100" }))).toEqual([]);
    expect(issues(schema.safeParse({ ...base, basis: "amount", value: "101" }))).toEqual([]);
  });

  it("keeps a late-fee waiver on the late fee, negative and within what is left", () => {
    const schema = adjustmentSchema(account().lines);
    const ask = (target: string, amount: string, reason_code: string) =>
      issues(schema.safeParse({ target, amount, reason_code, reason: "Waive the late fee" }));
    expect(ask("1:late", "-100", "late_fee_waiver")).toEqual([]);
    expect(ask("1:late", "-100.01", "late_fee_waiver")).toEqual(["amount:aboveLateFee"]);
    expect(ask("1:late", "50", "late_fee_waiver")).toEqual(["amount:waiverReducesLateFee"]);
    expect(ask(`1:${HEAD}`, "-50", "late_fee_waiver")).toEqual(["target:waiverOnLateFee"]);
    expect(ask("1:late", "-50", "correction")).toEqual(["reason_code:lateFeeWaiverOnly"]);
    expect(ask(`1:${HEAD}`, "-50", "correction")).toEqual([]);
    for (const key of [
      "aboveLateFee",
      "waiverReducesLateFee",
      "waiverOnLateFee",
      "lateFeeWaiverOnly",
    ]) {
      expect(messages.en.validation).toHaveProperty(key);
    }
  });
});

describe("decided requests show the checker's note (WF-03, FR-FEE-026)", () => {
  const decided = {
    requested_by: MEMBER,
    requested_by_name: "Synthetic Clerk",
    requested_at: "2026-10-16T05:00:00Z",
    decided_by: "0192f3a4-0000-7000-8000-0000000000c1",
    decided_by_name: "Synthetic Principal",
    decided_at: "2026-10-17T05:00:00Z",
    decision_note: "No sibling record in the admission register",
    version: 2,
  };
  const noteFrom = en.decisionNote.replace("{name}", "Synthetic Principal");

  it("the account's concession card shows why a concession was rejected", async () => {
    signedIn([COLLECT, FINANCE_READ]);
    stub.routes[`GET /bff/api/v1/fees/accounts/${ACCOUNT}`] = () => Response.json(account());
    stub.routes[`GET /bff/api/v1/fees/accounts/${ACCOUNT}/concessions`] = () =>
      Response.json([
        {
          id: "0192f3a4-0000-7000-8000-00000000f401",
          account_id: ACCOUNT,
          concession_type: "sibling",
          basis: "percent",
          value: "10",
          targets: [],
          reason: "Elder sister studies in Class X",
          evidence_document_id: null,
          status: "rejected",
          total_amount: null,
          ...decided,
        } satisfies Concession,
      ]);
    stub.routes[`GET /bff/api/v1/fees/accounts/${ACCOUNT}/adjustments`] = () => Response.json([]);
    renderWithIntl(<AccountScreen accountId={ACCOUNT} />);
    expect(await screen.findByText(decided.decision_note)).toBeInTheDocument();
    expect(screen.getByText(noteFrom)).toBeInTheDocument();
  });

  it("the receipt page shows why a cancellation was rejected", async () => {
    signedIn([COLLECT]);
    stub.routes[`GET /bff/api/v1/fees/receipts/${RECEIPT}`] = () =>
      Response.json(
        receipt({
          cancellation: {
            id: "0192f3a4-0000-7000-8000-00000000f501",
            receipt_id: RECEIPT,
            receipt_number: "RCT/2026-27/000123",
            reason_code: "wrong_amount",
            reason: "Typed the wrong amount",
            status: "rejected",
            cancelled_on: null,
            ...decided,
            decision_note: "The amount matches the counterfoil",
          },
        }),
      );
    renderWithIntl(<ReceiptDetailScreen receiptId={RECEIPT} />);
    expect(await screen.findByText("The amount matches the counterfoil")).toBeInTheDocument();
    expect(screen.getByText(noteFrom)).toBeInTheDocument();
  });
});

describe("new fee plan: the late fee is checked before saving (WD-01, FR-FEE-009)", () => {
  const YEAR = "0192f3a4-0000-7000-8000-00000000f301";
  const CLASS = "0192f3a4-0000-7000-8000-00000000f303";

  function setupRoutes() {
    signedIn(["fee.manage"]);
    stub.routes["GET /bff/api/v1/fees/status"] = () => Response.json(STATUS);
    stub.routes["GET /bff/api/v1/fees/heads"] = () =>
      Response.json([
        {
          id: HEAD,
          code: "TUI",
          name: "Tuition fee",
          kind: "tuition",
          gst_exempt: true,
          active: true,
          sort_order: 1,
          version: 1,
        },
      ]);
    stub.routes["GET /bff/api/v1/fees/plans"] = () => Response.json([]);
    stub.routes["GET /bff/api/v1/academic-years"] = () =>
      Response.json({
        data: [
          {
            id: YEAR,
            label: "2026-27",
            starts_on: "2026-06-01",
            ends_on: "2027-04-30",
            is_current: true,
          },
        ],
        next_cursor: null,
      });
    stub.routes["GET /bff/api/v1/classes"] = () =>
      Response.json({ data: [{ id: CLASS, display_en: "Class IX" }], next_cursor: null });
    stub.routes["POST /bff/api/v1/fees/plans"] = () =>
      Response.json({ id: "0192f3a4-0000-7000-8000-00000000f399" }, { status: 201 });
  }

  async function fillPlan(user: ReturnType<typeof userEvent.setup>) {
    await user.click(await screen.findByRole("button", { name: en.setup.newPlan }));
    await user.type(await screen.findByLabelText(en.setup.planName), "Class IX 2026-27");
    await user.type(screen.getByRole("textbox", { name: "Tuition fee, instalment 1" }), "12000");
    await user.selectOptions(screen.getByLabelText(en.setup.lateRule), "per_day");
  }

  it(
    "refuses a missing amount, too many grace days and a cap below the amount without calling the API",
    { timeout: 20_000 },
    async () => {
      setupRoutes();
      const user = userEvent.setup();
      renderWithIntl(<SetupScreen />);
      await fillPlan(user);
      await user.type(screen.getByLabelText(en.setup.lateCap), "5");
      await user.clear(screen.getByLabelText(en.setup.lateGrace));
      await user.type(screen.getByLabelText(en.setup.lateGrace), "120");
      await user.click(screen.getByRole("button", { name: en.setup.saveDraft }));
      expect((await screen.findAllByText(en.setup.check.lateAmount)).length).toBeGreaterThan(0);
      expect(screen.getAllByText(en.setup.check.lateGrace).length).toBeGreaterThan(0);
      expect(stub.callsTo("POST /bff/api/v1/fees/plans")).toHaveLength(0);

      await user.type(screen.getByLabelText(en.setup.lateAmount), "10");
      await user.clear(screen.getByLabelText(en.setup.lateGrace));
      await user.type(screen.getByLabelText(en.setup.lateGrace), "5");
      await user.click(screen.getByRole("button", { name: en.setup.saveDraft }));
      expect((await screen.findAllByText(en.setup.check.lateCap)).length).toBeGreaterThan(0);
      expect(stub.callsTo("POST /bff/api/v1/fees/plans")).toHaveLength(0);
    },
  );

  it(
    "sends the late fee as exact decimal strings (1,250 -> 1250.00)",
    { timeout: 20_000 },
    async () => {
      setupRoutes();
      const user = userEvent.setup();
      renderWithIntl(<SetupScreen />);
      await fillPlan(user);
      await user.type(screen.getByLabelText(en.setup.lateAmount), "10");
      await user.type(screen.getByLabelText(en.setup.lateCap), "1,250");
      await user.click(screen.getByRole("button", { name: en.setup.saveDraft }));
      await waitFor(() => expect(stub.callsTo("POST /bff/api/v1/fees/plans")).toHaveLength(1));
      const [call] = stub.callsTo("POST /bff/api/v1/fees/plans");
      const body = JSON.parse(call?.body ?? "{}") as { late_fee_rule: Record<string, unknown> };
      expect(body.late_fee_rule).toEqual({
        kind: "per_day",
        amount: "10.00",
        grace_days: 0,
        cap: "1250.00",
      });
    },
  );

  it("two synchronous submits save one plan (WD-06, FR-FEE-004)", { timeout: 20_000 }, async () => {
    setupRoutes();
    const user = userEvent.setup();
    renderWithIntl(<SetupScreen />);
    await fillPlan(user);
    await user.selectOptions(screen.getByLabelText(en.setup.lateRule), "none");
    const form = screen.getByRole("form", { name: en.setup.editorTitle });
    act(() => {
      fireEvent.submit(form);
      fireEvent.submit(form);
    });
    await waitFor(() => expect(stub.callsTo("POST /bff/api/v1/fees/plans")).toHaveLength(1));
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(stub.callsTo("POST /bff/api/v1/fees/plans")).toHaveLength(1);
  });

  it("a class added in academic structure appears in the plan's class picker without a reload (WD-08, FR-TEN-010)", async () => {
    setupRoutes();
    const client = testQueryClient();
    const user = userEvent.setup();
    renderWithIntl(<SetupScreen />, "en", client);
    await user.click(await screen.findByRole("button", { name: en.setup.newPlan }));
    const picker = await screen.findByLabelText(en.setup.class);
    expect(within(picker).queryByRole("option", { name: "Class X" })).toBeNull();
    stub.routes["GET /bff/api/v1/classes"] = () =>
      Response.json({
        data: [
          { id: CLASS, display_en: "Class IX" },
          { id: "0192f3a4-0000-7000-8000-00000000f304", display_en: "Class X" },
        ],
        next_cursor: null,
      });
    // What every structure write does (AcademicStructureScreen: invalidate ALL_STRUCTURE_KEYS).
    await act(() =>
      Promise.all(ALL_STRUCTURE_KEYS.map((queryKey) => client.invalidateQueries({ queryKey }))),
    );
    expect(await within(picker).findByRole("option", { name: "Class X" })).toBeInTheDocument();
  });
});

describe("two Enter presses in one frame record one payment (WD-06, FR-FEE-010)", () => {
  it("the counter sends one receipt for two synchronous submits", async () => {
    signedIn([COLLECT]);
    stub.routes["GET /bff/api/v1/fees/status"] = () => Response.json(STATUS);
    stub.routes[`GET /bff/api/v1/fees/accounts/${ACCOUNT}`] = () => Response.json(account());
    stub.routes["POST /bff/api/v1/fees/receipts"] = () => Response.json(receipt(), { status: 201 });
    renderWithIntl(<CounterScreen initialAccountId={ACCOUNT} />);
    const form = await screen.findByRole("form", { name: en.counter.paymentTitle });
    act(() => {
      fireEvent.submit(form);
      fireEvent.submit(form);
    });
    expect(await screen.findByText("RCT/2026-27/000123")).toBeInTheDocument();
    expect(stub.callsTo("POST /bff/api/v1/fees/receipts")).toHaveLength(1);
  });
});
