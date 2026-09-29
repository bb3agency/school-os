"use client";

import type { components } from "@schoolos/api-client";
import { useQuery } from "@tanstack/react-query";
import { z } from "zod";
import { AuthRedirectError } from "@/lib/bff/fetch";
import { ApiError, NotAvailableError, toLoadable, unwrap, useBffClient } from "@/lib/bff/query";
import type { Loadable } from "@/lib/loadable";

/**
 * Student timeline and early warning (M5; US-1701..US-1709). Attendance and marks are school
 * records; notes, flags and timelines are restricted and purpose-limited (08 §4): the API shows
 * them only to the student's class teacher and the principal and audits every read. Flags come
 * from fixed rules, never AI, and every flag needs a person to act. These permission values only
 * decide what the screens offer; the API checks every call.
 */

export const PERM = {
  attendanceRead: "attendance.read",
  attendanceRecord: "attendance.record",
  examManage: "exam.manage",
  marksRead: "marks.read",
  marksRecord: "marks.record",
  insightsRead: "insights.read",
  insightsNote: "insights.note",
  insightsAct: "insights.act",
  insightsManage: "insights.manage",
} as const;

type S = components["schemas"];
export type Flag = S["InsightFlagOut"];
export type FlagDetail = S["InsightFlagDetail"];
export type FlagAction = S["ActionOut"];
export type FlagStatus = Flag["status"];
export type FlagRule = Flag["rule"];
export type Indicator = Flag["indicator"];
export type Note = S["NoteOut"];
export type NoteCategory = Note["category"];
export type Summary = S["InsightSummaryOut"];
export type Settings = S["SettingsOut"];
export type RuleSetting = S["RuleSettingOut"];
export type Owner = S["OwnerOut"];
export type Timeline = S["TimelineOut"];
export type TimelineEntry = S["TimelineItem"];
export type AttendanceDay = S["AttendanceDayOut"];
export type AttendanceMonth = S["AttendanceMonthOut"];
export type AttendanceStatus = S["AttendanceEntryIn"]["status"];
export type AttendanceEntry = S["AttendanceEntryIn"];
export type AttendanceSheet = S["AttendanceSheetOut"];
export type Exam = S["ExamOut"];
export type MarksGrid = S["MarksGridOut"];
export type MarkIn = S["MarkIn-Input"];
export type MarksSheet = S["MarksSheetOut"];
export type SheetIssue = S["SheetIssueOut"];
export type ActionKind = S["ActionIn"]["kind"];
export type CloseReason = S["CloseIn"]["reason"];
export type EraseReason = S["EraseIn"]["reason"];

export const ATTENDANCE_STATUSES: readonly AttendanceStatus[] = [
  "present",
  "absent",
  "late",
  "leave",
];
export const NOTE_CATEGORIES: readonly NoteCategory[] = ["positive", "observation", "concern"];
export const ACTION_KINDS: readonly ActionKind[] = [
  "talked_with_student",
  "called_parent",
  "met_parent",
  "home_visit",
  "remedial_support",
  "referred_counsellor",
  "referred_principal",
  "other",
];
export const CLOSE_REASONS: readonly CloseReason[] = [
  "improved",
  "support_in_place",
  "parent_informed",
  "no_concern",
  "student_left",
  "raised_in_error",
];
export const ERASE_REASONS: readonly EraseReason[] = ["parent_request", "entered_in_error"];
export const INDICATORS: readonly Indicator[] = ["attendance", "behaviour", "course"];

export interface Paged<T> {
  data: T[];
  next_cursor: string | null;
}

export interface FlagFilters {
  view: "mine" | "all";
  status: FlagStatus | "active";
  indicator: Indicator | "any";
  due: "overdue" | "any";
}

export const KEYS = {
  all: ["staff", "insights"] as const,
  flags: ["staff", "insights", "flags"] as const,
  flagList: (filters: FlagFilters) => ["staff", "insights", "flags", "list", filters] as const,
  flag: (id: string) => ["staff", "insights", "flags", "detail", id] as const,
  owners: (id: string) => ["staff", "insights", "flags", "owners", id] as const,
  summary: ["staff", "insights", "summary"] as const,
  settings: ["staff", "insights", "settings"] as const,
  timeline: (studentId: string) => ["staff", "insights", "timeline", studentId] as const,
  attendance: ["staff", "attendance"] as const,
  day: (section: string, date: string) => ["staff", "attendance", "day", section, date] as const,
  month: (section: string, month: string) =>
    ["staff", "attendance", "month", section, month] as const,
  exams: ["staff", "exams"] as const,
  marks: (section: string, exam: string) => ["staff", "marks", section, exam] as const,
} as const;

