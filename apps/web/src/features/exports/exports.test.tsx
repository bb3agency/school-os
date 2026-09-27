import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SchoolShell } from "@/components/shell/SchoolShell";
import { registerStepUpHandler } from "@/lib/bff/step-up";
import { installBffStub, page, problem, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { intlErrors, renderWithIntl } from "@/test/render";
import {
  EXPORT_ID,
  exportAttributes,
  exportProfiles,
  exportRow,
  me,
  OTHER_MEMBERSHIP,
  SECTION,
  structureRoutes,
} from "@/test/school-fixtures";
import {
  exportRefetchInterval,
  setDownloadOpenerForTesting,
  setExportPollDelayForTesting,
} from "./data";
import { ExportDetailScreen } from "./ExportDetailScreen";
import { ExportsScreen } from "./ExportsScreen";
import { parseExportListFilters, parseNewPrecheckParams } from "./filters";
import { NewPrecheckScreen } from "./NewPrecheckScreen";
import { NewStudentListScreen } from "./NewStudentListScreen";

const push = vi.hoisted(() => vi.fn());

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => "/en/exports",
    useRouter: () => ({ push, replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
    useSearchParams: () => new URLSearchParams(),
    useParams: () => ({ locale: "en" }),
  };
});

const BOARD = "export.board";
const PORTAL = "export.portal";
const LIST = "student.export";
const READ_ALL = "export.read_all";
const READ_BASIC = "student.read_basic";
const SENSITIVE = "student.read_sensitive";
const FINDINGS = "dq.findings.read";
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const CREATED = "0192f3a4-0000-7000-8000-00000000e0b1";

let stub: BffStub;
let unregisterStepUp: (() => void) | null = null;

function setMe(permissions: string[]) {
  stub.routes["GET /bff/api/v1/me"] = () => Response.json(me(permissions));
}

function body(key: string, index = 0): Record<string, unknown> {
  return JSON.parse(stub.callsTo(key)[index]?.body ?? "{}") as Record<string, unknown>;
}

beforeEach(() => {
  push.mockReset();
  stub = installBffStub("staff");
  Object.assign(stub.routes, structureRoutes());
  stub.routes["GET /bff/api/v1/export-profiles"] = () => Response.json(exportProfiles());
  stub.routes["GET /bff/api/v1/attributes"] = () => Response.json(exportAttributes());
});

afterEach(() => {
  unregisterStepUp?.();
  unregisterStepUp = null;
  setExportPollDelayForTesting(null);
  setDownloadOpenerForTesting(null);
  uninstallBffStub();
  expect(intlErrors).toEqual([]);
});

