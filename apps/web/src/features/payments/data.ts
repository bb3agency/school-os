"use client";

import type { components } from "@schoolos/api-client";
import { unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import type { Loadable } from "@/lib/loadable";
import * as z from "@/lib/zod";

/**
 * Online fee payments through the school's own Razorpay account (R2-2D; ADR-0042; docs/17 §5.8).
 * The school is the merchant: money goes to its own Razorpay account and bank, never through
 * SchoolOS. Every route answers 404 `module_hidden` while fees are off for the school. Secrets
 * (the key secret typed once, the webhook secret shown once) live only in a dialog's memory:
 * never stored in the browser, never put into a URL. Payment links carry no student or parent
 * data. The API checks every permission; these values only decide what the screens offer.
 */

export const CONFIGURE = "payments.configure";
export const FINANCE_READ = "finance.read";
export const COLLECT = "fee.collect";
export const MANAGE = "fee.manage";
/** Online reconciliation (R2-2E permission, shared with bank statements). */
export const RECONCILE = "fee.reconcile";
export const NOTE_MIN = 10;
export const NOTE_MAX = 500;

type S = components["schemas"];
export type Connection = S["PaymentConnectionOut"];
export type WebhookSetup = S["WebhookSetupOut"];
export type ManualConnect = S["ManualConnectOut"];
export type ConnectionTest = S["ConnectionTestOut"];
export type Intent = S["PaymentIntentOut"];
export type IntentStatus = Intent["status"];
export type ReconItem = S["ReconciliationItemOut"];
export type ReconPage = S["ReconciliationPage"];
export type ReconSummary = S["ReconciliationSummaryOut"];
export type ItemKind = ReconItem["kind"];
export type ItemStatus = ReconItem["status"];

export const ITEM_KINDS: readonly ItemKind[] = [
  "paid_not_receipted",
  "receipted_not_captured",
  "amount_mismatch",
  "refunded",
  "refund_failed",
  "settlement_missing",
  "unknown_intent",
];
export const ITEM_STATUSES: readonly ItemStatus[] = ["open", "explained", "resolved"];

export const KEYS = {
  all: ["staff", "payments"] as const,
  connection: ["staff", "payments", "connection"] as const,
  intents: (accountId: string) => ["staff", "payments", "intents", accountId] as const,
  recon: (status: string, kind: string, cursor: string | null) =>
    ["staff", "payments", "recon", status, kind, cursor] as const,
  summary: ["staff", "payments", "summary"] as const,
} as const;

/** `W/"3"` for If-Match (the API's ETag format). */
export function ifMatch(version: number): string {
  return `W/"${version}"`;
}

/** Razorpay key ids: `rzp_test_…` or `rzp_live_…` (FR-PAY-003). */
export const KEY_ID = /^rzp_(test|live)_[A-Za-z0-9]{6,30}$/;

export const manualSchema = z.object({
  key_id: z.string().trim().regex(KEY_ID, { error: "invalidKeyId" }),
  key_secret: z.string().min(8, { error: "required" }).max(100),
});

export const explainSchema = z.object({
  note: z.string().trim().min(NOTE_MIN, { error: "tooShort" }).max(NOTE_MAX, { error: "tooLong" }),
});

export function useConnection(enabled = true): Loadable<Connection> {
  const api = useBffClient("staff");
  return useApiQuery(KEYS.connection, () => unwrap(api.GET("/api/v1/payments/connection")), {
    enabled,
  });
}

export function useIntents(accountId: string, enabled = true): Loadable<Intent[]> {
  const api = useBffClient("staff");
  return useApiQuery(
    KEYS.intents(accountId),
    () =>
      unwrap(
        api.GET("/api/v1/fees/accounts/{account_id}/payment-intents", {
          params: { path: { account_id: accountId } },
        }),
      ),
    { enabled },
  );
}

export function useReconciliation(
  status: ItemStatus | "",
  kind: ItemKind | "",
  cursor: string | null,
): Loadable<ReconPage> {
  const api = useBffClient("staff");
  return useApiQuery(KEYS.recon(status, kind, cursor), () =>
    unwrap(
      api.GET("/api/v1/fees/reconciliation/online", {
        params: {
          query: {
            ...(status ? { status } : {}),
            ...(kind ? { kind } : {}),
            ...(cursor ? { cursor } : {}),
          },
        },
      }),
    ),
  );
}

export function useReconSummary(): Loadable<ReconSummary> {
  const api = useBffClient("staff");
  return useApiQuery(KEYS.summary, () =>
    unwrap(api.GET("/api/v1/fees/reconciliation/online/summary")),
  );
}

/** An intent that can still be paid (status `created`, not past its expiry). */
export function isOpen(intent: Pick<Intent, "status" | "expires_at">, now = Date.now()): boolean {
  return intent.status === "created" && Date.parse(intent.expires_at) > now;
}
