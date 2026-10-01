import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import type * as Upload from "@/features/imports/upload";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { notificationHref } from "@/features/notifications/data";
import { installBffStub, page, problem, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { intlErrors, messages, renderWithIntl } from "@/test/render";
import { SECTION, me, structureRoutes } from "@/test/school-fixtures";
import { AttendanceScreen, recentMonths } from "./AttendanceScreen";
import {
  columnLetter,
  looksLikeAadhaar,
  noteSchema,
  todayIst,
  type AttendanceDay,
  type Flag,
  type FlagDetail,
  type Settings,
  type Summary,
  type Timeline,
} from "./data";
import { FlagDetailScreen } from "./FlagDetailScreen";
import { FlagsScreen } from "./FlagsScreen";
import { MarksScreen } from "./MarksScreen";
import { StudentInsights } from "./StudentInsights";

const push = vi.fn();
vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => "/flags",
    useRouter: () => ({ push, replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
    useSearchParams: () => new URLSearchParams(),
    useParams: () => ({ locale: "en" }),
  };
});

const uploads = vi.hoisted(() => ({ next: "0192f3a4-0000-7000-8000-00000000d0c1" }));
vi.mock("@/features/imports/upload", async (importOriginal) => {
  const actual = await importOriginal<typeof Upload>();
  return { ...actual, uploadDocument: vi.fn(() => Promise.resolve(uploads.next)) };
});

const en = messages.en;
const FLAG = "0192f3a4-0000-7000-8000-00000000f501";
const STUDENT_ID = "0192f3a4-0000-7000-8000-00000000e501";
const OTHER_STUDENT = "0192f3a4-0000-7000-8000-00000000e502";
const OWNER = "0192f3a4-0000-7000-8000-0000000000b1";
const NEW_OWNER = "0192f3a4-0000-7000-8000-0000000000b9";
const YEAR = "0192f3a4-0000-7000-8000-0000000000y1";
const CLASS = "0192f3a4-0000-7000-8000-0000000000c9";

const READ = ["insights.read", "student.read_sensitive"];
const TEACHER = [...READ, "insights.note", "insights.act"];
const PRINCIPAL = [...TEACHER, "insights.manage"];

function flag(overrides: Partial<Flag> = {}): Flag {
  return {
    id: FLAG,
    student: {
      id: STUDENT_ID,
      full_name: "Synthetica Ravi",
      admission_no: "SYN-0001",
      section_label: "IX-A",
    },
    indicator: "attendance",
    rule: "attendance_streak",
    evidence: { days: 3, from: "2026-09-22", to: "2026-09-24", threshold: 3 },
    status: "open",
    owner: { membership_id: OWNER, display_name: "Synthetic Teacher" },
    raised_on: "2026-09-24",
    due_on: "2026-10-01",
    overdue: true,
    actioned: false,
    first_action_at: null,
    closed_at: null,
    close_reason: null,
    raised_by: null,
    version: 4,
    ...overrides,
  };
}

function detail(overrides: Partial<FlagDetail> = {}): FlagDetail {
  return {
    ...flag(),
    actions: [
      {
        id: "0192f3a4-0000-7000-8000-00000000f601",
        kind: "raised",
        acted_on: "2026-09-24",
        note: null,
        by: null,
        created_at: "2026-09-24T12:10:00Z",
      },
    ],
    ...overrides,
  };
}

const SUMMARY: Summary = {
  since: "2026-08-30",
  raised: 4,
  actioned_on_time: 3,
  actioned_late: 0,
  not_actioned: 1,
  overdue: 1,
  open: 2,
};

function settings(): Settings {
  return {
    rules: [
      {
        key: "attendance_streak",
        indicator: "attendance",
        enabled: true,
        can_disable: false,
        threshold: 3,
        default: 3,
        min: 2,
        max: 5,
        window: null,
        min_days: null,
      },
      {
        key: "attendance_rate",
        indicator: "attendance",
        enabled: true,
        can_disable: true,
        threshold: 75,
        default: 75,
        min: 60,
        max: 90,
        window: 30,
        min_days: 10,
      },
    ],
    rules_version: 1,
    due_days: 7,
    version: 0,
    updated_at: null,
  };
}

function section() {
  return { id: SECTION, class_id: CLASS, academic_year_id: YEAR, label: "IX-A" };
}

