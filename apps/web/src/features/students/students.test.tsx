import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  CSRF,
  installBffStub,
  page,
  problem,
  uninstallBffStub,
  type BffStub,
} from "@/test/bff-stub";
import {
  ALL_RECORD_PERMISSIONS,
  ATTRIBUTES,
  CLASS,
  ID,
  SECTION,
  YEAR,
  fakeAadhaar,
  me,
  student,
  summary,
} from "@/test/records-fixtures";
import { setSchoolDateFormat } from "@/lib/date-format";
import { messages, renderWithIntl } from "@/test/render";
import { aadhaarDisplay, containsFullAadhaar, verhoeffValid } from "./aadhaar";
import { CreateStudentForm, createBody, createStudentSchema } from "./CreateStudent";
import { toIsoDate } from "./dates";
import { permissionsFrom } from "./me";
import { StudentDetailView } from "./StudentDetail";
import { PAGE_SIZE, StudentsScreen } from "./StudentList";

const push = vi.fn();
vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => "/en/students",
    useRouter: () => ({ push, replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
    useSearchParams: () => new URLSearchParams(),
    useParams: () => ({ locale: "en" }),
  };
});

/** Student screens (US-301..303, FR-STU-001..012, invariant 4). Synthetic data only. */

const sm = messages.en.students;
let stub: BffStub;

function structureRoutes() {
  stub.routes["GET /bff/api/v1/academic-years"] = () => page([YEAR]);
  stub.routes["GET /bff/api/v1/classes"] = () => page([CLASS]);
  stub.routes["GET /bff/api/v1/sections"] = () => page([SECTION]);
  stub.routes["GET /bff/api/v1/attributes"] = () => Response.json(ATTRIBUTES);
}

beforeEach(() => {
  stub = installBffStub("staff");
  push.mockReset();
  structureRoutes();
});
afterEach(uninstallBffStub);

describe("FR-STU-012 / BR-02: full Aadhaar numbers are recognised and refused", () => {
  it("checks Verhoeff like the API", () => {
    expect(verhoeffValid("2363")).toBe(true);
    expect(verhoeffValid("2364")).toBe(false);
    const number = fakeAadhaar();
    expect(containsFullAadhaar(number)).toBe(true);
    expect(
      containsFullAadhaar(`${number.slice(0, 4)} ${number.slice(4, 8)} ${number.slice(8)}`),
    ).toBe(true);
    // Wrong check digit, 11 digits, or a first digit 0/1: not an Aadhaar number.
    const wrong = `${number.slice(0, 11)}${(Number(number[11]) + 1) % 10}`;
    expect(containsFullAadhaar(wrong)).toBe(false);
    expect(containsFullAadhaar(number.slice(0, 11))).toBe(false);
    expect(containsFullAadhaar("0123 4567 8901")).toBe(false);
  });

  it("shows an Aadhaar reference only as XXXX XXXX 1234 and never widens a mask", () => {
    expect(aadhaarDisplay("1234")).toBe("XXXX XXXX 1234");
    expect(aadhaarDisplay("XXXX XXXX 1234")).toBe("XXXX XXXX 1234");
    expect(aadhaarDisplay("••••")).toBe("••••");
    expect(aadhaarDisplay(fakeAadhaar())).toBe("••••");
  });

  it("reads office dates as DD/MM/YYYY and refuses impossible ones", () => {
    expect(toIsoDate("14/03/2012")).toBe("2012-03-14");
    expect(toIsoDate("4.3.2012")).toBe("2012-03-04");
    expect(toIsoDate("2012-03-14")).toBe("2012-03-14");
    expect(toIsoDate("31/02/2012")).toBeNull();
    expect(toIsoDate("14/03/12")).toBeNull();
  });
});

