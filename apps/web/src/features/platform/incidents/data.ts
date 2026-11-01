import type { components } from "@schoolos/api-client";
import * as z from "@/lib/zod";
import { localDateTimeToUtc } from "@/lib/validation";
import { BREACH_CATEGORIES } from "@/features/breaches/types";

/**
 * SchoolOS incident records in the operator console (ADR-0050; FR-DPDP-030, FR-DPDP-031): the
 * CERT-In clock (T0 + 6 h), the affected schools with their notice clocks (confirmation + 24 h),
 * and the two-person confirmation (SEC-029) before any school is told. Control-plane data only:
 * categories and counts, never student data.
 */
type Schemas = components["schemas"];

export type Incident = Schemas["IncidentOut"];
export type IncidentDetail = Schemas["IncidentDetailOut"];
export type IncidentSchool = Schemas["IncidentSchoolOut"];
export type IncidentPatch = Schemas["IncidentPatch"];
export type Severity = Incident["severity"];
export type IncidentStatus = Incident["status"];

type Exhaustive<T extends string, L extends readonly T[]> = [T] extends [L[number]] ? L : never;

const severities = ["P1", "P2", "P3", "P4"] as const satisfies readonly Severity[];
export const SEVERITIES: Exhaustive<Severity, typeof severities> = severities;

const statuses = [
  "open",
  "contained",
  "resolved",
  "closed",
] as const satisfies readonly IncidentStatus[];
export const INCIDENT_STATUSES: Exhaustive<IncidentStatus, typeof statuses> = statuses;

export const INCIDENTS_READ = "platform.incidents.read";
export const INCIDENTS_MANAGE = "platform.incidents.manage";

export const INCIDENT_KEYS = {
  all: ["operator", "incidents"] as const,
  list: ["operator", "incidents", "list"] as const,
  detail: (id: string) => ["operator", "incidents", "detail", id] as const,
};

/** A run of 6 or more digits (phone, Aadhaar, account numbers) or an e-mail address. */
const PERSONAL = /\d{6,}|@/;

/**
 * The summary holds categories and counts only (FR-DPDP-030): no names, phone or Aadhaar
 * numbers, or e-mail addresses. The API refuses a run of 6+ digits and "@" (422); the form says
 * so before sending. 10 to 500 characters.
 */
export const incidentSummary = z
  .string()
  .trim()
  .min(1, { error: "required" })
  .min(10, { error: "descriptionTooShort" })
  .max(500, { error: "tooLong" })
  .refine((value) => !PERSONAL.test(value.split(/\s+/).join(" ")), {
    error: "summaryPersonalData",
  });

const pastDateTime = z
  .string()
  .trim()
  .min(1, { error: "required" })
  .refine((value) => localDateTimeToUtc(value) !== null, { error: "invalidDate" })
  .transform((value) => localDateTimeToUtc(value) as string)
  .refine((iso) => new Date(iso).getTime() <= Date.now() + 60_000, { error: "notInFuture" });

const optionalDateTime = z
  .string()
  .trim()
  .default("")
  .refine((value) => value === "" || localDateTimeToUtc(value) !== null, {
    error: "invalidDate",
  })
  .transform((value) => (value === "" ? null : (localDateTimeToUtc(value) as string)));

export const newIncidentSchema = z.object({
  opened_at: pastDateTime,
  severity: z.enum(SEVERITIES, { error: "chooseOption" }),
  summary: incidentSummary,
  cert_in_required: z
    .string()
    .optional()
    .transform((value) => value === "on"),
});

export const addSchoolSchema = z
  .object({
    tenant_id: z.string().trim().min(1, { error: "chooseOption" }),
    categories: z
      .array(z.string())
      .default([])
      .transform((values) =>
        values.filter((value): value is (typeof BREACH_CATEGORIES)[number] =>
          (BREACH_CATEGORIES as readonly string[]).includes(value),
        ),
      )
      .refine((values) => values.length >= 1, { error: "chooseCategories" }),
    approx_people: z
      .string()
      .trim()
      .min(1, { error: "required" })
      .refine((value) => /^\d{1,7}$/.test(value), { error: "invalidNumber" })
      .transform(Number),
    window_from: optionalDateTime,
    window_to: optionalDateTime,
  })
  .refine((data) => !data.window_from || !data.window_to || data.window_from <= data.window_to, {
    path: ["window_to"],
    error: "endAfterStart",
  });

export const reportedSchema = z.object({ cert_in_reported_at: pastDateTime });

const HOUR_MS = 3_600_000;

/**
 * A clock in words for the console ("due in 4 h", "overdue by 2 h", "within the hour"). Whole
 * hours rounded down; at least 1 h once overdue.
 */
export type Clock =
  { state: "due"; hours: number } | { state: "dueSoon" } | { state: "overdue"; hours: number };

export function clockFor(dueAt: string, now: Date = new Date()): Clock | null {
  const left = new Date(dueAt).getTime() - now.getTime();
  if (Number.isNaN(left)) return null;
  if (left < 0) return { state: "overdue", hours: Math.max(1, Math.floor(-left / HOUR_MS)) };
  const hours = Math.floor(left / HOUR_MS);
  return hours === 0 ? { state: "dueSoon" } : { state: "due", hours };
}

/**
 * Where the two-person confirmation stands (SEC-029): nobody yet, waiting for a second operator
 * (until `confirm_by`), or confirmed by two.
 */
export type Confirmation =
  | { state: "none" }
  | { state: "waiting"; mine: boolean; by: string | null }
  | { state: "confirmed"; at: string };

export function confirmationOf(
  incident: Pick<Incident, "confirmed_at" | "confirmed_by" | "confirm_by">,
  operatorId: string | null | undefined,
): Confirmation {
  if (incident.confirmed_at) return { state: "confirmed", at: incident.confirmed_at };
  if (incident.confirmed_by) {
    return {
      state: "waiting",
      mine: Boolean(operatorId) && incident.confirmed_by === operatorId,
      by: incident.confirm_by,
    };
  }
  return { state: "none" };
}

/** Why a closed incident should be reopened (D48): the summary's rule, categories and counts. */
export const reopenSchema = z.object({ reason: incidentSummary });

/**
 * Where a reopen of a closed incident stands (D48): a closed incident is locked; one operator
 * asks to reopen it and a different operator confirms (two-person rule, until
 * `reopen_confirm_by`).
 */
export type Reopen =
  { state: "none" } | { state: "waiting"; mine: boolean; by: string | null; reason: string | null };

export function reopenOf(
  incident: Pick<
    Incident,
    "status" | "reopen_requested_by" | "reopen_confirm_by" | "reopen_reason"
  >,
  operatorId: string | null | undefined,
): Reopen {
  if (incident.status !== "closed" || !incident.reopen_requested_by) return { state: "none" };
  return {
    state: "waiting",
    mine: Boolean(operatorId) && incident.reopen_requested_by === operatorId,
    by: incident.reopen_confirm_by ?? null,
    reason: incident.reopen_reason ?? null,
  };
}
