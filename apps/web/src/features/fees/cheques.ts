"use client";

import type { ApiClient } from "@schoolos/api-client";
import * as z from "@/lib/zod";
import { unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import type { Loadable } from "@/lib/loadable";

/**
 * Cheque status and the deposit list (FM-08; FR-FEE-131..141). A cheque receipt is `received` at
 * the counter, `deposited` when a person takes it to the bank, `cleared` once a person confirms
 * it from a bank statement, or `returned` (a `cheque_dishonoured` cancellation request goes to a
 * checker; the ledger changes only when it is approved). SchoolOS never holds money.
 *
 * LEAD (after `make openapi`): the routes below are not in the generated client yet, so the
 * calls go through {@link chequeApi} and the bodies are checked with the zod schemas here.
 * Swap them for `api.GET("/api/v1/fees/cheques")` etc. and `components["schemas"]["ChequeOut"]`
 * once the client is regenerated.
 */

export const CHEQUE_READERS = ["fee.collect", "fee.reconcile"] as const;
export const CHEQUE_STATUSES = ["received", "deposited", "cleared", "returned"] as const;
export type ChequeStatus = (typeof CHEQUE_STATUSES)[number];
export const DEPOSIT_SLIP_PATH = "/api/v1/fees/cheques/deposit-slip/print";

const money = z.string();
const date = z.string();

export const chequeSchema = z.object({
  receipt_id: z.string(),
  receipt_number: z.string(),
  receipt_date: date,
  receipt_status: z.string(),
  amount: money,
  cheque_number: z.string(),
  bank_name: z.string(),
  cheque_date: date.nullable(),
  student_name: z.string(),
  class_section: z.string(),
  cheque_status: z.enum(CHEQUE_STATUSES),
  deposited_on: date.nullable(),
  cleared_on: date.nullable(),
  cleared_from_statement: z.boolean(),
  returned_on: date.nullable(),
  cancellation_id: z.string().nullable(),
  deposit_overdue: z.boolean(),
});
export type Cheque = z.infer<typeof chequeSchema>;

export const chequeListSchema = z.object({
  as_of: date,
  date_from: date,
  date_to: date,
  deposit_due_days: z.number(),
  items: z.array(chequeSchema),
  counts: z.record(z.string(), z.number()),
  truncated: z.boolean(),
});
export type ChequeList = z.infer<typeof chequeListSchema>;

export const depositOutSchema = z.object({
  deposited: z.number(),
  already_deposited: z.number(),
  skipped: z.array(z.object({ receipt_id: z.string(), code: z.string() })),
});
export type DepositOut = z.infer<typeof depositOutSchema>;

export const CHEQUE_KEYS = {
  all: ["staff", "fees", "cheques"] as const,
  list: (status: string) => ["staff", "fees", "cheques", "list", status] as const,
} as const;

type Result = { data?: unknown; error?: unknown; response: Response };
type Init = {
  params?: { query?: Record<string, string>; path?: Record<string, string> };
  body?: unknown;
};
interface UntypedClient {
  GET(path: string, init?: Init): Promise<Result>;
  POST(path: string, init?: Init): Promise<Result>;
}

/** The BFF client without the generated path types (see the LEAD note above). */
export function chequeApi(api: ApiClient): UntypedClient {
  return api as unknown as UntypedClient;
}

export async function parsed<T>(schema: z.ZodType<T>, pending: Promise<Result>): Promise<T> {
  return schema.parse(await unwrap(pending));
}

export function useCheques(status: ChequeStatus | "", enabled = true): Loadable<ChequeList> {
  const api = useBffClient("staff");
  return useApiQuery(
    CHEQUE_KEYS.list(status),
    () =>
      parsed(
        chequeListSchema,
        chequeApi(api).GET("/api/v1/fees/cheques", {
          params: { query: status ? { status } : {} },
        }),
      ),
    { enabled },
  );
}

/** Cheques a person may tick for "Mark deposited": issued and still at the school. */
export function depositable(items: readonly Cheque[]): Cheque[] {
  return items.filter((c) => c.cheque_status === "received" && c.receipt_status === "issued");
}

/** Cheques the bank may have returned: deposited or cleared, receipt not cancelled. */
export function returnable(cheque: Cheque): boolean {
  return (
    cheque.receipt_status === "issued" &&
    (cheque.cheque_status === "deposited" || cheque.cheque_status === "cleared")
  );
}
