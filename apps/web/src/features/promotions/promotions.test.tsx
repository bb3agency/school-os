import type { components } from "@schoolos/api-client";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/bff/query";
import { installBffStub, page, problem, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { intlErrors, messages, renderWithIntl } from "@/test/render";
import { me } from "@/test/school-fixtures";
import PromotionsPage from "@/app/[locale]/(school)/settings/structure/years/[yearId]/promotions/page";
import { PromotionsIndexScreen } from "./PromotionsIndexScreen";
import { PromotionsScreen } from "./PromotionsScreen";
import { planRequest, promotionError, targetYears } from "./data";

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => "/en/settings/structure/years/x/promotions",
    useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
    useSearchParams: () => new URLSearchParams(),
    useParams: () => ({ locale: "en" }),
    notFound: () => {
      throw new Error("NEXT_NOT_FOUND");
    },
  };
});

type Schemas = components["schemas"];

// Synthetic fixtures in the generated API shapes (apps/api/openapi.json). Invented names only.
const MANAGE = "tenant.structure.manage";
const READ = "student.read_basic";
const STAMP = {
  version: 1,
  created_at: "2026-06-01T04:30:00Z",
  updated_at: "2026-06-01T04:30:00Z",
};
const id = (suffix: string) => `0192f3a4-0000-7000-8000-${suffix.padStart(12, "0")}`;

const year = (suffix: string, label: string, startsOn: string, extra = {}) => ({
  id: id(suffix),
  label,
  starts_on: startsOn,
  ends_on: `${Number(startsOn.slice(0, 4)) + 1}-04-30`,
  is_current: false,
  archived_at: null,
  ...STAMP,
  ...extra,
});
const FROM = year("a1", "2026-27", "2026-06-01", { is_current: true });
const NEXT = year("a2", "2027-28", "2027-06-01");
const LATER = year("a3", "2028-29", "2028-06-01");
const GONE = year("a4", "2029-30", "2029-06-01", { archived_at: "2026-07-01T00:00:00Z" });
const OLD = year("a0", "2025-26", "2025-06-01");

const CLASS_6 = {
  id: id("c6"),
  code: "6",
  display_en: "Class 6",
  display_te: "6వ తరగతి",
  sort_order: 6,
  archived_at: null,
  ...STAMP,
};
const CLASS_7 = { ...CLASS_6, id: id("c7"), code: "7", display_en: "Class 7", sort_order: 7 };

const section = (suffix: string, yearId: string, classId: string, name: string) => ({
  id: id(suffix),
  academic_year_id: yearId,
  class_id: classId,
  name,
  class_teacher_membership_id: null,
  archived_at: null,
  ...STAMP,
});
const S6A = section("5a", FROM.id, CLASS_6.id, "A");
const S6B = section("5b", FROM.id, CLASS_6.id, "B");
const T7A = section("7a", NEXT.id, CLASS_7.id, "A");
const T7C = section("7c", NEXT.id, CLASS_7.id, "C");

const summary = (
  suffix: string,
  name: string,
  admission: string,
  sectionId: string | null,
): Schemas["StudentSummary"] => ({
  id: id(suffix),
  display_name: name,
  admission_no: admission,
  status: "active",
  class_section: sectionId === S6B.id ? "6-B" : "6-A",
  section_id: sectionId,
  match: { field: null, score: null },
});
const ASHA = summary("d1", "Asha Test", "A-101", S6A.id);
const BALA = summary("d2", "Bala Test", "A-102", S6A.id);
const CHITRA = summary("d3", "Chitra Test", "A-103", S6B.id);
const OUTSIDER = summary("d9", "Bala Other", "A-999", id("5f"));

const student = (
  s: Schemas["StudentSummary"],
  outcome: Schemas["PromotionStudentOut"]["outcome"],
  to: string | null,
  reason: string | null = null,
): Schemas["PromotionStudentOut"] => ({
  student_id: s.id,
  enrollment_id: id(`e${s.id.slice(-2)}`),
  from_section_id: s.section_id ?? "",
  outcome,
  to_section_id: to,
  reason,
});

