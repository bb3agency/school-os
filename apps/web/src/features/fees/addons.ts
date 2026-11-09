"use client";

import type { ApiClient, components } from "@schoolos/api-client";
import { unwrap } from "@/lib/bff/query";

/**
 * Per-student add-on fees: transport bands, hostel, activities (FM-02; FR-FEE-081..100; D85).
 * Templates are set up with `fee.manage`; who is on an add-on is read with `fee.addon.read`
 * (a transport in-charge), `finance.read` or `fee.manage`, and amounts come back `null` unless
 * the caller holds `finance.read` or `fee.manage` (`amounts_shown`). Only ids travel in URLs;
 * whom to assign is a POST body.
 */

export const ADDON_READ = "fee.addon.read";

type S = components["schemas"];
export type Addon = S["AddonOut"];
export type AddonList = S["AddonsOut"];
export type AddonStudent = S["AddonStudentOut"];
export type AddonStudents = S["AddonStudentsOut"];
export type AddonSkipReason = S["AddonSkipped"]["reason"];
export type AddonAssignResult = S["AddonAssignOut"];
export type AddonUnassignResult = S["AddonUnassignOut"];
export type AddonCreateBody = S["AddonCreate"];

/** Exactly one of a class, some sections or picked students (FR-FEE-084). */
export type AddonTarget =
  { class_id: string } | { section_ids: string[] } | { student_ids: string[] };

/** Head kinds an add-on may charge (config `addons.yaml` `head_kinds`). */
export const ADDON_HEAD_KINDS: readonly string[] = ["transport", "books", "other"];

export const ADDON_KEYS = {
  all: ["staff", "fees", "addons"] as const,
  list: ["staff", "fees", "addons", "list"] as const,
  students: (id: string) => ["staff", "fees", "addons", "students", id] as const,
} as const;

/**
 * openapi-typescript types request fields that have a server default (`dry_run`, `replace`) as
 * required; the API fills them in, so the screen may leave them out of the body.
 */
type WithServerDefaults<T, K extends keyof T> = Omit<T, K> & Partial<Pick<T, K>>;
type TargetKeys = "class_id" | "section_ids" | "student_ids";
export type AddonAssignBody = AddonTarget &
  WithServerDefaults<Omit<S["AddonAssignIn"], TargetKeys>, "dry_run" | "replace">;
export type AddonUnassignBody = AddonTarget &
  WithServerDefaults<Omit<S["AddonUnassignIn"], TargetKeys>, "dry_run">;

/** The FM-02 add-on routes through the typed BFF client. */
export function addonApi(api: ApiClient) {
  const path = (addon_id: string) => ({ path: { addon_id } });
  return {
    list: () => unwrap(api.GET("/api/v1/fees/addons")),
    create: (body: AddonCreateBody, idempotencyKey: string) =>
      unwrap(
        api.POST("/api/v1/fees/addons", { body, headers: { "Idempotency-Key": idempotencyKey } }),
      ),
    patch: (addon: Addon, body: { label?: string; active?: boolean }) =>
      unwrap(
        api.PATCH("/api/v1/fees/addons/{addon_id}", {
          params: path(addon.id),
          body,
          headers: { "If-Match": `W/"${addon.version}"` },
        }),
      ),
    assign: (id: string, body: AddonAssignBody) =>
      unwrap(
        api.POST("/api/v1/fees/addons/{addon_id}/assign", {
          params: path(id),
          body: body as S["AddonAssignIn"],
        }),
      ),
    unassign: (id: string, body: AddonUnassignBody) =>
      unwrap(
        api.POST("/api/v1/fees/addons/{addon_id}/unassign", {
          params: path(id),
          body: body as S["AddonUnassignIn"],
        }),
      ),
    students: (id: string) =>
      unwrap(api.GET("/api/v1/fees/addons/{addon_id}/students", { params: path(id) })),
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
