import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
  CSRF,
  installBffStub,
  page,
  problem,
  uninstallBffStub,
  type BffStub,
} from "@/test/bff-stub";
import { messages, renderWithIntl } from "@/test/render";
import { InvoicesScreen } from "./BillingViews";
import { InvoiceDetailScreen } from "./InvoiceDetailView";

/**
 * Reverse a payment (owner request 2026-10-04; FR-PLT-018, docs/16 §5.9): the invoice page
 * lists GET /platform/invoices/{id}/payments and offers "Reverse payment" on recorded
 * payments to operators with `platform.invoices.manage` (no step-up, not two-person).
 * POST /platform/payments/{id}/reverse takes a reason of 10-500 characters; the invoice is
 * re-settled, so it is issued (unpaid) again. Synthetic data only.
 */

const T = "0192f3a4-0000-7000-8000-000000000001";
const OP = "0192f3a4-0000-7000-8000-0000000000f1";
const INV = "0192f3a4-0000-7000-8000-00000000c001";
const PAY_NEW = "0192f3a4-0000-7000-8000-00000000d002";
const PAY_OLD = "0192f3a4-0000-7000-8000-00000000d001";
const pm = messages.en.platform.invoices;
const pp = pm.payments;
const em = messages.en.errors;
const vm = messages.en.validation;

const MANAGE = ["platform.tenants.read", "platform.invoices.read", "platform.invoices.manage"];
let permissions: string[];

const INVOICE = (status: string, paid: string, tds: string, balance: string) => ({
  id: INV,
  tenant_id: T,
  subscription_id: "0192f3a4-0000-7000-8000-00000000b001",
  invoice_number: "SOS/26-27/000123",
  financial_year: "2026-27",
  status,
  period_start: "2026-09-01",
  period_end: "2026-09-30",
  issue_date: "2026-09-01",
  due_date: "2026-09-15",
  supplier_legal_name: "SchoolOS",
  supplier_gstin: "37AAAAA0000A1Z5",
  supplier_state_code: "37",
  recipient_legal_name: "Sample Education Society",
  recipient_gstin: null,
  place_of_supply_state_code: "37",
  tax_type: "cgst_sgst",
  taxable_value_inr: "5000.00",
  cgst_inr: "450.00",
  sgst_inr: "450.00",
  igst_inr: "0.00",
  total_inr: "5900.00",
  amount_paid_inr: paid,
  tds_inr: tds,
  balance_due_inr: balance,
  notes: null,
  void_reason: null,
  version: 3,
});

const PAYMENT = (id: string, extra: Record<string, unknown> = {}) => ({
  id,
  invoice_id: INV,
  provider: "manual",
  method: "upi",
  amount_inr: "5400.00",
  tds_inr: "500.00",
  received_on: "2026-09-20",
  reference: "UPI-SYNTH-0002",
  notes: null,
  status: "recorded",
  recorded_by: OP,
  recorded_by_name: "Synthetic Billing Admin",
  recorded_at: "2026-09-20T06:30:00Z",
  reversed_by: null,
  reversed_by_name: null,
  reversed_at: null,
  reversal_reason: null,
  ...extra,
});

const REVERSED_OLD = PAYMENT(PAY_OLD, {
  method: "cheque",
  amount_inr: "1000.00",
  tds_inr: "0.00",
  received_on: "2026-09-10",
  reference: "CHQ-SYNTH-0001",
  status: "reversed",
  reversed_by: OP,
  reversed_by_name: "Synthetic Owner",
  reversed_at: "2026-09-12T05:00:00Z",
  reversal_reason: "Cheque bounced at the bank",
});

const GET_INVOICE = `GET /bff/api/v1/platform/invoices/${INV}`;
const GET_PAYMENTS = `GET /bff/api/v1/platform/invoices/${INV}/payments`;
const REVERSE = `POST /bff/api/v1/platform/payments/${PAY_NEW}/reverse`;

