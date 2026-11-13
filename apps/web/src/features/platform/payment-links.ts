import type { ApiClient } from "@schoolos/api-client";
import { unwrap } from "@/lib/bff/query";
import { isPayUrl } from "@/lib/public-surface";
import * as z from "@/lib/zod";

/**
 * FA-13 (FR-PAY-017..024): Razorpay payment links of SchoolOS's own invoices (SchoolOS is the
 * merchant). The routes are new in wave F4 and not yet in the generated client: until the lead
 * regenerates `packages/api-client`, the calls go through a loosely typed view of the operator
 * BFF client and every answer is checked with the zod schemas below (hand-written from
 * `InvoicePaymentLinkOut` / `InvoicePaymentLinksOut` in `apps/api/app/platform/schemas.py`).
 */

export const LINK_STATUSES = ["active", "paid", "cancelled", "expired", "needs_review"] as const;
export type LinkStatus = (typeof LINK_STATUSES)[number];
export const REVIEW_REASONS = [
  "invoice_not_open",
  "amount_exceeds_balance",
  "amount_mismatch",
] as const;

const linkSchema = z.object({
  id: z.string(),
  invoice_id: z.string(),
  provider: z.string(),
  provider_link_id: z.string(),
  short_url: z.string(),
  amount_inr: z.string(),
  status: z.enum(LINK_STATUSES),
  expire_by: z.string(),
  created_at: z.string().nullish(),
  settled_at: z.string().nullish(),
  review_reason: z.enum(REVIEW_REASONS).nullish(),
  cancelled_at: z.string().nullish(),
});
export type InvoicePaymentLink = z.output<typeof linkSchema>;

const linksSchema = z.object({ enabled: z.boolean(), data: z.array(linkSchema) });
export type InvoicePaymentLinks = z.output<typeof linksSchema>;

/** The BFF client seen without the generated path types (routes not generated yet). */
interface LooseClient {
  GET(
    path: string,
    init?: object,
  ): Promise<{ data?: unknown; error?: unknown; response: Response }>;
  POST(
    path: string,
    init?: object,
  ): Promise<{ data?: unknown; error?: unknown; response: Response }>;
}

function loose(api: ApiClient): LooseClient {
  return api as unknown as LooseClient;
}

const BASE = "/api/v1/platform";

export async function fetchPaymentLinks(
  api: ApiClient,
  invoiceId: string,
): Promise<InvoicePaymentLinks> {
  const path = `${BASE}/invoices/${encodeURIComponent(invoiceId)}/payment-links`;
  return linksSchema.parse(await unwrap(loose(api).GET(path)));
}

export async function createPaymentLink(
  api: ApiClient,
  invoiceId: string,
  idempotencyKey: string,
): Promise<InvoicePaymentLink> {
  const path = `${BASE}/invoices/${encodeURIComponent(invoiceId)}/payment-links`;
  return linkSchema.parse(
    await unwrap(loose(api).POST(path, { headers: { "Idempotency-Key": idempotencyKey } })),
  );
}

export async function linkAction(
  api: ApiClient,
  linkId: string,
  action: "cancel" | "check",
): Promise<InvoicePaymentLink> {
  const path = `${BASE}/payment-links/${encodeURIComponent(linkId)}/${action}`;
  return linkSchema.parse(await unwrap(loose(api).POST(path)));
}

/**
 * The school's billing page: `payment_url` of GET /tenant/billing/invoices (FR-PAY-027), only
 * when it is an https URL on Razorpay's hosts (`rzp.example.test`, the fake, in development).
 */
export function paymentUrlOf(
  row: object,
  isDev: boolean = process.env.NODE_ENV !== "production",
): string | null {
  const value = (row as { payment_url?: unknown }).payment_url;
  return typeof value === "string" && isPayUrl(value, isDev) ? value : null;
}
