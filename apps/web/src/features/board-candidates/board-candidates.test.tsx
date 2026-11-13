import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { installBffStub, page, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { me } from "@/test/records-fixtures";
import { messages, renderWithIntl } from "@/test/render";
import { BoardCandidatesScreen } from "./BoardCandidatesScreen";
import { draftOf, entryFor, fillBlanks, gridSchema, type BoardScheme } from "./data";

/** Board candidates (FO-10, FR-STU-026..029). Synthetic data only. */

const bm = messages.en.boardCandidates;
const SECTION = "0192f3a4-0000-7000-8000-00000000f010";
const STUDENT = "0192f3a4-0000-7000-8000-00000000f011";
const OTHER = "0192f3a4-0000-7000-8000-00000000f012";

const SCHEME: BoardScheme = {
  key: "cbse-secondary",
  version: 1,
  label_en: "CBSE secondary (Classes IX and X)",
  board: "CBSE",
  classes: ["IX", "X"],
  verified: false,
  source: ["https://example.org/scheme"],
  source_note: "Secondary reading.",
  medium_required: false,
  medium_options: ["english", "hindi"],
  languages: [
    {
      slot: "first_language",
      required: true,
      options: [
        { code: "184", label: "English Language and Literature" },
        { code: "089", label: "Telugu" },
      ],
    },
    {
      slot: "second_language",
      required: true,
      options: [
        { code: "184", label: "English Language and Literature" },
        { code: "089", label: "Telugu" },
      ],
    },
  ],
  subjects: [
    {
      key: "mathematics",
      label_en: "Mathematics (standard or basic)",
      required: true,
      min: 1,
      max: 1,
      options: [
        { code: "041", label: "Mathematics Standard" },
        { code: "241", label: "Mathematics Basic" },
      ],
    },
    {
      key: "science",
      label_en: "Science",
      required: true,
      min: 1,
      max: 1,
      options: [{ code: "086", label: "Science" }],
    },
  ],
  max_subjects: 5,
  photo_required: true,
  keys: [
    "exam_medium",
    "first_language",
    "second_language",
    "board_subjects",
    "photo_signature_ready",
  ],
};

const GRID = {
  section_id: SECTION,
  class_section: "X-A",
  class_code: "X",
  scheme: SCHEME,
  available_schemes: ["cbse-secondary"],
  ready_count: 0,
  rows: [
    {
      student_id: STUDENT,
      admission_no: "A-101",
      display_name: "Synthetica Candidate",
      roll_no: "1",
      values: {
        exam_medium: null,
        first_language: "184",
        second_language: null,
        board_subjects: "086",
        photo_signature_ready: null,
      },
      issues: [
        { field: "second_language", reason: "missing" },
        { field: "board_subjects", reason: "group_missing", group: "mathematics" },
        { field: "photo_signature_ready", reason: "photo" },
      ],
      ready: false,
    },
    {
      student_id: OTHER,
      admission_no: "A-102",
      display_name: "Synthetica Second",
      roll_no: "2",
      values: {
        exam_medium: null,
        first_language: null,
        second_language: null,
        board_subjects: null,
        photo_signature_ready: null,
      },
      issues: [],
      ready: false,
    },
  ],
};

let stub: BffStub;
beforeEach(() => {
  stub = installBffStub("staff");
  stub.routes["GET /bff/api/v1/academic-years"] = () =>
    page([{ id: "y1", label: "2026-27", is_current: true }]);
  stub.routes["GET /bff/api/v1/classes"] = () =>
    page([{ id: "c10", code: "X", display_en: "Class X", display_te: null, sort_order: 130 }]);
  stub.routes["GET /bff/api/v1/sections"] = () =>
    page([{ id: SECTION, academic_year_id: "y1", class_id: "c10", name: "A" }]);
  stub.routes[`GET /bff/api/v1/sections/${SECTION}/board-candidates`] = () => Response.json(GRID);
});
afterEach(uninstallBffStub);

describe("board candidate helpers", () => {
  it("parses the grid and sorts subject codes into their groups", () => {
    const grid = gridSchema.parse(GRID);
    const draft = draftOf(SCHEME, grid.rows[0]?.values ?? {});
    expect(draft.subjects).toEqual({ mathematics: "", science: "086" });
    expect(draft.first_language).toBe("184");
    expect(draft.photo).toBe(false);
  });

  it("sends only what changed and never clears a value", () => {
    const before = draftOf(SCHEME, GRID.rows[0]?.values ?? {});
    expect(entryFor(SCHEME, STUDENT, before, before)).toBeNull();
    const after = {
      ...before,
      first_language: "",
      second_language: "089",
      subjects: { mathematics: "241", science: "086" },
      photo: true,
    };
    expect(entryFor(SCHEME, STUDENT, before, after)).toEqual({
      student_id: STUDENT,
      second_language: "089",
      board_subjects: ["241", "086"],
      photo_signature_ready: true,
    });
  });

  it("fills only empty cells", () => {
    const drafts = {
      a: draftOf(SCHEME, { first_language: "089" }),
      b: draftOf(SCHEME, {}),
    };
    const out = fillBlanks(drafts, { first_language: "184", exam_medium: "english" });
    expect(out.a?.first_language).toBe("089");
    expect(out.b?.first_language).toBe("184");
    expect(out.b?.exam_medium).toBe("english");
  });
});

describe("board candidates screen", () => {
  it("shows what is missing and saves the changed rows only", async () => {
    stub.routes["GET /bff/api/v1/me"] = () =>
      Response.json(me(["student.read_basic", "student.update_nonidentity"]));
    stub.routes[`PUT /bff/api/v1/sections/${SECTION}/board-candidates`] = () =>
      Response.json({ recorded: 2, unchanged: 0, grid: GRID });
    const user = userEvent.setup();
    renderWithIntl(<BoardCandidatesScreen />);
    await user.selectOptions(
      await screen.findByRole("combobox", { name: bm.section }),
      await screen.findByRole("option", { name: /Class X/ }),
    );
    const table = await screen.findByRole("table", { name: bm.tableLabel });
    expect(within(table).getByText("Photo and signature not collected")).toBeInTheDocument();
    expect(within(table).getByText("Choose Mathematics (standard or basic)")).toBeInTheDocument();
    expect(screen.getByText(bm.unverifiedTitle)).toBeInTheDocument();
    await user.selectOptions(
      within(table).getByRole("combobox", { name: "Second language: Synthetica Candidate" }),
      "089",
    );
    await user.click(within(table).getAllByRole("checkbox")[0] as HTMLElement);
    await user.click(screen.getByRole("button", { name: "Save 1 student" }));
    await waitFor(() =>
      expect(stub.callsTo(`PUT /bff/api/v1/sections/${SECTION}/board-candidates`)).toHaveLength(1),
    );
    const [call] = stub.callsTo(`PUT /bff/api/v1/sections/${SECTION}/board-candidates`);
    expect(JSON.parse(call?.body ?? "{}")).toEqual({
      scheme: "cbse-secondary",
      source: "parent_form",
      rows: [{ student_id: STUDENT, second_language: "089", photo_signature_ready: true }],
    });
    expect(await screen.findByText("Saved 2 values.")).toBeInTheDocument();
  });

  it("is read-only without student.update_nonidentity", async () => {
    stub.routes["GET /bff/api/v1/me"] = () => Response.json(me(["student.read_basic"]));
    const user = userEvent.setup();
    renderWithIntl(<BoardCandidatesScreen />);
    await user.selectOptions(
      await screen.findByRole("combobox", { name: bm.section }),
      await screen.findByRole("option", { name: /Class X/ }),
    );
    const table = await screen.findByRole("table", { name: bm.tableLabel });
    expect(within(table).getAllByRole("checkbox")[0]).toBeDisabled();
    expect(screen.queryByRole("button", { name: /^Save/ })).not.toBeInTheDocument();
    expect(screen.queryByText(bm.fillTitle)).not.toBeInTheDocument();
  });

  it("explains a missing permission", async () => {
    stub.routes["GET /bff/api/v1/me"] = () => Response.json(me(["fee.collect"]));
    renderWithIntl(<BoardCandidatesScreen />);
    expect(await screen.findByText(bm.noPermissionTitle)).toBeInTheDocument();
  });
});
