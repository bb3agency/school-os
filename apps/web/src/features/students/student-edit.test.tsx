import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import type { components } from "@schoolos/api-client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { installBffStub, page, problem, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import {
  ALL_RECORD_PERMISSIONS,
  ATTRIBUTES,
  CLASS,
  ID,
  NOW,
  SECTION,
  YEAR,
  fakeAadhaar,
  student,
} from "@/test/records-fixtures";
import { messages, renderWithIntl } from "@/test/render";
import { permissionsFrom } from "./me";
import { StudentDetailView } from "./StudentDetail";
import { endEnrolmentSchema, enrolmentPatchBody, enrolmentPatchSchema } from "./Enrolments";
import { enrolmentSchema, guardianCreateBody, guardianPatchBody } from "./StudentEdit";

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => `/en/students/${ID.student}`,
    useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
    useSearchParams: () => new URLSearchParams(),
    useParams: () => ({ locale: "en" }),
  };
});

/**
 * Editing a student's record (US-301, FR-STU-001..008, FR-STU-012, FR-CR-001, invariants 4
 * and 6): status, class, guardians and non-identity values directly; identity fields only
 * through a correction request. Synthetic data only (invariant 11).
 */

type Schemas = components["schemas"];
const sm = messages.en.students;
const SECTION_B = "0192f3a4-0000-7000-8000-00000000c404";
const OLD_YEAR = "0192f3a4-0000-7000-8000-00000000c405";
const OLD_SECTION = "0192f3a4-0000-7000-8000-00000000c406";
const CLASS_8 = "0192f3a4-0000-7000-8000-00000000c407";
const ENROLMENT = "0192f3a4-0000-7000-8000-00000000cb01";
const OLD_ENROLMENT = "0192f3a4-0000-7000-8000-00000000cb02";
let stub: BffStub;

function enrolment(extra: Partial<Schemas["EnrollmentOut"]> = {}): Schemas["EnrollmentOut"] {
  return {
    id: ENROLMENT,
    student_id: ID.student,
    section_id: ID.section,
    academic_year_id: ID.year,
    roll_no: "12",
    status: "active",
    started_on: "2026-06-01",
    ended_on: null,
    version: 6,
    ...extra,
  };
}

function enrolments(): Schemas["EnrollmentOut"][] {
  return [
    enrolment(),
    enrolment({
      id: OLD_ENROLMENT,
      section_id: OLD_SECTION,
      academic_year_id: OLD_YEAR,
      roll_no: "31",
      status: "completed",
      started_on: "2025-06-02",
      ended_on: "2026-04-30",
      version: 2,
    }),
  ];
}

function guardian(extra: Partial<Schemas["GuardianOut"]> = {}): Schemas["GuardianOut"] {
  return {
    id: ID.guardian,
    full_name: "Ramana K.",
    relationship: "father",
    is_primary: true,
    has_phone: true,
    has_address: false,
    phone: "••••",
    address: null,
    masked: true,
    version: 2,
    ...extra,
  };
}

function history(): Schemas["ValueOut"][] {
  const base = student().values;
  const old: Schemas["ValueOut"] = {
    ...(base.full_name?.[1] as Schemas["ValueOut"]),
    id: "0192f3a4-0000-7000-8000-00000000c204",
    value: "Venkata Sai",
    current: false,
    superseded_by: ID.value2,
    recorded_at: "2025-06-01T05:30:00Z",
  };
  const board: Schemas["ValueOut"] = {
    ...(base.full_name?.[0] as Schemas["ValueOut"]),
    id: "0192f3a4-0000-7000-8000-00000000c205",
    source: "board_registration",
    value: "Venkata  Sai K. ",
    recorded_at: NOW,
  };
  return [...(base.full_name ?? []), board, old, ...(base.aadhaar_last4 ?? [])];
}

const ready = <T,>(data: T) => ({ status: "ready" as const, data });