describe("US-302 / FR-STU-010: find students", () => {
  it("searches without putting the name in the page URL, and respects permissions", async () => {
    stub.routes["GET /bff/api/v1/me"] = () => Response.json(me(["student.read_basic"]));
    stub.routes["POST /bff/api/v1/students/search"] = async (request) => {
      const body = (await request.json()) as { query?: string };
      return body.query ? page([summary()]) : page([]);
    };
    const user = userEvent.setup();
    const before = window.location.href;
    renderWithIntl(<StudentsScreen />);

    expect(await screen.findByText(sm.list.emptyTitle)).toBeInTheDocument();
    await user.type(screen.getByLabelText(sm.list.searchLabel), "venkat sai 9b");
    await user.click(screen.getByRole("button", { name: messages.en.common.search }));

    expect(await screen.findByRole("link", { name: "Venkata Sai K." })).toHaveAttribute(
      "href",
      `/en/students/${ID.student}`,
    );
    expect(window.location.href).toBe(before);
    // SEC-008: the name travels in the POST body, never in the request URL (access logs).
    const searches = stub.callsTo("POST /bff/api/v1/students/search");
    expect(JSON.parse(searches.at(-1)?.body ?? "{}")).toEqual({
      query: "venkat sai 9b",
      limit: PAGE_SIZE,
    });
    expect(searches.at(-1)?.headers.get("x-csrf-token")).toBe(CSRF);
    expect(stub.callsTo("GET /bff/api/v1/students")).toHaveLength(0);
    for (const call of stub.calls) {
      expect(call.url.search).not.toMatch(/venkat/i);
      expect(decodeURIComponent(call.url.href)).not.toMatch(/venkat/i);
    }
    // Without student.create / import.run the actions are not offered.
    expect(screen.queryByRole("link", { name: sm.list.add })).toBeNull();
    expect(screen.queryByRole("link", { name: sm.list.import })).toBeNull();

    // Clearing starts again and puts the keyboard back in the search box.
    await user.click(screen.getByRole("button", { name: sm.list.clear }));
    await waitFor(() => expect(screen.getByLabelText(sm.list.searchLabel)).toHaveValue(""));
    await waitFor(() => expect(screen.getByLabelText(sm.list.searchLabel)).toHaveFocus());
  });

  it("pages with the cursor in the POST body, keeping the name out of every URL", async () => {
    stub.routes["GET /bff/api/v1/me"] = () => Response.json(me(["student.read_basic"]));
    stub.routes["POST /bff/api/v1/students/search"] = async (request) => {
      const body = (await request.json()) as { query?: string; cursor?: string };
      if (!body.query) return page([]);
      return body.cursor
        ? page([summary({ id: ID.student2, display_name: "Lakshmi D." })])
        : Response.json({ data: [summary()], next_cursor: "cursor-2" });
    };
    const user = userEvent.setup();
    renderWithIntl(<StudentsScreen />);
    await screen.findByText(sm.list.emptyTitle);
    await user.type(screen.getByLabelText(sm.list.searchLabel), "Venkata");
    await user.click(screen.getByRole("button", { name: messages.en.common.search }));
    await screen.findByRole("link", { name: "Venkata Sai K." });

    await user.click(screen.getByRole("button", { name: sm.list.next }));
    expect(await screen.findByRole("link", { name: "Lakshmi D." })).toBeInTheDocument();
    const bodies = stub
      .callsTo("POST /bff/api/v1/students/search")
      .map((call) => JSON.parse(call.body) as Record<string, unknown>);
    expect(bodies.at(-1)).toEqual({ query: "Venkata", limit: PAGE_SIZE, cursor: "cursor-2" });
    expect(stub.calls.filter((call) => /venkata/i.test(decodeURIComponent(call.url.href)))).toEqual(
      [],
    );
  });

  it("never sends a search that holds a full Aadhaar number", async () => {
    stub.routes["GET /bff/api/v1/me"] = () => Response.json(me(ALL_RECORD_PERMISSIONS));
    stub.routes["POST /bff/api/v1/students/search"] = () => page([]);
    const user = userEvent.setup();
    renderWithIntl(<StudentsScreen />);
    await screen.findByText(sm.list.emptyTitle);
    const calls = stub.callsTo("POST /bff/api/v1/students/search").length;

    await user.type(screen.getByLabelText(sm.list.searchLabel), fakeAadhaar());
    await user.click(screen.getByRole("button", { name: messages.en.common.search }));

    expect((await screen.findAllByText(sm.aadhaarNotAllowed)).length).toBeGreaterThan(0);
    expect(stub.callsTo("POST /bff/api/v1/students/search")).toHaveLength(calls);
    expect(screen.getByRole("link", { name: sm.list.add })).toBeInTheDocument();
  });
});

