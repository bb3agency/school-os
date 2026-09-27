"use client";

import type { AcademicYear, ApiClient, components } from "@schoolos/api-client";
import { ApiError, unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import type { Loadable } from "@/lib/loadable";
import { fetchAllPages } from "@/features/academic-structure/data";

/**
 * Year-end promotion (FR-TEN-011, US-202 AC2) through the BFF: preview (writes nothing),
 * commit (one transaction, Idempotency-Key, optional `plan_fingerprint`) and undo within
 * 24 hours. Every call needs `tenant.structure.manage`; student names come only from the
 * student list endpoints (`student.read_basic`, scoped by the API), never from the plan.
 */

type Schemas = components["schemas"];
export type PromotionRequest = Schemas["PromotionIn"];
export type PromotionPreview = Schemas["PromotionPreviewOut"];
export type PromotionRun = Schemas["PromotionRunOut"];
export type PromotionOutcome = Schemas["PromotionStudentOut"]["outcome"];
export type PromotionStudent = Schemas["PromotionStudentOut"];
export type StudentSummary = Schemas["StudentSummary"];

export const PROMOTE = "tenant.structure.manage";
export const STUDENT_READ = "student.read_basic";

export const PROMOTION_KEYS = {
  year: (yearId: string) => ["staff", "academic-years", "one", yearId] as const,
  runs: (yearId: string) => ["staff", "promotions", yearId] as const,
  names: (sectionId: string) => ["staff", "students", "promotion-names", sectionId] as const,
};

/** After a commit or an undo: enrolments, student lists and the structure all changed. */
export const AFTER_PROMOTION_KEYS = [
  ["staff", "promotions"],
  ["staff", "students"],
  ["staff", "sections"],
] as const;

/** Plan the user is building: target year, held-back students, section choices. */
export interface PromotionPlan {
  toYearId: string;
  /** Held-back student ids in the order chosen (with a label for the chips, if known). */
  heldBack: ReadonlyArray<{ id: string; label: string | null }>;
  /** `${fromSectionId}|${targetClassId}` → target section id. */
  sectionMap: Readonly<Record<string, string>>;
}

export function mapKey(fromSectionId: string, targetClassId: string): string {
  return `${fromSectionId}|${targetClassId}`;
}

/** The request body for preview and commit (ids only; sorted so equal plans compare equal). */
export function planRequest(plan: PromotionPlan): PromotionRequest {
  return {
    to_academic_year_id: plan.toYearId,
    held_back_student_ids: plan.heldBack.map((row) => row.id),
    section_map: Object.entries(plan.sectionMap)
      .filter(([, to]) => to !== "")
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([key, to]) => ({ from_section_id: key.split("|")[0] ?? "", to_section_id: to })),
  };
}

/** Same request (so the preview still describes what a commit would do)? */
export function sameRequest(a: PromotionRequest | null, b: PromotionRequest): boolean {
  return a !== null && JSON.stringify(a) === JSON.stringify(b);
}

/**
 * Years a promotion can go to: later than the source year (API rule `not_later`) and not
 * archived, earliest first. The first one is the default (normally next year).
 */
export function targetYears(
  years: readonly AcademicYear[],
  from: Pick<AcademicYear, "id" | "starts_on">,
): AcademicYear[] {
  return years
    .filter((year) => year.id !== from.id && !year.archived_at && year.starts_on > from.starts_on)
    .sort((a, b) => a.starts_on.localeCompare(b.starts_on));
}

/** The committed promotion that still blocks a new one (the API allows one per year). */
export function committedRun(runs: readonly PromotionRun[]): PromotionRun | undefined {
  return runs.find((run) => run.status === "committed");
}

export function useSourceYear(yearId: string, enabled: boolean): Loadable<AcademicYear> {
  const api = useBffClient("staff");
  return useApiQuery(
    PROMOTION_KEYS.year(yearId),
    () =>
      unwrap(
        api.GET("/api/v1/academic-years/{year_id}", { params: { path: { year_id: yearId } } }),
      ),
    { enabled },
  );
}

export function usePromotionRuns(
  yearId: string,
  enabled: boolean,
): Loadable<readonly PromotionRun[]> {
  const api = useBffClient("staff");
  return useApiQuery(
    PROMOTION_KEYS.runs(yearId),
    () =>
      unwrap(
        api.GET("/api/v1/academic-years/{year_id}/promotions", {
          params: { path: { year_id: yearId } },
        }),
      ),
    { enabled },
  );
}

