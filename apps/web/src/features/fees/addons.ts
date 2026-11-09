"use client";

import type { ApiClient } from "@schoolos/api-client";
import { unwrap } from "@/lib/bff/query";

/**
 * Per-student add-on fees: transport bands, hostel, activities (FM-02; FR-FEE-081..100; D85).
 * Templates are set up with `fee.manage`; who is on an add-on is read with `fee.addon.read`
 * (a transport in-charge), `finance.read` or `fee.manage`, and amounts come back `null` unless
 * the caller holds `finance.read` or `fee.manage` (`amounts_shown`). Only ids travel in URLs;
 * whom to assign is a POST body.
 *
 * LEAD: these routes are new in FM-02 and not in the generated client yet. The types below and
 * `addonApi` are a small locally typed wrapper; after `make openapi` swap them for
 * `components["schemas"]["AddonOut" | "AddonsOut" | ...]` and the typed `api.GET/POST/PATCH`.
 */

export const ADDON_READ = "fee.addon.read";

export interface Addon {
  id: string;
  academic_year_id: string;
  head_id: string;
  head_code: string;
  head_name: string;
  head_kind: string;
  label: string;
  active: boolean;
  assigned: number;
  amounts: Record<string, string> | null;
  total: string | null;
  version: number;
}

export interface AddonList {
  items: Addon[];
  amounts_shown: boolean;
  gst_note: string;
}

export interface AddonStudent {
  assignment_id: string;
  student_id: string;
  display_name: string | null;
  admission_no: string | null;
  class_section: string | null;
  start_instalment: number;
  end_instalment: number | null;
  status: "active" | "ended";
  charged: string | null;
}

export interface AddonStudents {
  addon_id: string;
  label: string;
  head_name: string;
  amounts_shown: boolean;
  items: AddonStudent[];
}

export type AddonSkipReason =
  "no_fee_account" | "account_closed" | "head_taken" | "already_assigned" | "no_instalments_left";

export interface AddonAssignResult {
  dry_run: boolean;
  students: number;
  assigned: number;
  charges_posted: number;
  replaced: number;
  credits_proposed: number;
  skipped: { student_id: string; reason: AddonSkipReason }[];
}

export interface AddonUnassignResult {
  dry_run: boolean;
  students: number;
  ended: number;
  not_assigned: number;
  credits_proposed: number;
  credit_total: string;
}

/** Exactly one of a class, some sections or picked students (FR-FEE-084). */
export type AddonTarget =
  { class_id: string } | { section_ids: string[] } | { student_ids: string[] };

export interface AddonCreateBody {
  academic_year_id: string;
  head_id: string;
  label: string;
  amounts: Record<string, string>;
}

/** Head kinds an add-on may charge (config `addons.yaml` `head_kinds`). */
export const ADDON_HEAD_KINDS: readonly string[] = ["transport", "books", "other"];

export const ADDON_KEYS = {
  all: ["staff", "fees", "addons"] as const,
  list: ["staff", "fees", "addons", "list"] as const,
  students: (id: string) => ["staff", "fees", "addons", "students", id] as const,
} as const;

type Result<T> = Promise<{ data?: T; error?: unknown; response: Response }>;
interface Loose {
  GET<T>(path: string, init?: object): Result<T>;
  POST<T>(path: string, init?: object): Result<T>;
  PATCH<T>(path: string, init?: object): Result<T>;
}

/** Locally typed calls of the FM-02 routes through the same BFF client (see LEAD above). */
export function addonApi(api: ApiClient) {
  const loose = api as unknown as Loose;
  const base = "/api/v1/fees/addons";
  return {
    list: () => unwrap(loose.GET<AddonList>(base)),
    create: (body: AddonCreateBody, idempotencyKey: string) =>
      unwrap(loose.POST<Addon>(base, { body, headers: { "Idempotency-Key": idempotencyKey } })),
    patch: (addon: Addon, body: { label?: string; active?: boolean }) =>
      unwrap(
        loose.PATCH<Addon>(`${base}/${encodeURIComponent(addon.id)}`, {
          body,
          headers: { "If-Match": `W/"${addon.version}"` },
        }),
      ),
    assign: (
      id: string,
      body: AddonTarget & {
        start_instalment: number;
        replace?: boolean;
        reason?: string;
        dry_run?: boolean;
      },
    ) =>
      unwrap(loose.POST<AddonAssignResult>(`${base}/${encodeURIComponent(id)}/assign`, { body })),
    unassign: (
      id: string,
      body: AddonTarget & { from_instalment: number; reason: string; dry_run?: boolean },
    ) =>
      unwrap(
        loose.POST<AddonUnassignResult>(`${base}/${encodeURIComponent(id)}/unassign`, { body }),
      ),
    students: (id: string) =>
      unwrap(loose.GET<AddonStudents>(`${base}/${encodeURIComponent(id)}/students`)),
  };
}

const AMOUNT = /^\d{1,12}(\.\d{1,2})?$/;

/**
 * Amounts per instalment typed as text ("1500", "1500.5") → the API's exact strings
 * ("1500.00"), or `null` when one is not a positive amount with at most two decimals. Empty
 * rows are left out; at least one amount is needed.
 */
export function amountsOf(values: readonly string[]): Record<string, string> | null {
  const out: Record<string, string> = {};
  values.forEach((raw, index) => {
    const value = raw.trim();
    if (value === "") return;
    if (!AMOUNT.test(value)) {
      out["invalid"] = value;
      return;
    }
    const [whole, part = ""] = value.split(".");
    if (/^0*$/.test(whole ?? "") && /^0*$/.test(part)) {
      out["invalid"] = value;
      return;
    }
    out[String(index + 1)] = `${String(Number(whole))}.${part.padEnd(2, "0")}`;
  });
  if ("invalid" in out || Object.keys(out).length === 0) return null;
  return out;
}