function renderDetail(
  permissions: readonly string[] = ALL_RECORD_PERMISSIONS,
  guardians: Schemas["GuardianOut"][] = [guardian()],
) {
  return renderWithIntl(
    <StudentDetailView
      student={ready(student())}
      attributes={ready(ATTRIBUTES)}
      guardians={ready(guardians)}
      permissions={permissionsFrom(permissions)}
    />,
  );
}

/** The profile is tabbed: parents, class history and values by source are later tabs. */
function openTab(name: string) {
  fireEvent.click(screen.getByRole("tab", { name }));
}

beforeEach(() => {
  stub = installBffStub("staff");
  stub.routes["GET /bff/api/v1/academic-years"] = () =>
    page([YEAR, { ...YEAR, id: OLD_YEAR, label: "2025-26", is_current: false }]);
  stub.routes["GET /bff/api/v1/classes"] = () =>
    page([CLASS, { ...CLASS, id: CLASS_8, code: "VIII", display_en: "Class 8", sort_order: 8 }]);
  stub.routes["GET /bff/api/v1/sections"] = () =>
    page([
      SECTION,
      { ...SECTION, id: SECTION_B, name: "B" },
      { ...SECTION, id: OLD_SECTION, academic_year_id: OLD_YEAR, class_id: CLASS_8 },
    ]);
  stub.routes[`GET /bff/api/v1/students/${ID.student}/enrollments`] = () =>
    Response.json(enrolments());
  stub.routes["GET /bff/api/v1/attributes"] = () => Response.json(ATTRIBUTES);
  stub.routes[`GET /bff/api/v1/students/${ID.student}/values`] = () => Response.json(history());
});
afterEach(uninstallBffStub);

describe("invariant 6 / FR-CR-001: identity fields change only through a correction request", () => {
  it("links identity fields to a new correction request and edits other fields directly", async () => {
    const user = userEvent.setup();
    renderDetail();
    const table = screen.getByRole("table", { name: sm.detail.valuesTable });
    const request = within(table).getByRole("link", {
      name: `${sm.edit.requestChange}: Full name`,
    });
    expect(request).toHaveAttribute(
      "href",
      `/en/change-requests/new?student_id=${ID.student}&attribute_key=full_name`,
    );
    // Identity rows are never edited in place: no direct "Change" for them.
    expect(
      within(table).queryByRole("button", { name: `${sm.edit.change}: Full name` }),
    ).toBeNull();

    // A non-identity field opens the record dialog with that field already chosen.
    await user.click(
      within(table).getByRole("button", { name: `${sm.edit.change}: Aadhaar (last 4 digits)` }),
    );
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByLabelText(sm.record.field)).toHaveValue("aadhaar_last4");
  });

  it("offers to reload when a value is recorded on top of a newer version (412)", async () => {
    stub.routes[`POST /bff/api/v1/students/${ID.student}/values`] = () =>
      problem(412, "precondition_failed");
    const user = userEvent.setup();
    renderDetail();
    await user.click(
      screen.getByRole("button", { name: `${sm.edit.change}: Aadhaar (last 4 digits)` }),
    );
    const dialog = screen.getByRole("dialog");
    await user.selectOptions(within(dialog).getByLabelText(sm.record.source), "aadhaar_as_printed");
    await user.type(within(dialog).getByLabelText(sm.record.value), "4821");
    await user.click(within(dialog).getByRole("button", { name: sm.record.submit }));
    expect(
      await within(dialog).findByText(sm.errors.precondition_failed.title),
    ).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: sm.edit.reload })).toBeInTheDocument();
  });

  it("offers no edit action without the permissions", () => {
    renderDetail(["student.read_basic"]);
    expect(screen.queryByRole("link", { name: new RegExp(sm.edit.requestChange) })).toBeNull();
    expect(screen.queryByRole("button", { name: new RegExp(`^${sm.edit.change}`) })).toBeNull();
    expect(screen.queryByRole("button", { name: sm.edit.statusOpen })).toBeNull();
    // Hidden tab panels count too, so these never pass only because a tab is closed.
    expect(screen.queryByRole("button", { name: sm.edit.enrolOpen, hidden: true })).toBeNull();
    expect(screen.queryByRole("button", { name: sm.guardians.add, hidden: true })).toBeNull();
    expect(
      screen.queryByRole("button", { name: new RegExp(`^${sm.guardians.edit}`), hidden: true }),
    ).toBeNull();
  });
});

