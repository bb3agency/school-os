import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { installBffStub, page, problem, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { ALL_RECORD_PERMISSIONS, ID } from "@/test/records-fixtures";
import { messages, renderWithIntl } from "@/test/render";
import {
  CreateStudentForm,
  createBody,
  createStudentSchema,
  existingRecordOf,
} from "./CreateStudent";
import { permissionsFrom } from "./me";
import { cleanFilters, penDigits } from "./StudentList";
import { ApiError } from "@/lib/bff/query";

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => "/students/new",
    useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
    useSearchParams: () => new URLSearchParams(),
    useParams: () => ({ locale: "en" }),
  };
});

/**
 * UDISE+ PEN and the transfer-in guard on the web (ADR-0039; FR-STU-017..019, US-1904).
 * Synthetic data only.
 */
const sm = messages.en.students;
let stub: BffStub;

beforeEach(() => {
  stub = installBffStub("staff");
  stub.routes["GET /bff/api/v1/academic-years"] = () => page([]);
  stub.routes["GET /bff/api/v1/classes"] = () => page([]);
  stub.routes["GET /bff/api/v1/sections"] = () => page([]);
});
afterEach(() => {
  uninstallBffStub();
});

const BASE = {
  source: "admission_register",
  admission_no: "2026/0101",
  full_name: "Synthetica Transfer",
  dob: "",
  gender: "",
  father_name: "",
  mother_name: "",
  section_id: "",
  roll_no: "",
  status: "active",
};

describe("FR-STU-017 PEN format", () => {
  it("keeps 11 digits and drops spaces and hyphens", () => {
    expect(penDigits("2134 5678-901")).toBe("21345678901");
    expect(penDigits("2134567890")).toBeNull();
    expect(penDigits("213456789012")).toBeNull();
  });

  it("search sends udise_pen only when it is 11 digits", () => {
    expect(cleanFilters({ pen: "2134 5678 901" })).toEqual({ udise_pen: "21345678901" });
    expect(cleanFilters({ pen: "12" })).toEqual({});
  });
});

describe("FR-STU-018 transfer-in by PEN", () => {
  it("a transfer-in needs the PEN, sent from its own source", () => {
    const missing = createStudentSchema.safeParse({ ...BASE, admission_kind: "transfer_in" });
    expect(missing.success).toBe(false);
    const parsed = createStudentSchema.parse({
      ...BASE,
      admission_kind: "transfer_in",
      udise_pen: "2134 5678 901",
      pen_source: "tc_incoming",
    });
    expect(createBody(parsed)).toMatchObject({
      admission_kind: "transfer_in",
      values: expect.arrayContaining([
        { attribute_key: "udise_pen", source: "tc_incoming", value: "21345678901" },
      ]),
    });
  });

  it("reads the existing record from a national_id_in_use error", () => {
    // The problem's field errors may carry `student_id` (FR-STU-018) beyond the base shape.
    const clash = {
      field: "values.1.value",
      code: "national_id_in_use",
      message_key: "errors.udise_pen_in_use",
      student_id: ID.student,
    };
    const failure = new ApiError(422, "validation_error", {
      type: "about:blank",
      title: "Validation failed",
      status: 422,
      code: "validation_error",
      errors: [clash],
    });
    expect(existingRecordOf(failure)).toBe(ID.student);
    expect(existingRecordOf(new Error("x"))).toBeNull();
  });

  it("checks a PEN in the body and links to the record already holding it", async () => {
    stub.routes["POST /bff/api/v1/students/national-id-check"] = () =>
      Response.json({
        matches: [
          {
            identifier: "udise_pen",
            student_id: ID.student,
            display_name: "Synthetica Existing",
            admission_no: "A-9",
            class_section: "IX-A",
            status: "active",
          },
        ],
        udise_action: "open_existing_record",
      });
    const user = userEvent.setup();
    renderWithIntl(<CreateStudentForm permissions={permissionsFrom(ALL_RECORD_PERMISSIONS)} />);
    await user.type(screen.getByLabelText(sm.create.pen), "2134 5678 901");
    await user.click(screen.getByRole("button", { name: sm.penCheck.check }));
    expect(
      await screen.findByText(sm.penCheck.actions.open_existing_record.title),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Synthetica Existing/ })).toHaveAttribute(
      "href",
      expect.stringContaining(`/students/${ID.student}`),
    );
    const [call] = stub.callsTo("POST /bff/api/v1/students/national-id-check");
    expect(JSON.parse(call?.body ?? "{}")).toEqual({ udise_pen: "21345678901" });
    expect(call?.url.search).toBe("");
  });

  it("shows a link to the existing record when the API refuses a duplicate PEN", async () => {
    stub.routes["POST /bff/api/v1/students"] = () =>
      problem(422, "validation_error", {
        errors: [
          {
            field: "values.1.value",
            code: "national_id_in_use",
            message_key: "errors.udise_pen_in_use",
            student_id: ID.student,
          },
        ],
      });
    const user = userEvent.setup();
    renderWithIntl(<CreateStudentForm permissions={permissionsFrom(ALL_RECORD_PERMISSIONS)} />);
    await user.type(screen.getByLabelText(sm.create.fullName), "Synthetica Twice");
    await user.type(screen.getByLabelText(sm.create.pen), "21345678901");
    await user.click(screen.getByRole("button", { name: sm.create.submit }));
    await waitFor(() => expect(screen.getByText(sm.create.clashTitle)).toBeInTheDocument());
    expect(screen.getByRole("link", { name: sm.create.openExisting })).toHaveAttribute(
      "href",
      expect.stringContaining(`/students/${ID.student}`),
    );
    expect(await screen.findByText(messages.en.errors.field.udise_pen_in_use)).toBeInTheDocument();
  });
});