/** `W/"3"` for If-Match (the API's ETag format). */
export function ifMatch(version: number): string {
  return `W/"${version}"`;
}

/** Today in India (IST) as `YYYY-MM-DD`: attendance dates are IST calendar dates (FR-ATT-001). */
export function todayIst(now: Date = new Date()): string {
  return new Date(now.getTime() + 330 * 60_000).toISOString().slice(0, 10);
}

/** 1-based column number as a spreadsheet letter (1 → A, 27 → AA) for sheet problems. */
export function columnLetter(column: number): string {
  let n = column;
  let out = "";
  while (n > 0) {
    const rest = (n - 1) % 26;
    out = String.fromCharCode(65 + rest) + out;
    n = Math.floor((n - 1) / 26);
  }
  return out;
}

/** Letter shown in the month register (the paper register's codes). */
export const REGISTER_CODE: Record<AttendanceStatus, string> = {
  present: "P",
  absent: "A",
  late: "L",
  leave: "LV",
};

/**
 * The evidence a rule saw, as ICU values for the "why" sentence. Numbers, codes and dates only
 * (the API never puts names or note text in evidence).
 */
export function evidenceValues(flag: Pick<Flag, "evidence">): Record<string, string | number> {
  const out: Record<string, string | number> = {};
  for (const [key, value] of Object.entries(flag.evidence ?? {})) {
    if (typeof value === "number" || typeof value === "string") out[key] = value;
  }
  return out;
}

function retry(count: number, error: unknown): boolean {
  return (
    count < 1 &&
    !(error instanceof NotAvailableError) &&
    !(error instanceof AuthRedirectError) &&
    !(error instanceof ApiError && error.status < 500)
  );
}

/** GET /insights/flags (soonest due first). */
export function useFlags(filters: FlagFilters, enabled: boolean): Loadable<Paged<Flag>> {
  const api = useBffClient("staff");
  const query = useQuery({
    queryKey: KEYS.flagList(filters),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/insights/flags", {
          params: {
            query: {
              view: filters.view,
              limit: 100,
              ...(filters.status !== "active" ? { status: filters.status } : {}),
              ...(filters.indicator !== "any" ? { indicator: filters.indicator } : {}),
              ...(filters.due !== "any" ? { due: filters.due } : {}),
            },
          },
        }),
      ),
    enabled,
    retry,
  });
  return toLoadable(query);
}

export function useFlag(id: string, enabled: boolean): Loadable<FlagDetail> {
  const api = useBffClient("staff");
  const query = useQuery({
    queryKey: KEYS.flag(id),
    queryFn: () =>
      unwrap(api.GET("/api/v1/insights/flags/{flag_id}", { params: { path: { flag_id: id } } })),
    enabled,
    retry,
  });
  return toLoadable(query);
}

export function useOwners(id: string, enabled: boolean): Loadable<Owner[]> {
  const api = useBffClient("staff");
  const query = useQuery({
    queryKey: KEYS.owners(id),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/insights/flags/{flag_id}/owners", {
          params: { path: { flag_id: id } },
        }),
      ),
    enabled,
    retry,
  });
  return toLoadable(query);
}

export function useSummary(enabled: boolean): Loadable<Summary> {
  const api = useBffClient("staff");
  const query = useQuery({
    queryKey: KEYS.summary,
    queryFn: () => unwrap(api.GET("/api/v1/insights/summary")),
    enabled,
    retry,
  });
  return toLoadable(query);
}

export function useSettings(enabled: boolean): Loadable<Settings> {
  const api = useBffClient("staff");
  const query = useQuery({
    queryKey: KEYS.settings,
    queryFn: () => unwrap(api.GET("/api/v1/insights/settings")),
    enabled,
    retry,
    staleTime: 60_000,
  });
  return toLoadable(query);
}