describe("US-301: record status (PATCH with If-Match)", () => {
  it("sends the student's ETag and the new status", async () => {
    stub.routes[`PATCH /bff/api/v1/students/${ID.student}`] = () =>
      Response.json(student({ status: "left", version: 5 }));
    const user = userEvent.setup();
    renderDetail();
    await user.click(screen.getByRole("button", { name: sm.edit.statusOpen }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByLabelText(sm.edit.status)).toHaveValue("active");
    await user.selectOptions(within(dialog).getByLabelText(sm.edit.status), "left");
    await user.click(within(dialog).getByRole("button", { name: sm.edit.statusSubmit }));
    await waitFor(() =>
      expect(stub.callsTo(`PATCH /bff/api/v1/students/${ID.student}`)).toHaveLength(1),
    );
    const call = stub.callsTo(`PATCH /bff/api/v1/students/${ID.student}`)[0];
    expect(call?.headers.get("if-match")).toBe('W/"4"');
    expect(JSON.parse(call?.body ?? "{}")).toEqual({ status: "left" });
  });

  it("explains a version conflict (412) and offers to reload", async () => {
    stub.routes[`PATCH /bff/api/v1/students/${ID.student}`] = () =>
      problem(412, "precondition_failed");
    const user = userEvent.setup();
    renderDetail();
    await user.click(screen.getByRole("button", { name: sm.edit.statusOpen }));
    const dialog = screen.getByRole("dialog");
    await user.selectOptions(within(dialog).getByLabelText(sm.edit.status), "left");
    await user.click(within(dialog).getByRole("button", { name: sm.edit.statusSubmit }));
    expect(
      await within(dialog).findByText(sm.errors.precondition_failed.title),
    ).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: sm.edit.reload }));
    expect(await within(dialog).findByText(sm.edit.reloaded)).toBeInTheDocument();
  });
});