describe("exports list (FR-EXP-003, ADR-0021)", () => {
  it("shows my exports only, with no whole-school view without export.read_all", async () => {
    setMe([BOARD, READ_BASIC, FINDINGS]);
    stub.routes["GET /bff/api/v1/exports"] = () => page([exportRow()]);
    renderWithIntl(<ExportsScreen filters={parseExportListFilters({ view: "all" })} />);
    expect(
      await screen.findByRole("link", { name: /Board pre-check · CISCE registration 2026/ }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Whole school" })).not.toBeInTheDocument();
    const calls = stub.callsTo("GET /bff/api/v1/exports");
    expect(calls.length).toBeGreaterThan(0);
    // ?view=all in the URL is ignored without the permission: the API is never asked for "all".
    expect(calls.every((call) => call.url.searchParams.get("requested_by") === "me")).toBe(true);
    const row = screen.getAllByRole("row")[1];
    expect(row && within(row).getByText("You")).toBeInTheDocument();
    expect(row && within(row).getByText("Ready")).toBeInTheDocument();
    expect(row && within(row).getByText("03/10/2026 10:31")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "New pre-check" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "New student list" })).not.toBeInTheDocument();
  });

  it("offers the whole-school view to export.read_all holders and asks for requested_by=all", async () => {
    setMe([READ_ALL]);
    stub.routes["GET /bff/api/v1/exports"] = (_request, url) =>
      url.searchParams.get("requested_by") === "all"
        ? page([
            exportRow({
              own: false,
              can_download: false,
              kind: "student_list",
              profile_key: null,
              requested_by: { membership_id: OTHER_MEMBERSHIP, display_name: "Ravi Sample" },
            }),
            exportRow({
              id: CREATED,
              own: false,
              can_download: false,
              requested_by: { membership_id: OTHER_MEMBERSHIP, display_name: null },
            }),
          ])
        : page([]);
    renderWithIntl(<ExportsScreen filters={parseExportListFilters({ view: "all" })} />);
    expect(await screen.findByText("Ravi Sample")).toBeInTheDocument();
    expect(screen.getByText("A former staff member")).toBeInTheDocument();
    const tabs = screen.getByRole("navigation", { name: "Whose exports" });
    expect(within(tabs).getByRole("link", { name: "Whole school" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(within(tabs).getByRole("link", { name: "My exports" })).toBeInTheDocument();
    expect(stub.callsTo("GET /bff/api/v1/exports")[0]?.url.searchParams.get("requested_by")).toBe(
      "all",
    );
    // No make-export permission: no buttons and no profile lookup (it would be refused).
    expect(screen.queryByRole("link", { name: "New pre-check" })).not.toBeInTheDocument();
    expect(stub.callsTo("GET /bff/api/v1/export-profiles")).toHaveLength(0);
  });

  it("says so when the member has no export permission (and asks the API nothing)", async () => {
    setMe([READ_BASIC]);
    renderWithIntl(<ExportsScreen filters={parseExportListFilters({})} />);
    expect(await screen.findByText("You can't see exports")).toBeInTheDocument();
    expect(stub.callsTo("GET /bff/api/v1/exports")).toHaveLength(0);
  });

  it("shows the empty state in Telugu without missing messages", async () => {
    setMe([LIST, READ_BASIC]);
    stub.routes["GET /bff/api/v1/exports"] = () => page([]);
    renderWithIntl(<ExportsScreen filters={parseExportListFilters({})} />, "te");
    expect(await screen.findByText("ఇంకా ఎగుమతులు లేవు")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "కొత్త విద్యార్థుల జాబితా" })).toBeInTheDocument();
  });

  it("drops unknown URL values", () => {
    expect(parseExportListFilters({ view: "everyone" })).toEqual({ view: "me" });
    expect(parseNewPrecheckParams({ profile: "../x" })).toEqual({ profileKey: null });
    expect(parseNewPrecheckParams({ profile: "udise-plus" })).toEqual({ profileKey: "udise-plus" });
  });
});

describe("status polling (FR-EXP-002)", () => {
  it("polls only while an export is queued or running", () => {
    setExportPollDelayForTesting(() => 1234);
    expect(exportRefetchInterval([{ status: "queued" }], 0)).toBe(1234);
    expect(exportRefetchInterval([{ status: "ready" }, { status: "running" }], 3)).toBe(1234);
    for (const status of ["ready", "failed", "expired"] as const) {
      expect(exportRefetchInterval([{ status }], 0)).toBe(false);
    }
    expect(exportRefetchInterval(undefined, 0)).toBe(false);
    expect(exportRefetchInterval([{ status: "queued" }], 0, true)).toBe(false);
  });

  it("the detail screen stops asking once the export is ready", async () => {
    setExportPollDelayForTesting(() => 20);
    setMe([BOARD, READ_BASIC, FINDINGS]);
    const answers = ["queued", "running", "ready"] as const;
    let served = 0;
    stub.routes[`GET /bff/api/v1/exports/${EXPORT_ID}`] = () => {
      const status = answers[Math.min(served, answers.length - 1)] ?? "ready";
      served += 1;
      return Response.json(exportRow(status === "ready" ? { status } : { status, files: [] }));
    };
    renderWithIntl(<ExportDetailScreen exportId={EXPORT_ID} />);
    expect(await screen.findByText("Waiting to start", { selector: "p" })).toBeInTheDocument();
    expect(
      await screen.findByRole("button", { name: /Download Excel \(XLSX\)/ }, { timeout: 3000 }),
    ).toBeInTheDocument();
    const settled = served;
    expect(settled).toBe(3);
    await new Promise((resolve) => setTimeout(resolve, 200));
    expect(served).toBe(settled);
  });
});

describe("new pre-check (US-501 AC4, FR-EXP-001..004, SEC-005)", () => {
  function created() {
    return new Response(JSON.stringify(exportRow({ id: CREATED, status: "queued", files: [] })), {
      status: 202,
      headers: { "content-type": "application/json", location: `/api/v1/exports/${CREATED}` },
    });
  }

  it("tells the user about step-up up front and sends the chosen profile with an Idempotency-Key", async () => {
    setMe([BOARD, READ_BASIC, FINDINGS]);
    stub.routes["POST /bff/api/v1/exports"] = created;
    renderWithIntl(<NewPrecheckScreen params={parseNewPrecheckParams({})} />);
    expect(await screen.findByText("You will be asked to confirm it's you")).toBeInTheDocument();
    // Only the board profile: the member has export.board, not export.portal.
    const profile = screen.getByLabelText(/^Format/);
    expect(within(profile).queryByRole("option", { name: /UDISE/ })).not.toBeInTheDocument();
    expect(await screen.findByText(/Admission number, Full name, gender/)).toBeInTheDocument();
    expect(screen.queryByLabelText(/Include restricted details/)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Make the pre-check" }));
    await waitFor(() => expect(push).toHaveBeenCalledWith(`/en/exports/${CREATED}`));
    const call = stub.callsTo("POST /bff/api/v1/exports")[0];
    expect(call?.headers.get("idempotency-key")).toMatch(UUID);
    expect(body("POST /bff/api/v1/exports")).toEqual({
      profile_key: "cisce-registration-2026",
      scope: {},
      format: ["xlsx", "pdf"],
      language: "en",
      include_sensitive: false,
    });
  });

  it("sends chosen sections, one file type and Telugu", async () => {
    setMe([BOARD, READ_BASIC, FINDINGS]);
    stub.routes["POST /bff/api/v1/exports"] = created;
    renderWithIntl(<NewPrecheckScreen params={parseNewPrecheckParams({})} />);
    await userEvent.click(await screen.findByRole("radio", { name: "Some sections" }));
    await userEvent.click(await screen.findByRole("checkbox", { name: "Class 9 · A" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "PDF" }));
    await userEvent.click(screen.getByRole("radio", { name: "Telugu" }));
    await userEvent.click(screen.getByRole("button", { name: "Make the pre-check" }));
    await waitFor(() => expect(stub.callsTo("POST /bff/api/v1/exports")).toHaveLength(1));
    expect(body("POST /bff/api/v1/exports")).toMatchObject({
      scope: { section_ids: [SECTION] },
      format: ["xlsx"],
      language: "te",
    });
  });

  it("checks the form before sending: a file type and a section are needed", async () => {
    setMe([BOARD, READ_BASIC, FINDINGS]);
    renderWithIntl(<NewPrecheckScreen params={parseNewPrecheckParams({})} />);
    await userEvent.click(await screen.findByRole("checkbox", { name: "Excel (XLSX)" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "PDF" }));
    await userEvent.click(screen.getByRole("radio", { name: "Some sections" }));
    await userEvent.click(screen.getByRole("button", { name: "Make the pre-check" }));
    expect(await screen.findByText("Choose at least one file type.")).toBeInTheDocument();
    expect(screen.getByText(/Choose at least one section/)).toBeInTheDocument();
    expect(stub.callsTo("POST /bff/api/v1/exports")).toHaveLength(0);
  });

  it("offers restricted details only to student.read_sensitive holders, with the audit warning", async () => {
    setMe([PORTAL, READ_BASIC, FINDINGS, SENSITIVE]);
    stub.routes["POST /bff/api/v1/exports"] = created;
    renderWithIntl(
      <NewPrecheckScreen params={parseNewPrecheckParams({ profile: "udise-plus" })} />,
    );
    const optIn = await screen.findByRole("checkbox", {
      name: "Include restricted details (for example category) in the file",
    });
    expect(optIn).not.toBeChecked();
    expect(screen.getByText(/This format has restricted details: Category/)).toBeInTheDocument();
    expect(screen.getByText(/recorded in the audit log/)).toBeInTheDocument();
    await userEvent.click(optIn);
    await userEvent.click(screen.getByRole("button", { name: "Make the pre-check" }));
    await waitFor(() => expect(stub.callsTo("POST /bff/api/v1/exports")).toHaveLength(1));
    expect(body("POST /bff/api/v1/exports")).toMatchObject({
      profile_key: "udise-plus",
      include_sensitive: true,
    });
  });

  it("hides the restricted-details opt-in without student.read_sensitive", async () => {
    setMe([PORTAL, READ_BASIC, FINDINGS]);
    renderWithIntl(
      <NewPrecheckScreen params={parseNewPrecheckParams({ profile: "udise-plus" })} />,
    );
    expect(await screen.findByRole("button", { name: "Make the pre-check" })).toBeInTheDocument();
    expect(screen.queryByText("Restricted details")).not.toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: /restricted/i })).not.toBeInTheDocument();
  });

  it("explains 403 sensitive_not_allowed in plain words", async () => {
    setMe([PORTAL, READ_BASIC, FINDINGS, SENSITIVE]);
    stub.routes["POST /bff/api/v1/exports"] = () => problem(403, "sensitive_not_allowed");
    renderWithIntl(
      <NewPrecheckScreen params={parseNewPrecheckParams({ profile: "udise-plus" })} />,
    );
    await userEvent.click(await screen.findByRole("checkbox", { name: /Include restricted/ }));
    await userEvent.click(screen.getByRole("button", { name: "Make the pre-check" }));
    expect(
      await screen.findByText("Restricted details are not allowed for you"),
    ).toBeInTheDocument();
    expect(push).not.toHaveBeenCalled();
  });

  it("shows 422 no_students next to the students choice", async () => {
    setMe([BOARD, READ_BASIC, FINDINGS]);
    stub.routes["POST /bff/api/v1/exports"] = () =>
      problem(422, "validation_error", {
        errors: [
          { field: "scope", code: "no_students", message_key: "errors.exports.no_students" },
        ],
      });
    renderWithIntl(<NewPrecheckScreen params={parseNewPrecheckParams({})} />);
    await userEvent.click(await screen.findByRole("button", { name: "Make the pre-check" }));
    expect(
      await screen.findByText("No students match this choice. Choose other classes or sections."),
    ).toBeInTheDocument();
  });

  it("428: the global step-up prompt confirms and the same request is sent again", async () => {
    setMe([BOARD, READ_BASIC, FINDINGS]);
    const prompt = vi.fn(async () => true);
    unregisterStepUp = registerStepUpHandler("staff", prompt);
    let attempts = 0;
    stub.routes["POST /bff/api/v1/exports"] = () => {
      attempts += 1;
      return attempts === 1
        ? problem(428, "step_up_required", { step_up_url: "/bff/auth/step-up?next=%2Fen" })
        : created();
    };
    renderWithIntl(<NewPrecheckScreen params={parseNewPrecheckParams({})} />);
    await userEvent.click(await screen.findByRole("button", { name: "Make the pre-check" }));
    await waitFor(() => expect(push).toHaveBeenCalledWith(`/en/exports/${CREATED}`));
    expect(prompt).toHaveBeenCalledTimes(1);
    const calls = stub.callsTo("POST /bff/api/v1/exports");
    expect(calls).toHaveLength(2);
    expect(calls[1]?.headers.get("idempotency-key")).toBe(calls[0]?.headers.get("idempotency-key"));
    expect(calls[1]?.body).toBe(calls[0]?.body);
  });

  it("428 then cancelled: nothing is made and the user is told how to go on", async () => {
    setMe([BOARD, READ_BASIC, FINDINGS]);
    unregisterStepUp = registerStepUpHandler("staff", async () => false);
    stub.routes["POST /bff/api/v1/exports"] = () => problem(428, "step_up_required");
    renderWithIntl(<NewPrecheckScreen params={parseNewPrecheckParams({})} />);
    await userEvent.click(await screen.findByRole("button", { name: "Make the pre-check" }));
    expect(
      await screen.findByText("Not done: we still need to confirm it's you"),
    ).toBeInTheDocument();
    expect(push).not.toHaveBeenCalled();
  });

  it("without export.board or export.portal the form is not offered", async () => {
    setMe([LIST, READ_BASIC]);
    renderWithIntl(<NewPrecheckScreen params={parseNewPrecheckParams({})} />);
    expect(await screen.findByText("You can't make pre-checks")).toBeInTheDocument();
    expect(stub.callsTo("GET /bff/api/v1/export-profiles")).toHaveLength(0);
  });
});