/**
 * Field errors of a 422 turned into one problem code this feature explains in plain
 * language (`academicStructure.errors.<code>`), so the user learns what to change instead of
 * a generic "check the form". Other errors pass through unchanged.
 */
export function promotionError(error: unknown): unknown {
  if (!(error instanceof ApiError) || error.status !== 422) return error;
  const items: ReadonlyArray<{ field?: unknown; code?: unknown }> = Array.isArray(
    error.problem.errors,
  )
    ? error.problem.errors
    : [];
  for (const item of items) {
    if (typeof item.field !== "string" || typeof item.code !== "string") continue;
    const code = PROMOTION_FIELD_CODES[item.code]?.(item.field);
    if (!code) continue;
    // Without `errors` the dialog shows this one message instead of mapping fields.
    const { errors: _dropped, ...rest } = error.problem;
    void _dropped;
    return new ApiError(422, code, { ...rest, code });
  }
  return error;
}

const PROMOTION_FIELD_CODES: Record<string, (field: string) => string | undefined> = {
  no_target_section: () => "no_target_section",
  not_in_year: () => "promotion_held_back_not_in_year",
  same_year: () => "promotion_target_not_later",
  not_later: () => "promotion_target_not_later",
  not_found: (field) =>
    field.startsWith("to_academic_year_id")
      ? "promotion_target_not_found"
      : "promotion_section_map_invalid",
  duplicate: () => "promotion_section_map_invalid",
};

export async function previewPromotion(
  api: ApiClient,
  yearId: string,
  body: PromotionRequest,
): Promise<PromotionPreview> {
  try {
    return await unwrap(
      api.POST("/api/v1/academic-years/{year_id}/promotions:preview", {
        params: { path: { year_id: yearId } },
        body,
      }),
    );
  } catch (error) {
    throw promotionError(error);
  }
}

export async function commitPromotion(
  api: ApiClient,
  yearId: string,
  body: PromotionRequest,
  fingerprint: string,
  idempotencyKey: string,
): Promise<PromotionRun> {
  try {
    return await unwrap(
      api.POST("/api/v1/academic-years/{year_id}/promotions:commit", {
        params: { path: { year_id: yearId } },
        headers: { "Idempotency-Key": idempotencyKey },
        body: { ...body, plan_fingerprint: fingerprint },
      }),
    );
  } catch (error) {
    throw promotionError(error);
  }
}

export function undoPromotion(api: ApiClient, yearId: string): Promise<PromotionRun> {
  return unwrap(
    api.POST("/api/v1/academic-years/{year_id}/promotions:undo", {
      params: { path: { year_id: yearId } },
    }),
  );
}

/* ------------------------------------------------------------------------- student names */

const NAME_PAGE = 200;

/**
 * Students of one section with their names, through POST /students/search (names never go in
 * a URL; SEC-008). Only for holders of `student.read_basic`; the API limits the answer to what
 * the caller may see and to current-year enrolments, so a student may have no name here.
 */
export function useSectionStudents(
  sectionId: string | null,
  enabled: boolean,
): Loadable<readonly StudentSummary[]> {
  const api = useBffClient("staff");
  return useApiQuery(
    PROMOTION_KEYS.names(sectionId ?? "none"),
    () =>
      fetchAllPages((cursor) =>
        unwrap(
          api.POST("/api/v1/students/search", {
            body: { section_id: sectionId, limit: NAME_PAGE, ...(cursor ? { cursor } : {}) },
          }),
        ),
      ),
    { enabled: enabled && sectionId !== null },
  );
}

/** Search by name or admission number (body only), for choosing held-back students. */
export function searchStudents(api: ApiClient, query: string): Promise<readonly StudentSummary[]> {
  return unwrap(api.POST("/api/v1/students/search", { body: { query, limit: 20 } })).then(
    (page) => page.data,
  );
}

/** "Asha Test (A-101)", or null when the list gave no name. */
export function studentLabel(student: StudentSummary | undefined): string | null {
  if (!student?.display_name) return null;
  return student.admission_no
    ? `${student.display_name} (${student.admission_no})`
    : student.display_name;
}
