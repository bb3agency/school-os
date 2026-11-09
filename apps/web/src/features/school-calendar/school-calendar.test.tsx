import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { installBffStub, page, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { intlErrors, messages, renderWithIntl } from "@/test/render";
import { me } from "@/test/school-fixtures";
import {
  addDays,
  shiftMonth,
  weekdayOf,
  type CalendarDay,
  type CalendarMonth,
  type SchoolCalendar,
} from "./data";
import { SchoolCalendarScreen } from "./SchoolCalendarScreen";

/**
 * School calendar screen (FT-06; FR-CAL-001, FR-CAL-009, FR-CAL-013, FR-CAL-020): the month grid
 * with keyboard moves, the weekly pattern, entries and unverified suggestions. Synthetic data.
 */

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => "/settings/structure/calendar",
    useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
    useSearchParams: () => new URLSearchParams(),
    useParams: () => ({ locale: "en" }),
  };
});

const t = messages.en.schoolCalendar;
const YEAR = "0192f3a4-0000-7000-8000-000000000a01";
const CLASS = "0192f3a4-0000-7000-8000-000000000a02";
const BASE = `/bff/api/v1/academic-years/${YEAR}/calendar`;

function calendar(overrides: Partial<SchoolCalendar> = {}): SchoolCalendar {
  return {
    academic_year_id: YEAR,
    starts_on: "2026-06-01",
    ends_on: "2027-03-31",
    set_up: true,
    weekly_off: [6],
    saturdays_off: [2],
    entries: [
      {
        id: "0192f3a4-0000-7000-8000-000000000a03",
        kind: "vacation",
        name: "Synthetic Dasara vacation",
        starts_on: "2026-10-11",
        ends_on: "2026-10-21",
        class_id: null,
        suggestion: null,
        days: 11,
      },
    ],
    working_days: 231,
    version: 3,
    ...overrides,
  };
}

function month(value: string): CalendarMonth {
  const days: CalendarDay[] = [];
  let day = `${value}-01`;
  while (day.startsWith(value)) {
    const sunday = weekdayOf(day) === 6;
    days.push({ on_date: day, working: !sunday, kind: sunday ? "weekly_off" : "working" });
    day = addDays(day, 1);
  }
  return {
    academic_year_id: YEAR,
    month: value,
    class_id: null,
    set_up: true,
    days,
    working_days: days.filter((d) => d.working).length,
  };
}

let stub: BffStub;

function signedIn(permissions: string[]) {
  stub.routes["GET /bff/api/v1/me"] = () => Response.json(me(permissions));
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"], now: new Date("2026-11-09T06:00:00Z") });
  stub = installBffStub("staff");
  stub.routes["GET /bff/api/v1/academic-years"] = () =>
    page([
      {
        id: YEAR,
        label: "2026-27",
        starts_on: "2026-06-01",
        ends_on: "2027-03-31",
        is_current: true,
        archived_at: null,
        in_use: true,
        version: 1,
      },
    ]);
  stub.routes["GET /bff/api/v1/classes"] = () =>
    page([
      {
        id: CLASS,
        code: "IX",
        display_en: "Class IX",
        display_te: "",
        sort_order: 120,
        archived_at: null,
        in_use: true,
        version: 1,
      },
    ]);
  stub.routes[`GET ${BASE}`] = () => Response.json(calendar());
  stub.routes[`GET ${BASE}/month`] = (_request, url) =>
    Response.json(month(url.searchParams.get("month") ?? "2026-11"));
  stub.routes[`GET ${BASE}/suggestions`] = () =>
    Response.json({
      verified: false,
      source: "Synthetic almanac",
      items: [
        {
          code: "republic_day_2027",
          group: "national",
          group_label: "National holidays",
          name: "Republic Day",
          kind: "holiday",
          starts_on: "2027-01-26",
          ends_on: "2027-01-26",
        },
      ],
    });
});
afterEach(() => {
  uninstallBffStub();
  vi.useRealTimers();
  expect(intlErrors).toEqual([]);
});

describe("school calendar helpers", () => {
  it("moves months and days without time-zone drift", () => {
    expect(shiftMonth("2026-12", 1)).toBe("2027-01");
    expect(shiftMonth("2026-01", -1)).toBe("2025-12");
    expect(weekdayOf("2026-11-09")).toBe(0);
    expect(weekdayOf("2026-11-08")).toBe(6);
    expect(addDays("2026-10-31", 1)).toBe("2026-11-01");
  });
});

