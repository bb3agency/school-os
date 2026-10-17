import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { installBffStub, problem, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { intlErrors, messages, renderWithIntl } from "@/test/render";
import { me, STUDENT } from "@/test/school-fixtures";
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
  type DueLine,
  type FeeStatus,
  type Receipt,
} from "./data";
import { ReceiptsScreen } from "./ReceiptsScreen";

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
    expect(await screen.findByText(en.counter.errors.amountAboveDue)).toBeInTheDocument();
    expect(screen.getByText(en.counter.errors.utr)).toBeInTheDocument();
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
