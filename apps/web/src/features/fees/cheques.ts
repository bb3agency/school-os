"use client";

import type { ApiClient, components } from "@schoolos/api-client";
import { unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import type { Loadable } from "@/lib/loadable";

/**
 * Cheque status and the deposit list (FM-08; FR-FEE-131..141). A cheque receipt is `received` at
 * the counter, `deposited` when a person takes it to the bank, `cleared` once a person confirms
 * it from a bank statement, or `returned` (a `cheque_dishonoured` cancellation request goes to a
 * checker; the ledger changes only when it is approved). SchoolOS never holds money.
 */

export const CHEQUE_READERS = ["fee.collect", "fee.reconcile"] as const;
export const CHEQUE_STATUSES = ["received", "deposited", "cleared", "returned"] as const;
export type ChequeStatus = (typeof CHEQUE_STATUSES)[number];
export const DEPOSIT_SLIP_PATH = "/api/v1/fees/cheques/deposit-slip/print";

type Schemas = components["schemas"];

export type Cheque = Schemas["ChequeOut"];
export type ChequeList = Schemas["ChequeListOut"];
export type DepositOut = Schemas["ChequeDepositOut"];

export const CHEQUE_KEYS = {
  all: ["staff", "fees", "cheques"] as const,
  list: (status: string) => ["staff", "fees", "cheques", "list", status] as const,
} as const;

/** "Mark deposited" for the ticked cheques (FR-FEE-133). */
export function markDeposited(
  api: ApiClient,
  receiptIds: readonly string[],
  depositedOn: string,
): Promise<DepositOut> {
  return unwrap(
    api.POST("/api/v1/fees/cheques/deposited", {
      body: { receipt_ids: [...receiptIds], deposited_on: depositedOn },
    }),
  );
}

/** "Mark returned": asks a checker to cancel the receipt as dishonoured (FR-FEE-136). */
export function markReturned(
  api: ApiClient,
  receiptId: string,
  body: { returned_on: string; reason: string },
): Promise<Schemas["ChequeReturnOut"]> {
  return unwrap(
    api.POST("/api/v1/fees/cheques/{receipt_id}/returned", {
      params: { path: { receipt_id: receiptId } },
      body,
    }),
  );
}

export function useCheques(status: ChequeStatus | "", enabled = true): Loadable<ChequeList> {
  const api = useBffClient("staff");
  return useApiQuery(
    CHEQUE_KEYS.list(status),
    () =>
      unwrap(
        api.GET("/api/v1/fees/cheques", {
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
