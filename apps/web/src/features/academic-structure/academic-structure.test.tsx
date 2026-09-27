import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { installBffStub, page, problem, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { intlErrors, messages, renderWithIntl } from "@/test/render";
import { me } from "@/test/school-fixtures";
import SchoolStructurePage from "@/app/[locale]/(school)/settings/structure/page";
import { AcademicStructureScreen } from "./AcademicStructureScreen";
import {
  classCreateSchema,
  fetchAllPages,
  pickYear,
  suggestYearLabel,
  yearCreateSchema,
  yearLabelFits,
} from "./data";

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => "/en/settings/structure",
    useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
    useSearchParams: () => new URLSearchParams(),
    useParams: () => ({ locale: "en" }),
  };
});

// Synthetic fixtures in the generated API shapes (apps/api/openapi.json).
const MANAGE = "tenant.structure.manage";
const READ = "student.read_basic";
const STAMP = {
  version: 1,
  created_at: "2026-06-01T04:30:00Z",
  updated_at: "2026-06-01T04:30:00Z",
};
const YEAR_NOW = {
  id: "0192f3a4-0000-7000-8000-0000000000a1",
  label: "2026-27",
  starts_on: "2026-06-01",
  ends_on: "2027-04-30",
  is_current: true,
  ...STAMP,
};
const YEAR_OLD = {
  id: "0192f3a4-0000-7000-8000-0000000000a0",
  label: "2025-26",
  starts_on: "2025-06-01",
  ends_on: "2026-04-30",
  is_current: false,
  ...STAMP,
  version: 4,
};
const CLASS_6 = {
  id: "0192f3a4-0000-7000-8000-0000000000c6",
  code: "6",
  display_en: "Class 6",
  display_te: "6వ తరగతి",
  sort_order: 6,
  ...STAMP,
  version: 2,
};
const SECTION_A = {
  id: "0192f3a4-0000-7000-8000-0000000005a1",
  academic_year_id: YEAR_NOW.id,
  class_id: CLASS_6.id,
  name: "A",
  class_teacher_membership_id: null,
  ...STAMP,
  version: 3,
};
const SECTION_OLD = {
  ...SECTION_A,
  id: "0192f3a4-0000-7000-8000-0000000005a0",
  academic_year_id: YEAR_OLD.id,
  name: "Old-B",
};

let stub: BffStub;

function structure(permissions: string[]) {
  stub.routes["GET /bff/api/v1/me"] = () => Response.json(me(permissions));
  stub.routes["GET /bff/api/v1/academic-years"] = () => page([YEAR_NOW, YEAR_OLD]);
  stub.routes["GET /bff/api/v1/classes"] = () => page([CLASS_6]);
  stub.routes["GET /bff/api/v1/sections"] = (_request, url) =>
    page(
      [SECTION_A, SECTION_OLD].filter(
        (row) => row.academic_year_id === url.searchParams.get("academic_year_id"),
      ),
    );
}

const bodyOf = (key: string, index = 0) =>
  JSON.parse(stub.callsTo(key)[index]?.body ?? "{}") as Record<string, unknown>;

beforeEach(() => {
  stub = installBffStub("staff");
});
afterEach(() => {
  uninstallBffStub();
  expect(intlErrors).toEqual([]);
});

/** A card (a <section> named by its heading), not the scrollable table inside it. */
async function region(name: string) {
  return screen.findByRole("region", { name });
}

