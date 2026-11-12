"use client";

import { useLocale } from "next-intl";
import { createBffFetch } from "@/lib/bff/fetch";
import { ApiError, NotAvailableError, useApiQuery } from "@/lib/bff/query";
import type { Loadable } from "@/lib/loadable";
import * as z from "@/lib/zod";

/**
 * Family counter (FM-07; FR-FEE-101..112) and fee structure notice (FM-10; FR-FEE-113..125).
 *
 * These routes are newer than the generated client (`packages/api-client`), so they are called
 * through the BFF with `createBffFetch` and their answers checked with the zod schemas below
 * (hand-written from `app/fees/family_schemas.py` and `structure_notice_schemas.py`). After the
 * OpenAPI document and client are regenerated these can move to `api.GET(...)` with the
 * generated types. Amounts stay exact decimal strings (FR-FEE-028).
 */

const money = z.string();

const yearDue = z.object({
  account_id: z.string(),
  academic_year_id: z.string(),
  academic_year_label: z.string().nullable(),
  earlier_year: z.boolean(),
  balance: money,
  overdue: money,
  collectable: money,
  oldest_due_on: z.string().nullable(),
});

const familyMember = z.object({
  student_id: z.string(),
  display_name: z.string().nullable(),
  admission_no: z.string().nullable(),
  class_section: z.string().nullable(),
  status: z.string(),
  selected: z.boolean(),
  linked_by: z.array(z.string()),
  accounts: z.array(yearDue),
  collectable: money,
});

export const familyCounterSchema = z.object({
  student_id: z.string(),
  as_of: z.string(),
  members: z.array(familyMember),
  total_collectable: money,
  max_accounts: z.number(),
  modes: z.array(z.string()),
});

const issuedReceipt = z.object({
  id: z.string(),
  receipt_number: z.string(),
  amount: money,
  account_id: z.string(),
  student_id: z.string(),
  student_name: z.string(),
  class_section: z.string(),
});

export const familyPaymentSchema = z.object({
  receipts: z.array(issuedReceipt),
  amount: money,
  mode: z.string(),
});

const noticeHead = z.object({
  head_id: z.string(),
  code: z.string(),
  name: z.string(),
  annual_amount: money,
});

const noticeClass = z.object({
  class_id: z.string(),
  class_label: z.string(),
  plan_id: z.string(),
  plan_status: z.enum(["draft", "active"]),
  heads: z.array(noticeHead),
  annual_total: money,
  instalments: z.array(
    z.object({ instalment_no: z.number(), label: z.string(), due_on: z.string(), amount: money }),
  ),
  late_fee_text: z.string(),
});

export const structureNoticeSchema = z.object({
  academic_year_id: z.string(),
  academic_year_label: z.string(),
  title: z.string(),
  disclaimer: z.string(),
  classes: z.array(noticeClass),
  classes_without_plan: z.array(z.string()),
});

const headChange = z.object({
  head_id: z.string(),
  code: z.string(),
  name: z.string(),
  change: z.enum(["added", "removed", "changed", "unchanged"]),
  previous_amount: money.nullable(),
  current_amount: money.nullable(),
  difference: money,
  percent: z.string().nullable(),
  above_threshold: z.boolean(),
});

const classChange = z.object({
  class_id: z.string(),
  class_label: z.string(),
  plan_status: z.enum(["draft", "active"]).nullable(),
  has_previous: z.boolean(),
  previous_total: money,
  current_total: money,
  difference: money,
  percent: z.string().nullable(),
  above_threshold: z.boolean(),
  heads: z.array(headChange),
});

export const structureChangesSchema = z.object({
  academic_year_id: z.string(),
  academic_year_label: z.string(),
  previous_year_id: z.string().nullable(),
  previous_year_label: z.string().nullable(),
  warn_percent: z.string(),
  warn_percent_verified: z.boolean(),
  classes: z.array(classChange),
  classes_above_threshold: z.number(),
});

export type FamilyCounter = z.infer<typeof familyCounterSchema>;
export type FamilyMember = z.infer<typeof familyMember>;
export type FamilyPayment = z.infer<typeof familyPaymentSchema>;
export type StructureNotice = z.infer<typeof structureNoticeSchema>;
export type StructureChanges = z.infer<typeof structureChangesSchema>;
export type ClassChange = z.infer<typeof classChange>;

export const FAMILY_KEYS = {
  family: (studentId: string) => ["staff", "fees", "family", studentId] as const,
  notice: (yearId: string) => ["staff", "fees", "structure-notice", yearId] as const,
  changes: (yearId: string, warn: string) =>
    ["staff", "fees", "structure-changes", yearId, warn] as const,
};

/** Paths of the print views (opened in a new tab through the BFF; FR-FEE-110). */
export function slipHref(receiptIds: readonly string[]): string {
  const query = receiptIds.map((id) => `receipt_ids=${encodeURIComponent(id)}`).join("&");
  return `/bff/api/v1/fees/family-payments/slip?${query}`;
}

export function noticePrintHref(yearId: string, classId?: string): string {
  const query = new URLSearchParams({ academic_year_id: yearId });
  if (classId) query.set("class_id", classId);
  return `/bff/api/v1/fees/structure-notice/print?${query.toString()}`;
}

/** JSON through the BFF (CSRF, step-up and sign-in handled by `createBffFetch`). */
export async function bffJson<T>(
  locale: string,
  path: string,
  schema: z.ZodType<T>,
  init: { method?: "GET" | "POST"; body?: unknown; headers?: Record<string, string> } = {},
): Promise<T> {
  const send = createBffFetch({ kind: "staff", locale });
  const headers: Record<string, string> = { accept: "application/json", ...init.headers };
  if (init.body !== undefined) headers["content-type"] = "application/json";
  const response = await send(
    new Request(`${window.location.origin}/bff${path}`, {
      method: init.method ?? "GET",
      headers,
      ...(init.body !== undefined ? { body: JSON.stringify(init.body) } : {}),
    }),
  );
  if (response.status === 405 || response.status === 501) throw new NotAvailableError();
  if (!response.ok) {
    const problem = (await response.json().catch(() => ({}))) as { code?: string };
    throw new ApiError(response.status, problem.code, problem);
  }
  return schema.parse(await response.json());
}

export function useFamily(studentId: string): Loadable<FamilyCounter> {
  const locale = useLocale();
  return useApiQuery(FAMILY_KEYS.family(studentId), () =>
    bffJson(
      locale,
      `/api/v1/fees/students/${encodeURIComponent(studentId)}/family`,
      familyCounterSchema,
    ),
  );
}

export function useStructureNotice(yearId: string | null): Loadable<StructureNotice> {
  const locale = useLocale();
  return useApiQuery(
    FAMILY_KEYS.notice(yearId ?? ""),
    () =>
      bffJson(
        locale,
        `/api/v1/fees/structure-notice?academic_year_id=${encodeURIComponent(yearId ?? "")}`,
        structureNoticeSchema,
      ),
    { enabled: yearId !== null },
  );
}

export function useStructureChanges(
  yearId: string | null,
  warn: string,
): Loadable<StructureChanges> {
  const locale = useLocale();
  return useApiQuery(
    FAMILY_KEYS.changes(yearId ?? "", warn),
    () => {
      const query = new URLSearchParams({ academic_year_id: yearId ?? "" });
      if (warn) query.set("warn_percent", warn);
      return bffJson(
        locale,
        `/api/v1/fees/structure-changes?${query.toString()}`,
        structureChangesSchema,
      );
    },
    { enabled: yearId !== null },
  );
}