describe("FR-STU-010 / FR-TEN-010: another year's class lists; archived sections are not offered", () => {
  const OLD_YEAR = {
    ...YEAR,
    id: "0192f3a4-0000-7000-8000-00000000c4a1",
    label: "2025-26",
    is_current: false,
  };
  const OLD_SECTION = {
    ...SECTION,
    id: "0192f3a4-0000-7000-8000-00000000c4a3",
    academic_year_id: OLD_YEAR.id,
    name: "C",
  };
  const ARCHIVED_SECTION = {
    ...SECTION,
    id: "0192f3a4-0000-7000-8000-00000000c4a4",
    name: "Z",
    archived_at: "2026-09-01T05:00:00Z",
  };
  const GONE_CLASS_SECTION = {
    ...SECTION,
    id: "0192f3a4-0000-7000-8000-00000000c4a5",
    class_id: "0192f3a4-0000-7000-8000-00000000c4a6",
    name: "Q",
  };

  function sectionNames(): string[] {
    const select = screen.getByLabelText(sm.list.filterSection);
    return within(select)
      .getAllByRole("option")
      .map((option) => option.textContent ?? "")
      .filter((label) => label !== messages.en.common.all);
  }

  it("lists another academic year with academic_year_id and offers that year's sections", async () => {
    stub.routes["GET /bff/api/v1/me"] = () => Response.json(me(["student.read_basic"]));
    stub.routes["GET /bff/api/v1/academic-years"] = () => page([YEAR, OLD_YEAR]);
    stub.routes["GET /bff/api/v1/sections"] = () =>
      page([SECTION, OLD_SECTION, ARCHIVED_SECTION, GONE_CLASS_SECTION]);
    stub.routes["POST /bff/api/v1/students/search"] = () => page([]);
    const user = userEvent.setup();
    renderWithIntl(<StudentsScreen />);
    await screen.findByText(sm.list.emptyTitle);
    await waitFor(() => expect(sectionNames()).toEqual(["Class 9 · A"]));

    await user.selectOptions(screen.getByLabelText(sm.list.filterYear), "2025-26");
    expect(sectionNames()).toEqual(["Class 9 · C"]);
    await user.click(screen.getByRole("button", { name: messages.en.common.search }));
    await waitFor(() =>
      expect(
        JSON.parse(stub.callsTo("POST /bff/api/v1/students/search").at(-1)?.body ?? "{}"),
      ).toEqual({ academic_year_id: OLD_YEAR.id, limit: PAGE_SIZE }),
    );
  });
});

