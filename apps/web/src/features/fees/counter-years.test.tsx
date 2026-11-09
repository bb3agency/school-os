import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { installBffStub, problem, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { intlErrors, messages, renderWithIntl } from "@/test/render";
import { me } from "@/test/school-fixtures";
import { CounterScreen } from "./CounterScreen";
import type { Account, AccountSummary, FeeStatus, Receipt, StudentYears } from "./data";

/**
 * FM-01 at the counter (FR-FEE-036, FR-FEE-037): the student's open accounts with earlier years
 * first, "Collect for <year>", and "Split oldest first" whose parts are issued one ordinary
 * receipt at a time. Synthetic names and amounts only.
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

const en = messages.en.fees.counter;
const re = (text: string) => new RegExp(text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
const STUDENT = "0192f3a4-0000-7000-8000-00000000e201";
const CURRENT = "0192f3a4-0000-7000-8000-00000000f211";
const EARLIER = "0192f3a4-0000-7000-8000-00000000f212";
const YEAR_NOW = "0192f3a4-0000-7000-8000-00000000c211";
const YEAR_OLD = "0192f3a4-0000-7000-8000-00000000c212";
const HEAD = "0192f3a4-0000-7000-8000-00000000f221";

const STATUS: FeeStatus = {
  enabled: true,
  today: "2026-11-09",
  financial_year: "2026-27",
  day_closed: false,
  paper_size: "A5",
};

function account(id: string, year: string, due: string): Account {
  return {
    id,
    student_id: STUDENT,
    display_name: "Synthetica Two Years",
    admission_no: "F-0201",
    class_section: "Class IX A",
    academic_year_id: year,
    plan_id: "0192f3a4-0000-7000-8000-00000000f231",
    plan_name: "Synthetic plan",
    category_code: null,
    status: "open",
    lines: [
      {
        instalment_no: 1,
        label: "Term 1",
        due_on: "2026-07-15",
        head_id: HEAD,
        head_code: "TUI",
        head_name: "Tuition fee",
        collectable: true,
        charged: due,
        concession: "0.00",
        late_fee: "0.00",
        adjustment: "0.00",
        paid: "0.00",
        due,
        overdue: true,
      },
    ],
    totals: {
      charged: due,
      concessions: "0.00",
      late_fees: "0.00",
      adjustments: "0.00",
      paid: "0.00",
      refunded: "0.00",
      balance: due,
      overdue: due,
      collectable: due,
    },
    pending_requests: 0,
    version: 1,
  };
}

function years(split: StudentYears["split"] = null, only = false): StudentYears {
  const old = {
    account_id: EARLIER,
    academic_year_id: YEAR_OLD,
    academic_year_label: "2025-26",
    earlier_year: true,
    balance: "4800.00",
    overdue: "4800.00",
    collectable: "4800.00",
    oldest_due_on: "2025-07-15",
  };
  const now = {
    account_id: CURRENT,
    academic_year_id: YEAR_NOW,
    academic_year_label: "2026-27",
    earlier_year: false,
    balance: "3500.00",
    overdue: "3500.00",
    collectable: "3500.00",
    oldest_due_on: "2026-07-15",
  };
  return {
    student_id: STUDENT,
    as_of: "2026-11-09",
    years: only ? [now] : [old, now],
    earlier_years_due: only ? "0.00" : "4800.00",
    total_collectable: only ? "3500.00" : "8300.00",
    split,
  };
}

function receipt(id: string, accountId: string, amount: string, number: string): Receipt {
  return {
    id,
    receipt_number: number,
    financial_year: "2026-27",
    receipt_date: "2026-11-09",
    amount,
    amount_in_words: "Synthetic amount in words",
    mode: "cash",
    mode_details: {},
    utr: null,
    status: "issued",
    account_id: accountId,
    student_id: STUDENT,
    student_name: "Synthetica Two Years",
    class_section: "Class IX A",
    admission_no: "F-0201",
    collected_by: null,
    collected_by_name: "Synthetic Clerk",
    issued_at: "2026-11-09T05:00:00Z",
    lines: [],
    content_sha256: "0".repeat(64),
    pdf_status: "queued",
    cancelled_on: null,
    cancellation: null,
    refunded: "0.00",
    refunds: [],
    version: 1,
  };
}

let stub: BffStub;

beforeEach(() => {
  stub = installBffStub("staff");
  stub.routes["GET /bff/api/v1/me"] = () =>
    Response.json(me(["fee.collect"], { roles: ["office_staff"] }));
  stub.routes["GET /bff/api/v1/fees/status"] = () => Response.json(STATUS);
  stub.routes[`GET /bff/api/v1/fees/accounts/${CURRENT}`] = () =>
    Response.json(account(CURRENT, YEAR_NOW, "3500.00"));
  stub.routes[`GET /bff/api/v1/fees/accounts/${EARLIER}`] = () =>
    Response.json(account(EARLIER, YEAR_OLD, "4800.00"));
  stub.routes[`GET /bff/api/v1/fees/students/${STUDENT}/years`] = (_request, url) => {
    const amount = url.searchParams.get("split_amount");
    if (amount === "8300.00") {
      return problem(422, "validation_error", {
        errors: [
          {
            field: "split_amount",
            code: "above_outstanding",
            message_key: "errors.above_outstanding",
          },
        ],
      });
    }
    return Response.json(
      years(
        amount === "5000.00"
          ? [
              { account_id: EARLIER, amount: "4800.00" },
              { account_id: CURRENT, amount: "200.00" },
            ]
          : null,
      ),
    );
  };
});
afterEach(() => {
  uninstallBffStub();
  expect(intlErrors).toEqual([]);
});

/** The years table (in its scroll region named after the panel). */
async function panel() {
  const region = await screen.findByRole("region", { name: en.years.title });
  return within(region).getByRole("table");
}

describe("Earlier years at the counter (FR-FEE-036)", () => {
  it("lists the student's open years with the earlier year first", async () => {
    renderWithIntl(<CounterScreen initialAccountId={CURRENT} />);
    const table = await panel();
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows[0]).toHaveTextContent("2025-26");
    expect(rows[0]).toHaveTextContent(en.years.earlierYear);
    expect(rows[1]).toHaveTextContent("2026-27");
    expect(rows[1]).toHaveTextContent(en.years.onForm);
    expect(screen.getByText(en.years.earlierTotal)).toBeInTheDocument();
  });

  it("puts an earlier year's account on the form with what is due on it", async () => {
    const user = userEvent.setup();
    renderWithIntl(<CounterScreen initialAccountId={CURRENT} />);
    await panel();
    await user.click(screen.getByRole("button", { name: "Collect for 2025-26" }));
    await waitFor(() => expect(screen.getByLabelText(re(en.amount))).toHaveValue("4800.00"));
    expect(stub.callsTo(`GET /bff/api/v1/fees/accounts/${EARLIER}`)).toHaveLength(1);
  });

  it("shows nothing extra when the student has one open year", async () => {
    stub.routes[`GET /bff/api/v1/fees/students/${STUDENT}/years`] = () =>
      Response.json(years(null, true));
    renderWithIntl(<CounterScreen initialAccountId={CURRENT} />);
    expect(await screen.findByLabelText(re(en.amount))).toHaveValue("3500.00");
    await waitFor(() =>
      expect(stub.callsTo(`GET /bff/api/v1/fees/students/${STUDENT}/years`)).toHaveLength(1),
    );
    expect(screen.queryByRole("region", { name: en.years.title })).toBeNull();
  });

  it("shows earlier-year dues in the student search", async () => {
    const match: AccountSummary = {
      account_id: CURRENT,
      student_id: STUDENT,
      display_name: "Synthetica Two Years",
      admission_no: "F-0201",
      class_section: "Class IX A",
      academic_year_id: YEAR_NOW,
      balance: "3500.00",
      overdue: "3500.00",
      earlier_years_due: "4800.00",
      status: "open",
    };
    stub.routes["POST /bff/api/v1/fees/accounts/search"] = () => Response.json([match]);
    const user = userEvent.setup();
    renderWithIntl(<CounterScreen />);
    await user.type(await screen.findByRole("combobox", { name: en.searchLabel }), "F-0201");
    const option = await screen.findByRole("option", { name: /Synthetica Two Years/ });
    expect(option).toHaveTextContent(en.earlierYearsDue);
    expect(option).toHaveTextContent("₹4,800.00");
  });
});