function day(overrides: Partial<AttendanceDay> = {}): AttendanceDay {
  return {
    section: section(),
    on_date: todayIst(),
    marked: false,
    students: [
      {
        student: {
          student_id: STUDENT_ID,
          full_name: "Synthetica Ravi",
          admission_no: "SYN-0001",
          roll_no: "1",
        },
        status: null,
      },
      {
        student: {
          student_id: OTHER_STUDENT,
          full_name: "Synthetica Anjali",
          admission_no: "SYN-0002",
          roll_no: "2",
        },
        status: null,
      },
    ],
    counts: {},
    ...overrides,
  };
}

function timeline(): Timeline {
  return {
    student: {
      id: STUDENT_ID,
      full_name: "Synthetica Ravi",
      admission_no: "SYN-0001",
      section_label: "IX-A",
    },
    indicators: {
      attendance: {
        days: 20,
        present: 16,
        late: 1,
        absent: 3,
        leave: 0,
        rate: 85,
        streak: 3,
        concern: true,
      },
      behaviour: { concerns: 1, window_days: 30, concern: false },
      course: {
        exam_id: "0192f3a4-0000-7000-8000-00000000ea01",
        percent: 62.5,
        previous_percent: 70,
        change: -7.5,
        concern: false,
      },
    },
    items: [
      { kind: "flag", on: "2026-09-24", flag: detail() },
      {
        kind: "note",
        on: "2026-09-20",
        note: {
          id: "0192f3a4-0000-7000-8000-00000000ab01",
          student_id: STUDENT_ID,
          category: "concern",
          noted_on: "2026-09-20",
          text: "Synthetic note: seemed tired in class.",
          by: { membership_id: OWNER, display_name: "Synthetic Teacher" },
          created_at: "2026-09-20T09:00:00Z",
        },
      },
      {
        kind: "exam",
        on: "2026-08-10",
        exam: {
          exam_id: "0192f3a4-0000-7000-8000-00000000ea01",
          name: "Formative assessment 1",
          percent: 62.5,
          papers: 5,
          absent_papers: 0,
        },
      },
      {
        kind: "attendance_month",
        on: "2026-09-01",
        attendance: { month: "2026-09", days: 20, present: 16, late: 1, absent: 3, leave: 0 },
      },
      {
        kind: "enrolment",
        on: "2026-06-12",
        enrolment: {
          section_label: "IX-A",
          status: "active",
          started_on: "2026-06-12",
          ended_on: null,
        },
      },
    ],
  };
}

let stub: BffStub;

beforeEach(() => {
  stub = installBffStub("staff");
  push.mockReset();
  Object.assign(stub.routes, structureRoutes());
  stub.routes["GET /bff/api/v1/insights/summary"] = () => Response.json(SUMMARY);
  stub.routes["GET /bff/api/v1/insights/settings"] = () => Response.json(settings());
});
afterEach(() => {
  uninstallBffStub();
  expect(intlErrors).toEqual([]);
});

function signedIn(permissions: string[]) {
  stub.routes["GET /bff/api/v1/me"] = () => Response.json(me(permissions));
}