describe("US-301 / US-303: add a student", () => {
  it("builds one value per filled field, all from the chosen source", () => {
    const parsed = createStudentSchema.parse({
      source: "admission_register",
      admission_no: "2019/0457",
      full_name: "Venkata Sai K.",
      dob: "14/03/2012",
      gender: "male",
      father_name: "",
      mother_name: "",
      section_id: ID.section,
      roll_no: "12",
      status: "active",
    });
    expect(createBody(parsed)).toEqual({
      values: [
        { attribute_key: "admission_no", source: "admission_register", value: "2019/0457" },
        { attribute_key: "full_name", source: "admission_register", value: "Venkata Sai K." },
        { attribute_key: "dob", source: "admission_register", value: "2012-03-14" },
        { attribute_key: "gender", source: "admission_register", value: "male" },
      ],
      status: "active",
      section_id: ID.section,
      roll_no: "12",
    });
  });

  it("refuses a pasted full Aadhaar number with an explanation, and sends nothing", async () => {
    const user = userEvent.setup();
    renderWithIntl(<CreateStudentForm permissions={permissionsFrom(ALL_RECORD_PERMISSIONS)} />);
    const name = screen.getByLabelText(sm.create.fatherName);
    name.focus();
    await user.paste(fakeAadhaar());
    expect(name).toHaveValue("");
    expect((await screen.findAllByText(sm.aadhaarNotAllowed)).length).toBeGreaterThan(0);
    expect(stub.callsTo("POST /bff/api/v1/students")).toHaveLength(0);
  });

  it("the date of birth follows the school's date format (FR-TEN-012)", async () => {
    setSchoolDateFormat("DD-MM-YYYY");
    try {
      stub.routes["POST /bff/api/v1/students"] = () => Response.json(student(), { status: 201 });
      const user = userEvent.setup();
      renderWithIntl(<CreateStudentForm permissions={permissionsFrom(ALL_RECORD_PERMISSIONS)} />);
      const dob = screen.getByLabelText(sm.create.dob);
      expect(dob).toHaveAttribute("placeholder", "DD-MM-YYYY");
      expect(dob).toHaveAccessibleDescription(
        expect.stringContaining("Use DD-MM-YYYY, for example 14-03-2012."),
      );
      await user.type(screen.getByLabelText(sm.create.fullName), "Venkata Sai K.");
      await user.type(dob, "14-03-2012");
      await user.click(screen.getByRole("button", { name: sm.create.submit }));
      await waitFor(() => expect(stub.callsTo("POST /bff/api/v1/students")).toHaveLength(1));
      const sent = JSON.parse(stub.callsTo("POST /bff/api/v1/students")[0]?.body ?? "{}") as {
        values: { attribute_key: string; value: string }[];
      };
      expect(sent.values.find((value) => value.attribute_key === "dob")?.value).toBe("2012-03-14");
    } finally {
      setSchoolDateFormat(null);
    }
  });

  it("posts the student with an Idempotency-Key and opens it", async () => {
    stub.routes["POST /bff/api/v1/students"] = () => Response.json(student(), { status: 201 });
    const created = vi.fn();
    const user = userEvent.setup();
    renderWithIntl(
      <CreateStudentForm
        permissions={permissionsFrom(ALL_RECORD_PERMISSIONS)}
        onCreated={created}
      />,
    );
    await user.click(screen.getByRole("button", { name: sm.create.submit }));
    // Full name is required: nothing is sent and the field says so.
    expect(stub.callsTo("POST /bff/api/v1/students")).toHaveLength(0);
    expect(screen.getByLabelText(sm.create.fullName)).toHaveAttribute("aria-invalid", "true");

    await user.type(screen.getByLabelText(sm.create.fullName), "Venkata Sai K.");
    await user.type(screen.getByLabelText(sm.create.dob), "31/02/2012");
    await user.click(screen.getByRole("button", { name: sm.create.submit }));
    expect(
      await screen.findByText(
        sm.dateInvalid.replace("{format}", "DD/MM/YYYY").replace("{example}", "14/03/2012"),
      ),
    ).toBeInTheDocument();

    await user.clear(screen.getByLabelText(sm.create.dob));
    await user.type(screen.getByLabelText(sm.create.dob), "14/03/2012");
    await user.click(screen.getByRole("button", { name: sm.create.submit }));
    await waitFor(() => expect(created).toHaveBeenCalled());
    const call = stub.callsTo("POST /bff/api/v1/students")[0];
    expect(call?.headers.get("idempotency-key")).toMatch(/^[0-9a-f-]{36}$/);
    expect(JSON.parse(call?.body ?? "{}")).toMatchObject({
      values: [
        { attribute_key: "full_name", source: "admission_register", value: "Venkata Sai K." },
        { attribute_key: "dob", source: "admission_register", value: "2012-03-14" },
      ],
      status: "active",
    });
  });

  it("explains that the user can't add students", () => {
    renderWithIntl(<CreateStudentForm permissions={permissionsFrom(["student.read_basic"])} />);
    expect(screen.getByText(sm.create.noPermissionTitle)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: sm.create.submit })).toBeNull();
  });
});