let stub: BffStub;
let invoice: ReturnType<typeof INVOICE>;
let payments: ReturnType<typeof PAYMENT>[];
beforeEach(() => {
  permissions = MANAGE;
  invoice = INVOICE("paid", "5400.00", "500.00", "0.00");
  payments = [PAYMENT(PAY_NEW), REVERSED_OLD];
  stub = installBffStub("operator");
  stub.routes["GET /bff/api/v1/platform/me"] = () =>
    Response.json({
      operator_id: OP,
      roles: ["billing_admin"],
      permissions,
      step_up_fresh: true,
    });
  stub.routes["GET /bff/api/v1/platform/tenants"] = () =>
    page([
      {
        tenant_id: T,
        school_name: "Sri Saraswati High School",
        code: "sshs",
        tier: "shared",
        tenant_status: "active",
        subscription_status: "active",
        plan_code: "standard",
        deployment_status: "healthy",
        app_version: null,
        last_heartbeat_at: null,
        created_at: "2026-06-01T04:30:00Z",
      },
    ]);
  stub.routes[GET_INVOICE] = () => Response.json(invoice);
  stub.routes[GET_PAYMENTS] = () => Response.json(payments);
});
afterEach(uninstallBffStub);

async function paymentsTable() {
  renderWithIntl(<InvoiceDetailScreen invoiceId={INV} />);
  const table = await screen.findByRole("table", { name: pp.title });
  await within(table).findByText("UPI-SYNTH-0002");
  return table;
}

function rowOf(table: HTMLElement, reference: string): HTMLElement {
  const row = within(table).getByText(reference).closest("tr");
  if (!row) throw new Error(`no row for ${reference}`);
  return row;
}

async function openReverse() {
  const user = userEvent.setup();
  const table = await paymentsTable();
  await waitFor(() =>
    expect(stub.callsTo("GET /bff/api/v1/platform/me").length).toBeGreaterThan(0),
  );
  const row = rowOf(table, "UPI-SYNTH-0002");
  await user.click(within(row).getByRole("button", { name: pp.reverse }));
  const dialog = screen.getByRole("dialog", { name: pp.reverseTitle });
  return { user, dialog };
}