describe("flags list (US-1705, US-1706, US-1708; FR-EW-009, FR-EW-015)", () => {
  it("shows my flags with the reason in words and overdue in words", async () => {
    signedIn(TEACHER);
    stub.routes["GET /bff/api/v1/insights/flags"] = () => page([flag()]);
    renderWithIntl(<FlagsScreen />);
    const link = await screen.findByRole("link", { name: "Synthetica Ravi" });
    expect(link).toHaveAttribute("href", `/flags/${FLAG}`);
    const row = link.closest("tr") as HTMLElement;
    expect(
      within(row).getByText(/Absent 3 school days in a row \(22\/09\/2026 to 24\/09\/2026\)/),
    ).toBeVisible();
    expect(within(row).getByText(en.insights.overdue)).toBeVisible();
    expect(screen.getByText(en.insights.purpose)).toBeVisible();
    // Counts only (FR-EW-015): 3 of 4 acted on in time.
    expect(await screen.findByText("75%")).toBeVisible();
    const call = stub.callsTo("GET /bff/api/v1/insights/flags")[0];
    expect(call?.url.searchParams.get("view")).toBe("mine");
    expect(call?.url.searchParams.get("status")).toBeNull();
    // Only the principal changes rules.
    expect(await screen.findByText(en.insights.rules.names.attendance_rate)).toBeVisible();
    expect(screen.queryByRole("button", { name: en.insights.rules.save })).toBeNull();
  });

  it("asks for the school view and a status filter", async () => {
    signedIn(TEACHER);
    stub.routes["GET /bff/api/v1/insights/flags"] = () => page([]);
    renderWithIntl(<FlagsScreen />);
    expect(await screen.findByText(en.insights.emptyTitle)).toBeVisible();
    await userEvent.click(screen.getByRole("radio", { name: en.insights.view.all }));
    await userEvent.selectOptions(screen.getByLabelText(en.insights.statusLabel), "closed");
    await waitFor(() =>
      expect(
        stub
          .callsTo("GET /bff/api/v1/insights/flags")
          .some(
            (c) =>
              c.url.searchParams.get("view") === "all" &&
              c.url.searchParams.get("status") === "closed",
          ),
      ).toBe(true),
    );
  });

  it("says who can see flags when the user may not", async () => {
    signedIn(["student.read_basic"]);
    renderWithIntl(<FlagsScreen />);
    expect(await screen.findByText(en.insights.noAccessTitle)).toBeVisible();
    expect(stub.callsTo("GET /bff/api/v1/insights/flags")).toHaveLength(0);
  });

  it("the principal saves thresholds with If-Match; the AP rule cannot be switched off", async () => {
    signedIn(PRINCIPAL);
    stub.routes["GET /bff/api/v1/insights/flags"] = () => page([]);
    stub.routes["PUT /bff/api/v1/insights/settings"] = () =>
      Response.json({ ...settings(), version: 1 });
    renderWithIntl(<FlagsScreen />);
    const input = await screen.findByLabelText(
      `Threshold for ${en.insights.rules.names.attendance_rate} (60 to 90)`,
    );
    await userEvent.clear(input);
    await userEvent.type(input, "80");
    // Only one on/off switch: attendance_rate (attendance_streak is always on). It is named
    // after its rule, not just "On" (several switches would otherwise sound the same).
    expect(screen.getAllByRole("checkbox")).toHaveLength(1);
    expect(
      screen.getByRole("checkbox", {
        name: `Use the rule ${en.insights.rules.names.attendance_rate}`,
      }),
    ).toBeChecked();
    await userEvent.click(screen.getByRole("button", { name: en.insights.rules.save }));
    await waitFor(() => expect(stub.callsTo("PUT /bff/api/v1/insights/settings")).toHaveLength(1));
    const call = stub.callsTo("PUT /bff/api/v1/insights/settings")[0];
    expect(call?.headers.get("If-Match")).toBe('W/"0"');
    expect(JSON.parse(call?.body ?? "{}")).toEqual({
      rules: {
        attendance_streak: { threshold: 3 },
        attendance_rate: { threshold: 80, enabled: true },
      },
    });
    expect(await screen.findByText(en.insights.rules.saved)).toBeVisible();
  });
});