describe("school calendar screen (FT-06)", () => {
  it("shows the month grid and entries; readers get no changes", async () => {
    signedIn(["student.read_basic"]);
    renderWithIntl(<SchoolCalendarScreen />);
    expect(await screen.findByRole("grid", { name: t.gridLabel })).toBeVisible();
    expect(screen.getByText("Synthetic Dasara vacation")).toBeVisible();
    expect(screen.getByText(/231 working days/)).toBeVisible();
    expect(screen.queryByRole("button", { name: t.pattern.change })).toBeNull();
    expect(screen.queryByRole("button", { name: t.add.open })).toBeNull();
    expect(screen.queryByText(t.suggestions.title)).toBeNull();
  });

  it("moves through days with the arrow keys and changes month with Page Down", async () => {
    signedIn(["student.read_basic"]);
    const user = userEvent.setup({ advanceTimers: () => undefined });
    renderWithIntl(<SchoolCalendarScreen />);
    const today = await screen.findByRole("button", { name: /09\/11\/2026/ });
    expect(today).toHaveAttribute("tabindex", "0");
    today.focus();
    await user.keyboard("{ArrowRight}");
    await waitFor(() => expect(screen.getByRole("button", { name: /10\/11\/2026/ })).toHaveFocus());
    await user.keyboard("{ArrowLeft}{ArrowLeft}");
    const sunday = screen.getByRole("button", { name: /08\/11\/2026/ });
    await waitFor(() => expect(sunday).toHaveFocus());
    expect(sunday).toHaveAccessibleName(/Weekly off/);
    await user.keyboard("{PageDown}");
    await waitFor(() => expect(screen.getByRole("button", { name: /08\/12\/2026/ })).toHaveFocus());
  });

  it("lets a manager save the weekly pattern with If-Match", async () => {
    signedIn(["student.read_basic", "tenant.structure.manage"]);
    stub.routes[`PUT ${BASE}`] = () =>
      Response.json(calendar({ saturdays_off: [2, 4], version: 4 }));
    const user = userEvent.setup({ advanceTimers: () => undefined });
    renderWithIntl(<SchoolCalendarScreen />);
    await user.click(await screen.findByRole("button", { name: t.pattern.change }));
    await user.click(screen.getByRole("checkbox", { name: t.saturday["4"] }));
    await user.click(screen.getByRole("button", { name: t.pattern.save }));
    await waitFor(() => expect(stub.callsTo(`PUT ${BASE}`)).toHaveLength(1));
    const call = stub.callsTo(`PUT ${BASE}`)[0];
    expect(call?.headers.get("If-Match")).toBe('W/"3"');
    expect(JSON.parse(call?.body ?? "{}")).toEqual({ weekly_off: [6], saturdays_off: [2, 4] });
  });

  it("offers unverified suggestions and adds one with its code", async () => {
    signedIn(["student.read_basic", "tenant.structure.manage"]);
    stub.routes[`POST ${BASE}/entries`] = () =>
      Response.json(
        {
          id: "0192f3a4-0000-7000-8000-000000000a04",
          kind: "holiday",
          name: "Republic Day",
          starts_on: "2027-01-26",
          ends_on: "2027-01-26",
          class_id: null,
          suggestion: "republic_day_2027",
          days: 1,
          attendance_marks: 0,
        },
        { status: 201 },
      );
    const user = userEvent.setup({ advanceTimers: () => undefined });
    renderWithIntl(<SchoolCalendarScreen />);
    expect(await screen.findByText(t.suggestions.unverifiedTitle)).toBeVisible();
    await user.click(screen.getByRole("button", { name: new RegExp(t.suggestions.check) }));
    await user.click(screen.getByRole("button", { name: t.add.save }));
    await waitFor(() => expect(stub.callsTo(`POST ${BASE}/entries`)).toHaveLength(1));
    const call = stub.callsTo(`POST ${BASE}/entries`)[0];
    expect(call?.headers.get("Idempotency-Key")).toBeTruthy();
    expect(JSON.parse(call?.body ?? "{}")).toMatchObject({
      kind: "holiday",
      name: "Republic Day",
      starts_on: "2027-01-26",
      ends_on: "2027-01-26",
      class_id: null,
      suggestion: "republic_day_2027",
    });
  });
});
