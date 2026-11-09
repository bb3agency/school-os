"use client";

import type { ApiClient, components } from "@schoolos/api-client";
import { unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import type { Loadable } from "@/lib/loadable";

/**
 * School calendar (FT-06; FR-CAL-001..FR-CAL-020) through the BFF. Reads need
 * `student.read_basic`; changes need `tenant.structure.manage`.
 */

type S = components["schemas"];
export type CalendarEntryKind = S["CalendarEntryOut"]["kind"];
export type CalendarDayKind = S["CalendarDayOut"]["kind"];
export type CalendarDay = S["CalendarDayOut"];
export type CalendarEntry = S["CalendarEntryOut"];
export type CalendarEntryAdded = S["CalendarEntryAddedOut"];
export type SchoolCalendar = S["SchoolCalendarOut"];
export type CalendarMonth = S["CalendarMonthOut"];
export type CalendarSuggestion = S["CalendarSuggestionOut"];
export type CalendarSuggestions = S["CalendarSuggestionsOut"];
export type CalendarEntryInput = S["CalendarEntryIn"];

export const CALENDAR_MANAGE = "tenant.structure.manage";
export const ENTRY_KINDS: readonly CalendarEntryKind[] = ["holiday", "vacation", "working_day"];
/** 0 = Monday .. 6 = Sunday (the API's numbering). */
export const WEEKDAYS = [0, 1, 2, 3, 4, 5, 6] as const;
export const SATURDAY_NUMBERS = [1, 2, 3, 4, 5] as const;

export const SCHOOL_CALENDAR_KEYS = {
  all: ["staff", "school-calendar"] as const,
  year: (yearId: string) => ["staff", "school-calendar", yearId, "year"] as const,
  month: (yearId: string, month: string, classId: string) =>
    ["staff", "school-calendar", yearId, "month", month, classId] as const,
  suggestions: (yearId: string) => ["staff", "school-calendar", yearId, "suggestions"] as const,
};

export function ifMatch(version: number): string {
  return `W/"${version}"`;
}

export async function loadCalendar(api: ApiClient, yearId: string): Promise<SchoolCalendar> {
  return unwrap(
    api.GET("/api/v1/academic-years/{year_id}/calendar", {
      params: { path: { year_id: yearId } },
    }),
  );
}

export async function savePattern(
  api: ApiClient,
  yearId: string,
  version: number,
  pattern: { weekly_off: number[]; saturdays_off: number[] },
): Promise<SchoolCalendar> {
  return unwrap(
    api.PUT("/api/v1/academic-years/{year_id}/calendar", {
      params: { path: { year_id: yearId } },
      headers: { "If-Match": ifMatch(version) },
      body: pattern,
    }),
  );
}

export async function addEntry(
  api: ApiClient,
  yearId: string,
  body: CalendarEntryInput,
  idempotencyKey: string,
): Promise<CalendarEntryAdded> {
  return unwrap(
    api.POST("/api/v1/academic-years/{year_id}/calendar/entries", {
      params: { path: { year_id: yearId } },
      headers: { "Idempotency-Key": idempotencyKey },
      body,
    }),
  );
}

export async function removeEntry(api: ApiClient, yearId: string, entryId: string): Promise<null> {
  await unwrap(
    api.DELETE("/api/v1/academic-years/{year_id}/calendar/entries/{entry_id}", {
      params: { path: { year_id: yearId, entry_id: entryId } },
    }),
  );
  return null;
}

export function useSchoolCalendar(yearId: string | null): Loadable<SchoolCalendar> {
  const api = useBffClient("staff");
  return useApiQuery(
    SCHOOL_CALENDAR_KEYS.year(yearId ?? ""),
    () => loadCalendar(api, yearId ?? ""),
    {
      enabled: yearId !== null,
    },
  );
}

export function useCalendarMonth(
  yearId: string | null,
  month: string,
  classId: string,
): Loadable<CalendarMonth> {
  const api = useBffClient("staff");
  return useApiQuery(
    SCHOOL_CALENDAR_KEYS.month(yearId ?? "", month, classId),
    () =>
      unwrap(
        api.GET("/api/v1/academic-years/{year_id}/calendar/month", {
          params: {
            path: { year_id: yearId ?? "" },
            query: classId ? { month, class_id: classId } : { month },
          },
        }),
      ),
    { enabled: yearId !== null },
  );
}

export function useCalendarSuggestions(
  yearId: string | null,
  enabled: boolean,
): Loadable<CalendarSuggestions> {
  const api = useBffClient("staff");
  return useApiQuery(
    SCHOOL_CALENDAR_KEYS.suggestions(yearId ?? ""),
    () =>
      unwrap(
        api.GET("/api/v1/academic-years/{year_id}/calendar/suggestions", {
          params: { path: { year_id: yearId ?? "" } },
        }),
      ),
    { enabled: enabled && yearId !== null },
  );
}

/** `YYYY-MM` of an ISO date. */
export function monthOf(isoDate: string): string {
  return isoDate.slice(0, 7);
}

/** The month before or after `YYYY-MM`. */
export function shiftMonth(month: string, by: number): string {
  const [y, m] = month.split("-").map(Number) as [number, number];
  const index = y * 12 + (m - 1) + by;
  const year = Math.floor(index / 12);
  const mon = (index % 12) + 1;
  return `${year}-${String(mon).padStart(2, "0")}`;
}

/** Monday-first weekday of an ISO date (0 = Monday .. 6 = Sunday), in UTC so no zone shift. */
export function weekdayOf(isoDate: string): number {
  const day = new Date(`${isoDate}T00:00:00Z`).getUTCDay();
  return (day + 6) % 7;
}

/** The ISO date `by` days after `isoDate`. */
export function addDays(isoDate: string, by: number): string {
  const date = new Date(`${isoDate}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + by);
  return date.toISOString().slice(0, 10);
}