describe("flag detail and intervention log (US-1706; FR-EW-007, FR-EW-014)", () => {
  it("logs an action with an idempotency key and closes with If-Match", async () => {
    signedIn(TEACHER);
    stub.routes[`GET /bff/api/v1/insights/flags/${FLAG}`] = () => Response.json(detail());
    stub.routes[`POST /bff/api/v1/insights/flags/${FLAG}/actions`] = () =>
      Response.json(detail({ status: "in_progress" }), { status: 201 });
    stub.routes[`POST /bff/api/v1/insights/flags/${FLAG}/close`] = () =>
      Response.json(detail({ status: "closed" }));
    renderWithIntl(<FlagDetailScreen flagId={FLAG} />);
    expect(await screen.findByText(en.insights.log.kind.raised)).toBeVisible();
    // Reassign and erase are for the principal only.
    expect(screen.queryByRole("button", { name: en.insights.assign.open })).toBeNull();
    expect(screen.queryByRole("button", { name: en.insights.erase.open })).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: en.insights.action.add }));
    const dialog = await screen.findByRole("dialog", { name: en.insights.action.title });
    await userEvent.selectOptions(
      within(dialog).getByLabelText(en.insights.action.kind),
      "called_parent",
    );
    await userEvent.type(within(dialog).getByLabelText(en.insights.action.note), "Synthetic call.");
    await userEvent.click(within(dialog).getByRole("button", { name: en.insights.action.save }));
    await waitFor(() =>
      expect(stub.callsTo(`POST /bff/api/v1/insights/flags/${FLAG}/actions`)).toHaveLength(1),
    );
    const action = stub.callsTo(`POST /bff/api/v1/insights/flags/${FLAG}/actions`)[0];
    expect(action?.headers.get("Idempotency-Key")).toBeTruthy();
    expect(JSON.parse(action?.body ?? "{}")).toEqual({
      kind: "called_parent",
      acted_on: todayIst(),
      note: "Synthetic call.",
    });

    await userEvent.click(screen.getByRole("button", { name: en.insights.close.open }));
    const close = await screen.findByRole("dialog", { name: en.insights.close.title });
    await userEvent.selectOptions(
      within(close).getByLabelText(en.insights.close.reasonLabel),
      "support_in_place",
    );
    await userEvent.click(within(close).getByRole("button", { name: en.insights.close.save }));
    await waitFor(() =>
      expect(stub.callsTo(`POST /bff/api/v1/insights/flags/${FLAG}/close`)).toHaveLength(1),
    );
    const closed = stub.callsTo(`POST /bff/api/v1/insights/flags/${FLAG}/close`)[0];
    expect(closed?.headers.get("If-Match")).toBe('W/"4"');
    expect(JSON.parse(closed?.body ?? "{}")).toEqual({ reason: "support_in_place" });
  });

  it("refuses a 12-digit number in an action note before sending it", async () => {
    signedIn(TEACHER);
    stub.routes[`GET /bff/api/v1/insights/flags/${FLAG}`] = () => Response.json(detail());
    renderWithIntl(<FlagDetailScreen flagId={FLAG} />);
    await userEvent.click(await screen.findByRole("button", { name: en.insights.action.add }));
    const dialog = await screen.findByRole("dialog", { name: en.insights.action.title });
    await userEvent.selectOptions(within(dialog).getByLabelText(en.insights.action.kind), "other");
    await userEvent.type(within(dialog).getByLabelText(en.insights.action.note), "2345 6789 0123");
    await userEvent.click(within(dialog).getByRole("button", { name: en.insights.action.save }));
    expect(await within(dialog).findByText(en.validation.noAadhaar)).toBeVisible();
    expect(stub.callsTo(`POST /bff/api/v1/insights/flags/${FLAG}/actions`)).toHaveLength(0);
  });

  it("the principal gives the flag to an eligible member with If-Match", async () => {
    signedIn(PRINCIPAL);
    stub.routes[`GET /bff/api/v1/insights/flags/${FLAG}`] = () => Response.json(detail());
    stub.routes[`GET /bff/api/v1/insights/flags/${FLAG}/owners`] = () =>
      Response.json([
        { membership_id: OWNER, display_name: "Synthetic Teacher", roles: ["class_teacher"] },
        { membership_id: NEW_OWNER, display_name: "Synthetic Principal", roles: ["principal"] },
      ]);
    stub.routes[`POST /bff/api/v1/insights/flags/${FLAG}/assign`] = () =>
      Response.json(
        detail({ owner: { membership_id: NEW_OWNER, display_name: "Synthetic Principal" } }),
      );
    renderWithIntl(<FlagDetailScreen flagId={FLAG} />);
    await userEvent.click(await screen.findByRole("button", { name: en.insights.assign.open }));
    const dialog = await screen.findByRole("dialog", { name: en.insights.assign.title });
    await userEvent.selectOptions(
      within(dialog).getByLabelText(en.insights.assign.owner),
      NEW_OWNER,
    );
    await userEvent.click(within(dialog).getByRole("button", { name: en.insights.assign.save }));
    await waitFor(() =>
      expect(stub.callsTo(`POST /bff/api/v1/insights/flags/${FLAG}/assign`)).toHaveLength(1),
    );
    const call = stub.callsTo(`POST /bff/api/v1/insights/flags/${FLAG}/assign`)[0];
    expect(call?.headers.get("If-Match")).toBe('W/"4"');
    expect(JSON.parse(call?.body ?? "{}")).toEqual({ owner_membership_id: NEW_OWNER });
  });

  it("explains a closed flag in plain words", async () => {
    signedIn(TEACHER);
    stub.routes[`GET /bff/api/v1/insights/flags/${FLAG}`] = () => Response.json(detail());
    stub.routes[`POST /bff/api/v1/insights/flags/${FLAG}/close`] = () =>
      problem(409, "flag_closed");
    renderWithIntl(<FlagDetailScreen flagId={FLAG} />);
    await userEvent.click(await screen.findByRole("button", { name: en.insights.close.open }));
    const dialog = await screen.findByRole("dialog", { name: en.insights.close.title });
    await userEvent.selectOptions(
      within(dialog).getByLabelText(en.insights.close.reasonLabel),
      "improved",
    );
    await userEvent.click(within(dialog).getByRole("button", { name: en.insights.close.save }));
    expect(await within(dialog).findByText(en.insights.errors.flag_closed.title)).toBeVisible();
  });
});

