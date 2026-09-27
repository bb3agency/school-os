"use client";

import type { AcademicYear, SchoolClass, Section } from "@schoolos/api-client";
import type { QueryClient } from "@tanstack/react-query";
import { z } from "zod";
import { ApiError, unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import type { Loadable } from "@/lib/loadable";
import { isoDate, requiredInt, text, uuid } from "@/lib/validation";

/**
 * Academic structure (US-202, FR-TEN-010): academic years, classes and sections through the
 * BFF. Reads need `student.read_basic`; writes need `tenant.structure.manage` (no step-up).
 * Showing or hiding controls is UX only: the API checks every call.
 */

export const STRUCTURE_MANAGE = "tenant.structure.manage";

/**
 * Query keys. The first two segments match the keys the student list and the older read-only
 * structure view use, so invalidating a prefix refreshes every screen that shows the structure.
 */
export const STRUCTURE_KEYS = {
  years: ["staff", "academic-years"] as const,
  classes: ["staff", "classes"] as const,
  sections: ["staff", "sections"] as const,
  allYears: ["staff", "academic-years", "all"] as const,
  allClasses: ["staff", "classes", "all"] as const,
  sectionsForYear: (yearId: string) => ["staff", "sections", "year", yearId] as const,
};

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

function pageQuery(cursor: string | undefined) {
  return { limit: PAGE_SIZE, ...(cursor ? { cursor } : {}) };
}

export interface StructureLists {
  years: Loadable<readonly AcademicYear[]>;
  classes: Loadable<readonly SchoolClass[]>;
}

/** Academic years (newest first) and classes (display order), every page. */
export function useStructureLists(): StructureLists {
  const api = useBffClient("staff");
  const years = useApiQuery(STRUCTURE_KEYS.allYears, () =>
    fetchAllPages((cursor) =>
      unwrap(api.GET("/api/v1/academic-years", { params: { query: pageQuery(cursor) } })),
    ),
  );
  const classes = useApiQuery(STRUCTURE_KEYS.allClasses, () =>
    fetchAllPages((cursor) =>
      unwrap(api.GET("/api/v1/classes", { params: { query: pageQuery(cursor) } })),
    ),
  );
  return { years, classes };
}

/** Sections of one academic year (server-side filter), every page. */
export function useYearSections(yearId: string | null): Loadable<readonly Section[]> {
  const api = useBffClient("staff");
  return useApiQuery(
    STRUCTURE_KEYS.sectionsForYear(yearId ?? "none"),
    () =>
      fetchAllPages((cursor) =>
        unwrap(
          api.GET("/api/v1/sections", {
            params: { query: { ...pageQuery(cursor), academic_year_id: yearId ?? "" } },
          }),
        ),
      ),
    { enabled: yearId !== null },
  );
}

/** The year to show sections for: the one chosen, else the current year, else the newest. */
export function pickYear(years: readonly AcademicYear[], chosen: string | null): string | null {
  if (chosen && years.some((year) => year.id === chosen)) return chosen;
  return (years.find((year) => year.is_current) ?? years[0])?.id ?? null;
}

/** Class name in the UI language (the API stores English and Telugu names). */
export function classLabel(schoolClass: SchoolClass, locale: string): string {
  return locale === "te" && schoolClass.display_te
    ? schoolClass.display_te
    : schoolClass.display_en;
}

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

export const sectionCreateSchema = z.object({
  academic_year_id: uuid,
  class_id: uuid,
  name: text(16),
});

export const sectionEditSchema = z.object({ name: text(16) });

/** Next free display position after the last class (for a new class). */
export function nextSortOrder(classes: readonly SchoolClass[]): number {
  return Math.min(10000, classes.reduce((max, row) => Math.max(max, row.sort_order), -1) + 1);
}
