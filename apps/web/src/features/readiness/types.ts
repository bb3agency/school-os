/**
 * Board and portal readiness types (US-503..US-505, ADR-0040), all from the generated API client.
 * Runtime lists carry `satisfies` checks so they fail to compile if the API adds a value.
 */
import type { components } from "@schoolos/api-client";

type Schemas = components["schemas"];

export type ReadinessSummary = Schemas["ReadinessSummaryOut"];
export type ReadinessSection = Schemas["ReadinessSectionOut"];
export type ReadinessStudent = Schemas["ReadinessStudentOut"];
export type ReadinessDetail = Schemas["ReadinessStudentDetailOut"];
export type ReadinessFieldDetail = Schemas["ReadinessFieldOut"];
export type ReadinessItem = Schemas["ReadinessItemOut"];
export type ReadinessValue = Schemas["ReadinessValueOut"];
export type DiffSegment = Schemas["DiffSegment"];
export type ReadinessStatus = ReadinessStudent["status"];
export type FixOwner = ReadinessItem["owner"];

type Exhaustive<T extends string, L extends readonly T[]> = [T] extends [L[number]] ? L : never;

const statuses = [
  "blocked",
  "needs_school",
  "needs_parent",
  "ready",
] as const satisfies readonly ReadinessStatus[];
/** Worst first (the API's order for a section's students). */
export const READINESS_STATUSES: Exhaustive<ReadinessStatus, typeof statuses> = statuses;

const owners = [
  "parent_aadhaar",
  "school_udise",
  "school_register",
  "unknown",
] as const satisfies readonly FixOwner[];
export const FIX_OWNERS: Exhaustive<FixOwner, typeof owners> = owners;

/** The profile shown when none is chosen: the AP SSC board (the Feb 2027 window, D1). */
export const DEFAULT_PROFILE = "bseap-ssc-2027";

const PROFILE_KEY = /^[a-z0-9][a-z0-9-]{0,63}$/;

export function isProfileKey(value: unknown): value is string {
  return typeof value === "string" && PROFILE_KEY.test(value);
}

/** "142 of 160 ready" as a fraction for the progress bar (0 when the section is empty). */
export function readyShare(counts: { students: number; ready: number }): number {
  return counts.students > 0 ? counts.ready / counts.students : 0;
}

/** The parent verification slips through the BFF (an API page with its own strict CSP). */
export function slipHref(
  profileKey: string,
  target: { studentId: string } | { sectionId: string },
): string {
  const query =
    "studentId" in target
      ? `student_id=${encodeURIComponent(target.studentId)}`
      : `section_id=${encodeURIComponent(target.sectionId)}`;
  return `/bff/api/v1/dq/readiness/${encodeURIComponent(profileKey)}/slips?${query}`;
}
