"use client";

import type { ApiClient, components } from "@schoolos/api-client";
import { unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import type { Loadable } from "@/lib/loadable";

/**
 * Family counter (FM-07; FR-FEE-101..112) and fee structure notice (FM-10; FR-FEE-113..125).
 * The calls go through the BFF client, so CSRF, step-up (428) and sign-in handling apply.
 * Amounts stay exact decimal strings (FR-FEE-028).
 */

type Schemas = components["schemas"];

export type FamilyCounter = Schemas["FamilyCounterOut"];
export type FamilyMember = Schemas["FamilyMember"];
export type FamilyPayment = Schemas["FamilyPaymentOut"];
export type FamilyPaymentInput = Schemas["FamilyPaymentCreate"];
export type StructureNotice = Schemas["FeeStructureNoticeOut"];
export type StructureChanges = Schemas["FeeStructureChangesOut"];
export type ClassChange = StructureChanges["classes"][number];

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

/** Takes one family payment (FR-FEE-104): one receipt per account, one idempotency key. */
export function postFamilyPayment(
  api: ApiClient,
  body: FamilyPaymentInput,
  idempotencyKey: string,
): Promise<FamilyPayment> {
  return unwrap(
    api.POST("/api/v1/fees/family-payments", {
      body,
      headers: { "Idempotency-Key": idempotencyKey },
    }),
  );
}

export function useFamily(studentId: string): Loadable<FamilyCounter> {
  const api = useBffClient("staff");
  return useApiQuery(FAMILY_KEYS.family(studentId), () =>
    unwrap(
      api.GET("/api/v1/fees/students/{student_id}/family", {
        params: { path: { student_id: studentId } },
      }),
    ),
  );
}

export function useStructureNotice(yearId: string | null): Loadable<StructureNotice> {
  const api = useBffClient("staff");
  return useApiQuery(
    FAMILY_KEYS.notice(yearId ?? ""),
    () =>
      unwrap(
        api.GET("/api/v1/fees/structure-notice", {
          params: { query: { academic_year_id: yearId ?? "" } },
        }),
      ),
    { enabled: yearId !== null },
  );
}

export function useStructureChanges(
  yearId: string | null,
  warn: string,
): Loadable<StructureChanges> {
  const api = useBffClient("staff");
  return useApiQuery(
    FAMILY_KEYS.changes(yearId ?? "", warn),
    () =>
      unwrap(
        api.GET("/api/v1/fees/structure-changes", {
          params: {
            query: { academic_year_id: yearId ?? "", ...(warn ? { warn_percent: warn } : {}) },
          },
        }),
      ),
    { enabled: yearId !== null },
  );
}