describe("US-301 / FR-STU-005: class and section", () => {
  it("reads the start date as DD/MM/YYYY and refuses impossible ones", () => {
    expect(
      enrolmentSchema.parse({ section_id: SECTION_B, roll_no: " 7 ", started_on: "01/07/2026" }),
    ).toEqual({ section_id: SECTION_B, roll_no: "7", started_on: "2026-07-01" });
    expect(enrolmentSchema.parse({ section_id: SECTION_B, roll_no: "", started_on: "" })).toEqual({
      section_id: SECTION_B,
      roll_no: null,
      started_on: null,
    });
    expect(
      enrolmentSchema.safeParse({ section_id: SECTION_B, roll_no: "", started_on: "31/02/2026" })
        .success,
    ).toBe(false);
    expect(enrolmentSchema.safeParse({ section_id: "", roll_no: "", started_on: "" }).success).toBe(
      false,
    );
  });

  it("moves the student to another section with an Idempotency-Key", async () => {
    stub.routes[`POST /bff/api/v1/students/${ID.student}/enrollments`] = () =>
      Response.json(
        {
          id: "0192f3a4-0000-7000-8000-00000000cb01",
          student_id: ID.student,
          section_id: SECTION_B,
          academic_year_id: ID.year,
          roll_no: "7",
          status: "active",
          started_on: "2026-07-01",
          ended_on: null,
          version: 1,
        },
        { status: 201 },
      );
    const user = userEvent.setup();
    renderDetail();
    openTab(sm.detail.tabEnrolments);
    await user.click(screen.getByRole("button", { name: sm.edit.enrolOpen }));
    const dialog = screen.getByRole("dialog");
    const select = within(dialog).getByLabelText(sm.edit.section);
    await waitFor(() => expect(within(select).getAllByRole("option").length).toBeGreaterThan(2));
    await user.selectOptions(select, SECTION_B);
    await user.type(within(dialog).getByLabelText(sm.edit.rollNo), "7");
    await user.type(within(dialog).getByLabelText(sm.edit.startedOn), "01/07/2026");
    await user.click(within(dialog).getByRole("button", { name: sm.edit.enrolSubmit }));
    await waitFor(() =>
      expect(stub.callsTo(`POST /bff/api/v1/students/${ID.student}/enrollments`)).toHaveLength(1),
    );
    const call = stub.callsTo(`POST /bff/api/v1/students/${ID.student}/enrollments`)[0];
    expect(call?.headers.get("idempotency-key")).toMatch(/^[0-9a-f-]{36}$/);
    expect(JSON.parse(call?.body ?? "{}")).toEqual({
      section_id: SECTION_B,
      roll_no: "7",
      started_on: "2026-07-01",
    });
  });

  it("explains 409 already_enrolled", async () => {
    stub.routes[`POST /bff/api/v1/students/${ID.student}/enrollments`] = () =>
      problem(409, "already_enrolled");
    const user = userEvent.setup();
    renderDetail();
    openTab(sm.detail.tabEnrolments);
    await user.click(screen.getByRole("button", { name: sm.edit.enrolOpen }));
    const dialog = screen.getByRole("dialog");
    const select = within(dialog).getByLabelText(sm.edit.section);
    await waitFor(() => expect(within(select).getAllByRole("option").length).toBeGreaterThan(2));
    await user.selectOptions(select, ID.section);
    await user.click(within(dialog).getByRole("button", { name: sm.edit.enrolSubmit }));
    expect(await within(dialog).findByText(sm.errors.already_enrolled.title)).toBeInTheDocument();
  });
});