describe("Split oldest first (FR-FEE-037)", () => {
  it("splits only when asked and issues each part as its own receipt", async () => {
    let n = 0;
    stub.routes["POST /bff/api/v1/fees/receipts"] = async (request) => {
      const body = (await request.json()) as { account_id: string; amount: string };
      n += 1;
      return Response.json(
        receipt(
          `0192f3a4-0000-7000-8000-00000000f2a${n}`,
          body.account_id,
          body.amount,
          `RCT/2026-27/00020${n}`,
        ),
        { status: 201 },
      );
    };
    const user = userEvent.setup();
    renderWithIntl(<CounterScreen initialAccountId={CURRENT} />);
    await panel();
    // Nothing crosses accounts until the clerk asks.
    expect(
      stub
        .callsTo(`GET /bff/api/v1/fees/students/${STUDENT}/years`)
        .every((call) => !call.url.searchParams.has("split_amount")),
    ).toBe(true);
    const field = screen.getByLabelText(re(en.years.splitAmount));
    expect(field).toHaveValue("8300.00");
    // Above what is owed: refused on the page, no call.
    await user.clear(field);
    await user.type(field, "9000");
    await user.click(screen.getByRole("button", { name: en.years.split }));
    expect(field).toHaveAccessibleDescription(
      expect.stringContaining(en.years.errors.above_outstanding),
    );
    await user.clear(field);
    await user.type(field, "5000");
    await user.click(screen.getByRole("button", { name: en.years.split }));
    const parts = await screen.findByRole("region", { name: en.years.partsTitle });
    expect(
      stub
        .callsTo(`GET /bff/api/v1/fees/students/${STUDENT}/years`)
        .some((call) => call.url.searchParams.get("split_amount") === "5000.00"),
    ).toBe(true);
    const items = within(parts).getAllByRole("listitem");
    expect(items[0]).toHaveTextContent("2025-26");
    expect(items[0]).toHaveTextContent("₹4,800.00");
    expect(items[1]).toHaveTextContent("2026-27");
    expect(items[1]).toHaveTextContent("₹200.00");
    // Nothing is saved yet.
    expect(stub.callsTo("POST /bff/api/v1/fees/receipts")).toHaveLength(0);

    await user.click(within(parts).getByRole("button", { name: "Collect the 2025-26 part" }));
    await waitFor(() => expect(screen.getByLabelText(re(en.amount))).toHaveValue("4800.00"));
    await user.click(screen.getByRole("button", { name: re(en.save) }));
    expect(await screen.findByText("RCT/2026-27/000201")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Next part: 2026-27" }));
    await waitFor(() => expect(screen.getByLabelText(re(en.amount))).toHaveValue("200.00"));
    expect(screen.getByText(en.years.partDone)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: re(en.save) }));
    expect(await screen.findByText("RCT/2026-27/000202")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Next part/ })).toBeNull();

    const posts = stub.callsTo("POST /bff/api/v1/fees/receipts");
    expect(posts.map((call) => JSON.parse(call.body) as object)).toEqual([
      expect.objectContaining({ account_id: EARLIER, amount: "4800.00" }),
      expect.objectContaining({ account_id: CURRENT, amount: "200.00" }),
    ]);
    // One idempotency key per receipt.
    expect(posts[0]?.headers.get("Idempotency-Key")).not.toBe(
      posts[1]?.headers.get("Idempotency-Key"),
    );
  });

  it("says in plain language when the API refuses the split (422 above_outstanding)", async () => {
    const user = userEvent.setup();
    renderWithIntl(<CounterScreen initialAccountId={CURRENT} />);
    await panel();
    await user.click(screen.getByRole("button", { name: en.years.split }));
    const field = screen.getByLabelText(re(en.years.splitAmount));
    await waitFor(() =>
      expect(field).toHaveAccessibleDescription(
        expect.stringContaining(en.years.errors.above_outstanding),
      ),
    );
    expect(screen.queryByRole("region", { name: en.years.partsTitle })).toBeNull();
  });
});
