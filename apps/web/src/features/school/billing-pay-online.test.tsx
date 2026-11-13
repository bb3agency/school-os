import { screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { installBffStub, page, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { messages, renderWithIntl } from "@/test/render";
import { BillingScreen } from "./BillingView";

/**
 * FA-13 (FR-PAY-027): the school's Plan and billing page shows "Pay online" for an invoice whose
 * active SchoolOS payment link the API returns as `payment_url`, opening Razorpay in a new tab.
 * Synthetic data only.
 */

const pm = messages.en.invoicePayments.school;
let stub: BffStub;

const INVOICE = (id: string, number: string, extra: Record<string, unknown> = {}) => ({
  invoice_id: id,
  invoice_number: number,
  period_start: "2026-10-01",
  period_end: "2026-10-31",
  issue_date: "2026-10-01",
  due_date: "2026-10-15",
  total_inr: "5898.82",
  amount_due_inr: "5898.82",
  status: "issued",
  ...extra,
});

beforeEach(() => {
  stub = installBffStub("staff");
  stub.routes["GET /bff/api/v1/tenant/billing"] = () =>
    Response.json({
      available: true,
      plan_code: "shared",
      plan_name: "Shared",
      tier: "shared",
      billing_period: "monthly",
      status: "active",
      current_period_start: "2026-10-01",
      current_period_end: "2026-10-31",
      trial_ends_at: null,
      past_due_since: null,
      grace_ends_on: null,
      cancel_at_period_end: false,
      usage_date: null,
      usage: [],
      amount_due_inr: "11797.64",
    });
  stub.routes["GET /bff/api/v1/tenant/billing/invoices"] = () =>
    page([
      INVOICE("0192f3a4-0000-7000-8000-00000000c002", "SOS/26-27/000124", {
        payment_url: "https://rzp.io/i/SYNTH1",
      }),
      INVOICE("0192f3a4-0000-7000-8000-00000000c001", "SOS/26-27/000123", {
        payment_url: null,
      }),
    ]);
});
afterEach(uninstallBffStub);

describe("school billing: Pay online (FA-13)", () => {
  it("offers Pay online only where SchoolOS made a payment link", async () => {
    renderWithIntl(<BillingScreen />);
    const link = await screen.findByRole("link", {
      name: pm.payOnlineLabel.replace("{number}", "SOS/26-27/000124"),
    });
    expect(link).toHaveAttribute("href", "https://rzp.io/i/SYNTH1");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", expect.stringContaining("noopener"));
    expect(screen.getAllByRole("link", { name: /Pay online/ })).toHaveLength(1);
    const other = screen.getByText("SOS/26-27/000123").closest("tr");
    expect(other).not.toBeNull();
    expect(within(other as HTMLElement).queryByRole("link")).toBeNull();
    expect(screen.getByText(pm.payNote)).toBeInTheDocument();
  });
});