describe("new student list (US-901, FR-EXP-004)", () => {
  it("sends the chosen columns in order with an Idempotency-Key; Aadhaar-as-printed is never offered", async () => {
    setMe([LIST, READ_BASIC]);
    stub.routes["POST /bff/api/v1/exports/student-list"] = () =>
      Response.json(exportRow({ id: CREATED, kind: "student_list", status: "queued" }), {
        status: 202,
      });
    renderWithIntl(<NewStudentListScreen />);
    expect(await screen.findByRole("checkbox", { name: "Full name" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Roll number" })).toBeChecked();
    expect(screen.queryByRole("checkbox", { name: /Name on Aadhaar/ })).not.toBeInTheDocument();
    // Restricted columns need student.read_sensitive.
    expect(screen.queryByRole("checkbox", { name: /Category/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: /Aadhaar last 4/ })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("checkbox", { name: "Date of birth" }));
    await userEvent.click(screen.getByRole("radio", { name: "CSV" }));
    await userEvent.click(screen.getByRole("button", { name: "Make the student list" }));
    await waitFor(() => expect(push).toHaveBeenCalledWith(`/en/exports/${CREATED}`));
    const call = stub.callsTo("POST /bff/api/v1/exports/student-list")[0];
    expect(call?.headers.get("idempotency-key")).toMatch(UUID);
    expect(body("POST /bff/api/v1/exports/student-list")).toEqual({
      columns: ["class", "section", "roll_no", "admission_no", "full_name", "dob"],
      scope: {},
      format: "csv",
      language: "en",
    });
  });

  it("marks restricted columns for student.read_sensitive holders and warns about the audit log", async () => {
    setMe([LIST, READ_BASIC, SENSITIVE]);
    renderWithIntl(<NewStudentListScreen />);
    const category = await screen.findByRole("checkbox", { name: /Category/ });
    expect(category).not.toBeChecked();
    expect(screen.getAllByText("Restricted").length).toBe(2);
    expect(screen.getByText(/recorded in the audit log when you include them/)).toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: /Name on Aadhaar/ })).not.toBeInTheDocument();
  });

  it("needs at least one column", async () => {
    setMe([LIST, READ_BASIC]);
    renderWithIntl(<NewStudentListScreen />);
    await screen.findByRole("checkbox", { name: "Full name" });
    for (const box of screen.getAllByRole("checkbox")) {
      if ((box as HTMLInputElement).checked) await userEvent.click(box);
    }
    await userEvent.click(screen.getByRole("button", { name: "Make the student list" }));
    expect(await screen.findByText("Choose at least one column.")).toBeInTheDocument();
    expect(stub.callsTo("POST /bff/api/v1/exports/student-list")).toHaveLength(0);
  });

  it("without student.export the form is not offered", async () => {
    setMe([BOARD]);
    renderWithIntl(<NewStudentListScreen />);
    expect(await screen.findByText("You can't make student lists")).toBeInTheDocument();
  });
});

