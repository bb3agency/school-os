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
let stub: BffStub;

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

beforeEach(() => {
  stub = installBffStub("staff");
  stub.routes["GET /bff/api/v1/academic-years"] = () => page([YEAR]);
  stub.routes["GET /bff/api/v1/classes"] = () => page([CLASS]);
  stub.routes["GET /bff/api/v1/sections"] = () =>
    page([SECTION, { ...SECTION, id: SECTION_B, name: "B" }]);
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
    expect(screen.queryByRole("button", { name: sm.edit.enrolOpen })).toBeNull();
    expect(screen.queryByRole("button", { name: sm.guardians.add })).toBeNull();
    expect(screen.queryByRole("button", { name: new RegExp(`^${sm.guardians.edit}`) })).toBeNull();
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

describe("US-301 / FR-STU-002: values by source from GET /students/{id}/values", () => {
  it("lines up each source's current value and marks differences from the register", async () => {
    renderDetail();
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
});
