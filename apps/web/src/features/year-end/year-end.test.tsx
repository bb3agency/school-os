import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { installBffStub, page, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { intlErrors, renderWithIntl } from "@/test/render";
import { me } from "@/test/school-fixtures";
import {
  changedEntries,
  choiceProblem,
  initialChoices,
  type HeldBack,
  type YearResults,
  type YearResultStudent,
} from "./data";
import { HeldBackSuggestion } from "./HeldBackSuggestion";
import { suggestionsOf } from "./TcSuggestion";
import { YearEndScreen } from "./YearEndScreen";

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => "/year-end",
    useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
    useSearchParams: () => new URLSearchParams(),
    useParams: () => ({ locale: "en" }),
  };
});

// Synthetic fixtures only (invented names).
const id = (suffix: string) => `0192f3a4-0000-7000-8000-${suffix.padStart(12, "0")}`;
const STAMP = {
  version: 1,
  created_at: "2026-06-01T04:30:00Z",
  updated_at: "2026-06-01T04:30:00Z",
};
const YEAR = {
  id: id("y1"),
  label: "2026-27",
  starts_on: "2026-06-01",
  ends_on: "2027-03-31",
  is_current: true,
  archived_at: null,
  ...STAMP,
};
const CLASS_IX = {
  id: id("c9"),
  code: "IX",
  display_en: "Class IX",
  display_te: "9వ తరగతి",
  sort_order: 120,
  archived_at: null,
  ...STAMP,
};
const SECTION = {
  id: id("s9"),
  academic_year_id: YEAR.id,
  class_id: CLASS_IX.id,
  name: "A",
  class_teacher_membership_id: null,
  archived_at: null,
  ...STAMP,
};

const row = (
  suffix: string,
  name: string,
  proposed: YearResultStudent["proposal"]["result"],
  confirmed: YearResultStudent["confirmed"] = null,
): YearResultStudent => ({
  student: {
    student_id: id(suffix),
    full_name: name,
    admission_no: `SYN-${suffix}`,
    roll_no: suffix,
  },
  proposal: {
    result: proposed,
    percent: proposed === "passed" ? 80 : 20,
    exams_counted: 1,
    days_attended: 3,
    working_days: 4,
  },
  confirmed,
  warnings: [],
});

const ASHA = row("1", "Asha Synthetic", "passed");
const BALA = row("2", "Bala Synthetic", "detained");
const RESULTS: YearResults = {
  section: { id: SECTION.id, class_id: CLASS_IX.id, academic_year_id: YEAR.id, label: "IX-A" },
  academic_year_label: "2026-27",
  no_detention_class: false,
  rules: {
    rule_version: 1,
    pass_percent: 35,
    pass_percent_verified: false,
    no_detention_class_codes: ["I", "VIII"],
    no_detention_verified: false,
  },
  counts: {
    students: 2,
    confirmed: 0,
    unconfirmed: 2,
    passed: 0,
    promoted: 0,
    detained: 0,
    not_appeared: 0,
  },
  students: [ASHA, BALA],
};

let stub: BffStub;

beforeEach(() => {
  stub = installBffStub("staff");
});
afterEach(() => {
  uninstallBffStub();
  expect(intlErrors).toEqual([]);
});

function school(permissions: string[]) {
  stub.routes["GET /bff/api/v1/me"] = () => Response.json(me(permissions));
  stub.routes["GET /bff/api/v1/academic-years"] = () => page([YEAR]);
  stub.routes["GET /bff/api/v1/classes"] = () => page([CLASS_IX]);
  stub.routes["GET /bff/api/v1/sections"] = () => page([SECTION]);
  stub.routes[`GET /bff/api/v1/sections/${SECTION.id}/year-results`] = () => Response.json(RESULTS);
}