describe("US-301 / FR-STU-004: parents and guardians", () => {
  it("builds bodies that only carry what the clerk changed", () => {
    expect(
      guardianCreateBody({
        full_name: " Lakshmi K. ",
        relationship: "mother",
        phone: "98765 43210",
        address: "",
        is_primary: false,
        clear_phone: false,
        clear_address: false,
      }),
    ).toEqual({
      full_name: "Lakshmi K.",
      relationship: "mother",
      phone: "98765 43210",
      is_primary: false,
    });
    const current = guardian();
    expect(
      guardianPatchBody(current, {
        full_name: "Ramana K.",
        relationship: "father",
        phone: "",
        address: "",
        is_primary: true,
        clear_phone: true,
        clear_address: false,
      }),
    ).toEqual({ phone: null });
    expect(
      guardianPatchBody(current, {
        full_name: "Ramana Kumar",
        relationship: "guardian",
        phone: "9876543210",
        address: "",
        is_primary: false,
        clear_phone: false,
        clear_address: false,
      }),
    ).toEqual({
      full_name: "Ramana Kumar",
      relationship: "guardian",
      phone: "9876543210",
      is_primary: false,
    });
  });

  it("adds a guardian, refusing a pasted full Aadhaar number", async () => {
    stub.routes[`POST /bff/api/v1/students/${ID.student}/guardians`] = () =>
      Response.json(guardian({ id: "0192f3a4-0000-7000-8000-00000000c302" }), { status: 201 });
    const user = userEvent.setup();
    renderDetail();
    openTab(sm.detail.tabGuardians);
    await user.click(screen.getByRole("button", { name: sm.guardians.add }));
    const dialog = screen.getByRole("dialog");
    await user.type(within(dialog).getByLabelText(sm.guardians.fullName), "Lakshmi K.");
    await user.selectOptions(
      within(dialog).getByLabelText(sm.guardians.relationshipLabel),
      "mother",
    );
    const phone = within(dialog).getByLabelText(sm.guardians.phone);
    phone.focus();
    await user.paste(fakeAadhaar());
    expect(phone).toHaveValue("");
    expect((await within(dialog).findAllByText(sm.aadhaarNotAllowed)).length).toBeGreaterThan(0);
    await user.type(phone, "98765 43210");
    await user.click(within(dialog).getByRole("button", { name: sm.guardians.addSubmit }));
    await waitFor(() =>
      expect(stub.callsTo(`POST /bff/api/v1/students/${ID.student}/guardians`)).toHaveLength(1),
    );
    const call = stub.callsTo(`POST /bff/api/v1/students/${ID.student}/guardians`)[0];
    expect(call?.headers.get("idempotency-key")).toMatch(/^[0-9a-f-]{36}$/);
    expect(JSON.parse(call?.body ?? "{}")).toEqual({
      full_name: "Lakshmi K.",
      relationship: "mother",
      phone: "98765 43210",
      is_primary: false,
    });
  });

  it("edits a guardian with the guardian's ETag and never pre-fills restricted contact data", async () => {
    stub.routes[`PATCH /bff/api/v1/students/${ID.student}/guardians/${ID.guardian}`] = () =>
      Response.json(guardian({ version: 3, has_phone: false, phone: null }));
    const user = userEvent.setup();
    renderDetail();
    openTab(sm.detail.tabGuardians);
    await user.click(screen.getByRole("button", { name: `${sm.guardians.edit}: Ramana K.` }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByLabelText(sm.guardians.fullName)).toHaveValue("Ramana K.");
    expect(within(dialog).getByLabelText(sm.guardians.phone)).toHaveValue("");
    await user.click(within(dialog).getByLabelText(sm.guardians.clearPhone));
    await user.click(within(dialog).getByRole("button", { name: sm.guardians.editSubmit }));
    await waitFor(() =>
      expect(
        stub.callsTo(`PATCH /bff/api/v1/students/${ID.student}/guardians/${ID.guardian}`),
      ).toHaveLength(1),
    );
    const call = stub.callsTo(
      `PATCH /bff/api/v1/students/${ID.student}/guardians/${ID.guardian}`,
    )[0];
    expect(call?.headers.get("if-match")).toBe('W/"2"');
    expect(JSON.parse(call?.body ?? "{}")).toEqual({ phone: null });
  });

  it("explains a guardian version conflict (412)", async () => {
    stub.routes[`PATCH /bff/api/v1/students/${ID.student}/guardians/${ID.guardian}`] = () =>
      problem(412, "precondition_failed");
    const user = userEvent.setup();
    renderDetail();
    openTab(sm.detail.tabGuardians);
    await user.click(screen.getByRole("button", { name: `${sm.guardians.edit}: Ramana K.` }));
    const dialog = screen.getByRole("dialog");
    await user.clear(within(dialog).getByLabelText(sm.guardians.fullName));
    await user.type(within(dialog).getByLabelText(sm.guardians.fullName), "Ramana Kumar");
    await user.click(within(dialog).getByRole("button", { name: sm.guardians.editSubmit }));
    expect(
      await within(dialog).findByText(sm.errors.precondition_failed.title),
    ).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: sm.edit.reload })).toBeInTheDocument();
  });
});