describe("attendance (US-1701, US-1702; FR-ATT-001..004)", () => {
  it("marks a section's day: everyone starts present, one absent is saved", async () => {
    signedIn(["attendance.read", "attendance.record"]);
    stub.routes[`GET /bff/api/v1/sections/${SECTION}/attendance`] = () => Response.json(day());
    stub.routes[`POST /bff/api/v1/sections/${SECTION}/attendance`] = () =>
      Response.json({ written: 2, unchanged: 0, dates: [todayIst()] });
    renderWithIntl(<AttendanceScreen />);
    const picker = await screen.findByLabelText(en.attendance.section);
    await waitFor(() => expect(within(picker).getAllByRole("option").length).toBeGreaterThan(1));
    await userEvent.selectOptions(picker, SECTION);
    expect(await screen.findByText(en.attendance.notMarkedYet)).toBeVisible();
    const anjali = screen.getByRole("group", { name: "Attendance of Synthetica Anjali" });
    await userEvent.click(within(anjali).getByRole("radio", { name: en.attendance.status.absent }));
    await userEvent.click(screen.getByRole("button", { name: en.attendance.save }));
    await waitFor(() =>
      expect(stub.callsTo(`POST /bff/api/v1/sections/${SECTION}/attendance`)).toHaveLength(1),
    );
    const call = stub.callsTo(`POST /bff/api/v1/sections/${SECTION}/attendance`)[0];
    expect(JSON.parse(call?.body ?? "{}")).toEqual({
      source: "mark",
      entries: [
        { student_id: STUDENT_ID, on_date: todayIst(), status: "present" },
        { student_id: OTHER_STUDENT, on_date: todayIst(), status: "absent" },
      ],
    });
    expect(await screen.findByText("Saved 2 entries.")).toBeVisible();
    expect(
      stub
        .callsTo(`GET /bff/api/v1/sections/${SECTION}/attendance`)[0]
        ?.url.searchParams.get("date"),
    ).toBe(todayIst());
  });

  it("imports a sheet: problems name the cell, a clean preview saves as an import", async () => {
    signedIn(["attendance.read", "attendance.record"]);
    stub.routes[`GET /bff/api/v1/sections/${SECTION}/attendance`] = () => Response.json(day());
    let previews = 0;
    stub.routes[`POST /bff/api/v1/sections/${SECTION}/attendance/sheet`] = () => {
      previews += 1;
      return Response.json(
        previews === 1
          ? {
              entries: [],
              dates: ["2026-09-01"],
              students: 1,
              issues: [{ row: 3, column: 3, code: "bad_code" }],
              issue_count: 1,
            }
          : {
              entries: [{ student_id: STUDENT_ID, on_date: "2026-09-01", status: "absent" }],
              dates: ["2026-09-01"],
              students: 1,
              issues: [],
              issue_count: 0,
            },
      );
    };
    stub.routes[`POST /bff/api/v1/sections/${SECTION}/attendance`] = () =>
      Response.json({ written: 1, unchanged: 0, dates: ["2026-09-01"] });
    renderWithIntl(<AttendanceScreen />);
    const picker = await screen.findByLabelText(en.attendance.section);
    await waitFor(() => expect(within(picker).getAllByRole("option").length).toBeGreaterThan(1));
    await userEvent.selectOptions(picker, SECTION);
    const file = new File(["Admission no.,01/09/2026\nSYN-0001,X\n"], "september.csv", {
      type: "text/csv",
    });
    await userEvent.upload(await screen.findByLabelText(en.insights.sheet.file), file);
    await userEvent.click(screen.getByRole("button", { name: en.insights.sheet.read }));
    expect(await screen.findByText("C3")).toBeVisible();
    expect(screen.getByText(en.insights.sheet.codes.bad_code)).toBeVisible();
    expect(screen.getByText(en.insights.sheet.fixFirst)).toBeVisible();
    expect(stub.callsTo(`POST /bff/api/v1/sections/${SECTION}/attendance`)).toHaveLength(0);

    await userEvent.upload(screen.getByLabelText(en.insights.sheet.file), file);
    await userEvent.click(screen.getByRole("button", { name: en.insights.sheet.read }));
    await userEvent.click(await screen.findByRole("button", { name: "Save 1 entry" }));
    await waitFor(() =>
      expect(stub.callsTo(`POST /bff/api/v1/sections/${SECTION}/attendance`)).toHaveLength(1),
    );
    expect(
      JSON.parse(stub.callsTo(`POST /bff/api/v1/sections/${SECTION}/attendance`)[0]?.body ?? "{}"),
    ).toEqual({
      source: "import",
      entries: [{ student_id: STUDENT_ID, on_date: "2026-09-01", status: "absent" }],
    });
    const preview = stub.callsTo(`POST /bff/api/v1/sections/${SECTION}/attendance/sheet`)[0];
    expect(JSON.parse(preview?.body ?? "{}")).toEqual({ document_id: uploads.next });
  });

  it("shows the month register with register codes, printable on A4 landscape", async () => {
    signedIn(["attendance.read"]);
    stub.routes[`GET /bff/api/v1/sections/${SECTION}/attendance`] = () => Response.json(day());
    stub.routes[`GET /bff/api/v1/sections/${SECTION}/attendance/month`] = () =>
      Response.json({
        section: section(),
        month: todayIst().slice(0, 7),
        school_days: ["2026-09-01", "2026-09-02"],
        students: [
          {
            student: day().students[0]?.student,
            days: { "2026-09-01": "present", "2026-09-02": "absent" },
            counts: { present: 1, absent: 1 },
          },
        ],
      });
    const { container } = renderWithIntl(<AttendanceScreen />);
    const picker = await screen.findByLabelText(en.attendance.section);
    await waitFor(() => expect(within(picker).getAllByRole("option").length).toBeGreaterThan(1));
    await userEvent.selectOptions(picker, SECTION);
    // Read only: no save button and the import is not offered.
    expect(await screen.findByText(en.attendance.notMarkedYet)).toBeVisible();
    expect(screen.queryByRole("button", { name: en.attendance.save })).toBeNull();
    expect(screen.queryByLabelText(en.insights.sheet.file)).toBeNull();
    await userEvent.click(screen.getByRole("radio", { name: en.attendance.view.month }));
    const table = await screen.findByRole("table");
    expect(within(table).getByText("A")).toBeVisible();
    // The code is read out in words too (header "Absent" + the cell).
    expect(within(table).getAllByText(en.attendance.status.absent)).toHaveLength(2);
    expect(container.querySelector('[data-print="landscape"]')).not.toBeNull();
    expect(screen.getByRole("button", { name: en.attendance.print })).toBeVisible();
    expect(
      stub
        .callsTo(`GET /bff/api/v1/sections/${SECTION}/attendance/month`)[0]
        ?.url.searchParams.get("month"),
    ).toBe(todayIst().slice(0, 7));
  });

  it("refuses a file that is not a sheet before uploading", async () => {
    signedIn(["attendance.read", "attendance.record"]);
    stub.routes[`GET /bff/api/v1/sections/${SECTION}/attendance`] = () => Response.json(day());
    renderWithIntl(<AttendanceScreen />);
    const picker = await screen.findByLabelText(en.attendance.section);
    await waitFor(() => expect(within(picker).getAllByRole("option").length).toBeGreaterThan(1));
    await userEvent.selectOptions(picker, SECTION);
    await userEvent.upload(
      await screen.findByLabelText(en.insights.sheet.file),
      new File(["x"], "photo.jpg", { type: "image/jpeg" }),
      { applyAccept: false },
    );
    await userEvent.click(screen.getByRole("button", { name: en.insights.sheet.read }));
    expect(await screen.findByText(en.insights.sheet.fileProblem.fileType)).toBeVisible();
  });
});

