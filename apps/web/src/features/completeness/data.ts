import type { ApiClient } from "@schoolos/api-client";
import * as z from "@/lib/zod";
import { unwrap } from "@/lib/bff/query";

/**
 * Records completeness (FT-14, FR-ATT-016..025, FR-MRK-016..025) and the management follow-up
 * counts (FT-13, FR-EW-026..028). Counts only: no student is named anywhere here.
 *
 * The three routes are not in the generated client yet (the lead regenerates apps/api/openapi.json
 * after the wave): until then the calls go through `loose()` and every answer is checked with
 * the zod schemas below. After regeneration, swap `loose(api)` for `api` and the zod types for
 * `components["schemas"]["AttendanceCompletenessOut" | "MarksCompletenessOut" | ...]`.
 */

export const COMPLETENESS_READ = "completeness.read";
export const COMPLETENESS_REMIND = "completeness.remind";
export const COUNTS_READ = "insights.counts_read";

export const COMPLETENESS_KEYS = {
  all: ["staff", "completeness"] as const,
  attendance: (date: string) => ["staff", "completeness", "attendance", date] as const,
  exam: (examId: string) => ["staff", "completeness", "exam", examId] as const,
  counts: (since: string) => ["staff", "insights", "counts", since] as const,
};

const teacher = z.object({ membership_id: z.string(), display_name: z.string().nullable() });

export const attendanceCompletenessSchema = z.object({
  on_date: z.string(),
  academic_year_id: z.string(),
  sections: z.number(),
  complete: z.number(),
  partial: z.number(),
  not_taken: z.number(),
  day_off: z.number(),
  no_students: z.number(),
  reminders_enabled: z.boolean(),
  rows: z.array(
    z.object({
      section_id: z.string(),
      class_id: z.string(),
      label: z.string(),
      students: z.number(),
      marked: z.number(),
      status: z.enum(["complete", "partial", "not_taken", "day_off", "no_students"]),
      calendar: z.string().nullable(),
      class_teacher: teacher.nullable(),
    }),
  ),
});
export type AttendanceCompleteness = z.infer<typeof attendanceCompletenessSchema>;
export type AttendanceRowStatus = AttendanceCompleteness["rows"][number]["status"];

export const marksCompletenessSchema = z.object({
  exam_id: z.string(),
  exam_name: z.string(),
  held_on: z.string(),
  locked: z.boolean(),
  subjects: z.array(z.string()),
  sections: z.number(),
  complete: z.number(),
  partial: z.number(),
  not_started: z.number(),
  no_students: z.number(),
  reminders_enabled: z.boolean(),
  rows: z.array(
    z.object({
      section_id: z.string(),
      class_id: z.string(),
      label: z.string(),
      students: z.number(),
      status: z.enum(["complete", "partial", "not_started", "no_students"]),
      subjects: z.array(
        z.object({
          subject: z.string(),
          entered: z.number(),
          status: z.enum(["complete", "partial", "missing", "no_students"]),
        }),
      ),
      class_teacher: teacher.nullable(),
    }),
  ),
});
export type MarksCompleteness = z.infer<typeof marksCompletenessSchema>;

export const followUpCountsSchema = z.object({
  since: z.string(),
  raised: z.number(),
  actioned_on_time: z.number(),
  actioned_late: z.number(),
  not_actioned: z.number(),
  overdue: z.number(),
  open: z.number(),
  review_due: z.number(),
  closed: z.number(),
  outcomes: z.record(z.string(), z.number()),
});
export type FollowUpCounts = z.infer<typeof followUpCountsSchema>;

type Init = {
  params?: { path?: Record<string, string>; query?: Record<string, string> };
  body?: unknown;
};
type Result = Promise<{ data?: unknown; error?: unknown; response: Response }>;
interface LooseClient {
  GET(path: string, init?: Init): Result;
  POST(path: string, init?: Init): Result;
}

/** The generated client without path typing, for the routes not generated yet (see above). */
function loose(api: ApiClient): LooseClient {
  return api as unknown as LooseClient;
}

export async function fetchAttendanceCompleteness(
  api: ApiClient,
  date: string,
): Promise<AttendanceCompleteness> {
  const data = await unwrap(
    loose(api).GET("/api/v1/completeness/attendance", { params: { query: { date } } }),
  );
  return attendanceCompletenessSchema.parse(data);
}

export async function fetchMarksCompleteness(
  api: ApiClient,
  examId: string,
): Promise<MarksCompleteness> {
  const data = await unwrap(
    loose(api).GET("/api/v1/completeness/exams/{exam_id}", {
      params: { path: { exam_id: examId } },
    }),
  );
  return marksCompletenessSchema.parse(data);
}

export async function sendReminder(
  api: ApiClient,
  input: { section_id: string; kind: "attendance" | "marks"; exam_id?: string },
): Promise<{ sent: number }> {
  const data = await unwrap(loose(api).POST("/api/v1/completeness/reminders", { body: input }));
  return z.object({ sent: z.number() }).parse(data);
}

export async function fetchFollowUpCounts(api: ApiClient, since: string): Promise<FollowUpCounts> {
  const data = await unwrap(
    loose(api).GET("/api/v1/insights/counts", since ? { params: { query: { since } } } : {}),
  );
  return followUpCountsSchema.parse(data);
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
