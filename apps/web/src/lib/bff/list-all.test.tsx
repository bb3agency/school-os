import { screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useSchoolMembers } from "@/features/documents/data";
import { useSectionOptions } from "@/features/findings/data";
import { installBffStub, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { renderWithIntl } from "@/test/render";
import { LIST_ALL_MAX_PAGES, listAll } from "./query";

describe("listAll (FR-TEN-010)", () => {
  it("follows next_cursor until the last page", async () => {
    const load = vi.fn(async (cursor: string | undefined) =>
      cursor === undefined
        ? { data: [1, 2], next_cursor: "c2" }
        : cursor === "c2"
          ? { data: [3], next_cursor: "c3" }
          : { data: [4], next_cursor: null },
    );
    await expect(listAll(load)).resolves.toEqual([1, 2, 3, 4]);
    expect(load.mock.calls.map(([cursor]) => cursor)).toEqual([undefined, "c2", "c3"]);
  });

  it("stops after the page limit even if the API keeps answering a cursor", async () => {
    const load = vi.fn(async () => ({ data: [0], next_cursor: "again" }));
    await expect(listAll(load)).resolves.toHaveLength(LIST_ALL_MAX_PAGES);
  });

  it("reads a missing or odd body as no rows", async () => {
    await expect(listAll(async () => ({}))).resolves.toEqual([]);
  });
});

const YEAR = "0192f3a4-0000-7000-8000-0000000000a1";
const CLASS_9 = "0192f3a4-0000-7000-8000-0000000000c9";
const CLASS_10 = "0192f3a4-0000-7000-8000-0000000000ca";
const SECTION_9A = "0192f3a4-0000-7000-8000-00000000059a";
const SECTION_10A = "0192f3a4-0000-7000-8000-00000000510a";

function SectionLabels() {
  const { sections, loading } = useSectionOptions();
  if (loading) return <p>Loading</p>;
  return (
    <ul aria-label="Sections">
      {sections.map((section) => (
        <li key={section.id}>{section.label}</li>
      ))}
    </ul>
  );
}

describe("section pickers list every section of a large school (FR-TEN-010)", () => {
  let stub: BffStub;
  beforeEach(() => {
    stub = installBffStub("staff");
  });
  afterEach(() => uninstallBffStub());

  it("includes sections from the second page of /sections", async () => {
    stub.routes["GET /bff/api/v1/academic-years"] = () =>
      Response.json({ data: [{ id: YEAR, is_current: true }], next_cursor: null });
    stub.routes["GET /bff/api/v1/classes"] = () =>
      Response.json({
        data: [
          { id: CLASS_9, display_en: "Class 9", display_te: "", sort_order: 9 },
          { id: CLASS_10, display_en: "Class 10", display_te: "", sort_order: 10 },
        ],
        next_cursor: null,
      });
    stub.routes["GET /bff/api/v1/sections"] = (_request, url) =>
      url.searchParams.get("cursor") === "page-2"
        ? Response.json({
            data: [{ id: SECTION_10A, class_id: CLASS_10, academic_year_id: YEAR, name: "A" }],
            next_cursor: null,
          })
        : Response.json({
            data: [{ id: SECTION_9A, class_id: CLASS_9, academic_year_id: YEAR, name: "A" }],
            next_cursor: "page-2",
          });
    renderWithIntl(<SectionLabels />);
    const list = await screen.findByRole("list", { name: "Sections" });
    expect(list).toHaveTextContent("Class 9 · A");
    expect(list).toHaveTextContent("Class 10 · A");
    expect(stub.callsTo("GET /bff/api/v1/sections")).toHaveLength(2);
  });
});

function MemberNames() {
  const members = useSchoolMembers(true);
  if (!members.data) return <p>Loading</p>;
  return (
    <ul aria-label="Members">
      {members.data.map((member) => (
        <li key={member.id}>{member.display_name}</li>
      ))}
    </ul>
  );
}

describe("document access pickers list every staff member (WEB-05)", () => {
  beforeEach(() => {
    installBffStub("staff").routes["GET /bff/api/v1/users"] = (_request, url) =>
      url.searchParams.get("cursor") === "users-2"
        ? Response.json({
            data: [
              { id: "0192f3a4-0000-7000-8000-0000000000d2", display_name: "Second Page Teacher" },
            ],
            next_cursor: null,
          })
        : Response.json({
            data: [
              { id: "0192f3a4-0000-7000-8000-0000000000d1", display_name: "First Page Clerk" },
            ],
            next_cursor: "users-2",
          });
  });
  afterEach(() => uninstallBffStub());

  it("includes people from the second page of /users", async () => {
    renderWithIntl(<MemberNames />);
    const list = await screen.findByRole("list", { name: "Members" });
    expect(list).toHaveTextContent("First Page Clerk");
    expect(list).toHaveTextContent("Second Page Teacher");
  });
});