describe("marks (US-1703; FR-MRK-001..003)", () => {
  it("enters one subject's marks with AB for absent", async () => {
    const EXAM = "0192f3a4-0000-7000-8000-00000000ea01";
    const exam = {
      id: EXAM,
      academic_year_id: YEAR,
      name: "Formative assessment 1",
      held_on: "2026-08-10",
      version: 1,
    };
    signedIn(["marks.read", "marks.record"]);
    stub.routes["GET /bff/api/v1/exams"] = () => Response.json([exam]);
    stub.routes[`GET /bff/api/v1/sections/${SECTION}/exams/${EXAM}/marks`] = () =>
      Response.json({
        section: section(),
        exam,
        subjects: [],
        students: day().students.map((row) => ({ student: row.student, marks: [], percent: null })),
      });
    stub.routes[`POST /bff/api/v1/sections/${SECTION}/exams/${EXAM}/marks`] = () =>
      Response.json({ written: 2, unchanged: 0, dates: [] });
    renderWithIntl(<MarksScreen />);
    const picker = await screen.findByLabelText(en.marks.section);
    await waitFor(() => expect(within(picker).getAllByRole("option").length).toBeGreaterThan(1));
    await userEvent.selectOptions(picker, SECTION);
    const exams = screen.getByLabelText(en.marks.exam);
    await waitFor(() => expect(within(exams).getAllByRole("option").length).toBeGreaterThan(1));
    await userEvent.selectOptions(exams, EXAM);
    // Only exam.manage holders add exams.
    expect(screen.queryByRole("button", { name: en.marks.newExam.submit })).toBeNull();
    await userEvent.type(await screen.findByLabelText(en.marks.entry.subject), "Telugu");
    await userEvent.type(screen.getByLabelText("Marks of Synthetica Ravi"), "42.5");
    await userEvent.type(screen.getByLabelText("Marks of Synthetica Anjali"), "ab");
    await userEvent.click(screen.getByRole("button", { name: en.marks.entry.save }));
    await waitFor(() =>
      expect(stub.callsTo(`POST /bff/api/v1/sections/${SECTION}/exams/${EXAM}/marks`)).toHaveLength(
        1,
      ),
    );
    const call = stub.callsTo(`POST /bff/api/v1/sections/${SECTION}/exams/${EXAM}/marks`)[0];
    expect(JSON.parse(call?.body ?? "{}")).toEqual({
      source: "mark",
      entries: [
        {
          student_id: STUDENT_ID,
          subject: "Telugu",
          max_marks: "100",
          marks: "42.5",
          absent: false,
        },
        {
          student_id: OTHER_STUDENT,
          subject: "Telugu",
          max_marks: "100",
          marks: null,
          absent: true,
        },
      ],
    });
  });

  it("refuses marks above the maximum before sending", async () => {
    const EXAM = "0192f3a4-0000-7000-8000-00000000ea01";
    const exam = {
      id: EXAM,
      academic_year_id: YEAR,
      name: "Formative assessment 1",
      held_on: "2026-08-10",
      version: 1,
    };
    signedIn(["marks.read", "marks.record"]);
    stub.routes["GET /bff/api/v1/exams"] = () => Response.json([exam]);
    stub.routes[`GET /bff/api/v1/sections/${SECTION}/exams/${EXAM}/marks`] = () =>
      Response.json({
        section: section(),
        exam,
        subjects: [],
        students: day().students.map((row) => ({ student: row.student, marks: [], percent: null })),
      });
    renderWithIntl(<MarksScreen />);
    const picker = await screen.findByLabelText(en.marks.section);
    await waitFor(() => expect(within(picker).getAllByRole("option").length).toBeGreaterThan(1));
    await userEvent.selectOptions(picker, SECTION);
    const exams = screen.getByLabelText(en.marks.exam);
    await waitFor(() => expect(within(exams).getAllByRole("option").length).toBeGreaterThan(1));
    await userEvent.selectOptions(exams, EXAM);
    await userEvent.type(await screen.findByLabelText(en.marks.entry.subject), "Telugu");
    await userEvent.type(screen.getByLabelText("Marks of Synthetica Ravi"), "142");
    await userEvent.click(screen.getByRole("button", { name: en.marks.entry.save }));
    expect(await screen.findByText(en.marks.entry.problem.marksInvalid)).toBeVisible();
    expect(stub.callsTo(`POST /bff/api/v1/sections/${SECTION}/exams/${EXAM}/marks`)).toHaveLength(
      0,
    );
  });
});

