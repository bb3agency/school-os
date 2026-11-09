"use client";

import type { components } from "@schoolos/api-client";
import { ApiError } from "@/lib/bff/query";
import { runPollDelay } from "@/features/findings/RunChecks";

/**
 * Tuition fee certificate for income tax (FM-06; FR-FEE-046..060). Issued only on request by
 * staff with `fee.collect` (one student or one class or section); read with `fee.collect` or
 * `finance.read`. The certificate shows personal financial data (C2): ids only in queries and
 * links, nothing kept in the browser. The screens never say whether a deduction applies: the
 * school certifies what was paid ("not tax advice", FR-FEE-056).
 */

type S = components["schemas"];
export type TaxCertificate = S["TaxCertificateOut"];
export type TaxCertificateListItem = S["TaxCertificateListItem"];
export type TaxCertificateBulk = S["TaxCertificateBulkOut"];
export type TaxCertificateStatus = TaxCertificateListItem["status"];

/** Financial years back a certificate can be issued for (config `limits.max_years_back`). */
export const MAX_YEARS_BACK = 8;

export const TAX_KEYS = {
  all: ["staff", "fees", "tax-certificates"] as const,
  list: (filters: object, cursor: string | null) =>
    ["staff", "fees", "tax-certificates", "list", filters, cursor] as const,
  student: (studentId: string, financialYear: string) =>
    ["staff", "fees", "tax-certificates", "student", studentId, financialYear] as const,
} as const;

const FY = /^(20\d{2})-(\d{2})$/;

/**
 * The financial years a certificate can be issued for, newest first: the current one (from
 * the API's fee status, never the PC's clock) and the `MAX_YEARS_BACK` before it. Years that
 * have not started are never offered (422 `financial_year` `not_started`).
 */
export function financialYears(current: string): string[] {
  const match = FY.exec(current);
  if (!match) return [];
  const first = Number(match[1]);
  return Array.from({ length: MAX_YEARS_BACK + 1 }, (_, i) => {
    const year = first - i;
    return `${year}-${String((year + 1) % 100).padStart(2, "0")}`;
  });
}

/**
 * A 422 on `financial_year` (codes `invalid`, `not_started`, `too_old`) as its own problem
 * code, so the dialog explains it in plain words (`financial_year_not_started` …) instead of
 * a generic "check this value". Other errors pass through unchanged.
 */
export function taxIssueError(error: unknown): unknown {
  if (!(error instanceof ApiError) || error.status !== 422) return error;
  const errors = error.problem.errors;
  if (!Array.isArray(errors)) return error;
  const fy = errors.find((item) => item?.field === "financial_year");
  if (!fy) return error;
  const reason = ["invalid", "not_started", "too_old"].includes(String(fy.code))
    ? String(fy.code)
    : "invalid";
  const code = `financial_year_${reason}`;
  return new ApiError(422, code, { ...error.problem, code, errors: [] });
}

/** Rethrows the failure of an issue call with the financial year explained (see above). */
export async function withTaxErrors<T>(pending: Promise<T>): Promise<T> {
  try {
    return await pending;
  } catch (error) {
    throw taxIssueError(error);
  }
}

/**
 * Pause before reading certificates again while a PDF is made (2 s, 3 s, 4.5 s … at most
 * 15 s, as receipts XF-07). Tests shorten it.
 */
let pollDelay: (attempt: number) => number = runPollDelay;
export function taxPollDelay(attempt: number): number {
  return pollDelay(attempt);
}
export function setTaxPollDelayForTesting(delay: ((attempt: number) => number) | null) {
  pollDelay = delay ?? runPollDelay;
}
