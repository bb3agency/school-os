import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { verhoeffValid } from "@/lib/aadhaar";
import { installBffStub, page, problem, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { intlErrors, renderWithIntl } from "@/test/render";
import { changeRequest, me, ME_MEMBERSHIP, STUDENT } from "@/test/school-fixtures";
import { ChangeRequestDetailScreen } from "./ChangeRequestDetailScreen";
import { ChangeRequestsScreen } from "./ChangeRequestsScreen";
import { displayDateToIso } from "./dates";
import { parseChangeRequestFilters } from "./filters";
import { buildRequestSchema, NewChangeRequestScreen } from "./NewChangeRequestScreen";
import { setStorageSendForTesting } from "./upload";

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => "/en/change-requests",
    useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
    useSearchParams: () => new URLSearchParams(),
    useParams: () => ({ locale: "en" }),
  };
});

const CR = "0192f3a4-0000-7000-8000-00000000c001";
const REQUEST = "student.identity_change.request";
const APPROVE = "student.identity_change.approve";
let stub: BffStub;

beforeEach(() => {
  stub = installBffStub("staff");
  stub.routes[`GET /bff/api/v1/students/${STUDENT}`] = () =>
    Response.json({
      id: STUDENT,
      status: "active",
      admission_no: "A-101",
      version: 1,
      created_at: "2026-06-01T00:00:00Z",
      updated_at: "2026-06-01T00:00:00Z",
      enrollment: null,
      canonical: {
        full_name: {
          value: "Asha Tset",
          source: "admission_register",
          verified: true,
          provisional: false,
          masked: false,
          conflicts: [],
        },
      },
      values: {},
      sensitive_revealable: false,
    });
});
afterEach(() => {
  uninstallBffStub();
  expect(intlErrors).toEqual([]);
});

describe("change request list (US-601, FR-CR-002)", () => {
  it("marks requests waiting for this approver, never the approver's own", async () => {
    stub.routes["GET /bff/api/v1/me"] = () => Response.json(me([REQUEST, APPROVE]));
    stub.routes["GET /bff/api/v1/change-requests"] = () =>
      page([
        changeRequest(),
        changeRequest({
          id: "0192f3a4-0000-7000-8000-00000000c002",
          requested_by: ME_MEMBERSHIP,
          can_decide: false,
          can_cancel: true,
        }),
      ]);
    renderWithIntl(<ChangeRequestsScreen filters={parseChangeRequestFilters({})} />);
    const rows = await screen.findAllByRole("row");
    const other = rows.find((row) => within(row).queryByText("Someone else"));
    const mine = rows.find((row) => within(row).queryByText("You"));
    expect(other && within(other).getByText("Waiting for you")).toBeInTheDocument();
    expect(mine && within(mine).queryByText("Waiting for you")).toBeNull();
    expect(screen.getByRole("link", { name: "Request a correction" })).toBeInTheDocument();
  });

  it("filters by a student ID from the URL (API filter), ignoring anything that is not a UUID", async () => {
    expect(parseChangeRequestFilters({ student_id: "not-a-uuid", status: "bogus" })).toEqual({
      status: null,
      studentId: null,
    });
    stub.routes["GET /bff/api/v1/me"] = () => Response.json(me([REQUEST]));
    stub.routes["GET /bff/api/v1/change-requests"] = () => page([changeRequest()]);
    renderWithIntl(
      <ChangeRequestsScreen filters={parseChangeRequestFilters({ student_id: STUDENT })} />,
    );
    expect(await screen.findByText("Showing one student only.")).toBeInTheDocument();
    const call = stub.callsTo("GET /bff/api/v1/change-requests")[0];
    expect(call?.url.searchParams.get("student_id")).toBe(STUDENT);
  });

  it("keeps masked values masked", async () => {
    stub.routes["GET /bff/api/v1/me"] = () => Response.json(me([APPROVE]));
    stub.routes["GET /bff/api/v1/change-requests"] = () =>
      page([changeRequest({ masked: true, old_value: "••••", new_value: "••••" })]);
    renderWithIntl(<ChangeRequestsScreen filters={parseChangeRequestFilters({})} />);
    expect((await screen.findAllByText("Hidden to protect personal details")).length).toBe(2);
    expect(screen.queryByRole("link", { name: "Request a correction" })).not.toBeInTheDocument();
  });
});