describe("year-end form helpers (FR-YRE-004, FR-YRE-005)", () => {
  it("starts from the confirmed result, else the proposal", () => {
    const confirmed = row("3", "Chitra Synthetic", "passed", {
      result: "promoted",
      proposed_result: "passed",
      reason: null,
      percent: 80,
      days_attended: 3,
      working_days: 4,
      rule_version: 1,
      confirmed_by_name: "Synthetic Principal",
      confirmed_at: "2027-03-30T05:00:00Z",
      version: 2,
    });
    const choices = initialChoices([ASHA, confirmed]);
    expect(choices[ASHA.student.student_id]?.result).toBe("passed");
    expect(choices[confirmed.student.student_id]?.result).toBe("promoted");
    // Unchanged confirmed rows are not sent; new ones carry version 0.
    expect(changedEntries([ASHA, confirmed], choices)).toEqual([
      { student_id: ASHA.student.student_id, result: "passed", reason: null, expected_version: 0 },
    ]);
  });

  it("asks for a reason to detain and refuses Aadhaar-like numbers", () => {
    expect(choiceProblem({ result: "detained", reason: " " })).toBe("reason_required");
    expect(choiceProblem({ result: "detained", reason: "Ref 2341 2341 2346" })).toBe("aadhaar");
    expect(choiceProblem({ result: "detained", reason: "Long absence" })).toBeNull();
    expect(choiceProblem({ result: "passed", reason: "" })).toBeNull();
  });

  it("reads TC suggestions from the certificate preview (FR-YRE-014)", () => {
    expect(suggestionsOf(null)).toEqual([]);
    expect(suggestionsOf({})).toEqual([]);
    expect(
      suggestionsOf({
        input_suggestions: [{ key: "working_days", value: "180", source: "attendance_register" }],
      }),
    ).toEqual([{ key: "working_days", value: "180", source: "attendance_register" }]);
  });
});

describe("year-end screen (FR-YRE-019)", () => {
  it("without year_result.read it explains and asks for no results", async () => {
    school([]);
    renderWithIntl(<YearEndScreen />);
    expect(await screen.findByText("You cannot see annual results")).toBeInTheDocument();
    expect(stub.callsTo(`GET /bff/api/v1/sections/${SECTION.id}/year-results`)).toHaveLength(0);
  });

  it("the principal reviews proposals, gives a reason and confirms", async () => {
    school(["year_result.read", "year_result.confirm"]);
    stub.routes[`POST /bff/api/v1/sections/${SECTION.id}/year-results`] = () =>
      Response.json({ written: 2, unchanged: 0, warnings: [] });
    const user = userEvent.setup();
    renderWithIntl(<YearEndScreen />);
    const picker = await screen.findByRole("combobox", { name: "Section" });
    await waitFor(() => expect(picker).not.toBeDisabled());
    await user.selectOptions(picker, SECTION.id);
    expect(await screen.findByText("Asha Synthetic")).toBeInTheDocument();
    expect(screen.getAllByText(/Not yet confirmed for your school/)).not.toHaveLength(0);
    const save = screen.getByRole("button", { name: /Confirm/ });
    // Bala is proposed as detained: the reason is required before saving.
    expect(save).toBeDisabled();
    await user.type(
      screen.getByRole("textbox", { name: "Reason for detaining Bala Synthetic" }),
      "Synthetic long absence",
    );
    await waitFor(() => expect(save).toBeEnabled());
    await user.click(save);
    expect(await screen.findByText("2 saved, 0 unchanged.")).toBeInTheDocument();
    const [call] = stub.callsTo(`POST /bff/api/v1/sections/${SECTION.id}/year-results`);
    const body = JSON.parse(call?.body ?? "{}") as { entries: Array<Record<string, unknown>> };
    expect(body.entries).toEqual([
      { student_id: ASHA.student.student_id, result: "passed", reason: null, expected_version: 0 },
      {
        student_id: BALA.student.student_id,
        result: "detained",
        reason: "Synthetic long absence",
        expected_version: 0,
      },
    ]);
  });

  it("a reader without confirm sees the results read-only", async () => {
    school(["year_result.read"]);
    const user = userEvent.setup();
    renderWithIntl(<YearEndScreen />);
    const picker = await screen.findByRole("combobox", { name: "Section" });
    await waitFor(() => expect(picker).not.toBeDisabled());
    await user.selectOptions(picker, SECTION.id);
    expect(await screen.findByText("Only the principal can confirm results.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Confirm/ })).toBeNull();
  });
});

describe("held-back suggestion on the promotion screen (FR-YRE-008)", () => {
  it("adds the confirmed detained students only when asked", async () => {
    const held: HeldBack = {
      academic_year_id: YEAR.id,
      held_back_student_ids: [id("2"), id("4")],
      no_detention_student_ids: [id("4")],
      confirmed: 2,
      unconfirmed: 1,
      no_detention_verified: false,
    };
    stub.routes["GET /bff/api/v1/me"] = () => Response.json(me(["year_result.read"]));
    stub.routes[`GET /bff/api/v1/academic-years/${YEAR.id}/year-results/held-back`] = () =>
      Response.json(held);
    const onAdd = vi.fn();
    const user = userEvent.setup();
    renderWithIntl(<HeldBackSuggestion yearId={YEAR.id} held={new Set([id("2")])} onAdd={onAdd} />);
    expect(await screen.findByText("2 students are confirmed as detained.")).toBeInTheDocument();
    expect(onAdd).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Add them to the held-back list" }));
    expect(onAdd).toHaveBeenCalledWith([id("4")]);
  });
});
