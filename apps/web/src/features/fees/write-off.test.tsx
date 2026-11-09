import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { installBffStub, problem, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { intlErrors, messages, renderWithIntl } from "@/test/render";
import { me } from "@/test/school-fixtures";
import { AccountScreen, adjustmentSchema } from "./AccountScreen";
import { ApprovalsScreen } from "./ApprovalsScreen";
import type { Account, ApprovalItem, Statement } from "./data";

/**
 * FM-01 write-off (FR-FEE-041, FR-FEE-042): reason `write_off` "Written off by management" in
 * the adjustment form (a negative amount on a fee line, never more than its due; the API's 422
 * write_off_reduces_due and 409 adjustment_exceeds_due in plain language), the label on the
 * approval queue and on the statement. Synthetic names and amounts only.
 */

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => "/fees/accounts/x",
    useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
    useSearchParams: () => new URLSearchParams(),
    useParams: () => ({ locale: "en" }),
  };
});

const en = messages.en.fees;
const LABEL = en.adjustmentReasons.write_off;
const ACCOUNT = "0192f3a4-0000-7000-8000-00000000f301";
const STUDENT = "0192f3a4-0000-7000-8000-00000000e301";
const HEAD = "0192f3a4-0000-7000-8000-00000000f321";
const MEMBER = "0192f3a4-0000-7000-8000-0000000000b9";

function account(): Account {
  return {
    id: ACCOUNT,
    student_id: STUDENT,
    display_name: "Synthetica Write Off",
    admission_no: "F-0301",
    class_section: "Class X A",
    academic_year_id: "0192f3a4-0000-7000-8000-00000000c301",
    plan_id: "0192f3a4-0000-7000-8000-00000000f331",
    plan_name: "Synthetic plan 2025-26",
    category_code: null,
    status: "open",
    lines: [
      {
        instalment_no: 1,
        label: "Term 1",
        due_on: "2025-07-15",
        head_id: HEAD,
        head_code: "TUI",
        head_name: "Tuition fee",
        collectable: true,
        charged: "3000.00",
        concession: "0.00",
        late_fee: "0.00",
        adjustment: "0.00",
        paid: "1000.00",
        due: "2000.00",
        overdue: true,
      },
      {
        instalment_no: 1,
        label: "Term 1",
        due_on: "2025-07-15",
        head_id: null,
        head_code: null,
        head_name: null,
        collectable: true,
        charged: "0.00",
        concession: "0.00",
        late_fee: "100.00",
        adjustment: "0.00",
        paid: "0.00",
        due: "100.00",
        overdue: true,
      },
    ],
    totals: {
      charged: "3000.00",
      concessions: "0.00",
      late_fees: "100.00",
      adjustments: "0.00",
      paid: "1000.00",
      refunded: "0.00",
      balance: "2100.00",
      overdue: "2100.00",
      collectable: "2100.00",
    },
    pending_requests: 0,
    version: 3,
  };
}

const STATEMENT: Statement = {
  account_id: ACCOUNT,
  entries: [
    {
      id: "0192f3a4-0000-7000-8000-00000000f341",
      entry_date: "2026-11-09",
      entry_kind: "adjustment",
      reason_code: "write_off",
      instalment_no: 1,
      head_code: "TUI",
      receipt_id: null,
      receipt_number: null,
      source_id: null,
      amount: "-2000.00",
      balance: "100.00",
    },
  ],
  balance: "100.00",
};

let stub: BffStub;

beforeEach(() => {
  stub = installBffStub("staff");
  stub.routes[`GET /bff/api/v1/fees/accounts/${ACCOUNT}`] = () => Response.json(account());
  stub.routes[`GET /bff/api/v1/fees/accounts/${ACCOUNT}/statement`] = () =>
    Response.json(STATEMENT);
  stub.routes[`GET /bff/api/v1/fees/accounts/${ACCOUNT}/concessions`] = () => Response.json([]);
  stub.routes[`GET /bff/api/v1/fees/accounts/${ACCOUNT}/adjustments`] = () => Response.json([]);
});
afterEach(() => {
  uninstallBffStub();
  expect(intlErrors).toEqual([]);
});

function signedIn(permissions: string[]) {
  stub.routes["GET /bff/api/v1/me"] = () =>
    Response.json(me(permissions, { roles: ["accountant"] }));
}

async function openForm() {
  const user = userEvent.setup();
  renderWithIntl(<AccountScreen accountId={ACCOUNT} />);
  await user.click(await screen.findByRole("button", { name: messages.en.ui.more }));
  await user.click(await screen.findByRole("menuitem", { name: en.account.requestAdjustment }));
  const dialog = await screen.findByRole("dialog", { name: en.account.adjustmentTitle });
  return { user, dialog };
}

async function fill(user: ReturnType<typeof userEvent.setup>, dialog: HTMLElement, amount: string) {
  await user.selectOptions(within(dialog).getByLabelText(en.account.line), `1:${HEAD}`);
  await user.type(within(dialog).getByLabelText(en.account.adjustmentAmount), amount);
  await user.selectOptions(within(dialog).getByLabelText(en.account.reasonCode), "write_off");
  await user.type(
    within(dialog).getByLabelText(en.account.reason),
    "Family moved away, management decided",
  );
  await user.click(within(dialog).getByRole("button", { name: en.account.requestAdjustment }));
}

