import type { ApiClient } from "@schoolos/api-client";
import { describe, expect, it } from "vitest";
import { addDays, actionSchema } from "@/features/insights/data";
import { notificationHref } from "@/features/notifications/data";
import {
  fetchAttendanceCompleteness,
  fetchFollowUpCounts,
  fetchMarksCompleteness,
  sendReminder,
  takenShare,
  waitingFirst,
  type AttendanceCompleteness,
} from "./data";

/** FT-14 completeness (FR-ATT-016..025, FR-MRK-016..025) and FT-13 follow-ups (FR-EW-019..030). */

const SECTION = "00000000-0000-7000-8000-000000000001";
const EXAM = "00000000-0000-7000-8000-000000000002";

const ATTENDANCE: AttendanceCompleteness = {
  on_date: "2026-11-12",
  academic_year_id: "00000000-0000-7000-8000-000000000003",
  sections: 4,
  complete: 1,
  partial: 1,
  not_taken: 1,
  day_off: 0,
  no_students: 1,
  reminders_enabled: false,
  rows: [
    {
      section_id: "a",
      class_id: "c",
      label: "IX-A",
      students: 32,
      marked: 32,
      status: "complete",
      calendar: null,
      class_teacher: null,
    },
    {
      section_id: "b",
      class_id: "c",
      label: "IX-B",
      students: 30,
      marked: 18,
      status: "partial",
      calendar: null,
      class_teacher: { membership_id: "m", display_name: "Synthetic Teacher Anitha" },
    },
    {
      section_id: "c",
      class_id: "c",
      label: "IX-C",
      students: 28,
      marked: 0,
      status: "not_taken",
      calendar: null,
      class_teacher: null,
    },
    {
      section_id: "d",
      class_id: "c",
      label: "IX-D",
      students: 0,
      marked: 0,
      status: "no_students",
      calendar: null,
      class_teacher: null,
    },
  ],
};

function fakeApi(calls: { method: string; path: string; init?: unknown }[], body: unknown) {
  const answer = async (method: string, path: string, init?: unknown) => {
    calls.push({ method, path, init });
    return { data: body, response: new Response(null, { status: 200 }) };
  };
  return {
    GET: (path: string, init?: unknown) => answer("GET", path, init),
    POST: (path: string, init?: unknown) => answer("POST", path, init),
  } as unknown as ApiClient;
}

describe("FR-ATT-019 taken in N of M sections", () => {
  it("leaves out sections without students or school that day", () => {
    expect(takenShare(ATTENDANCE)).toEqual({ taken: 1, of: 3 });
  });

  it("lists sections that still wait first, keeping the school's order otherwise", () => {
    expect(waitingFirst(ATTENDANCE.rows).map((r) => r.label)).toEqual([
      "IX-C",
      "IX-B",
      "IX-A",
      "IX-D",
    ]);
  });
});

describe("FR-ATT-016 / FR-MRK-016 API calls", () => {
  it("reads attendance for a date and checks the answer", async () => {
    const calls: { method: string; path: string; init?: unknown }[] = [];
    const out = await fetchAttendanceCompleteness(fakeApi(calls, ATTENDANCE), "2026-11-12");
    expect(out.partial).toBe(1);
    expect(calls[0]).toEqual({
      method: "GET",
      path: "/api/v1/completeness/attendance",
      init: { params: { query: { date: "2026-11-12" } } },
    });
  });

  it("reads an exam's grid by id", async () => {
    const calls: { method: string; path: string; init?: unknown }[] = [];
    const grid = {
      exam_id: EXAM,
      exam_name: "Unit test 1",
      held_on: "2026-11-12",
      locked: false,
      subjects: ["Maths"],
      sections: 1,
      complete: 0,
      partial: 1,
      not_started: 0,
      no_students: 0,
      reminders_enabled: false,
      rows: [
        {
          section_id: SECTION,
          class_id: "c",
          label: "IX-A",
          students: 2,
          status: "partial",
          subjects: [{ subject: "Maths", entered: 1, status: "partial" }],
          class_teacher: null,
        },
      ],
    };
    expect((await fetchMarksCompleteness(fakeApi(calls, grid), EXAM)).subjects).toEqual(["Maths"]);
    expect(calls[0]?.init).toEqual({ params: { path: { exam_id: EXAM } } });
  });

  it("sends a reminder for one section", async () => {
    const calls: { method: string; path: string; init?: unknown }[] = [];
    const out = await sendReminder(fakeApi(calls, { sent: 1 }), {
      section_id: SECTION,
      kind: "attendance",
    });
    expect(out.sent).toBe(1);
    expect(calls[0]).toEqual({
      method: "POST",
      path: "/api/v1/completeness/reminders",
      init: { body: { section_id: SECTION, kind: "attendance" } },
    });
  });

  it("a reminder opens the attendance or marks screen", () => {
    expect(
      notificationHref({
        resource_type: "section",
        resource_id: SECTION,
        params: { kind: "attendance", section_id: SECTION },
      }),
    ).toBe("/attendance");
    expect(
      notificationHref({
        resource_type: "section",
        resource_id: SECTION,
        params: { kind: "marks", section_id: SECTION },
      }),
    ).toBe("/marks");
  });
});

describe("FR-EW-026 management counts", () => {
  it("reads the school's counts", async () => {
    const calls: { method: string; path: string; init?: unknown }[] = [];
    const body = {
      since: "2026-10-13",
      raised: 7,
      actioned_on_time: 5,
      actioned_late: 1,
      not_actioned: 1,
      overdue: 1,
      open: 3,
      review_due: 1,
      closed: 4,
      outcomes: { improved: 1, raised_in_error: 3 },
    };
    expect((await fetchFollowUpCounts(fakeApi(calls, body), "")).closed).toBe(4);
    expect(calls[0]?.path).toBe("/api/v1/insights/counts");
  });
});

describe("FR-EW-019 planned reviews", () => {
  it("accepts an empty or ISO review date", () => {
    const base = { kind: "called_parent", acted_on: "2026-11-12", note: "" };
    expect(actionSchema.safeParse({ ...base, review_on: "" }).success).toBe(true);
    expect(actionSchema.safeParse({ ...base, review_on: "2026-11-19" }).success).toBe(true);
    expect(actionSchema.safeParse({ ...base, review_on: "19/11/2026" }).success).toBe(false);
    expect(actionSchema.safeParse(base).success).toBe(true);
  });

  it("adds calendar days across a month end", () => {
    expect(addDays("2026-11-30", 1)).toBe("2026-12-01");
    expect(addDays("2026-11-12", 60)).toBe("2027-01-11");
  });
});
