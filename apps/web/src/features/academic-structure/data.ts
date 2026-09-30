"use client";

import type {
  AcademicYear,
  ApiClient,
  SchoolClass,
  Section,
  components,
} from "@schoolos/api-client";
import type { QueryClient } from "@tanstack/react-query";
import { z } from "zod";
import { ApiError, unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import type { Loadable } from "@/lib/loadable";
import { UUID_PATTERN, isoDate, requiredInt, text, uuid } from "@/lib/validation";

/**
 * Academic structure (US-202, FR-TEN-010): academic years, classes and sections through the
 * BFF. Reads need `student.read_basic`; writes need `tenant.structure.manage` (no step-up).
 * Showing or hiding controls is UX only: the API checks every call. Archived rows are hidden
 * unless the screen asks for them (`include_archived=true`).
 */

export const STRUCTURE_MANAGE = "tenant.structure.manage";

/**
 * Query keys. The first two segments match the keys the student list uses, so invalidating a
 * prefix refreshes every screen that shows the structure.
 */
export const STRUCTURE_KEYS = {
  years: ["staff", "academic-years"] as const,
  classes: ["staff", "classes"] as const,
  sections: ["staff", "sections"] as const,
  allYears: (archived: boolean) =>
    ["staff", "academic-years", "all", archivedKey(archived)] as const,
  allClasses: (archived: boolean) => ["staff", "classes", "all", archivedKey(archived)] as const,
  sectionsForYear: (yearId: string, archived = false) =>
    ["staff", "sections", "year", yearId, archivedKey(archived)] as const,
  /** Staff directory for the class teacher picker (GET /staff). */
  staff: ["staff", "staff-directory"] as const,
};

function archivedKey(archived: boolean): string {
  return archived ? "with-archived" : "in-use";
}

/** Every structure list (after a write, or after a 412 so the next edit starts fresh). */
export const ALL_STRUCTURE_KEYS = [
  STRUCTURE_KEYS.years,
  STRUCTURE_KEYS.classes,
  STRUCTURE_KEYS.sections,
] as const;

/** `W/"3"` for If-Match from a resource's `version` (the API's ETag format). */
export function ifMatch(version: number): string {
  return `W/"${version}"`;
}

const PAGE_SIZE = 200;
/** A school has a few dozen classes and sections per year; stop well before anything odd. */
const MAX_PAGES = 20;

type PageOf<T> = { data: T[]; next_cursor: string | null };

/** Follow `next_cursor` so a school with many years or sections never sees a cut-off list. */
export async function fetchAllPages<T>(
  load: (cursor: string | undefined) => Promise<PageOf<T>>,
): Promise<T[]> {
  const rows: T[] = [];
  let cursor: string | undefined;
  for (let pageNo = 0; pageNo < MAX_PAGES; pageNo += 1) {
    const result = await load(cursor);
    rows.push(...result.data);
    if (!result.next_cursor) break;
    cursor = result.next_cursor;
  }
  return rows;
}

function pageQuery(cursor: string | undefined, archived = false) {
  return {
    limit: PAGE_SIZE,
    ...(cursor ? { cursor } : {}),
    ...(archived ? { include_archived: true } : {}),
  };
}

export interface StructureLists {
  years: Loadable<readonly AcademicYear[]>;
  classes: Loadable<readonly SchoolClass[]>;
}

/**
 * Academic years (newest first) and classes (display order), every page. Archived rows come
 * only when `archived` is true (the "Show archived" switch).
 */
export function useStructureLists(archived = false): StructureLists {
  const api = useBffClient("staff");
  const years = useApiQuery(STRUCTURE_KEYS.allYears(archived), () =>
    fetchAllPages((cursor) =>
      unwrap(api.GET("/api/v1/academic-years", { params: { query: pageQuery(cursor, archived) } })),
    ),
  );
  const classes = useApiQuery(STRUCTURE_KEYS.allClasses(archived), () =>
    fetchAllPages((cursor) =>
      unwrap(api.GET("/api/v1/classes", { params: { query: pageQuery(cursor, archived) } })),
    ),
  );
  return { years, classes };
}

/** Sections of one academic year (server-side filter), every page. */
export function useYearSections(
  yearId: string | null,
  archived = false,
): Loadable<readonly Section[]> {
  const api = useBffClient("staff");
  return useApiQuery(
    STRUCTURE_KEYS.sectionsForYear(yearId ?? "none", archived),
    () =>
      fetchAllPages((cursor) =>
        unwrap(
          api.GET("/api/v1/sections", {
            params: { query: { ...pageQuery(cursor, archived), academic_year_id: yearId ?? "" } },
          }),
        ),
      ),
    { enabled: yearId !== null },
  );
}

/** Archived rows are kept for old records but hidden from lists (US-202). */
export function isArchived(row: { archived_at?: string | null }): boolean {
  return Boolean(row.archived_at);
}

/* ------------------------------------------------------------------- archive / unarchive */

export type StructureKind = "year" | "class" | "section";

/**
 * POST .../archive or .../unarchive with If-Match (US-202, FR-TEN-010). The current year
 * answers 409 `academic_year_current`; a row with active enrolments 409 `structure_in_use`.
 */
export function setArchived(
  api: ApiClient,
  kind: StructureKind,
  row: { id: string; version: number },
  archive: boolean,
): Promise<unknown> {
  const headers = { "If-Match": ifMatch(row.version) };
  switch (kind) {
    case "year": {
      const params = { path: { year_id: row.id } };
      return archive
        ? unwrap(api.POST("/api/v1/academic-years/{year_id}/archive", { params, headers }))
        : unwrap(api.POST("/api/v1/academic-years/{year_id}/unarchive", { params, headers }));
    }
    case "class": {
      const params = { path: { class_id: row.id } };
      return archive
        ? unwrap(api.POST("/api/v1/classes/{class_id}/archive", { params, headers }))
        : unwrap(api.POST("/api/v1/classes/{class_id}/unarchive", { params, headers }));
    }
    case "section": {
      const params = { path: { section_id: row.id } };
      return archive
        ? unwrap(api.POST("/api/v1/sections/{section_id}/archive", { params, headers }))
        : unwrap(api.POST("/api/v1/sections/{section_id}/unarchive", { params, headers }));
    }
  }
}

/* ------------------------------------------------------------------ class teacher picker */

export type StaffMember = components["schemas"]["StaffMemberOut"];

/**
 * Staff directory (GET /staff: membership id, display name and role keys only; needs
 * `tenant.structure.manage` or `user.manage`), every page, sorted by name for the picker.
 */
export function useStaffDirectory(
  enabled: boolean,
  locale: string,
): Loadable<readonly StaffMember[]> {
  const api = useBffClient("staff");
  return useApiQuery(
    [...STRUCTURE_KEYS.staff, locale],
    async () =>
      sortStaff(
        await fetchAllPages((cursor) =>
          unwrap(api.GET("/api/v1/staff", { params: { query: pageQuery(cursor) } })),
        ),
        locale,
      ),
    { enabled },
  );
}

/** By display name (case-insensitive, in the UI language), then id so the order is stable. */
export function sortStaff(rows: readonly StaffMember[], locale = "en"): StaffMember[] {
  return [...rows].sort(
    (a, b) =>
      a.display_name.localeCompare(b.display_name, locale, { sensitivity: "base" }) ||
      a.membership_id.localeCompare(b.membership_id),
  );
}

/** The year to show sections for: the one chosen, else the current year, else the newest. */
export function pickYear(years: readonly AcademicYear[], chosen: string | null): string | null {
  if (chosen && years.some((year) => year.id === chosen)) return chosen;
  return (years.find((year) => year.is_current) ?? years[0])?.id ?? null;
}

/** Class name in the UI language (shared helper; re-exported for this feature's imports). */
export { classLabel } from "@/lib/school-class";

/**
 * Run an edit; when someone else changed the record first (412) refetch the lists before the
 * error is shown, so "reload and try again" starts from the latest version.
 */
export async function withFreshOnConflict<T>(
  queryClient: QueryClient,
  run: () => Promise<T>,
): Promise<T> {
  try {
    return await run();
  } catch (error) {
    if (error instanceof ApiError && (error.status === 412 || error.status === 409)) {
      await Promise.all(
        ALL_STRUCTURE_KEYS.map((queryKey) => queryClient.invalidateQueries({ queryKey })),
      );
    }
    throw error;
  }
}

/* ------------------------------------------------------------------ form schemas (zod) */

/** "2026-27": the year it starts, then the last two digits of the next year (API rule). */
export const YEAR_LABEL_PATTERN = /^\d{4}-\d{2}$/;
/** Class code as the API accepts it: capitals, digits, "_" or "-" (for example NUR, LKG, 10). */
export const CLASS_CODE_PATTERN = /^[A-Z0-9][A-Z0-9_-]{0,15}$/;

export function yearLabelFits(label: string, startsOn: string): boolean {
  if (!YEAR_LABEL_PATTERN.test(label)) return false;
  const first = Number(label.slice(0, 4));
  const second = Number(label.slice(5));
  if (second !== (first + 1) % 100) return false;
  return !/^\d{4}/.test(startsOn) || Number(startsOn.slice(0, 4)) === first;
}

/** A suggested label for a start date: 2026-06-01 → "2026-27". */
export function suggestYearLabel(startsOn: string): string {
  const match = /^(\d{4})-/.exec(startsOn);
  if (!match) return "";
  const first = Number(match[1]);
  return `${first}-${String((first + 1) % 100).padStart(2, "0")}`;
}

const yearFields = z.object({
  label: z
    .string()
    .trim()
    .min(1, { error: "required" })
    .regex(YEAR_LABEL_PATTERN, { error: "invalid" }),
  starts_on: z.string().trim().min(1, { error: "required" }).pipe(isoDate),
  ends_on: z.string().trim().min(1, { error: "required" }).pipe(isoDate),
});

function checkYear(
  value: { label: string; starts_on: string; ends_on: string },
  ctx: z.RefinementCtx,
): void {
  if (!yearLabelFits(value.label, value.starts_on)) {
    ctx.addIssue({ code: "custom", path: ["label"], message: "invalid" });
  }
  if (!(value.starts_on < value.ends_on)) {
    ctx.addIssue({ code: "custom", path: ["ends_on"], message: "endAfterStart" });
  }
}

export const yearCreateSchema = yearFields
  .extend({
    is_current: z
      .string()
      .optional()
      .transform((value) => value === "on"),
  })
  .superRefine(checkYear);

export const yearEditSchema = yearFields.superRefine(checkYear);

const className = text(100);

export const classCreateSchema = z.object({
  code: z
    .string()
    .trim()
    .toUpperCase()
    .min(1, { error: "required" })
    .regex(CLASS_CODE_PATTERN, { error: "invalid" }),
  display_en: className,
  display_te: className,
  sort_order: requiredInt(0, 10000),
});

export const classEditSchema = z.object({
  display_en: className,
  display_te: className,
  sort_order: requiredInt(0, 10000),
});

/**
 * ADR-0036: the class forms ask for the Telugu name only while Telugu is switched on. With it
 * off the field is not shown, so it may be absent.
 */
export function classCreateSchemaFor(telugu: boolean) {
  return classCreateSchema.extend({ display_te: telugu ? className : className.optional() });
}

export function classEditSchemaFor(telugu: boolean) {
  return classEditSchema.extend({ display_te: telugu ? className : className.optional() });
}

/**
 * The class teacher picker: "" means no class teacher (null); absent (undefined) means the
 * picker was not shown, so the field is left as it is.
 */
const classTeacher = z
  .string()
  .trim()
  .optional()
  .refine((value) => value === undefined || value === "" || UUID_PATTERN.test(value), {
    error: "chooseOption",
  })
  .transform((value) => (value === undefined ? undefined : value === "" ? null : value));

export const sectionCreateSchema = z.object({
  academic_year_id: uuid,
  class_id: uuid,
  name: text(16),
  class_teacher_membership_id: classTeacher,
});

export const sectionEditSchema = z.object({
  name: text(16),
  class_teacher_membership_id: classTeacher,
});

/** Next free display position after the last class (for a new class). */
export function nextSortOrder(classes: readonly SchoolClass[]): number {
  return Math.min(10000, classes.reduce((max, row) => Math.max(max, row.sort_order), -1) + 1);
}
