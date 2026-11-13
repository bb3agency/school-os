import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { installBffStub, page, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { messages, renderWithIntl } from "@/test/render";
import { InvoiceDetailScreen } from "./InvoiceDetailView";
import { paymentUrlOf } from "./payment-links";

/**
 * FA-13 (FR-PAY-017, FR-PAY-023, FR-PAY-024, FR-PAY-027): the invoice page's "Online payment"
 * card lists the invoice's Razorpay payment links and offers "Create payment link", "Check
 * payment" and "Cancel link" to operators with `platform.invoices.manage`. Synthetic data only.
 */

const T = "0192f3a4-0000-7000-8000-000000000001";
const OP = "0192f3a4-0000-7000-8000-0000000000f1";
const INV = "0192f3a4-0000-7000-8000-00000000c001";
const LINK = "0192f3a4-0000-7000-8000-00000000e001";
const lm = messages.en.invoicePayments;

const INVOICE = {
  id: INV,
  tenant_id: T,
  subscription_id: "0192f3a4-0000-7000-8000-00000000b001",
  invoice_number: "SOS/26-27/000123",
  financial_year: "2026-27",
  status: "issued",
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
  amount_paid_inr: "0.00",
  tds_inr: "0.00",
  balance_due_inr: "5900.00",
  notes: null,
  void_reason: null,
  version: 2,
};

const LINK_ROW = (extra: Record<string, unknown> = {}) => ({
  id: LINK,
  invoice_id: INV,
  tenant_id: T,
  provider: "fake",
  provider_link_id: "plink_SYNTHabc1",
  short_url: "https://rzp.example.test/schoolos/plink_SYNTHabc1",
  amount_inr: "5900.00",
  status: "active",
  expire_by: "2026-12-13T06:00:00Z",
  created_by: OP,
  created_at: "2026-11-13T06:00:00Z",
  payment_id: null,
  provider_payment_id: null,
  settled_at: null,
  review_reason: null,
  cancelled_by: null,
  cancelled_at: null,
  version: 1,
  ...extra,
});

const LINKS = `GET /bff/api/v1/platform/invoices/${INV}/payment-links`;
const CREATE = `POST /bff/api/v1/platform/invoices/${INV}/payment-links`;
const CHECK = `POST /bff/api/v1/platform/payment-links/${LINK}/check`;

let stub: BffStub;
let links: { enabled: boolean; data: ReturnType<typeof LINK_ROW>[] };
beforeEach(() => {
  links = { enabled: true, data: [] };
  stub = installBffStub("operator");
  stub.routes["GET /bff/api/v1/platform/me"] = () =>
    Response.json({
      operator_id: OP,
      roles: ["billing_admin"],
      permissions: ["platform.tenants.read", "platform.invoices.read", "platform.invoices.manage"],
      step_up_fresh: true,
    });
  stub.routes["GET /bff/api/v1/platform/tenants"] = () => page([]);
  stub.routes[`GET /bff/api/v1/platform/invoices/${INV}`] = () => Response.json(INVOICE);
  stub.routes[`GET /bff/api/v1/platform/invoices/${INV}/payments`] = () => Response.json([]);
  stub.routes[LINKS] = () => Response.json(links);
});
afterEach(uninstallBffStub);

describe("invoice payment links (FA-13)", () => {
  it("creates a payment link for the balance due", async () => {
    const user = userEvent.setup();
    stub.routes[CREATE] = () => {
      links = { enabled: true, data: [LINK_ROW()] };
      return Response.json(LINK_ROW(), { status: 201 });
    };
    renderWithIntl(<InvoiceDetailScreen invoiceId={INV} />);
    await user.click(await screen.findByRole("button", { name: lm.create }));
    const dialog = screen.getByRole("dialog", { name: lm.createTitle });
    expect(within(dialog).getByText(/₹5,900\.00/)).toBeVisible();
    await user.click(within(dialog).getByRole("button", { name: lm.create }));
    await waitFor(() => expect(stub.callsTo(CREATE)).toHaveLength(1));
    expect(stub.callsTo(CREATE)[0]?.headers.get("Idempotency-Key")).toBeTruthy();
    expect(
      await screen.findByText("https://rzp.example.test/schoolos/plink_SYNTHabc1"),
    ).toBeVisible();
    expect(screen.getByText(lm.status.active)).toBeVisible();
    expect(screen.queryByRole("button", { name: lm.create })).toBeNull();
  });

  it("checks an active link and shows a link that needs review with its reason", async () => {
    const user = userEvent.setup();
    links = {
      enabled: true,
      data: [
        LINK_ROW(),
        LINK_ROW({
          id: "0192f3a4-0000-7000-8000-00000000e000",
          status: "needs_review",
          review_reason: "amount_exceeds_balance",
          settled_at: "2026-11-12T06:00:00Z",
          provider_payment_id: "pay_SYNTH1",
        }),
      ],
    };
    stub.routes[CHECK] = () => Response.json(LINK_ROW({ status: "paid" }));
    renderWithIntl(<InvoiceDetailScreen invoiceId={INV} />);
    expect(await screen.findByText(lm.review.amount_exceeds_balance)).toBeVisible();
    expect(screen.getByText(lm.status.needs_review)).toBeVisible();
    await user.click(await screen.findByRole("button", { name: lm.check }));
    await waitFor(() => expect(stub.callsTo(CHECK)).toHaveLength(1));
  });

  it("says when online payment is not set up and offers no link", async () => {
    links = { enabled: false, data: [] };
    renderWithIntl(<InvoiceDetailScreen invoiceId={INV} />);
    expect(await screen.findByText(lm.off)).toBeVisible();
    expect(screen.queryByRole("button", { name: lm.create })).toBeNull();
  });

  it("the school's Pay online link is only a Razorpay https URL", () => {
    expect(paymentUrlOf({ payment_url: "https://rzp.io/i/abc" }, false)).toBe(
      "https://rzp.io/i/abc",
    );
    expect(paymentUrlOf({ payment_url: "https://rzp.example.test/x" }, false)).toBeNull();
    expect(paymentUrlOf({ payment_url: "https://rzp.example.test/x" }, true)).toBe(
      "https://rzp.example.test/x",
    );
    expect(paymentUrlOf({ payment_url: "https://evil.example/rzp.io" }, true)).toBeNull();
    expect(paymentUrlOf({ payment_url: "javascript:alert(1)" }, true)).toBeNull();
    expect(paymentUrlOf({}, true)).toBeNull();
  });
});