describe("US-301 / FR-STU-005: enrolments (list, correct, end)", () => {
  const PATCH = `PATCH /bff/api/v1/students/${ID.student}/enrollments/${ENROLMENT}`;
  const END = `POST /bff/api/v1/students/${ID.student}/enrollments/${ENROLMENT}/end`;

  async function enrolmentRow(year: string) {
    const table = await screen.findByRole("table", { name: sm.enrolments.table });
    await within(table).findByText("Class 8 · A");
    const row = within(table)
      .getAllByRole("row")
      .find((item) => (item.textContent ?? "").includes(year));
    if (!row) throw new Error(`no enrolment row for ${year}`);
    return row;
  }

  it("lists every enrolment, newest first, with its year, class, roll number and status", async () => {
    renderDetail();
    openTab(sm.detail.tabEnrolments);
    const current = await enrolmentRow("2026-27");
    expect(within(current).getByText("Class 9 · A")).toBeInTheDocument();
    expect(within(current).getByText("12")).toBeInTheDocument();
    expect(within(current).getByText(sm.enrolments.status.active)).toBeInTheDocument();
    const old = await enrolmentRow("2025-26");
    expect(within(old).getByText(sm.enrolments.status.completed)).toBeInTheDocument();
    // A closed enrolment can still have its roll number corrected, but cannot end again.
    expect(within(old).getByRole("button", { name: /^Correct/ })).toBeInTheDocument();
    expect(within(old).queryByRole("button", { name: /^End/ })).toBeNull();
  });

  it("builds a PATCH body with only what changed (an empty roll number clears it)", () => {
    const current = enrolment();
    expect(
      enrolmentPatchBody(current, enrolmentPatchSchema.parse({ roll_no: " 14 ", section_id: "" })),
    ).toEqual({ roll_no: "14" });
    expect(
      enrolmentPatchBody(
        current,
        enrolmentPatchSchema.parse({ roll_no: "", section_id: SECTION_B }),
      ),
    ).toEqual({ roll_no: null, section_id: SECTION_B });
    expect(
      enrolmentPatchBody(
        current,
        enrolmentPatchSchema.parse({ roll_no: "12", section_id: ID.section }),
      ),
    ).toEqual({});
    expect(enrolmentPatchSchema.safeParse({ roll_no: fakeAadhaar(), section_id: "" }).success).toBe(
      false,
    );
    expect(endEnrolmentSchema.parse({ status: "transferred", ended_on: "15/07/2026" })).toEqual({
      status: "transferred",
      ended_on: "2026-07-15",
    });
    expect(endEnrolmentSchema.parse({ status: "completed", ended_on: "" })).toEqual({
      status: "completed",
      ended_on: null,
    });
    expect(endEnrolmentSchema.safeParse({ status: "left", ended_on: "" }).success).toBe(false);
  });

  it("corrects the roll number and moves to another section of the same class, with If-Match", async () => {
    stub.routes[PATCH] = () => Response.json(enrolment({ roll_no: "14", section_id: SECTION_B }));
    const user = userEvent.setup();
    renderDetail();
    openTab(sm.detail.tabEnrolments);
    const row = await enrolmentRow("2026-27");
    await user.click(within(row).getByRole("button", { name: /^Correct/ }));
    const dialog = screen.getByRole("dialog");
    const section = within(dialog).getByLabelText(sm.enrolments.section);
    // Only sections of the same class and academic year are offered.
    expect(
      within(section)
        .getAllByRole("option")
        .map((option) => option.textContent),
    ).toEqual(["Class 9 · A", "Class 9 · B"]);
    expect(section).toHaveValue(ID.section);
    await user.selectOptions(section, SECTION_B);
    const roll = within(dialog).getByLabelText(sm.enrolments.rollNo);
    expect(roll).toHaveValue("12");
    await user.clear(roll);
    await user.type(roll, "14");
    await user.click(within(dialog).getByRole("button", { name: sm.enrolments.editSubmit }));
    await waitFor(() => expect(stub.callsTo(PATCH)).toHaveLength(1));
    const call = stub.callsTo(PATCH)[0];
    expect(call?.headers.get("if-match")).toBe('W/"6"');
    expect(JSON.parse(call?.body ?? "{}")).toEqual({ roll_no: "14", section_id: SECTION_B });
  });

  it("explains a stale enrolment (412) with a reload, and a section of another class (422)", async () => {
    let attempts = 0;
    stub.routes[PATCH] = () => {
      attempts += 1;
      return attempts === 1
        ? problem(412, "precondition_failed")
        : problem(422, "validation_error", {
            errors: [
              {
                field: "section_id",
                code: "different_class_or_year",
                message_key: "errors.different_class_or_year",
              },
            ],
          });
    };
    const user = userEvent.setup();
    renderDetail();
    openTab(sm.detail.tabEnrolments);
    const row = await enrolmentRow("2026-27");
    await user.click(within(row).getByRole("button", { name: /^Correct/ }));
    const dialog = screen.getByRole("dialog");
    await user.selectOptions(within(dialog).getByLabelText(sm.enrolments.section), SECTION_B);
    await user.click(within(dialog).getByRole("button", { name: sm.enrolments.editSubmit }));
    expect(
      await within(dialog).findByText(sm.errors.precondition_failed.title),
    ).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: sm.edit.reload }));
    expect(await within(dialog).findByText(sm.edit.reloaded)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: sm.enrolments.editSubmit }));
    expect(
      await within(dialog).findByText(messages.en.errors.field.different_class_or_year),
    ).toBeInTheDocument();
  });

  it("ends an enrolment only after the confirm dialog, as a transfer, with If-Match", async () => {
    stub.routes[END] = () =>
      Response.json(enrolment({ status: "transferred", ended_on: "2026-07-15", version: 7 }));
    const user = userEvent.setup();
    renderDetail();
    openTab(sm.detail.tabEnrolments);
    const row = await enrolmentRow("2026-27");
    await user.click(within(row).getByRole("button", { name: /^End/ }));
    let dialog = screen.getByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: messages.en.common.cancel }));
    expect(stub.callsTo(END)).toHaveLength(0);
    await user.click(within(row).getByRole("button", { name: /^End/ }));
    dialog = screen.getByRole("dialog");
    await user.click(within(dialog).getByLabelText(sm.enrolments.endStatus.transferred));
    await user.type(within(dialog).getByLabelText(sm.enrolments.endedOn), "15/07/2026");
    await user.click(within(dialog).getByRole("button", { name: sm.enrolments.endSubmit }));
    await waitFor(() => expect(stub.callsTo(END)).toHaveLength(1));
    const call = stub.callsTo(END)[0];
    expect(call?.headers.get("if-match")).toBe('W/"6"');
    expect(JSON.parse(call?.body ?? "{}")).toEqual({
      status: "transferred",
      ended_on: "2026-07-15",
    });
  });

  it("explains 409 enrollment_not_active and offers to reload", async () => {
    stub.routes[END] = () => problem(409, "enrollment_not_active");
    const user = userEvent.setup();
    renderDetail();
    openTab(sm.detail.tabEnrolments);
    const row = await enrolmentRow("2026-27");
    await user.click(within(row).getByRole("button", { name: /^End/ }));
    const dialog = screen.getByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: sm.enrolments.endSubmit }));
    expect(
      await within(dialog).findByText(sm.errors.enrollment_not_active.title),
    ).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: sm.edit.reload })).toBeInTheDocument();
  });

  it("offers no enrolment changes without student.update_nonidentity", async () => {
    renderDetail(["student.read_basic"]);
    openTab(sm.detail.tabEnrolments);
    const row = await enrolmentRow("2026-27");
    expect(within(row).queryByRole("button")).toBeNull();
  });
});