const FINGERPRINT = "a".repeat(64);
const PREVIEW_PROBLEM: Schemas["PromotionPreviewOut"] = {
  from_academic_year_id: FROM.id,
  to_academic_year_id: NEXT.id,
  counts: { promoted: 3, held_back: 0, graduated: 0, skipped: 0 },
  groups: [
    {
      from_section_id: S6A.id,
      from_label: "6-A",
      outcome: "promoted",
      to_section_id: T7A.id,
      to_label: "7-A",
      count: 2,
    },
  ],
  problems: [
    {
      code: "no_target_section",
      from_section_id: S6B.id,
      from_label: "6-B",
      target_class_id: CLASS_7.id,
      count: 1,
    },
  ],
  students: [
    student(ASHA, "promoted", T7A.id),
    student(BALA, "promoted", T7A.id),
    student(CHITRA, "promoted", null, "no_target_section"),
  ],
  plan_fingerprint: "b".repeat(64),
  can_commit: false,
};
const PREVIEW_OK: Schemas["PromotionPreviewOut"] = {
  ...PREVIEW_PROBLEM,
  counts: { promoted: 2, held_back: 1, graduated: 0, skipped: 0 },
  groups: [
    { ...PREVIEW_PROBLEM.groups[0]!, count: 1 },
    {
      from_section_id: S6A.id,
      from_label: "6-A",
      outcome: "held_back",
      to_section_id: id("6a"),
      to_label: "6-A",
      count: 1,
    },
    {
      from_section_id: S6B.id,
      from_label: "6-B",
      outcome: "promoted",
      to_section_id: T7C.id,
      to_label: "7-C",
      count: 1,
    },
  ],
  problems: [],
  students: [
    student(ASHA, "promoted", T7A.id),
    student(BALA, "held_back", id("6a")),
    student(CHITRA, "promoted", T7C.id),
  ],
  plan_fingerprint: FINGERPRINT,
  can_commit: true,
};

const RUN: Schemas["PromotionRunOut"] = {
  id: id("f1"),
  from_academic_year_id: FROM.id,
  to_academic_year_id: NEXT.id,
  status: "committed",
  counts: { promoted: 2, held_back: 1, graduated: 0, skipped: 0 },
  plan_fingerprint: FINGERPRINT,
  committed_by: id("b1"),
  committed_at: "2026-09-27T05:00:00Z",
  undo_until: "2026-09-28T05:00:00Z",
  can_undo: true,
  undone_by: null,
  undone_at: null,
  version: 1,
};

const PROMOTIONS = `/bff/api/v1/academic-years/${FROM.id}/promotions`;
let stub: BffStub;

function school(permissions: string[], runs: Schemas["PromotionRunOut"][] = []) {
  stub.routes["GET /bff/api/v1/me"] = () => Response.json(me(permissions));
  stub.routes[`GET /bff/api/v1/academic-years/${FROM.id}`] = () => Response.json(FROM);
  stub.routes[`GET ${PROMOTIONS}`] = () => Response.json(runs);
  stub.routes["GET /bff/api/v1/academic-years"] = () => page([GONE, LATER, NEXT, FROM, OLD]);
  stub.routes["GET /bff/api/v1/classes"] = () => page([CLASS_6, CLASS_7]);
  stub.routes["GET /bff/api/v1/sections"] = (_r, url) =>
    page(
      [S6A, S6B, T7A, T7C].filter(
        (row) => row.academic_year_id === url.searchParams.get("academic_year_id"),
      ),
    );
  stub.routes["POST /bff/api/v1/students/search"] = async (request) => {
    const body = (await request.json()) as { section_id?: string; query?: string };
    if (body.query) return page([BALA, OUTSIDER]);
    return page([ASHA, BALA, CHITRA].filter((row) => row.section_id === body.section_id));
  };
}

const bodies = (key: string) =>
  stub.callsTo(key).map((call) => JSON.parse(call.body || "{}") as Record<string, unknown>);

beforeEach(() => {
  stub = installBffStub("staff");
});
afterEach(() => {
  uninstallBffStub();
  expect(intlErrors).toEqual([]);
});

async function openPlan() {
  renderWithIntl(<PromotionsScreen yearId={FROM.id} />);
  expect(
    await screen.findByRole("heading", { level: 1, name: "Promote students of 2026-27" }),
  ).toBeInTheDocument();
  return screen.findByRole("button", { name: "Preview promotion" });
}