describe("export detail and download (FR-EXP-003..004, SEC-005, ADR-0021)", () => {
  function detail(permissions: string[], overrides = {}) {
    setMe(permissions);
    stub.routes[`GET /bff/api/v1/exports/${EXPORT_ID}`] = () => Response.json(exportRow(overrides));
  }

  it("downloads through a short-lived link opened at once and never stored", async () => {
    detail([BOARD, READ_BASIC, FINDINGS]);
    const opened: string[] = [];
    setDownloadOpenerForTesting((url) => opened.push(url));
    const link = "https://files.schoolos.example/t/x/precheck.xlsx?sig=synthetic";
    stub.routes[`GET /bff/api/v1/exports/${EXPORT_ID}/download-url`] = () =>
      Response.json({
        url: link,
        expires_at: "2026-09-26T05:05:00Z",
        format: "xlsx",
        content_type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename: "precheck.xlsx",
      });
    renderWithIntl(<ExportDetailScreen exportId={EXPORT_ID} />);
    expect(await screen.findByText("You", { selector: "dd" })).toBeInTheDocument();
    await userEvent.click(await screen.findByRole("button", { name: /Download Excel \(XLSX\)/ }));
    await waitFor(() => expect(opened).toEqual([link]));
    const call = stub.callsTo(`GET /bff/api/v1/exports/${EXPORT_ID}/download-url`)[0];
    expect(call?.url.searchParams.get("format")).toBe("xlsx");
    // Used once and dropped: the link is not rendered anywhere on the page (browser storage is
    // already off limits by lint rule, docs/07 §5.2).
    expect(document.body.innerHTML).not.toContain("sig=synthetic");
  });

  it("someone else's export without can_download: explains not_own_export, no download buttons", async () => {
    detail([READ_ALL], {
      own: false,
      can_download: false,
      requested_by: { membership_id: OTHER_MEMBERSHIP, display_name: "Ravi Sample" },
    });
    renderWithIntl(<ExportDetailScreen exportId={EXPORT_ID} />);
    expect(
      await screen.findByText("Only the person who made it can download this"),
    ).toBeInTheDocument();
    expect(screen.getByText("Ravi Sample")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Download/ })).not.toBeInTheDocument();
    expect(stub.callsTo(`GET /bff/api/v1/exports/${EXPORT_ID}/download-url`)).toHaveLength(0);
  });

  it("someone else's export with can_download says a fresh sign-in may be needed", async () => {
    detail([READ_ALL, "export.download_any", READ_BASIC], {
      own: false,
      requested_by: { membership_id: OTHER_MEMBERSHIP, display_name: "Ravi Sample" },
    });
    renderWithIntl(<ExportDetailScreen exportId={EXPORT_ID} />);
    expect(await screen.findByRole("button", { name: /Download PDF/ })).toBeInTheDocument();
    expect(screen.getByText(/you may be asked to sign in again/)).toBeInTheDocument();
  });

  it("explains 403 not_own_export and 409 export_expired from the download link", async () => {
    detail([BOARD, READ_BASIC, FINDINGS]);
    let answer = problem(403, "not_own_export");
    stub.routes[`GET /bff/api/v1/exports/${EXPORT_ID}/download-url`] = () => answer;
    renderWithIntl(<ExportDetailScreen exportId={EXPORT_ID} />);
    await userEvent.click(await screen.findByRole("button", { name: /Download PDF/ }));
    expect(
      await screen.findByText("Ask the person who made the export, or make a new one yourself."),
    ).toBeInTheDocument();
    answer = problem(409, "export_expired");
    await userEvent.click(screen.getByRole("button", { name: /Download PDF/ }));
    expect(
      await screen.findByText("Files are deleted 7 days after they are ready. Make a new export."),
    ).toBeInTheDocument();
  });

  it("an expired export offers a new one instead of a download", async () => {
    detail([BOARD, READ_BASIC, FINDINGS], { status: "expired", files: [] });
    renderWithIntl(<ExportDetailScreen exportId={EXPORT_ID} />);
    expect(await screen.findByText("The files have been deleted")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Make a new export" })).toHaveAttribute(
      "href",
      "/en/exports/new/precheck?profile=cisce-registration-2026",
    );
    expect(screen.queryByRole("button", { name: /Download/ })).not.toBeInTheDocument();
  });

  it("a failed export says why in plain words", async () => {
    detail([LIST, READ_BASIC], {
      status: "failed",
      kind: "student_list",
      profile_key: null,
      error_code: "no_students",
      files: [],
      columns: ["class", "full_name"],
    });
    renderWithIntl(<ExportDetailScreen exportId={EXPORT_ID} />);
    expect(await screen.findByText("This export could not be made")).toBeInTheDocument();
    expect(screen.getByText(/No students matched/)).toBeInTheDocument();
    expect(await screen.findByText("Class, Full name")).toBeInTheDocument();
  });

  it("someone else's id without export.read_all is simply not found (404)", async () => {
    setMe([BOARD]);
    stub.routes[`GET /bff/api/v1/exports/${EXPORT_ID}`] = () => problem(404, "not_found");
    renderWithIntl(<ExportDetailScreen exportId={EXPORT_ID} />);
    expect(await screen.findByText("We couldn't find this export")).toBeInTheDocument();
  });
});

describe("navigation (UX only; the API checks every call)", () => {
  function navLinks() {
    return within(screen.getByRole("navigation", { name: "Main" }))
      .getAllByRole("link")
      .map((link) => link.textContent);
  }

  it("shows Exports to members with any export permission, including export.read_all", () => {
    renderWithIntl(
      <SchoolShell permissions={[READ_ALL]}>
        <p>x</p>
      </SchoolShell>,
    );
    expect(navLinks()).toContain("Exports");
  });

  it("hides Exports without an export permission", () => {
    renderWithIntl(
      <SchoolShell permissions={[READ_BASIC, "export.download_any"]}>
        <p>x</p>
      </SchoolShell>,
    );
    expect(navLinks()).not.toContain("Exports");
  });
});