describe("change request detail (US-601 AC1–AC4, FR-CR-002, FR-CR-004)", () => {
  function detail(permissions: string[], overrides = {}) {
    stub.routes["GET /bff/api/v1/me"] = () => Response.json(me(permissions));
    stub.routes[`GET /bff/api/v1/change-requests/${CR}`] = () =>
      Response.json(changeRequest(overrides));
  }

  it("an approver approves with If-Match and an optional note", async () => {
    detail([APPROVE, "student.read_basic"]);
    stub.routes[`POST /bff/api/v1/change-requests/${CR}/approve`] = () =>
      Response.json(changeRequest({ status: "approved", can_decide: false }));
    renderWithIntl(<ChangeRequestDetailScreen changeRequestId={CR} />);
    expect(await screen.findByText("Asha Tset", { selector: "dd *, dd" })).toBeInTheDocument();
    await userEvent.click(await screen.findByRole("button", { name: "Approve" }));
    const dialog = screen.getByRole("dialog", { name: "Approve this correction" });
    expect(within(dialog).getByText(/you may be asked to sign in again/)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Approve" }));
    await waitFor(() =>
      expect(stub.callsTo(`POST /bff/api/v1/change-requests/${CR}/approve`)).toHaveLength(1),
    );
    const call = stub.callsTo(`POST /bff/api/v1/change-requests/${CR}/approve`)[0];
    expect(call?.headers.get("if-match")).toBe('W/"1"');
    expect(JSON.parse(call?.body ?? "{}")).toEqual({ note: null });
  });

  it("puts the checker's decision in its own card and shows the steps as a timeline", async () => {
    detail([APPROVE]);
    renderWithIntl(<ChangeRequestDetailScreen changeRequestId={CR} />);
    const decision = await screen.findByRole("region", { name: "Waiting for a decision" });
    expect(within(decision).getByRole("button", { name: "Approve" })).toBeInTheDocument();
    expect(within(decision).getByRole("button", { name: "Reject" })).toBeInTheDocument();
    const steps = screen.getByRole("list", { name: "Steps of this request" });
    expect(within(steps).getByText("Asked on")).toBeInTheDocument();
    expect(within(steps).getByText("Waiting for a decision")).toBeInTheDocument();
    expect(screen.getByText("Evidence document")).toBeInTheDocument();
  });

  it("rejecting needs a reason of at least 10 characters", async () => {
    detail([APPROVE]);
    renderWithIntl(<ChangeRequestDetailScreen changeRequestId={CR} />);
    await userEvent.click(await screen.findByRole("button", { name: "Reject" }));
    const dialog = screen.getByRole("dialog", { name: "Reject this correction" });
    await userEvent.type(within(dialog).getByLabelText("Reason for rejecting"), "No.");
    await userEvent.click(within(dialog).getByRole("button", { name: "Reject" }));
    expect(
      within(dialog).getByText("Give a reason of at least 10 characters."),
    ).toBeInTheDocument();
    expect(stub.callsTo(`POST /bff/api/v1/change-requests/${CR}/reject`)).toHaveLength(0);
  });

  it("the requester is never offered approve or reject, even if the API said can_decide", async () => {
    detail([REQUEST, APPROVE], { requested_by: ME_MEMBERSHIP, can_decide: true, can_cancel: true });
    renderWithIntl(<ChangeRequestDetailScreen changeRequestId={CR} />);
    expect(await screen.findByText("You asked for this")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Withdraw" })).toBeInTheDocument(),
    );
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reject" })).not.toBeInTheDocument();
  });

  it("explains self_approval_forbidden in plain words", async () => {
    detail([APPROVE]);
    stub.routes[`POST /bff/api/v1/change-requests/${CR}/approve`] = () =>
      problem(403, "self_approval_forbidden");
    renderWithIntl(<ChangeRequestDetailScreen changeRequestId={CR} />);
    await userEvent.click(await screen.findByRole("button", { name: "Approve" }));
    const dialog = screen.getByRole("dialog", { name: "Approve this correction" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Approve" }));
    expect(await within(dialog).findByText("Someone else must decide this")).toBeInTheDocument();
  });

  it("links the printable memo through the BFF, opening in a new tab", async () => {
    detail([REQUEST]);
    renderWithIntl(<ChangeRequestDetailScreen changeRequestId={CR} />);
    const memo = await screen.findByRole("link", { name: /Open the memo to print/ });
    expect(memo).toHaveAttribute("href", `/bff/api/v1/change-requests/${CR}/memo`);
    expect(memo).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("shows an image evidence preview from a short-lived link", async () => {
    detail([APPROVE]);
    stub.routes["GET /bff/api/v1/documents/0192f3a4-0000-7000-8000-00000000d0c1/download-url"] =
      () =>
        Response.json({
          url: "https://files.schoolos.example/t/x/evidence.png?sig=synthetic",
          expires_at: "2026-09-26T05:05:00Z",
          version_no: 1,
          mime_type: "image/png",
          filename: "evidence.png",
        });
    renderWithIntl(<ChangeRequestDetailScreen changeRequestId={CR} />);
    await userEvent.click(await screen.findByRole("button", { name: "Show the evidence" }));
    expect(
      await screen.findByRole("img", { name: "Evidence document for this correction" }),
    ).toHaveAttribute("src", "https://files.schoolos.example/t/x/evidence.png?sig=synthetic");
  });
});

describe("new change request (US-601 AC1, FR-CR-001, BR-04)", () => {
  const nameAttribute = {
    key: "full_name",
    data_type: "text",
    classification: "C2",
    is_identity: true,
    label_en: "Full name",
    label_te: "పూర్తి పేరు",
    sort_order: 1,
    allowed_sources: ["admission_register", "birth_certificate"],
    allowed_values: null,
    precedence: [],
    is_global: true,
  };
  const dobAttribute = {
    ...nameAttribute,
    key: "date_of_birth",
    data_type: "date",
    label_en: "Date of birth",
    label_te: "పుట్టిన తేదీ",
    sort_order: 2,
  };

  function setup(permissions = [REQUEST, "document.upload"]) {
    stub.routes["GET /bff/api/v1/me"] = () => Response.json(me(permissions));
    stub.routes["GET /bff/api/v1/attributes"] = () =>
      Response.json([
        nameAttribute,
        dobAttribute,
        { ...nameAttribute, key: "blood_group", is_identity: false, label_en: "Blood group" },
      ]);
  }

  it("dates are typed DD/MM/YYYY and Aadhaar-like text is refused (invariant 4)", () => {
    const schema = buildRequestSchema(
      { ...dobAttribute, data_type: "date" } as Parameters<typeof buildRequestSchema>[0],
      "2026-09-27",
    );
    const base = {
      student_id: STUDENT,
      attribute_key: "date_of_birth",
      target_source: "admission_register",
      reason: "Birth certificate shows another date.",
    };
    const ok = schema.safeParse({ ...base, new_value: "01/06/2012" });
    expect(ok.success && ok.data.new_value).toBe("2012-06-01");
    const future = schema.safeParse({ ...base, new_value: "01/06/2030" });
    expect(future.error?.issues[0]?.message).toBe("dateInFuture");
    expect(displayDateToIso("31/02/2012")).toBeNull();
    // A synthetic Verhoeff-valid 12-digit number (as core.redaction checks on the server).
    const stem = "23412341234";
    const check = [..."0123456789"].find((digit) => verhoeffValid(`${stem}${digit}`)) ?? "";
    const aadhaar = schema.safeParse({
      ...base,
      new_value: "01/06/2012",
      reason: `Checked Aadhaar ${stem}${check} in the office`,
    });
    expect(aadhaar.error?.issues.map((issue) => [issue.path[0], issue.message])).toEqual([
      ["reason", "noAadhaar"],
    ]);
  });

  it("offers identity fields only and asks for evidence before sending anything", async () => {
    setup();
    renderWithIntl(
      <NewChangeRequestScreen
        params={{ studentId: STUDENT, attributeKey: null, findingId: null }}
      />,
    );
    const field = await screen.findByRole("combobox", { name: "Field" });
    expect(within(field).queryByRole("option", { name: "Blood group" })).toBeNull();
    await userEvent.selectOptions(field, "full_name");
    await userEvent.type(screen.getByRole("textbox", { name: "Correct value" }), "Asha Test");
    await userEvent.type(
      screen.getByRole("textbox", { name: "Reason" }),
      "Spelling differs from the birth certificate.",
    );
    await userEvent.click(screen.getByRole("button", { name: "Send for approval" }));
    expect(
      await screen.findByText("Choose the evidence file (a scan or photo of the document)."),
    ).toBeInTheDocument();
    expect(stub.calls.filter((call) => call.method === "POST")).toHaveLength(0);
  });

  it("uploads the evidence, registers it, then submits the request (nothing is applied)", async () => {
    setup();
    const upload = "0192f3a4-0000-7000-8000-00000000u001";
    const doc = "0192f3a4-0000-7000-8000-00000000d0c9";
    stub.routes["POST /bff/api/v1/documents/uploads"] = () =>
      Response.json(
        {
          upload_id: upload,
          url: "https://files.schoolos.example/bucket",
          fields: { key: "t/x/evidence", policy: "synthetic" },
          expires_at: "2026-09-26T05:10:00Z",
          max_bytes: 26214400,
          purpose: "evidence",
          document_id: null,
          batch_id: null,
        },
        { status: 201 },
      );
    const stored: string[] = [];
    setStorageSendForTesting(async (url, init) => {
      stored.push(`${init.method ?? "GET"} ${url}`);
      return new Response(null, { status: 204 });
    });
    stub.routes["POST /bff/api/v1/documents"] = () => Response.json({ id: doc }, { status: 202 });
    stub.routes["POST /bff/api/v1/change-requests"] = () =>
      Response.json(changeRequest({ requested_by: ME_MEMBERSHIP }), { status: 201 });
    renderWithIntl(
      <NewChangeRequestScreen
        params={{
          studentId: STUDENT,
          attributeKey: "full_name",
          findingId: "0192f3a4-0000-7000-8000-00000000f001",
        }}
      />,
    );
    await screen.findByRole("combobox", { name: "Field" });
    await userEvent.type(screen.getByRole("textbox", { name: "Correct value" }), "Asha Test");
    await userEvent.type(
      screen.getByRole("textbox", { name: "Reason" }),
      "Spelling differs from the birth certificate.",
    );
    const file = new File(["%PDF-1.4 synthetic"], "certificate.pdf", { type: "application/pdf" });
    await userEvent.upload(screen.getByLabelText("Scan or photo"), file);
    await userEvent.click(screen.getByRole("button", { name: "Send for approval" }));
    expect(await screen.findByText("Request sent")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to the problem" })).toHaveAttribute(
      "href",
      "/en/findings/0192f3a4-0000-7000-8000-00000000f001",
    );
    setStorageSendForTesting(undefined);
    expect(stored).toEqual(["POST https://files.schoolos.example/bucket"]);
    const presign = JSON.parse(stub.callsTo("POST /bff/api/v1/documents/uploads")[0]?.body ?? "{}");
    expect(presign).toMatchObject({ purpose: "evidence", content_type: "application/pdf" });
    const submitted = stub.callsTo("POST /bff/api/v1/change-requests")[0];
    expect(submitted?.headers.get("idempotency-key")).toMatch(/[0-9a-f-]{36}/);
    expect(JSON.parse(submitted?.body ?? "{}")).toEqual({
      student_id: STUDENT,
      attribute_key: "full_name",
      target_source: "admission_register",
      reason: "Spelling differs from the birth certificate.",
      evidence_document_id: doc,
      new_value: "Asha Test",
    });
    // Invariant 6: the form never writes the student record itself.
    expect(stub.calls.some((call) => call.url.pathname.includes("/values"))).toBe(false);
    expect(stub.calls.some((call) => call.method === "PATCH")).toBe(false);
  });

  it("sends a date as new_value_date and can use a document already uploaded", async () => {
    setup([REQUEST, "document.read"]);
    const doc = "0192f3a4-0000-7000-8000-00000000d0c7";
    stub.routes["GET /bff/api/v1/documents"] = () =>
      page([
        {
          id: doc,
          title: "Birth certificate scan",
          created_at: "2026-09-20T05:00:00Z",
          current_version: { status: "ready" },
        },
      ]);
    stub.routes["POST /bff/api/v1/change-requests"] = () =>
      Response.json(changeRequest({ requested_by: ME_MEMBERSHIP }), { status: 201 });
    renderWithIntl(
      <NewChangeRequestScreen
        params={{ studentId: STUDENT, attributeKey: "date_of_birth", findingId: null }}
      />,
    );
    await screen.findByRole("combobox", { name: "Field" });
    await userEvent.type(screen.getByRole("textbox", { name: "Correct value" }), "01/06/2012");
    await userEvent.type(
      screen.getByRole("textbox", { name: "Reason" }),
      "Birth certificate shows a different date.",
    );
    await userEvent.click(screen.getByRole("radio", { name: "Use a document already uploaded" }));
    const select = await screen.findByRole("combobox", { name: "Document" });
    await screen.findByRole("option", { name: /Birth certificate scan/ });
    await userEvent.selectOptions(select, doc);
    await userEvent.click(screen.getByRole("button", { name: "Send for approval" }));
    expect(await screen.findByText("Request sent")).toBeInTheDocument();
    const body = JSON.parse(stub.callsTo("POST /bff/api/v1/change-requests")[0]?.body ?? "{}");
    expect(body).toMatchObject({ new_value_date: "2012-06-01", evidence_document_id: doc });
    expect(body.new_value).toBeUndefined();
    expect(stub.callsTo("POST /bff/api/v1/documents/uploads")).toHaveLength(0);
  });

  it("says so when the user may not request corrections", async () => {
    setup(["student.read_basic"]);
    renderWithIntl(
      <NewChangeRequestScreen params={{ studentId: null, attributeKey: null, findingId: null }} />,
    );
    expect(await screen.findByText("You can't request corrections")).toBeInTheDocument();
  });
});