export function useTimeline(studentId: string, enabled: boolean): Loadable<Timeline> {
  const api = useBffClient("staff");
  const query = useQuery({
    queryKey: KEYS.timeline(studentId),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/students/{student_id}/timeline", {
          params: { path: { student_id: studentId } },
        }),
      ),
    enabled,
    retry,
  });
  return toLoadable(query);
}

export function useAttendanceDay(
  section: string,
  date: string,
  enabled: boolean,
): Loadable<AttendanceDay> {
  const api = useBffClient("staff");
  const query = useQuery({
    queryKey: KEYS.day(section, date),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/sections/{section_id}/attendance", {
          params: { path: { section_id: section }, query: { date } },
        }),
      ),
    enabled: enabled && section !== "" && date !== "",
    retry,
  });
  return toLoadable(query);
}

export function useAttendanceMonth(
  section: string,
  month: string,
  enabled: boolean,
): Loadable<AttendanceMonth> {
  const api = useBffClient("staff");
  const query = useQuery({
    queryKey: KEYS.month(section, month),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/sections/{section_id}/attendance/month", {
          params: { path: { section_id: section }, query: { month } },
        }),
      ),
    enabled: enabled && section !== "" && /^\d{4}-\d{2}$/.test(month),
    retry,
  });
  return toLoadable(query);
}

export function useExams(enabled: boolean): Loadable<Exam[]> {
  const api = useBffClient("staff");
  const query = useQuery({
    queryKey: KEYS.exams,
    queryFn: () => unwrap(api.GET("/api/v1/exams")),
    enabled,
    retry,
  });
  return toLoadable(query);
}

export function useMarks(section: string, exam: string, enabled: boolean): Loadable<MarksGrid> {
  const api = useBffClient("staff");
  const query = useQuery({
    queryKey: KEYS.marks(section, exam),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/sections/{section_id}/exams/{exam_id}/marks", {
          params: { path: { section_id: section, exam_id: exam } },
        }),
      ),
    enabled: enabled && section !== "" && exam !== "",
    retry,
  });
  return toLoadable(query);
}

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

/** A 12-digit number never goes into a note (the API refuses full Aadhaar numbers too). */
export function looksLikeAadhaar(text: string): boolean {
  return /(?<!\d)\d{4}[\s-]?\d{4}[\s-]?\d{4}(?!\d)/.test(text);
}

const freeText = (max: number) =>
  z
    .string()
    .trim()
    .max(max, { error: "tooLong" })
    .refine((value) => !looksLikeAadhaar(value), { error: "noAadhaar" });

/** A behaviour note (FR-EW-010): category, date, 1..500 characters, never an Aadhaar number. */
export const noteSchema = z.object({
  category: z.enum(["positive", "observation", "concern"], { error: "chooseOption" }),
  noted_on: z.string().regex(ISO_DATE, { error: "invalidDate" }),
  text: freeText(500).refine((value) => value.length > 0, { error: "required" }),
});

/** An action on a flag (FR-EW-007): what was done, when, an optional note. */
export const actionSchema = z.object({
  kind: z.enum(ACTION_KINDS as [ActionKind, ...ActionKind[]], { error: "chooseOption" }),
  acted_on: z.string().regex(ISO_DATE, { error: "invalidDate" }),
  note: freeText(1000),
});

/** Closing a flag: a reason and an optional note. */
export const closeSchema = z.object({
  reason: z.enum(CLOSE_REASONS as [CloseReason, ...CloseReason[]], { error: "chooseOption" }),
  note: freeText(1000),
});

/** Raising a flag by hand (FR-EW-008): the indicator and an optional note. */
export const raiseSchema = z.object({
  indicator: z.enum(["attendance", "behaviour", "course"], { error: "chooseOption" }),
  note: freeText(1000),
});

/** Giving a flag to another eligible staff member (FR-EW-014). */
export const assignSchema = z.object({
  owner_membership_id: z.string().uuid({ error: "chooseOption" }),
});

/** Erasing a flag or note (FR-EW-014): why, from a fixed list. */
export const eraseSchema = z.object({
  reason: z.enum(ERASE_REASONS as [EraseReason, ...EraseReason[]], { error: "chooseOption" }),
});

/** A new exam of the current year (FR-MRK-001). */
export const examSchema = z.object({
  name: z.string().trim().min(1, { error: "required" }).max(80, { error: "tooLong" }),
  held_on: z.string().regex(ISO_DATE, { error: "invalidDate" }),
});
