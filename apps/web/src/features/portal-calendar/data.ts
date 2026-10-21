"use client";

import { useQuery } from "@tanstack/react-query";
import type { components } from "@schoolos/api-client";
import { unwrap, useBffClient } from "@/lib/bff/query";

/** Portal calendar types (US-3101..US-3104, ADR-0053), all from the generated API client. */
type Schemas = components["schemas"];
export type CalendarOut = Schemas["CalendarOut"];
export type CalendarEvent = Schemas["CalendarEventOut"];
export type CalendarState = CalendarEvent["state"];
export type CalendarSourceKind = NonNullable<CalendarEvent["source_kind"]>;
export type PortalWindow = Schemas["PortalWindowOut"];
export type CalendarAssignee = Schemas["CalendarAssigneeOut"];

type Exhaustive<T extends string, L extends readonly T[]> = [T] extends [L[number]] ? L : never;

const sourceKinds = [
  "circular",
  "portal",
  "phone",
  "other",
] as const satisfies readonly CalendarSourceKind[];
/** Where a confirmed date came from (FR-RDY-004), in the order the dialog offers them. */
export const SOURCE_KINDS: Exhaustive<CalendarSourceKind, typeof sourceKinds> = sourceKinds;

export const CALENDAR_KEYS = {
  all: ["staff", "dq", "calendar"],
  year: (yearId: string | null) =>
    ["staff", "dq", "calendar", "year", yearId ?? "current"] as const,
  assignees: ["staff", "dq", "calendar", "assignees"] as const,
} as const;

export function ifMatch(version: number): string {
  return `W/"${version}"`;
}

/** GET /dq/calendar (the current academic year unless one is chosen). */
export function useCalendar(yearId: string | null) {
  const api = useBffClient("staff");
  return useQuery({
    queryKey: CALENDAR_KEYS.year(yearId),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/dq/calendar", {
          params: { query: yearId ? { academic_year_id: yearId } : {} },
        }),
      ),
    retry: false,
  });
}

/** GET /dq/calendar/assignees (readiness.calendar.manage): who can own a window. */
export function useCalendarAssignees(enabled: boolean) {
  const api = useBffClient("staff");
  return useQuery({
    queryKey: CALENDAR_KEYS.assignees,
    queryFn: () => unwrap(api.GET("/api/v1/dq/calendar/assignees")),
    enabled,
    retry: false,
    staleTime: 60_000,
  });
}

/** Month and year of an ISO date ("Feb 2027"), for "Expected around …". */
export function monthYear(iso: string, locale: string): string {
  const [year, month] = iso.split("-").map(Number);
  return new Intl.DateTimeFormat(locale === "te" ? "te-IN" : "en-IN", {
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  }).format(new Date(Date.UTC(year ?? 1970, (month ?? 1) - 1, 1)));
}

/** "Feb 2027" or "Sep – Nov 2026" for an expected (unconfirmed) window. */
export function expectedRange(opensOn: string, closesOn: string, locale: string): string {
  const from = monthYear(opensOn, locale);
  const to = monthYear(closesOn, locale);
  return from === to ? to : `${from} – ${to}`;
}