describe("student timeline and notes (US-1704, US-1705, US-1707; FR-EW-010..013)", () => {
  it("shows indicators and the timeline newest first with the flag's log", async () => {
    signedIn(TEACHER);
    stub.routes[`GET /bff/api/v1/students/${STUDENT_ID}/timeline`] = () =>
      Response.json(timeline());
    renderWithIntl(<StudentInsights studentId={STUDENT_ID} />);
    expect(await screen.findByText("85%")).toBeVisible();
    expect(screen.getByText("62.5%")).toBeVisible();
    expect(screen.getByText("-7.5 points from the previous exam")).toBeVisible();
    const list = screen.getByRole("list", { name: en.insights.timeline.title });
    const items = within(list).getAllByRole("listitem");
    expect(items[0]).toHaveTextContent("Follow-up flag: Attendance");
    expect(within(list).getByText("Synthetic note: seemed tired in class.")).toBeVisible();
    expect(within(list).getByText(en.insights.log.kind.raised)).toBeVisible();
    expect(within(list).getByText("Placed in IX-A")).toBeVisible();
    // Erasing a note is for the principal only.
    expect(screen.queryByRole("button", { name: en.insights.notes.erase })).toBeNull();
  });

  it("adds a behaviour note and refuses Aadhaar numbers", async () => {
    signedIn(TEACHER);
    stub.routes[`GET /bff/api/v1/students/${STUDENT_ID}/timeline`] = () =>
      Response.json(timeline());
    stub.routes[`POST /bff/api/v1/students/${STUDENT_ID}/behaviour-notes`] = () =>
      Response.json(timeline().items[1]?.note, { status: 201 });
    renderWithIntl(<StudentInsights studentId={STUDENT_ID} />);
    await userEvent.click(await screen.findByRole("button", { name: en.insights.notes.add }));
    const dialog = await screen.findByRole("dialog", { name: en.insights.notes.title });
    await userEvent.selectOptions(
      within(dialog).getByLabelText(en.insights.notes.categoryLabel),
      "positive",
    );
    const text = within(dialog).getByLabelText(en.insights.notes.text);
    await userEvent.type(text, "Helped a classmate 234567890123");
    await userEvent.click(within(dialog).getByRole("button", { name: en.insights.notes.save }));
    expect(await within(dialog).findByText(en.validation.noAadhaar)).toBeVisible();
    expect(stub.callsTo(`POST /bff/api/v1/students/${STUDENT_ID}/behaviour-notes`)).toHaveLength(0);

    await userEvent.clear(text);
    await userEvent.type(text, "Helped a classmate with maths.");
    await userEvent.click(within(dialog).getByRole("button", { name: en.insights.notes.save }));
    await waitFor(() =>
      expect(stub.callsTo(`POST /bff/api/v1/students/${STUDENT_ID}/behaviour-notes`)).toHaveLength(
        1,
      ),
    );
    const call = stub.callsTo(`POST /bff/api/v1/students/${STUDENT_ID}/behaviour-notes`)[0];
    expect(call?.headers.get("Idempotency-Key")).toBeTruthy();
    expect(JSON.parse(call?.body ?? "{}")).toEqual({
      category: "positive",
      noted_on: todayIst(),
      text: "Helped a classmate with maths.",
    });
  });

  it("outside the caller's scope says who can see the timeline", async () => {
    signedIn(TEACHER);
    stub.routes[`GET /bff/api/v1/students/${STUDENT_ID}/timeline`] = () =>
      problem(404, "not_found");
    renderWithIntl(<StudentInsights studentId={STUDENT_ID} />);
    expect(await screen.findByText(en.insights.timeline.notInScopeTitle)).toBeVisible();
  });
});

