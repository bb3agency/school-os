import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { installBffStub, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { intlErrors, messages, renderWithIntl } from "@/test/render";
import { me } from "@/test/school-fixtures";
import { ChequesScreen } from "./ChequesScreen";
import { depositable, returnable, type Cheque } from "./cheques";
import { initialSelection, receiptModeFor, type StatementLine } from "./upi";

/**
 * Cheque status and the deposit list (FM-08; FR-FEE-131..141). Synthetic cheques, receipts and
 * students only. Nothing here edits a receipt: a returned cheque asks for a cancellation.
 */

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => "/fees/cheques",
    useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
    useSearchParams: () => new URLSearchParams(),
    useParams: () => ({ locale: "en" }),
  };
});

const en = messages.en.fees.cheques;
const R1 = "0192f3a4-0000-7000-8000-00000000c101";
const R2 = "0192f3a4-0000-7000-8000-00000000c102";

function cheque(overrides: Partial<Cheque>): Cheque {
  return {
    receipt_id: R1,
    receipt_number: "RCT/2026-27/000101",
    receipt_date: "2026-11-10",
    receipt_status: "issued",
    amount: "4500.00",
    cheque_number: "123456",
    bank_name: "Synthetic Co-operative Bank",
    cheque_date: "2026-11-10",
    student_name: "Synthetica Cheque Student",
    class_section: "Class IX A",
    cheque_status: "received",
    deposited_on: null,
    cleared_on: null,
    cleared_from_statement: false,
    returned_on: null,
    cancellation_id: null,
    deposit_overdue: false,
    ...overrides,
  };
}

const LIST = {
  as_of: "2026-11-12",
  date_from: "2026-10-13",
  date_to: "2026-11-12",
  deposit_due_days: 2,
  items: [
    cheque({}),
    cheque({
      receipt_id: R2,
      receipt_number: "RCT/2026-27/000102",
      cheque_number: "654321",
      cheque_status: "deposited",
      deposited_on: "2026-11-11",
    }),
  ],
  counts: { received: 1, deposited: 1, cleared: 0, returned: 0 },
  truncated: false,
};

let stub: BffStub;

beforeEach(() => {
  stub = installBffStub("staff");
  stub.routes["GET /bff/api/v1/fees/cheques"] = () => Response.json(LIST);
});
afterEach(() => {
  uninstallBffStub();
  expect(intlErrors).toEqual([]);
});

function signedIn(permissions: string[]) {
  stub.routes["GET /bff/api/v1/me"] = () =>
    Response.json(me(permissions, { roles: ["accountant"] }));
}

describe("helpers (FR-FEE-127, FR-FEE-132)", () => {
  it("ticks exact cheque and transfer matches and picks the receipt mode of a credit", () => {
    const base = { match_status: "proposed", ref_kind: "reference" } as const;
    const lines = [
      { ...base, id: "a", match_kind: "cheque_exact", cheque_no: "123456" },
      { ...base, id: "b", match_kind: "transfer_exact", transfer_ref: "SBIN426123456789" },
      { ...base, id: "c", match_kind: "amount_date", ref_kind: "none" },
    ] as unknown as StatementLine[];
    expect([...initialSelection(lines)]).toEqual(["a", "b"]);
    expect(lines.map(receiptModeFor)).toEqual(["cheque", "bank_transfer", "upi_qr"]);
  });

  it("only cheques at school are deposited; only deposited or cleared ones are returned", () => {
    expect(depositable(LIST.items).map((c) => c.receipt_id)).toEqual([R1]);
    expect(LIST.items.map(returnable)).toEqual([false, true]);
  });
});

describe("ChequesScreen (FR-FEE-131..141)", () => {
  it("is for fee.collect or fee.reconcile holders only (no API call otherwise)", async () => {
    signedIn(["finance.read"]);
    renderWithIntl(<ChequesScreen />);
    expect(await screen.findByText(messages.en.fees.noAccessTitle)).toBeInTheDocument();
    expect(stub.callsTo("GET /bff/api/v1/fees/cheques")).toHaveLength(0);
  });

  it("marks the ticked cheques deposited and links the deposit list", async () => {
    signedIn(["fee.collect"]);
    stub.routes["POST /bff/api/v1/fees/cheques/deposited"] = () =>
      Response.json({ deposited: 1, already_deposited: 0, skipped: [] });
    const user = userEvent.setup();
    renderWithIntl(<ChequesScreen />);
    const table = await screen.findByRole("region", { name: en.tableLabel });
    expect(within(table).getAllByRole("checkbox")).toHaveLength(1);
    // A collector without fee.reconcile does not record returns.
    expect(within(table).queryByRole("button", { name: /Returned by bank/ })).toBeNull();
    await user.click(within(table).getByRole("checkbox", { name: "Cheque 123456" }));
    await user.click(screen.getByRole("button", { name: "Mark 1 cheque deposited" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Mark 1 cheque deposited" }));
    await waitFor(() =>
      expect(stub.callsTo("POST /bff/api/v1/fees/cheques/deposited")).toHaveLength(1),
    );
    const body: unknown = JSON.parse(
      stub.callsTo("POST /bff/api/v1/fees/cheques/deposited")[0]?.body ?? "null",
    );
    expect(body).toEqual({ receipt_ids: [R1], deposited_on: "2026-11-12" });
    expect(screen.getByRole("link", { name: new RegExp(en.printSlip) })).toHaveAttribute(
      "href",
      expect.stringContaining("/fees/cheques/deposit-slip/print?day=2026-11-12"),
    );
  });

  it("records a returned cheque as a cancellation request", async () => {
    signedIn(["fee.collect", "fee.reconcile"]);
    stub.routes[`POST /bff/api/v1/fees/cheques/${R2}/returned`] = () =>
      Response.json({ cheque: {}, cancellation: { id: "0192f3a4-0000-7000-8000-00000000c901" } });
    const user = userEvent.setup();
    renderWithIntl(<ChequesScreen />);
    const table = await screen.findByRole("region", { name: en.tableLabel });
    await user.click(within(table).getByRole("button", { name: /Returned by bank/ }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: en.returnConfirm }));
    await waitFor(() =>
      expect(stub.callsTo(`POST /bff/api/v1/fees/cheques/${R2}/returned`)).toHaveLength(1),
    );
    const body: unknown = JSON.parse(
      stub.callsTo(`POST /bff/api/v1/fees/cheques/${R2}/returned`)[0]?.body ?? "null",
    );
    expect(body).toEqual({ returned_on: "2026-11-12", reason: en.returnReasonDefault });
  });
});