describe("write-off in the adjustment form (FR-FEE-041)", () => {
  it("is offered as 'Written off by management' only to fee.manage", async () => {
    signedIn(["finance.read", "fee.manage"]);
    const { dialog } = await openForm();
    const reason = within(dialog).getByLabelText(en.account.reasonCode);
    expect(within(reason).getByRole("option", { name: LABEL })).toHaveValue("write_off");
  });

  it("is not in the menu without fee.manage", async () => {
    signedIn(["finance.read"]);
    renderWithIntl(<AccountScreen accountId={ACCOUNT} />);
    await screen.findByRole("heading", { name: "Synthetica Write Off" });
    expect(screen.queryByRole("button", { name: messages.en.ui.more })).toBeNull();
  });

  it("is checked like the API: negative, on a fee head, at most what is due", () => {
    const schema = adjustmentSchema(account().lines);
    const ask = (target: string, amount: string) => {
      const parsed = schema.safeParse({
        target,
        amount,
        reason_code: "write_off",
        reason: "Management decided",
      });
      return parsed.success
        ? []
        : parsed.error.issues.map((i) => `${String(i.path[0])}:${i.message}`);
    };
    expect(ask(`1:${HEAD}`, "-2000")).toEqual([]);
    expect(ask(`1:${HEAD}`, "500")).toEqual(["amount:writeOffReducesDue"]);
    expect(ask(`1:${HEAD}`, "-2000.01")).toEqual(["amount:writeOffAboveDue"]);
    expect(ask("1:late", "-50")).toEqual(["reason_code:lateFeeWaiverOnly"]);
    expect(messages.en.validation).toHaveProperty("writeOffReducesDue");
    expect(messages.en.validation).toHaveProperty("writeOffAboveDue");
  });

  it("sends a negative write-off with one idempotency key", async () => {
    signedIn(["finance.read", "fee.manage"]);
    stub.routes[`POST /bff/api/v1/fees/accounts/${ACCOUNT}/adjustments`] = () =>
      Response.json({ id: "0192f3a4-0000-7000-8000-00000000f351" }, { status: 201 });
    const { user, dialog } = await openForm();
    await fill(user, dialog, "-2000");
    await waitFor(() =>
      expect(stub.callsTo(`POST /bff/api/v1/fees/accounts/${ACCOUNT}/adjustments`)).toHaveLength(1),
    );
    const call = stub.callsTo(`POST /bff/api/v1/fees/accounts/${ACCOUNT}/adjustments`)[0];
    expect(call?.headers.get("Idempotency-Key")).toBeTruthy();
    expect(JSON.parse(call?.body ?? "{}")).toMatchObject({
      instalment_no: 1,
      head_id: HEAD,
      amount: "-2000",
      reason_code: "write_off",
    });
  });

  it("shows a positive amount refused by the API in plain language (422 write_off_reduces_due)", async () => {
    signedIn(["finance.read", "fee.manage"]);
    stub.routes[`POST /bff/api/v1/fees/accounts/${ACCOUNT}/adjustments`] = () =>
      problem(422, "validation_error", {
        errors: [
          {
            field: "amount",
            code: "write_off_reduces_due",
            message_key: "errors.write_off_reduces_due",
          },
        ],
      });
    const { user, dialog } = await openForm();
    // The page checks the sign too; a stale due on the page leaves the API to refuse.
    await fill(user, dialog, "-10");
    expect(
      await within(dialog).findByText(messages.en.errors.field.write_off_reduces_due),
    ).toBeInTheDocument();
  });

  it("explains 409 adjustment_exceeds_due", async () => {
    signedIn(["finance.read", "fee.manage"]);
    stub.routes[`POST /bff/api/v1/fees/accounts/${ACCOUNT}/adjustments`] = () =>
      problem(409, "adjustment_exceeds_due");
    const { user, dialog } = await openForm();
    await fill(user, dialog, "-1500");
    expect(
      await within(dialog).findByText(en.errors.adjustment_exceeds_due.title),
    ).toBeInTheDocument();
    expect(within(dialog).getByText(en.errors.adjustment_exceeds_due.body)).toBeInTheDocument();
  });
});

describe("the write-off label (FR-FEE-042)", () => {
  it("reads 'Written off by management' on the statement", async () => {
    signedIn(["finance.read"]);
    const user = userEvent.setup();
    renderWithIntl(<AccountScreen accountId={ACCOUNT} />);
    await user.click(await screen.findByRole("tab", { name: en.account.tabStatement }));
    const table = await screen.findByRole("region", { name: en.account.statementTitle });
    expect(await within(table).findByText(LABEL)).toBeInTheDocument();
  });

  it("reads 'Written off by management' in the approval queue", async () => {
    signedIn(["fee.concession.approve"]);
    stub.routes["GET /bff/api/v1/fees/approvals"] = () =>
      Response.json([
        {
          kind: "adjustment",
          id: "0192f3a4-0000-7000-8000-00000000f361",
          account_id: ACCOUNT,
          student_id: STUDENT,
          student_name: "Synthetica Write Off",
          class_section: "Class X A",
          amount: "-2000.00",
          summary_code: "write_off",
          reason: "Family moved away, management decided",
          requested_by: MEMBER,
          requested_by_name: "Synthetic Clerk",
          requested_at: "2026-11-09T05:00:00Z",
          receipt_id: null,
          receipt_number: null,
          mine: false,
          can_decide: true,
          version: 1,
        } satisfies ApprovalItem,
      ]);
    renderWithIntl(<ApprovalsScreen />);
    expect(await screen.findByText(LABEL)).toBeInTheDocument();
  });
});