describe("promotions screen (FR-TEN-011, US-202 AC2)", () => {
  it("without tenant.structure.manage it explains and asks the API nothing", async () => {
    school([READ]);
    renderWithIntl(<PromotionsScreen yearId={FROM.id} />);
    expect(
      await screen.findByText(messages.en.academicStructure.promotions.noAccessTitle),
    ).toBeInTheDocument();
    expect(stub.callsTo(`GET ${PROMOTIONS}`)).toHaveLength(0);
    expect(stub.callsTo(`GET /bff/api/v1/academic-years/${FROM.id}`)).toHaveLength(0);
    expect(screen.queryByRole("button", { name: "Preview promotion" })).toBeNull();
  });

  it("previews into the next later year: counts, groups and the section problem; commit waits", async () => {
    school([READ, MANAGE]);
    stub.routes[`POST ${PROMOTIONS}:preview`] = () => Response.json(PREVIEW_PROBLEM);
    const preview = await openPlan();
    const steps = screen.getByRole("navigation", { name: "Promotion steps" });
    expect(within(steps).getByText("Plan").closest("li")).toHaveAttribute("aria-current", "step");
    const target = screen.getByLabelText("Promote into");
    expect(target).toHaveValue(NEXT.id);
    // Only later years that are in use: not the archived one, not older ones.
    expect(
      within(target)
        .getAllByRole("option")
        .map((option) => option.textContent),
    ).toEqual(["2027-28", "2028-29"]);

    await userEvent.click(preview);
    await waitFor(() => expect(stub.callsTo(`POST ${PROMOTIONS}:preview`)).toHaveLength(1));
    expect(bodies(`POST ${PROMOTIONS}:preview`)[0]).toEqual({
      to_academic_year_id: NEXT.id,
      held_back_student_ids: [],
      section_map: [],
    });
    const card = await screen.findByRole("region", { name: "Preview: 2026-27 to 2027-28" });
    expect(within(card).getByText("1 student has no section in the new year")).toBeInTheDocument();
    expect(within(card).getByText("6-B: 1 student")).toBeInTheDocument();
    const groups = within(card).getByRole("table", { name: "Where each section goes" });
    expect(within(groups).getByText("7-A")).toBeInTheDocument();
    expect(within(card).getByRole("button", { name: "Promote students" })).toBeDisabled();
    expect(
      within(card).getByText(messages.en.academicStructure.promotions.cannotCommit),
    ).toBeInTheDocument();
  });

  it("maps a section, holds back a student found by search, previews again and commits", async () => {
    school([READ, MANAGE]);
    let previews = 0;
    stub.routes[`POST ${PROMOTIONS}:preview`] = () => {
      previews += 1;
      return Response.json(previews === 1 ? PREVIEW_PROBLEM : PREVIEW_OK);
    };
    stub.routes[`POST ${PROMOTIONS}:commit`] = () => Response.json(RUN, { status: 201 });
    await userEvent.click(await openPlan());

    // Section choice for the problem: only sections of Class 7 in 2027-28.
    const choice = await screen.findByLabelText("Students of 6-B going to Class 7");
    expect(
      within(choice)
        .getAllByRole("option")
        .map((option) => option.textContent),
    ).toEqual(["Choose a section", "Class 7 A", "Class 7 C"]);
    await userEvent.selectOptions(choice, T7C.id);
    expect(
      await screen.findByText(messages.en.academicStructure.promotions.stale),
    ).toBeInTheDocument();

    // Held back: search (body only, SEC-008); students of other years are left out.
    await userEvent.type(screen.getByLabelText("Find a student"), "Bala");
    await userEvent.click(screen.getByRole("button", { name: "Search" }));
    expect(await screen.findByText("1 student of this year matches.")).toBeInTheDocument();
    expect(screen.queryByText(/Bala Other/)).toBeNull();
    const searchCall = stub
      .callsTo("POST /bff/api/v1/students/search")
      .find((call) => call.body.includes("Bala"));
    expect(searchCall?.url.search).toBe("");
    const picker = screen.getByRole("region", { name: "Students who stay in the same class" });
    const keep = within(picker).getByRole("button", {
      name: "Keep in class (student Bala Test (A-102))",
    });
    expect(keep).toHaveAttribute("aria-pressed", "false");
    await userEvent.click(keep);
    expect(keep).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("1 student stays in the same class")).toBeInTheDocument();
    expect(
      within(picker).getByRole("button", { name: "Remove (student Bala Test (A-102))" }),
    ).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Preview promotion" }));
    await waitFor(() => expect(stub.callsTo(`POST ${PROMOTIONS}:preview`)).toHaveLength(2));
    expect(bodies(`POST ${PROMOTIONS}:preview`)[1]).toEqual({
      to_academic_year_id: NEXT.id,
      held_back_student_ids: [BALA.id],
      section_map: [{ from_section_id: S6B.id, to_section_id: T7C.id }],
    });

    // Per-student outcomes with names from the student list, one section at a time.
    const card = await screen.findByRole("region", { name: "Preview: 2026-27 to 2027-28" });
    await waitFor(() => expect(within(card).getByText("Asha Test (A-101)")).toBeInTheDocument());
    const students = within(card).getByRole("table", { name: "Students of 6-A" });
    const bala = within(students).getByText("Bala Test (A-102)").closest("tr") as HTMLElement;
    expect(within(bala).getByText("Held back")).toBeInTheDocument();
    expect(screen.queryByText(messages.en.academicStructure.promotions.stale)).toBeNull();

    const commit = within(card).getByRole("button", { name: "Promote students" });
    expect(commit).toBeEnabled();
    await userEvent.click(commit);
    const dialog = screen.getByRole("dialog", { name: "Promote 3 students now?" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Promote students" }));
    await waitFor(() => expect(stub.callsTo(`POST ${PROMOTIONS}:commit`)).toHaveLength(1));
    const call = stub.callsTo(`POST ${PROMOTIONS}:commit`)[0];
    expect(call?.headers.get("Idempotency-Key")).toMatch(/^[0-9a-f-]{36}$/);
    expect(JSON.parse(call?.body ?? "{}")).toEqual({
      to_academic_year_id: NEXT.id,
      held_back_student_ids: [BALA.id],
      section_map: [{ from_section_id: S6B.id, to_section_id: T7C.id }],
      plan_fingerprint: FINGERPRINT,
    });
    // The status is read again after the commit.
    await waitFor(() => expect(stub.callsTo(`GET ${PROMOTIONS}`).length).toBeGreaterThan(1));
  });

  it("explains promotion_plan_changed on commit and asks for a new preview", async () => {
    school([READ, MANAGE]);
    stub.routes[`POST ${PROMOTIONS}:preview`] = () => Response.json(PREVIEW_OK);
    stub.routes[`POST ${PROMOTIONS}:commit`] = () => problem(409, "promotion_plan_changed");
    await userEvent.click(await openPlan());
    const card = await screen.findByRole("region", { name: "Preview: 2026-27 to 2027-28" });
    await userEvent.click(within(card).getByRole("button", { name: "Promote students" }));
    const dialog = screen.getByRole("dialog", { name: "Promote 3 students now?" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Promote students" }));
    expect(
      await within(dialog).findByText(
        messages.en.academicStructure.errors.promotion_plan_changed.title,
      ),
    ).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(within(card).getByRole("button", { name: "Promote students" })).toBeDisabled();
    expect(screen.getByText(messages.en.academicStructure.promotions.stale)).toBeInTheDocument();
  });

  it.each([
    ["promotion_already_committed", 409, {}],
    ["nothing_to_promote", 409, {}],
    [
      "no_target_section",
      422,
      { code: "validation_error", errors: [{ field: "section_map", code: "no_target_section" }] },
    ],
  ] as const)("explains %s on commit in plain language", async (code, status, extra) => {
    school([READ, MANAGE]);
    stub.routes[`POST ${PROMOTIONS}:preview`] = () => Response.json(PREVIEW_OK);
    stub.routes[`POST ${PROMOTIONS}:commit`] = () =>
      Object.keys(extra).length > 0
        ? problem(status, "validation_error", extra)
        : problem(status, code);
    await userEvent.click(await openPlan());
    const card = await screen.findByRole("region", { name: "Preview: 2026-27 to 2027-28" });
    await userEvent.click(within(card).getByRole("button", { name: "Promote students" }));
    const dialog = screen.getByRole("dialog", { name: "Promote 3 students now?" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Promote students" }));
    expect(
      await within(dialog).findByText(messages.en.academicStructure.errors[code].title),
    ).toBeInTheDocument();
  });

  it("explains validation codes from the preview (held-back student not in this year)", async () => {
    school([READ, MANAGE]);
    stub.routes[`POST ${PROMOTIONS}:preview`] = () =>
      problem(422, "validation_error", {
        errors: [
          {
            field: "held_back_student_ids.0",
            code: "not_in_year",
            message_key: "errors.not_in_year",
          },
        ],
      });
    await userEvent.click(await openPlan());
    expect(
      await screen.findByText(
        messages.en.academicStructure.errors.promotion_held_back_not_in_year.title,
      ),
    ).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: /Preview:/ })).toBeNull();
  });

  it("without student.read_basic: no name search and no names or ids, only the results", async () => {
    school([MANAGE]);
    stub.routes[`POST ${PROMOTIONS}:preview`] = () => Response.json(PREVIEW_OK);
    await userEvent.click(await openPlan());
    expect(
      screen.getByText(messages.en.academicStructure.promotions.noStudentRead),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText("Find a student")).toBeNull();
    const card = await screen.findByRole("region", { name: "Preview: 2026-27 to 2027-28" });
    expect(within(card).getAllByText("Name hidden").length).toBeGreaterThan(0);
    expect(card.textContent).not.toContain(ASHA.id);
    expect(stub.callsTo("POST /bff/api/v1/students/search")).toHaveLength(0);
  });

  it("shows a committed promotion with Undo (confirm) and hides the plan", async () => {
    school([READ, MANAGE], [RUN]);
    stub.routes[`POST ${PROMOTIONS}:undo`] = () =>
      Response.json({ ...RUN, status: "undone", can_undo: false });
    renderWithIntl(<PromotionsScreen yearId={FROM.id} />);
    expect(
      await screen.findByText(messages.en.academicStructure.promotions.doneTitle),
    ).toBeInTheDocument();
    // In the status and in the history table.
    expect(screen.getAllByText("2 promoted, 1 held back, 0 graduated, 0 skipped")).toHaveLength(2);
    expect(screen.getByText("You can undo it until 28/09/2026 10:30.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Preview promotion" })).toBeNull();
    // Step indicator: plan, preview and promote are done; undo is the current step.
    const steps = screen.getByRole("navigation", { name: "Promotion steps" });
    expect(within(steps).getByText("Undo within 24 hours").closest("li")).toHaveAttribute(
      "aria-current",
      "step",
    );
    expect(within(steps).getAllByText("Done:", { exact: false })).toHaveLength(3);
    await userEvent.click(screen.getByRole("button", { name: "Undo promotion" }));
    const dialog = screen.getByRole("dialog", { name: "Undo this promotion?" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Undo promotion" }));
    await waitFor(() => expect(stub.callsTo(`POST ${PROMOTIONS}:undo`)).toHaveLength(1));
  });

  it.each(["promotion_has_dependents", "promotion_undo_expired", "no_promotion"] as const)(
    "explains %s when undoing",
    async (code) => {
      school([READ, MANAGE], [RUN]);
      stub.routes[`POST ${PROMOTIONS}:undo`] = () => problem(409, code);
      renderWithIntl(<PromotionsScreen yearId={FROM.id} />);
      await userEvent.click(await screen.findByRole("button", { name: "Undo promotion" }));
      const dialog = screen.getByRole("dialog", { name: "Undo this promotion?" });
      await userEvent.click(within(dialog).getByRole("button", { name: "Undo promotion" }));
      expect(
        await within(dialog).findByText(messages.en.academicStructure.errors[code].title),
      ).toBeInTheDocument();
    },
  );

  it("after 24 hours there is no Undo, only an explanation; undone runs are listed", async () => {
    school(
      [READ, MANAGE],
      [
        { ...RUN, can_undo: false },
        { ...RUN, id: id("f0"), status: "undone", undone_at: "2026-09-26T06:00:00Z" },
      ],
    );
    renderWithIntl(<PromotionsScreen yearId={FROM.id} />);
    expect(
      await screen.findByText(messages.en.academicStructure.promotions.undoExpired),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Undo promotion" })).toBeNull();
    const history = screen.getByRole("table", { name: "Promotions of this year" });
    expect(within(history).getByText("Undone")).toBeInTheDocument();
    expect(within(history).getAllByText("2027-28")).toHaveLength(2);
  });

  it("history names who promoted and who undid (display names only)", async () => {
    school(
      [READ, MANAGE],
      [
        { ...RUN, can_undo: false, committed_by_name: "Lakshmi Office" },
        {
          ...RUN,
          id: id("f0"),
          status: "undone",
          committed_by_name: "Lakshmi Office",
          undone_by: id("b2"),
          undone_by_name: "Ravi Principal",
          undone_at: "2026-09-26T06:00:00Z",
        },
      ],
    );
    renderWithIntl(<PromotionsScreen yearId={FROM.id} />);
    const history = await screen.findByRole("table", { name: "Promotions of this year" });
    expect(within(history).getAllByText("by Lakshmi Office")).toHaveLength(2);
    expect(within(history).getByText("by Ravi Principal")).toBeInTheDocument();
  });

  it("says to add the next year first when there is no later year", async () => {
    school([READ, MANAGE]);
    stub.routes["GET /bff/api/v1/academic-years"] = () => page([FROM, OLD]);
    renderWithIntl(<PromotionsScreen yearId={FROM.id} />);
    expect(
      await screen.findByText(messages.en.academicStructure.promotions.noTargetTitle),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open school structure" })).toHaveAttribute(
      "href",
      "/en/settings/structure",
    );
  });

  it("renders in Telugu with no missing messages", async () => {
    school([READ, MANAGE]);
    stub.routes[`POST ${PROMOTIONS}:preview`] = () => Response.json(PREVIEW_PROBLEM);
    renderWithIntl(<PromotionsScreen yearId={FROM.id} />, "te");
    const t = messages.te.academicStructure.promotions;
    await userEvent.click(await screen.findByRole("button", { name: t.preview }));
    expect(await screen.findByText(t.mapTitle)).toBeInTheDocument();
  });

  it("the page refuses ids that are not UUIDs and renders the screen otherwise", async () => {
    const params = (yearId: string) => Promise.resolve({ locale: "en", yearId });
    await expect(PromotionsPage({ params: params("../me") })).rejects.toThrow("NEXT_NOT_FOUND");
    school([READ, MANAGE]);
    renderWithIntl(await PromotionsPage({ params: params(FROM.id) }));
    expect(
      await screen.findByRole("heading", { level: 1, name: "Promote students of 2026-27" }),
    ).toBeInTheDocument();
  });
});

describe("promotion helpers", () => {
  it("sends ids only, with section choices sorted and empty choices left out", () => {
    expect(
      planRequest({
        toYearId: NEXT.id,
        heldBack: [{ id: BALA.id, label: "Bala Test" }],
        sectionMap: {
          [`${S6B.id}|${CLASS_7.id}`]: T7C.id,
          [`${S6A.id}|${CLASS_7.id}`]: "",
        },
      }),
    ).toEqual({
      to_academic_year_id: NEXT.id,
      held_back_student_ids: [BALA.id],
      section_map: [{ from_section_id: S6B.id, to_section_id: T7C.id }],
    });
  });

  it("offers only later years in use, earliest first", () => {
    expect(targetYears([GONE, LATER, NEXT, FROM, OLD], FROM).map((row) => row.label)).toEqual([
      "2027-28",
      "2028-29",
    ]);
  });

  it("turns API field codes into this feature's messages", () => {
    const err = (field: string, code: string) =>
      new ApiError(422, "validation_error", {
        code: "validation_error",
        errors: [{ field, code, message_key: `errors.${code}` }],
      });
    const codeOf = (error: unknown) => (error as ApiError).code;
    expect(codeOf(promotionError(err("to_academic_year_id", "same_year")))).toBe(
      "promotion_target_not_later",
    );
    expect(codeOf(promotionError(err("to_academic_year_id", "not_found")))).toBe(
      "promotion_target_not_found",
    );
    expect(codeOf(promotionError(err("section_map.0.to_section_id", "not_found")))).toBe(
      "promotion_section_map_invalid",
    );
    expect(codeOf(promotionError(err("section_map.1", "duplicate")))).toBe(
      "promotion_section_map_invalid",
    );
    expect(codeOf(promotionError(err("x", "something_else")))).toBe("validation_error");
    const conflict = new ApiError(409, "nothing_to_promote");
    expect(promotionError(conflict)).toBe(conflict);
  });
});

describe("promotions menu entry (FR-TEN-011, US-202 AC2)", () => {
  it("lists the years, the current one first, each linking to its promotion (ids only)", async () => {
    school([READ, MANAGE]);
    stub.routes["GET /bff/api/v1/academic-years"] = () => page([NEXT, FROM, OLD]);
    renderWithIntl(<PromotionsIndexScreen />);
    const links = await screen.findAllByRole("link", { name: /Promote students/ });
    expect(links.map((link) => link.getAttribute("href"))).toEqual([
      `/en/settings/structure/years/${FROM.id}/promotions`,
      `/en/settings/structure/years/${NEXT.id}/promotions`,
      `/en/settings/structure/years/${OLD.id}/promotions`,
    ]);
    expect(links[0]).toHaveAccessibleName("Promote students (academic year 2026-27)");
  });

  it("without tenant.structure.manage it explains instead", async () => {
    school([READ]);
    renderWithIntl(<PromotionsIndexScreen />, "te");
    expect(
      await screen.findByText(messages.te.academicStructure.promotions.noAccessTitle),
    ).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Promote/ })).toBeNull();
  });
});