describe("US-301 / FR-STU-004: remove a parent or guardian", () => {
  const DELETE = `DELETE /bff/api/v1/students/${ID.student}/guardians/${ID.guardian}`;

  it("removes only after the confirm dialog, says what is deleted, and sends If-Match", async () => {
    stub.routes[DELETE] = () => new Response(null, { status: 204 });
    const user = userEvent.setup();
    renderDetail();
    openTab(sm.detail.tabGuardians);
    await user.click(screen.getByRole("button", { name: `${sm.guardians.remove}: Ramana K.` }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText(sm.guardians.removeBody)).toBeInTheDocument();
    expect(stub.callsTo(DELETE)).toHaveLength(0);
    await user.click(within(dialog).getByRole("button", { name: sm.guardians.removeSubmit }));
    await waitFor(() => expect(stub.callsTo(DELETE)).toHaveLength(1));
    expect(stub.callsTo(DELETE)[0]?.headers.get("if-match")).toBe('W/"2"');
  });

  it("explains a guardian changed meanwhile (412) and offers to reload", async () => {
    stub.routes[DELETE] = () => problem(412, "precondition_failed");
    const user = userEvent.setup();
    renderDetail();
    openTab(sm.detail.tabGuardians);
    await user.click(screen.getByRole("button", { name: `${sm.guardians.remove}: Ramana K.` }));
    const dialog = screen.getByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: sm.guardians.removeSubmit }));
    expect(
      await within(dialog).findByText(sm.errors.precondition_failed.title),
    ).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: sm.edit.reload })).toBeInTheDocument();
  });

  it("offers no remove without student.update_nonidentity", () => {
    renderDetail(["student.read_basic"]);
    openTab(sm.detail.tabGuardians);
    expect(
      screen.queryByRole("button", { name: new RegExp(`^${sm.guardians.remove}`) }),
    ).toBeNull();
  });
});