describe("helpers, messages and links", () => {
  it("names spreadsheet columns and spots Aadhaar-like numbers", () => {
    expect(columnLetter(1)).toBe("A");
    expect(columnLetter(26)).toBe("Z");
    expect(columnLetter(27)).toBe("AA");
    expect(looksLikeAadhaar("2345 6789 0123")).toBe(true);
    expect(looksLikeAadhaar("2345-6789-0123")).toBe(true);
    expect(looksLikeAadhaar("Roll 12, marks 45")).toBe(false);
    expect(
      noteSchema.safeParse({ category: "concern", noted_on: "2026-09-20", text: " " }).success,
    ).toBe(false);
  });

  it("offers the last twelve months for the register", () => {
    expect(recentMonths("2026-01-15", 3)).toEqual(["2026-01", "2025-12", "2025-11"]);
    expect(recentMonths("2026-09-29")).toHaveLength(12);
  });

  it("dates attendance in India time", () => {
    expect(todayIst(new Date("2026-09-28T19:00:00Z"))).toBe("2026-09-29");
    expect(todayIst(new Date("2026-09-28T18:00:00Z"))).toBe("2026-09-28");
  });

  it("every attendance, marks and insights message exists in Telugu", () => {
    const keys = (value: unknown, prefix = ""): string[] =>
      value && typeof value === "object"
        ? Object.entries(value).flatMap(([k, v]) => keys(v, `${prefix}${k}.`))
        : [prefix.slice(0, -1)];
    for (const ns of ["attendance", "marks", "insights"] as const) {
      expect(keys(messages.te[ns]).sort()).toEqual(keys(messages.en[ns]).sort());
    }
  });

  it("flag notifications open the flag", () => {
    expect(notificationHref({ resource_type: "insight_flag", resource_id: FLAG })).toBe(
      `/flags/${FLAG}`,
    );
  });
});
