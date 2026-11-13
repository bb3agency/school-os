import type { ApiClient, components } from "@schoolos/api-client";
import { unwrap } from "@/lib/bff/query";

/**
 * Records completeness (FT-14, FR-ATT-016..025, FR-MRK-016..025) and the management follow-up
 * counts (FT-13, FR-EW-026..028). Counts only: no student is named anywhere here.
 */

type Schemas = components["schemas"];

export const COMPLETENESS_READ = "completeness.read";
export const COMPLETENESS_REMIND = "completeness.remind";
export const COUNTS_READ = "insights.counts_read";

export const COMPLETENESS_KEYS = {
  all: ["staff", "completeness"] as const,
  attendance: (date: string) => ["staff", "completeness", "attendance", date] as const,
  exam: (examId: string) => ["staff", "completeness", "exam", examId] as const,
  counts: (since: string) => ["staff", "insights", "counts", since] as const,
};

export type AttendanceCompleteness = Schemas["AttendanceCompletenessOut"];
export type AttendanceRowStatus = Schemas["AttendanceCompletenessRow"]["status"];
export type MarksCompleteness = Schemas["MarksCompletenessOut"];
export type FollowUpCounts = Schemas["InsightCountsOut"];
export type ReminderInput = Schemas["CompletenessReminderIn"];

export function fetchAttendanceCompleteness(
  api: ApiClient,
  date: string,
): Promise<AttendanceCompleteness> {
  return unwrap(api.GET("/api/v1/completeness/attendance", { params: { query: { date } } }));
}

export function fetchMarksCompleteness(api: ApiClient, examId: string): Promise<MarksCompleteness> {
  return unwrap(
    api.GET("/api/v1/completeness/exams/{exam_id}", { params: { path: { exam_id: examId } } }),
  );
}

export function sendReminder(
  api: ApiClient,
  input: ReminderInput,
): Promise<Schemas["CompletenessReminderOut"]> {
  return unwrap(api.POST("/api/v1/completeness/reminders", { body: input }));
}

export function fetchFollowUpCounts(api: ApiClient, since: string): Promise<FollowUpCounts> {
  return unwrap(api.GET("/api/v1/insights/counts", since ? { params: { query: { since } } } : {}));
}

/** "Attendance taken in 14 of 16 sections": sections with students, and those complete. */
export function takenShare(out: AttendanceCompleteness): { taken: number; of: number } {
  const of = out.sections - out.no_students - out.day_off;
  return { taken: out.complete, of: Math.max(of, 0) };
}

/** Rows that still wait on someone come first; otherwise the school's class order is kept. */
export function waitingFirst<T extends { status: string }>(rows: readonly T[]): T[] {
  const rank = (s: string) =>
    s === "not_taken" || s === "not_started" ? 0 : s === "partial" ? 1 : 2;
  return rows
    .map((row, index) => ({ row, index }))
    .sort((a, b) => rank(a.row.status) - rank(b.row.status) || a.index - b.index)
    .map(({ row }) => row);
}