describe("US-301 / FR-STU-002: values by source from GET /students/{id}/values", () => {
  it("lines up each source's current value and marks differences from the register", async () => {
    renderDetail();
    openTab(sm.detail.tabBySource);
    const table = await screen.findByRole("table", { name: sm.bySource.table });
    await within(table).findByText("Venkatasai Kumar");
    const row = within(table).getByRole("row", { name: /Full name/ });
    expect(within(row).getAllByText(/Venkata\s+Sai K\./)).toHaveLength(2);
    // UDISE+ differs; the board value differs only in spacing, so it matches.
    expect(within(row).getAllByText(sm.bySource.differs)).toHaveLength(1);
    // Superseded history is not in the comparison.
    expect(within(table).queryByText("Venkata Sai")).toBeNull();
    // Restricted values stay masked; no digits of an Aadhaar number are ever rendered.
    expect(within(table).queryByText(/\d{4}/)).toBeNull();
  });

  it("shows a field's full history, oldest values marked as replaced", async () => {
    const user = userEvent.setup();
    renderDetail();
    openTab(sm.detail.tabBySource);
    const table = await screen.findByRole("table", { name: sm.bySource.table });
    await within(table).findByText("Venkatasai Kumar");
    await user.click(
      within(table).getByRole("button", { name: `${sm.bySource.history}: Full name` }),
    );
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("Venkata Sai")).toBeInTheDocument();
    expect(within(dialog).getAllByText(sm.bySource.replaced).length).toBe(1);
    fireEvent.keyDown(dialog, { key: "Escape" });
  });

  it("marks a value that matches the register apart from spacing as matching (FR-STU-002)", async () => {
    renderDetail();
    openTab(sm.detail.tabBySource);
    const table = await screen.findByRole("table", { name: sm.bySource.table });
    await within(table).findByText("Venkatasai Kumar");
    const row = within(table).getByRole("row", { name: /Full name/ });
    expect(within(row).getAllByText(sm.bySource.matches)).toHaveLength(1);
  });
});

describe("US-301: the History tab lists every recorded value, newest first", () => {
  it("shows replaced values as a timeline without unmasking anything", async () => {
    renderDetail();
    openTab(sm.detail.tabHistory);
    const list = await screen.findByRole("list", { name: sm.detail.historyTimeline });
    await within(list).findByText("Venkata Sai");
    const items = within(list).getAllByRole("listitem");
    // The oldest value (recorded in 2025) comes last and is marked as replaced.
    expect(
      within(items[items.length - 1] as HTMLElement).getByText("Venkata Sai"),
    ).toBeInTheDocument();
    expect(within(list).getAllByText(sm.bySource.replaced).length).toBeGreaterThan(0);
    expect(within(list).queryByText(/\d{4} \d{4}/)).toBeNull();
  });
});