describe("invoice payments (FR-PLT-018, docs/16 §5.9)", () => {
  it("the invoice list links each invoice to its page", async () => {
    stub.routes["GET /bff/api/v1/platform/invoices"] = () => page([invoice]);
    renderWithIntl(<InvoicesScreen />);
    const link = await screen.findByRole("link", {
      name: pm.openInvoice.replace("{number}", "SOS/26-27/000123"),
    });
    expect(link).toHaveAttribute("href", expect.stringContaining(`/platform/invoices/${INV}`));
  });

  it("lists payments newest first with Recorded and Reversed pills and the reversal details", async () => {
    const table = await paymentsTable();
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(2);
    const [recorded, reversed] = rows as [HTMLElement, HTMLElement];
    expect(within(recorded).getByText("UPI-SYNTH-0002")).toBeVisible();
    expect(within(recorded).getByText(pp.status.recorded)).toBeVisible();
    expect(within(recorded).getByText(pm.methods.upi)).toBeVisible();
    expect(within(recorded).getByText("Synthetic Billing Admin")).toBeVisible();
    expect(within(recorded).getByText("₹5,400.00")).toBeVisible();
    expect(within(recorded).getByText("+ TDS ₹500.00")).toBeVisible();
    expect(within(reversed).getByText("CHQ-SYNTH-0001")).toBeVisible();
    expect(within(reversed).getByText(pp.status.reversed)).toBeVisible();
    expect(within(reversed).getByText(pm.methods.cheque)).toBeVisible();
    expect(within(reversed).getByText(/Reversed by Synthetic Owner on/)).toBeVisible();
    expect(within(reversed).getByText("Reason: Cheque bounced at the bank")).toBeVisible();
  });

  it("shows an empty state when the invoice has no payments", async () => {
    payments = [];
    renderWithIntl(<InvoiceDetailScreen invoiceId={INV} />);
    expect(await screen.findByText(pp.emptyTitle)).toBeVisible();
    expect(screen.queryByRole("button", { name: pp.reverse })).toBeNull();
  });

  it("offers Reverse payment only on recorded payments", async () => {
    const table = await paymentsTable();
    await waitFor(() =>
      expect(
        within(rowOf(table, "UPI-SYNTH-0002")).getByRole("button", { name: pp.reverse }),
      ).toBeVisible(),
    );
    expect(
      within(rowOf(table, "CHQ-SYNTH-0001")).queryByRole("button", { name: pp.reverse }),
    ).toBeNull();
  });

  it("hides Reverse payment from an operator without platform.invoices.manage", async () => {
    permissions = ["platform.tenants.read", "platform.invoices.read"];
    await paymentsTable();
    await waitFor(() =>
      expect(stub.callsTo("GET /bff/api/v1/platform/me").length).toBeGreaterThan(0),
    );
    await waitFor(() => expect(screen.queryByRole("button", { name: pp.reverse })).toBeNull());
    expect(screen.getByText(pp.status.recorded)).toBeVisible();
  });

  it("says what reversing does and checks the reason (10-500 characters) before sending", async () => {
    const { user, dialog } = await openReverse();
    expect(
      within(dialog).getByText(pp.reverseConsequence.replace("{amount}", "₹5,900.00")),
    ).toBeVisible();
    expect(within(dialog).getByText(pp.reverseNote)).toBeVisible();
    await user.click(within(dialog).getByRole("button", { name: pp.reverse }));
    const field = within(dialog).getByLabelText(pp.reason);
    expect(field).toHaveAttribute("aria-invalid", "true");
    expect(field).toHaveAccessibleDescription(expect.stringContaining(vm.required));
    await user.type(field, "Too short");
    await user.click(within(dialog).getByRole("button", { name: pp.reverse }));
    expect(field).toHaveAccessibleDescription(expect.stringContaining(vm.reasonTooShort));
    expect(stub.callsTo(REVERSE)).toHaveLength(0);
  });

  it("reverses with CSRF and the reason, then reloads: the invoice is issued again", async () => {
    stub.routes[REVERSE] = () => {
      invoice = INVOICE("issued", "0.00", "0.00", "5900.00");
      const done = PAYMENT(PAY_NEW, {
        status: "reversed",
        reversed_by: OP,
        reversed_by_name: "Synthetic Billing Admin",
        reversed_at: "2026-10-04T05:00:00Z",
        reversal_reason: "Recorded against the wrong invoice",
      });
      payments = [done, REVERSED_OLD];
      return Response.json(done);
    };
    const { user, dialog } = await openReverse();
    expect(screen.getAllByText(messages.en.status.invoice.paid).length).toBeGreaterThan(0);
    await user.type(within(dialog).getByLabelText(pp.reason), "Recorded against the wrong invoice");
    await user.click(within(dialog).getByRole("button", { name: pp.reverse }));
    await waitFor(() => expect(dialog).not.toHaveAttribute("open"));
    const [call] = stub.callsTo(REVERSE);
    expect(call?.headers.get("x-csrf-token")).toBe(CSRF);
    expect(JSON.parse(call?.body ?? "{}")).toEqual({
      reason: "Recorded against the wrong invoice",
    });
    await waitFor(() =>
      expect(screen.getAllByText(messages.en.status.invoice.issued).length).toBeGreaterThan(0),
    );
    expect(screen.queryByText(messages.en.status.invoice.paid)).toBeNull();
    expect(stub.callsTo(GET_INVOICE).length).toBeGreaterThan(1);
    expect(stub.callsTo(GET_PAYMENTS).length).toBeGreaterThan(1);
    expect(screen.getAllByText(pp.status.reversed)).toHaveLength(2);
    expect(screen.queryByRole("button", { name: pp.reverse })).toBeNull();
  });

  it("409 invalid_state: says it was already reversed and reloads the list", async () => {
    stub.routes[REVERSE] = () => problem(409, "invalid_state");
    const { user, dialog } = await openReverse();
    const before = stub.callsTo(GET_PAYMENTS).length;
    await user.type(within(dialog).getByLabelText(pp.reason), "Recorded against the wrong invoice");
    await user.click(within(dialog).getByRole("button", { name: pp.reverse }));
    expect(await within(dialog).findByText(pp.errors.invalid_state.title)).toBeVisible();
    expect(within(dialog).getByText(pp.errors.invalid_state.body)).toBeVisible();
    await waitFor(() => expect(stub.callsTo(GET_PAYMENTS).length).toBeGreaterThan(before));
  });

  it("403 says the role is missing", async () => {
    stub.routes[REVERSE] = () => problem(403, "forbidden");
    const { user, dialog } = await openReverse();
    await user.type(within(dialog).getByLabelText(pp.reason), "Recorded against the wrong invoice");
    await user.click(within(dialog).getByRole("button", { name: pp.reverse }));
    expect(await within(dialog).findByText(em.api.forbidden.title)).toBeVisible();
  });

  it("422 on the reason lands on the field", async () => {
    stub.routes[REVERSE] = () =>
      problem(422, "validation_error", {
        errors: [{ field: "reason", code: "invalid", message_key: "errors.invalid" }],
      });
    const { user, dialog } = await openReverse();
    const field = within(dialog).getByLabelText(pp.reason);
    await user.type(field, "Recorded against the wrong invoice");
    await user.click(within(dialog).getByRole("button", { name: pp.reverse }));
    await waitFor(() => expect(field).toHaveAttribute("aria-invalid", "true"));
    expect(field).toHaveAccessibleDescription(expect.stringContaining(em.field.invalid));
  });

  it("404: the payment is gone, says so and reloads", async () => {
    stub.routes[REVERSE] = () => problem(404, "not_found");
    const { user, dialog } = await openReverse();
    const before = stub.callsTo(GET_PAYMENTS).length;
    await user.type(within(dialog).getByLabelText(pp.reason), "Recorded against the wrong invoice");
    await user.click(within(dialog).getByRole("button", { name: pp.reverse }));
    expect(await within(dialog).findByText(pp.errors.not_found.title)).toBeVisible();
    await waitFor(() => expect(stub.callsTo(GET_PAYMENTS).length).toBeGreaterThan(before));
  });

  it("428 step-up (if the catalog ever asks for it) leaves for re-authentication", async () => {
    stub.routes[REVERSE] = () =>
      problem(428, "step_up_required", {
        step_up_url: "/bff/auth/platform/step-up?next=%2Fplatform%2Finvoices",
      });
    const { user, dialog } = await openReverse();
    await user.type(within(dialog).getByLabelText(pp.reason), "Recorded against the wrong invoice");
    await user.click(within(dialog).getByRole("button", { name: pp.reverse }));
    await waitFor(() =>
      expect(stub.navigate).toHaveBeenCalledWith(
        "/bff/auth/platform/step-up?next=%2Fplatform%2Finvoices",
      ),
    );
  });

  it("Escape closes the dialog and focus returns to Reverse payment", async () => {
    const { user, dialog } = await openReverse();
    await user.keyboard("{Escape}");
    await waitFor(() => expect(dialog).not.toHaveAttribute("open"));
    const row = rowOf(screen.getByRole("table", { name: pp.title }), "UPI-SYNTH-0002");
    await waitFor(() =>
      expect(within(row).getByRole("button", { name: pp.reverse })).toHaveFocus(),
    );
  });

  it("an unknown invoice shows the load error", async () => {
    stub.routes[GET_INVOICE] = () => problem(404, "not_found");
    renderWithIntl(<InvoiceDetailScreen invoiceId={INV} />);
    expect(await screen.findByText(messages.en.common.loadErrorTitle)).toBeVisible();
  });
});