describe("academic structure screen (US-202, FR-TEN-010)", () => {
  it("AC3: without tenant.structure.manage it lists the structure but offers no changes", async () => {
    structure([READ]);
    renderWithIntl(<AcademicStructureScreen />);
    expect(await within(await region("Academic years")).findByText("2025-26")).toBeInTheDocument();
    const sections = await region("Sections");
    expect(await within(sections).findByText("A")).toBeInTheDocument();
    expect(within(sections).queryByText("Old-B")).toBeNull();
    expect(screen.queryByRole("button", { name: /Add|Edit|Make current/ })).toBeNull();
    expect(
      screen.getByText(messages.en.academicStructure.readOnlyNote, { exact: false }),
    ).toBeInTheDocument();
  });

  it("the page renders the screen under the school structure title", async () => {
    structure([READ]);
    renderWithIntl(<SchoolStructurePage />);
    expect(
      await screen.findByRole("heading", { level: 1, name: messages.en.school.structure.title }),
    ).toBeInTheDocument();
  });

  it("shows sections of the current year, and of another year when chosen (server filter)", async () => {
    structure([READ]);
    renderWithIntl(<AcademicStructureScreen />);
    const sections = await region("Sections");
    expect(await within(sections).findByText("A")).toBeInTheDocument();
    const years = stub
      .callsTo("GET /bff/api/v1/sections")
      .map((call) => call.url.searchParams.get("academic_year_id"));
    expect(years).toEqual([YEAR_NOW.id]);
    await userEvent.selectOptions(
      screen.getByLabelText(messages.en.academicStructure.sections.yearLabel),
      YEAR_OLD.id,
    );
    expect(await within(await region("Sections")).findByText("Old-B")).toBeInTheDocument();
  });

  it("follows next_cursor so long lists are never cut off", async () => {
    const load = vi
      .fn()
      .mockResolvedValueOnce({ data: [1, 2], next_cursor: "c1" })
      .mockResolvedValueOnce({ data: [3], next_cursor: null });
    expect(await fetchAllPages(load)).toEqual([1, 2, 3]);
    expect(load).toHaveBeenNthCalledWith(2, "c1");
  });

  it("adds an academic year with an Idempotency-Key and can make it current", async () => {
    structure([READ, MANAGE]);
    stub.routes["POST /bff/api/v1/academic-years"] = () =>
      Response.json({ ...YEAR_NOW, id: "0192f3a4-0000-7000-8000-0000000000a2" }, { status: 201 });
    renderWithIntl(<AcademicStructureScreen />);
    await userEvent.click(await screen.findByRole("button", { name: "Add academic year" }));
    const dialog = screen.getByRole("dialog", { name: "Add academic year" });
    await userEvent.type(within(dialog).getByLabelText("Year"), "2027-28");
    await userEvent.type(within(dialog).getByLabelText("Starts on"), "2027-06-01");
    await userEvent.type(within(dialog).getByLabelText("Ends on"), "2028-04-30");
    await userEvent.click(within(dialog).getByLabelText(/Make this the current year/));
    await userEvent.click(within(dialog).getByRole("button", { name: "Add year" }));
    await waitFor(() => expect(stub.callsTo("POST /bff/api/v1/academic-years")).toHaveLength(1));
    const call = stub.callsTo("POST /bff/api/v1/academic-years")[0];
    expect(call?.headers.get("Idempotency-Key")).toMatch(/^[0-9a-f-]{36}$/);
    expect(bodyOf("POST /bff/api/v1/academic-years")).toEqual({
      label: "2027-28",
      starts_on: "2027-06-01",
      ends_on: "2028-04-30",
      is_current: true,
    });
  });

  it("checks the year label and dates before sending anything", async () => {
    expect(yearLabelFits("2026-27", "2026-06-01")).toBe(true);
    expect(yearLabelFits("2026-28", "2026-06-01")).toBe(false);
    expect(yearLabelFits("2099-00", "2099-06-01")).toBe(true);
    expect(yearLabelFits("2026-27", "2025-06-01")).toBe(false);
    expect(suggestYearLabel("2026-06-01")).toBe("2026-27");
    const bad = yearCreateSchema.safeParse({
      label: "2026-28",
      starts_on: "2026-06-01",
      ends_on: "2026-05-01",
    });
    expect(bad.error?.issues.map((issue) => [issue.path[0], issue.message])).toEqual([
      ["label", "invalid"],
      ["ends_on", "endAfterStart"],
    ]);

    structure([READ, MANAGE]);
    renderWithIntl(<AcademicStructureScreen />);
    await userEvent.click(await screen.findByRole("button", { name: "Add academic year" }));
    const dialog = screen.getByRole("dialog", { name: "Add academic year" });
    await userEvent.type(within(dialog).getByLabelText("Year"), "2026");
    await userEvent.click(within(dialog).getByRole("button", { name: "Add year" }));
    expect(await within(dialog).findAllByText(messages.en.validation.required)).toHaveLength(2);
    expect(within(dialog).getByLabelText("Year")).toHaveAttribute("aria-invalid", "true");
    expect(stub.callsTo("POST /bff/api/v1/academic-years")).toHaveLength(0);
  });

  it("explains overlapping dates (409 academic_year_overlap) in plain language", async () => {
    structure([READ, MANAGE]);
    stub.routes["POST /bff/api/v1/academic-years"] = () => problem(409, "academic_year_overlap");
    renderWithIntl(<AcademicStructureScreen />);
    await userEvent.click(await screen.findByRole("button", { name: "Add academic year" }));
    const dialog = screen.getByRole("dialog", { name: "Add academic year" });
    await userEvent.type(within(dialog).getByLabelText("Year"), "2026-27");
    await userEvent.type(within(dialog).getByLabelText("Starts on"), "2026-07-01");
    await userEvent.type(within(dialog).getByLabelText("Ends on"), "2027-03-31");
    await userEvent.click(within(dialog).getByRole("button", { name: "Add year" }));
    expect(
      await within(dialog).findByText(
        messages.en.academicStructure.errors.academic_year_overlap.title,
      ),
    ).toBeInTheDocument();
  });

  it("makes another year current with If-Match (FR-TEN-010: one current year)", async () => {
    structure([READ, MANAGE]);
    stub.routes[`POST /bff/api/v1/academic-years/${YEAR_OLD.id}/make-current`] = () =>
      Response.json({ ...YEAR_OLD, is_current: true, version: 5 });
    renderWithIntl(<AcademicStructureScreen />);
    const years = await region("Academic years");
    const row = (await within(years).findByText("2025-26")).closest("tr") as HTMLElement;
    expect(within(years).getAllByRole("button", { name: /Make current/ })).toHaveLength(1);
    await userEvent.click(within(row).getByRole("button", { name: /Make current/ }));
    const dialog = screen.getByRole("dialog", { name: "Make 2025-26 the current year?" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Make current" }));
    await waitFor(() =>
      expect(
        stub.callsTo(`POST /bff/api/v1/academic-years/${YEAR_OLD.id}/make-current`),
      ).toHaveLength(1),
    );
    expect(
      stub
        .callsTo(`POST /bff/api/v1/academic-years/${YEAR_OLD.id}/make-current`)[0]
        ?.headers.get("If-Match"),
    ).toBe('W/"4"');
  });

  it("edits a year with If-Match; a 412 says someone else changed it and reloads the lists", async () => {
    structure([READ, MANAGE]);
    stub.routes[`PATCH /bff/api/v1/academic-years/${YEAR_OLD.id}`] = () =>
      problem(412, "precondition_failed");
    renderWithIntl(<AcademicStructureScreen />);
    const years = await region("Academic years");
    const row = (await within(years).findByText("2025-26")).closest("tr") as HTMLElement;
    await userEvent.click(within(row).getByRole("button", { name: "Edit" }));
    const dialog = screen.getByRole("dialog", { name: "Change academic year 2025-26" });
    expect(within(dialog).getByLabelText("Starts on")).toHaveValue("2025-06-01");
    const before = stub.callsTo("GET /bff/api/v1/academic-years").length;
    await userEvent.click(within(dialog).getByRole("button", { name: "Save changes" }));
    expect(
      await within(dialog).findByText(
        messages.en.academicStructure.errors.precondition_failed.title,
      ),
    ).toBeInTheDocument();
    const call = stub.callsTo(`PATCH /bff/api/v1/academic-years/${YEAR_OLD.id}`)[0];
    expect(call?.headers.get("If-Match")).toBe('W/"4"');
    expect(JSON.parse(call?.body ?? "{}")).toEqual({
      label: "2025-26",
      starts_on: "2025-06-01",
      ends_on: "2026-04-30",
    });
    await waitFor(() =>
      expect(stub.callsTo("GET /bff/api/v1/academic-years").length).toBeGreaterThan(before),
    );
  });

  it("adds a class (code in capitals) and edits its names and order with If-Match", async () => {
    expect(
      classCreateSchema.safeParse({
        code: "lkg",
        display_en: "LKG",
        display_te: "ఎల్‌కేజీ",
        sort_order: "1",
      }).data?.code,
    ).toBe("LKG");
    expect(
      classCreateSchema.safeParse({
        code: "class 1",
        display_en: "x",
        display_te: "y",
        sort_order: "1",
      }).success,
    ).toBe(false);

    structure([READ, MANAGE]);
    stub.routes["POST /bff/api/v1/classes"] = () =>
      Response.json({ ...CLASS_6, id: "0192f3a4-0000-7000-8000-0000000000c7" }, { status: 201 });
    stub.routes[`PATCH /bff/api/v1/classes/${CLASS_6.id}`] = () =>
      Response.json({ ...CLASS_6, version: 3 });
    renderWithIntl(<AcademicStructureScreen />);

    await userEvent.click(await screen.findByRole("button", { name: "Add class" }));
    const add = screen.getByRole("dialog", { name: "Add class" });
    expect(within(add).getByLabelText("Position in lists")).toHaveValue("7");
    await userEvent.type(within(add).getByLabelText("Class code"), "7");
    await userEvent.type(within(add).getByLabelText("Name in English"), "Class 7");
    await userEvent.type(within(add).getByLabelText("Name in Telugu"), "7వ తరగతి");
    await userEvent.click(within(add).getByRole("button", { name: "Add class" }));
    await waitFor(() => expect(stub.callsTo("POST /bff/api/v1/classes")).toHaveLength(1));
    expect(bodyOf("POST /bff/api/v1/classes")).toEqual({
      code: "7",
      display_en: "Class 7",
      display_te: "7వ తరగతి",
      sort_order: 7,
    });
    expect(
      stub.callsTo("POST /bff/api/v1/classes")[0]?.headers.get("Idempotency-Key"),
    ).toBeTruthy();

    const classes = await region("Classes");
    const row = within(classes).getByText("Class 6").closest("tr") as HTMLElement;
    await userEvent.click(within(row).getByRole("button", { name: "Edit" }));
    const edit = screen.getByRole("dialog", { name: "Change Class 6" });
    const english = within(edit).getByLabelText("Name in English");
    await userEvent.clear(english);
    await userEvent.type(english, "Class VI");
    await userEvent.click(within(edit).getByRole("button", { name: "Save changes" }));
    await waitFor(() =>
      expect(stub.callsTo(`PATCH /bff/api/v1/classes/${CLASS_6.id}`)).toHaveLength(1),
    );
    const patch = stub.callsTo(`PATCH /bff/api/v1/classes/${CLASS_6.id}`)[0];
    expect(patch?.headers.get("If-Match")).toBe('W/"2"');
    expect(JSON.parse(patch?.body ?? "{}")).toEqual({
      display_en: "Class VI",
      display_te: "6వ తరగతి",
      sort_order: 6,
    });
  });

  it("adds the standard classes Nursery to XII (US-202 AC1)", async () => {
    structure([READ, MANAGE]);
    stub.routes["POST /bff/api/v1/classes/defaults"] = () => page([CLASS_6]);
    renderWithIntl(<AcademicStructureScreen />);
    await userEvent.click(await screen.findByRole("button", { name: "Add standard classes" }));
    const dialog = screen.getByRole("dialog", { name: "Add standard classes?" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Add missing classes" }));
    await waitFor(() => expect(stub.callsTo("POST /bff/api/v1/classes/defaults")).toHaveLength(1));
  });

  it("adds a section to the chosen year and renames one with If-Match", async () => {
    structure([READ, MANAGE]);
    stub.routes["POST /bff/api/v1/sections"] = () =>
      Response.json(
        { ...SECTION_A, id: "0192f3a4-0000-7000-8000-0000000005b1", name: "B" },
        {
          status: 201,
        },
      );
    stub.routes[`PATCH /bff/api/v1/sections/${SECTION_A.id}`] = () =>
      Response.json({ ...SECTION_A, name: "A1", version: 4 });
    renderWithIntl(<AcademicStructureScreen />);
    const sections = await region("Sections");
    await within(sections).findByText("A");

    await userEvent.click(screen.getByRole("button", { name: "Add section" }));
    const add = screen.getByRole("dialog", { name: "Add a section to 2026-27" });
    await userEvent.selectOptions(within(add).getByLabelText("Class"), CLASS_6.id);
    await userEvent.type(within(add).getByLabelText("Section name"), "B");
    await userEvent.click(within(add).getByRole("button", { name: "Add section" }));
    await waitFor(() => expect(stub.callsTo("POST /bff/api/v1/sections")).toHaveLength(1));
    expect(bodyOf("POST /bff/api/v1/sections")).toEqual({
      academic_year_id: YEAR_NOW.id,
      class_id: CLASS_6.id,
      name: "B",
    });

    const row = within(await region("Sections"))
      .getByText("A")
      .closest("tr") as HTMLElement;
    await userEvent.click(within(row).getByRole("button", { name: "Edit" }));
    const edit = screen.getByRole("dialog", { name: "Rename section A of Class 6" });
    const name = within(edit).getByLabelText("Section name");
    await userEvent.clear(name);
    await userEvent.type(name, "A1");
    await userEvent.click(within(edit).getByRole("button", { name: "Save changes" }));
    await waitFor(() =>
      expect(stub.callsTo(`PATCH /bff/api/v1/sections/${SECTION_A.id}`)).toHaveLength(1),
    );
    const patch = stub.callsTo(`PATCH /bff/api/v1/sections/${SECTION_A.id}`)[0];
    expect(patch?.headers.get("If-Match")).toBe('W/"3"');
    expect(JSON.parse(patch?.body ?? "{}")).toEqual({ name: "A1" });
  });

  it("picks the chosen year, else the current one, else the newest", () => {
    expect(pickYear([YEAR_NOW, YEAR_OLD], YEAR_OLD.id)).toBe(YEAR_OLD.id);
    expect(pickYear([YEAR_OLD, YEAR_NOW], "bogus")).toBe(YEAR_NOW.id);
    expect(pickYear([{ ...YEAR_NOW, is_current: false }, YEAR_OLD], null)).toBe(YEAR_NOW.id);
    expect(pickYear([], null)).toBeNull();
  });

  it("renders in Telugu with class names in Telugu and no missing messages", async () => {
    structure([READ, MANAGE]);
    renderWithIntl(<AcademicStructureScreen />, "te");
    const classes = await region(messages.te.school.structure.classes.title);
    expect(await within(classes).findByText("6వ తరగతి")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: messages.te.academicStructure.years.add }),
    ).toBeInTheDocument();
  });
});