describe("US-301: a student's values with their sources", () => {
  const ready = <T,>(data: T) => ({ status: "ready" as const, data });

  it("shows every source side by side, and keeps restricted values hidden until Show", async () => {
    stub.routes[`POST /bff/api/v1/students/${ID.student}/sensitive-reveal`] = () =>
      Response.json({
        attribute_key: "aadhaar_last4",
        source: "aadhaar_as_printed",
        value: "4821",
        display: "XXXX XXXX 4821",
        value_id: null,
        guardian_id: null,
      });
    const user = userEvent.setup();
    renderWithIntl(
      <StudentDetailView
        student={ready(student())}
        attributes={ready(ATTRIBUTES)}
        guardians={ready([])}
        permissions={permissionsFrom(ALL_RECORD_PERMISSIONS)}
      />,
    );
    expect(screen.getByRole("heading", { level: 1, name: "Venkata Sai K." })).toBeInTheDocument();
    const table = screen.getByRole("table", { name: sm.detail.valuesTable });
    expect(within(table).getByText("Venkatasai Kumar")).toBeInTheDocument();
    expect(within(table).getAllByText(sm.sources.udise_plus).length).toBeGreaterThan(0);
    expect(within(table).getByText(/Differs from/)).toBeInTheDocument();
    // Restricted: masked, with a Show button that is audited by the API.
    expect(within(table).queryByText("XXXX XXXX 4821")).toBeNull();
    const show = within(table).getAllByRole("button", { name: /Show/ })[0];
    expect(show).toBeDefined();
    await user.click(show as HTMLElement);
    expect(await within(table).findByText("XXXX XXXX 4821")).toBeInTheDocument();
    const reveal = stub.callsTo(`POST /bff/api/v1/students/${ID.student}/sensitive-reveal`)[0];
    expect(JSON.parse(reveal?.body ?? "{}")).toMatchObject({ attribute_key: "aadhaar_last4" });
  });

  it("offers no Show button or record action without the permissions", () => {
    renderWithIntl(
      <StudentDetailView
        student={ready(student())}
        attributes={ready(ATTRIBUTES)}
        guardians={ready([])}
        permissions={permissionsFrom(["student.read_basic"])}
      />,
    );
    expect(screen.queryByRole("button", { name: /Show/ })).toBeNull();
    expect(screen.queryByRole("button", { name: sm.record.open })).toBeNull();
    expect(screen.queryByRole("button", { name: /Mark verified/ })).toBeNull();
  });

  it("points an identity change to a correction request (403 identity_change_required)", async () => {
    stub.routes[`POST /bff/api/v1/students/${ID.student}/values`] = () =>
      problem(403, "identity_change_required");
    const user = userEvent.setup();
    renderWithIntl(
      <StudentDetailView
        student={ready(student())}
        attributes={ready(ATTRIBUTES)}
        guardians={ready([])}
        permissions={permissionsFrom(ALL_RECORD_PERMISSIONS)}
      />,
    );
    await user.click(screen.getByRole("button", { name: sm.record.open }));
    const dialog = screen.getByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText(sm.record.field), {
      target: { value: "full_name" },
    });
    await user.selectOptions(within(dialog).getByLabelText(sm.record.source), "admission_register");
    await user.type(within(dialog).getByLabelText(sm.record.value), "Venkata Sai Kumar");
    await user.click(within(dialog).getByRole("button", { name: sm.record.submit }));
    expect(
      await within(dialog).findByText(sm.errors.identity_change_required.title),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByRole("link", { name: sm.record.goToChangeRequests }),
    ).toHaveAttribute("href", `/en/change-requests?student_id=${ID.student}`);
    const call = stub.callsTo(`POST /bff/api/v1/students/${ID.student}/values`)[0];
    expect(call?.headers.get("if-match")).toBe('"4"');
  });

  it("the page title and heading never show a raw 404 (student outside scope)", () => {
    renderWithIntl(
      <StudentDetailView
        student={{ status: "error", reason: "not_found" }}
        attributes={ready(ATTRIBUTES)}
        guardians={ready([])}
        permissions={permissionsFrom(ALL_RECORD_PERMISSIONS)}
      />,
    );
    expect(
      screen.getByRole("heading", { level: 1, name: sm.detail.loadingTitle }),
    ).toBeInTheDocument();
    expect(screen.getByText(messages.en.errors.load.not_found)).toBeInTheDocument();
  });
});
