import type { ApiClient, components } from "@schoolos/api-client";
import { unwrap } from "@/lib/bff/query";
import { isPayUrl } from "@/lib/public-surface";

/**
 * FA-13 (FR-PAY-017..024): Razorpay payment links of SchoolOS's own invoices (SchoolOS is the
 * merchant), on the generated operator client.
 */

type S = components["schemas"];

export const LINK_STATUSES = [
  "active",
  "paid",
  "cancelled",
  "expired",
  "needs_review",
] as const satisfies readonly InvoicePaymentLink["status"][];
export type LinkStatus = InvoicePaymentLink["status"];
export const REVIEW_REASONS = [
  "invoice_not_open",
  "amount_exceeds_balance",
  "amount_mismatch",
] as const satisfies readonly NonNullable<InvoicePaymentLink["review_reason"]>[];

export type InvoicePaymentLink = S["InvoicePaymentLinkOut"];
export type InvoicePaymentLinks = S["InvoicePaymentLinksOut"];

export async function fetchPaymentLinks(
  api: ApiClient,
  invoiceId: string,
): Promise<InvoicePaymentLinks> {
  return unwrap(
    api.GET("/api/v1/platform/invoices/{invoice_id}/payment-links", {
      params: { path: { invoice_id: invoiceId } },
    }),
  );
}

export async function createPaymentLink(
  api: ApiClient,
  invoiceId: string,
  idempotencyKey: string,
): Promise<InvoicePaymentLink> {
  return unwrap(
    api.POST("/api/v1/platform/invoices/{invoice_id}/payment-links", {
      params: { path: { invoice_id: invoiceId } },
      headers: { "Idempotency-Key": idempotencyKey },
    }),
  );
}

export async function linkAction(
  api: ApiClient,
  linkId: string,
  action: "cancel" | "check",
): Promise<InvoicePaymentLink> {
  const params = { params: { path: { link_id: linkId } } };
  return unwrap(
    action === "cancel"
      ? api.POST("/api/v1/platform/payment-links/{link_id}/cancel", params)
      : api.POST("/api/v1/platform/payment-links/{link_id}/check", params),
  );
}

/**
 * The school's billing page: `payment_url` of GET /tenant/billing/invoices (FR-PAY-027), only
 * when it is an https URL on Razorpay's hosts (`rzp.example.test`, the fake, in development).
 */
export function paymentUrlOf(
  row: Pick<S["TenantInvoice"], "payment_url">,
  isDev: boolean = process.env.NODE_ENV !== "production",
): string | null {
  const value = row.payment_url;
  return typeof value === "string" && isPayUrl(value, isDev) ? value : null;
}
