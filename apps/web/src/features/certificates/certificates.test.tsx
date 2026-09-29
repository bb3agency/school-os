import type { components } from "@schoolos/api-client";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { notificationHref } from "@/features/notifications/data";
import { installBffStub, page, problem, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { intlErrors, renderWithIntl } from "@/test/render";
import { me, ME_MEMBERSHIP, OTHER_MEMBERSHIP, STUDENT } from "@/test/school-fixtures";
import { CertificateDetailScreen } from "./CertificateDetailScreen";
import { CertificatesScreen } from "./CertificatesScreen";
import { parseCertificateFilters, parseIssueType } from "./filters";
import { buildInputSchema, IssueCertificateScreen } from "./IssueCertificateScreen";
import { LetterheadCard } from "./LetterheadCard";
import { registerQuery, RegistersScreen } from "./RegistersScreen";

type Schemas = components["schemas"];

const push = vi.hoisted(() => vi.fn());
vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => "/en/certificates",
    useRouter: () => ({ push, replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
    useSearchParams: () => new URLSearchParams(),
    useParams: () => ({ locale: "en" }),
  };
});

// Synthetic data only (CLAUDE.md §6.11).
const CERT = "0192f3a4-0000-7000-8000-0000000ce001";
const FINDING = "0192f3a4-0000-7000-8000-0000000f0001";
const YEAR = "0192f3a4-0000-7000-8000-0000000000a1";
const READ = "certificate.read";
const ISSUE = "certificate.issue";
const APPROVE = "certificate.approve";

function certificate(
  overrides: Partial<Schemas["CertificateOut"]> = {},
): Schemas["CertificateOut"] {
  return {
    id: CERT,
    student_id: STUDENT,
    certificate_type: "transfer",
    status: "pending",
    requires_approval: true,
    inputs: {
      leaving_date: "2026-09-29",
      leaving_reason: "parent_transferred",
      promotion: "promoted",
      conduct: "good",
    },
    original_certificate_id: null,
    duplicate_no: null,
    duplicate_reason: null,
    academic_year_id: null,
    serial: null,
    student_name: "Synthetica Asha Tset",
    admission_no: "A-101",
    content: null,
    requested_by: OTHER_MEMBERSHIP,
    requested_at: "2026-09-29T04:30:00Z",
    decided_by: null,
    decided_at: null,
    decision_note: null,
    issued_by: null,
    issued_at: null,
    cancelled_by: null,
    cancelled_at: null,
    cancel_reason: null,
    document_id: null,
    pdf_status: "none",
    version: 1,
    can_approve: false,
    can_withdraw: false,
    can_cancel: false,
    can_duplicate: false,
    ...overrides,
  };
}

function issued(overrides: Partial<Schemas["CertificateOut"]> = {}): Schemas["CertificateOut"] {
  return certificate({
    certificate_type: "bonafide",
    requires_approval: false,
    status: "issued",
    serial: "BC/2026-27/0007",
    academic_year_id: YEAR,
    issued_at: "2026-09-29T05:00:00Z",
    issued_by: ME_MEMBERSHIP,
    pdf_status: "ready",
    document_id: "0192f3a4-0000-7000-8000-0000000d0001",
    inputs: { purpose: "bus_pass" },
    content: {
      certificate_type: "bonafide",
      title_en: "Bonafide certificate",
      title_te: "బోనఫైడ్ ధృవీకరణ పత్రం",
      serial: "BC/2026-27/0007",
      academic_year_label: "2026-27",
      issued_on: "2026-09-29",
      school_name_en: "Synthetic Model School",
      school_name_te: "",
      school_address_en: "",
      school_address_te: "",
      school_affiliation: "",
      school_place: "",
      student_name: "Synthetica Asha Tset",
      admission_no: "A-101",
      class_label_en: "Class IX A",
      class_label_te: "9వ తరగతి A",
      fields: [
        {
          key: "full_name",
          label_en: "Full name",
          label_te: "పూర్తి పేరు",
          value: "Synthetica Asha Tset",
        },
        { key: "dob", label_en: "Date of birth", label_te: "పుట్టిన తేదీ", value: "14/03/2012" },
      ],
      details: [
        { key: "purpose", label_en: "Purpose", label_te: "ప్రయోజనం", value: "Bus pass" },
        { key: "purpose_te", label_en: "", label_te: "", value: "బస్ పాస్" },
      ],
      blanks: [],
    },
    ...overrides,
  });
}

const TYPES: Schemas["CertificateTypeOut"][] = [
  {
    key: "transfer",
    label_en: "Transfer certificate",
    label_te: "బదిలీ ధృవీకరణ పత్రం",
    requires_approval: true,
    ends_enrolment: true,
    printed: ["full_name", "dob"],
    inputs: [
      { key: "leaving_date", kind: "date", required: true, max_length: null, choices: [] },
      {
        key: "leaving_reason",
        kind: "choice",
        required: true,
        max_length: null,
        choices: [
          {
            value: "parent_request",
            label_en: "At the request of the parent",
            label_te: "తల్లిదండ్రుల అభ్యర్థన మేరకు",
          },
          {
            value: "parent_transferred",
            label_en: "Parent transferred",
            label_te: "తల్లిదండ్రుల బదిలీ",
          },
        ],
      },
      {
        key: "promotion",
        kind: "choice",
        required: true,
        max_length: null,
        choices: [{ value: "promoted", label_en: "Yes, promoted", label_te: "అవును" }],
      },
      {
        key: "conduct",
        kind: "choice",
        required: true,
        max_length: null,
        choices: [{ value: "good", label_en: "Good", label_te: "మంచిది" }],
      },
      { key: "remarks", kind: "text", required: false, max_length: 200, choices: [] },
    ],
  },
  {
    key: "bonafide",
    label_en: "Bonafide certificate",
    label_te: "బోనఫైడ్ ధృవీకరణ పత్రం",
    requires_approval: false,
    ends_enrolment: false,
    printed: ["full_name", "dob"],
    inputs: [
      {
        key: "purpose",
        kind: "choice",
        required: true,
        max_length: null,
        choices: [{ value: "bus_pass", label_en: "Bus pass", label_te: "బస్ పాస్" }],
      },
      { key: "purpose_note", kind: "text", required: false, max_length: 120, choices: [] },
    ],
  },
];

function preview(
  overrides: Partial<Schemas["CertificatePreview"]> = {},
): Schemas["CertificatePreview"] {
  return {
    student_id: STUDENT,
    certificate_type: "bonafide",
    requires_approval: false,
    fields: [
      {
        key: "full_name",
        label_en: "Full name",
        label_te: "పూర్తి పేరు",
        value: "Synthetica Asha Tset",
        source: "admission_register",
        verified: false,
        provisional: true,
      },
      {
        key: "dob",
        label_en: "Date of birth",
        label_te: "పుట్టిన తేదీ",
        value: "14/03/2012",
        source: "admission_register",
        verified: true,
        provisional: false,
      },
    ],
    class_label: "Class IX A",
    academic_year_label: "2026-27",
    blockers: [],
    warnings: [{ code: "provisional_value", attribute_key: "full_name" }],
    can_issue: true,
    ...overrides,
  };
}

let stub: BffStub;

beforeEach(() => {
  push.mockReset();
  stub = installBffStub("staff");
  stub.routes["GET /bff/api/v1/certificates/types"] = () => Response.json(TYPES);
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
          value: "Synthetica Asha Tset",
          source: "admission_register",
          verified: false,
          provisional: true,
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

describe("URL parameters and helpers", () => {
  it("drops unknown filter values and non-UUID students", () => {
    expect(
      parseCertificateFilters({ status: "bogus", certificate_type: "passport", student_id: "x" }),
    ).toEqual({ status: null, certificateType: null, studentId: null });
    expect(
      parseCertificateFilters({
        status: "issued",
        certificate_type: "transfer",
        student_id: STUDENT,
      }),
    ).toEqual({ status: "issued", certificateType: "transfer", studentId: STUDENT });
    expect(parseIssueType({ type: "study" })).toBe("study");
    expect(parseIssueType({ type: "../x" })).toBeNull();
  });

  it("register links carry only IDs and codes (docs/09 §2)", () => {
    expect(registerQuery("transfer", YEAR, "study")).toBe(`?academic_year_id=${YEAR}`);
    expect(registerQuery("certificates", YEAR, "study")).toBe(
      `?academic_year_id=${YEAR}&certificate_type=study`,
    );
    expect(registerQuery("admission", "", "")).toBe("");
  });

  it("notifications about certificates open the certificate (US-1102)", () => {
    expect(notificationHref({ resource_type: "certificate", resource_id: CERT })).toBe(
      `/certificates/${CERT}`,
    );
  });

  it("input schemas follow the type's inputs (required, choices, length)", () => {
    const schema = buildInputSchema(TYPES[0] as Schemas["CertificateTypeOut"]);
    expect(
      schema.safeParse({ leaving_date: "", leaving_reason: "", promotion: "", conduct: "" })
        .success,
    ).toBe(false);
    const ok = schema.safeParse({
      leaving_date: "2026-09-29",
      leaving_reason: "parent_request",
      promotion: "promoted",
      conduct: "good",
      remarks: "",
    });
    expect(ok.success && ok.data).toEqual({
      leaving_date: "2026-09-29",
      leaving_reason: "parent_request",
      promotion: "promoted",
      conduct: "good",
    });
    expect(
      schema.safeParse({
        leaving_date: "2026-09-29",
        leaving_reason: "lottery",
        promotion: "promoted",
        conduct: "good",
      }).success,
    ).toBe(false);
  });
});

describe("certificate list (US-1101..US-1105)", () => {
  it("names certificates by type and serial and marks those waiting for this approver", async () => {
    stub.routes["GET /bff/api/v1/me"] = () => Response.json(me([READ, APPROVE, "register.read"]));
    stub.routes["GET /bff/api/v1/certificates"] = () =>
      page([
        certificate({ can_approve: true }),
        issued({ id: "0192f3a4-0000-7000-8000-0000000ce002" }),
      ]);
    renderWithIntl(<CertificatesScreen filters={parseCertificateFilters({})} />);
    expect(await screen.findByText("Bonafide certificate BC/2026-27/0007")).toBeInTheDocument();
    const rows = screen.getAllByRole("row");
    const pending = rows.find((row) => within(row).queryByText("Transfer certificate"));
    expect(pending && within(pending).getByText("Waiting for you")).toBeInTheDocument();
    expect(
      pending && within(pending).getByText("Gets its serial number when approved"),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Registers" })).toHaveAttribute(
      "href",
      "/en/registers",
    );
  });

  it("sends the student filter to the API and offers issuing for that student", async () => {
    stub.routes["GET /bff/api/v1/me"] = () => Response.json(me([ISSUE]));
    stub.routes["GET /bff/api/v1/certificates"] = () => page([]);
    renderWithIntl(
      <CertificatesScreen filters={parseCertificateFilters({ student_id: STUDENT })} />,
    );
    expect(await screen.findByText("Showing one student only.")).toBeInTheDocument();
    expect(await screen.findByRole("link", { name: "Issue a certificate" })).toHaveAttribute(
      "href",
      `/en/students/${STUDENT}/certificates/new`,
    );
    await waitFor(() => expect(stub.callsTo("GET /bff/api/v1/certificates")).toHaveLength(1));
    expect(
      stub.callsTo("GET /bff/api/v1/certificates")[0]?.url.searchParams.get("student_id"),
    ).toBe(STUDENT);
  });
});

describe("certificate detail (US-1102..US-1107)", () => {
  function detail(permissions: string[], body: Schemas["CertificateOut"]) {
    stub.routes["GET /bff/api/v1/me"] = () => Response.json(me(permissions));
    stub.routes[`GET /bff/api/v1/certificates/${CERT}`] = () => Response.json(body);
  }

  it("an approver approves a TC with If-Match (step-up note shown)", async () => {
    detail([READ, APPROVE], certificate({ can_approve: true }));
    stub.routes[`POST /bff/api/v1/certificates/${CERT}/approve`] = () =>
      Response.json(certificate({ status: "issued", serial: "TC/2026-27/0001" }));
    renderWithIntl(<CertificateDetailScreen certificateId={CERT} />);
    await userEvent.click(await screen.findByRole("button", { name: "Approve and issue" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/takes the student off the rolls/)).toBeInTheDocument();
    await userEvent.type(
      within(dialog).getByLabelText("Note (optional)"),
      "Checked with the register",
    );
    await userEvent.click(within(dialog).getByRole("button", { name: "Approve and issue" }));
    await waitFor(() =>
      expect(stub.callsTo(`POST /bff/api/v1/certificates/${CERT}/approve`)).toHaveLength(1),
    );
    const call = stub.callsTo(`POST /bff/api/v1/certificates/${CERT}/approve`)[0];
    expect(call?.headers.get("if-match")).toBe('W/"1"');
    expect(JSON.parse(call?.body ?? "{}")).toEqual({ note: "Checked with the register" });
    expect(screen.getByText("Reason for leaving")).toBeInTheDocument();
    expect(screen.getByText("Parent transferred")).toBeInTheDocument();
  });

  it("the requester sees their own request, can withdraw it and is never offered approval", async () => {
    detail(
      [READ, ISSUE, APPROVE],
      certificate({ requested_by: ME_MEMBERSHIP, can_withdraw: true, can_approve: false }),
    );
    renderWithIntl(<CertificateDetailScreen certificateId={CERT} />);
    expect(await screen.findByText("You prepared this")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve and issue" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Withdraw" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Open print view/ })).toHaveAttribute(
      "href",
      `/bff/api/v1/certificates/${CERT}/print`,
    );
  });

  it("rejecting needs a reason of at least 10 characters", async () => {
    detail([READ, APPROVE], certificate({ can_approve: true }));
    renderWithIntl(<CertificateDetailScreen certificateId={CERT} />);
    await userEvent.click(await screen.findByRole("button", { name: "Reject" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText("Reason"), "short");
    await userEvent.click(within(dialog).getByRole("button", { name: "Reject" }));
    expect(stub.callsTo(`POST /bff/api/v1/certificates/${CERT}/reject`)).toHaveLength(0);
  });

  it("an issued certificate shows its frozen values, downloads the PDF and can be duplicated", async () => {
    detail([READ, ISSUE], issued({ can_duplicate: true }));
    stub.routes[`GET /bff/api/v1/certificates/${CERT}/download-url`] = () =>
      Response.json({
        url: "https://files.synthetic.example/x.pdf",
        expires_at: "2026-09-29T05:05:00Z",
        filename: "bonafide-BC-2026-27-0007.pdf",
      });
    stub.routes[`POST /bff/api/v1/certificates/${CERT}/duplicates`] = () =>
      Response.json(issued({ id: "0192f3a4-0000-7000-8000-0000000ce009", duplicate_no: 1 }), {
        status: 201,
      });
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign });
    renderWithIntl(<CertificateDetailScreen certificateId={CERT} />);
    expect(
      await screen.findByRole("heading", {
        level: 1,
        name: "Bonafide certificate BC/2026-27/0007",
      }),
    ).toBeInTheDocument();
    expect(screen.getByText("What the certificate prints")).toBeInTheDocument();
    expect(screen.getByText("14/03/2012")).toBeInTheDocument();
    expect(screen.queryByText("purpose_te")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Cancel certificate" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Download PDF" }));
    await waitFor(() =>
      expect(assign).toHaveBeenCalledWith("https://files.synthetic.example/x.pdf"),
    );
    await userEvent.click(screen.getByRole("button", { name: "Issue a duplicate" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(
      within(dialog).getByLabelText("Why is a duplicate needed?"),
      "Original lost in the rain",
    );
    await userEvent.click(within(dialog).getByRole("button", { name: "Issue a duplicate" }));
    await waitFor(() =>
      expect(push).toHaveBeenCalledWith("/en/certificates/0192f3a4-0000-7000-8000-0000000ce009"),
    );
    const call = stub.callsTo(`POST /bff/api/v1/certificates/${CERT}/duplicates`)[0];
    expect(call?.headers.get("idempotency-key")).toBeTruthy();
    vi.unstubAllGlobals();
  });

  it("an approver cancels an issued certificate with a reason and If-Match", async () => {
    detail([READ, APPROVE], issued({ can_cancel: true, version: 3 }));
    stub.routes[`POST /bff/api/v1/certificates/${CERT}/cancel`] = () =>
      Response.json(issued({ status: "cancelled", cancel_reason: "Wrong purpose printed" }));
    renderWithIntl(<CertificateDetailScreen certificateId={CERT} />);
    await userEvent.click(await screen.findByRole("button", { name: "Cancel certificate" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(
      within(dialog).getByLabelText("Reason for cancelling"),
      "Wrong purpose printed",
    );
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancel certificate" }));
    await waitFor(() =>
      expect(stub.callsTo(`POST /bff/api/v1/certificates/${CERT}/cancel`)).toHaveLength(1),
    );
    expect(
      stub.callsTo(`POST /bff/api/v1/certificates/${CERT}/cancel`)[0]?.headers.get("if-match"),
    ).toBe('W/"3"');
  });

  it("renders in Telugu without missing messages", async () => {
    detail([READ, APPROVE], certificate({ can_approve: true }));
    renderWithIntl(<CertificateDetailScreen certificateId={CERT} />, "te");
    expect(await screen.findByRole("button", { name: "ఆమోదించి జారీ చేయండి" })).toBeInTheDocument();
    expect(screen.getByText("తల్లిదండ్రుల బదిలీ")).toBeInTheDocument();
  });
});

describe("issue a certificate (US-1101, US-1102, FR-CERT-002)", () => {
  it("shows what will be printed with sources and flags unverified values", async () => {
    stub.routes["GET /bff/api/v1/me"] = () => Response.json(me([ISSUE, "student.read_basic"]));
    stub.routes[`GET /bff/api/v1/students/${STUDENT}/certificates/preview`] = () =>
      Response.json(preview());
    renderWithIntl(<IssueCertificateScreen studentId={STUDENT} initialType="bonafide" />);
    expect(await screen.findByText("14/03/2012")).toBeInTheDocument();
    expect(screen.getByText("Not yet verified")).toBeInTheDocument();
    expect(screen.getByText(/Full name\. Check them on paper/)).toBeInTheDocument();
    const table = screen.getByRole("table");
    expect(within(table).getAllByText("Register")).toHaveLength(2);
    expect(within(table).getByText("Not verified")).toBeInTheDocument();
    const call = stub.callsTo(`GET /bff/api/v1/students/${STUDENT}/certificates/preview`)[0];
    expect(call?.url.searchParams.get("certificate_type")).toBe("bonafide");
  });

  it("lists blockers with links to the finding and a correction request, and cannot issue", async () => {
    stub.routes["GET /bff/api/v1/me"] = () => Response.json(me([ISSUE]));
    stub.routes[`GET /bff/api/v1/students/${STUDENT}/certificates/preview`] = () =>
      Response.json(
        preview({
          can_issue: false,
          blockers: [
            { code: "dq_blocker", attribute_key: "dob", finding_id: FINDING, rule_id: "DQ-002" },
            { code: "missing_value", attribute_key: "full_name", finding_id: null, rule_id: null },
          ],
        }),
      );
    renderWithIntl(<IssueCertificateScreen studentId={STUDENT} initialType="bonafide" />);
    expect(await screen.findByText("Fix these first")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open the finding" })).toHaveAttribute(
      "href",
      `/en/findings/${FINDING}`,
    );
    const corrections = screen.getAllByRole("link", { name: "Request a correction" });
    expect(corrections.map((link) => link.getAttribute("href"))).toEqual([
      `/en/change-requests/new?student_id=${STUDENT}&attribute_key=dob`,
      `/en/change-requests/new?student_id=${STUDENT}&attribute_key=full_name`,
    ]);
    expect(
      screen.getByText(/A data check found a blocking problem \(DQ-002\) in Date of birth/),
    ).toBeInTheDocument();
    expect(screen.getByText(/Full name is empty in the admission register/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Issue certificate" })).toBeDisabled();
  });

  it("issues a bonafide certificate with its inputs and opens it", async () => {
    stub.routes["GET /bff/api/v1/me"] = () => Response.json(me([ISSUE]));
    stub.routes[`GET /bff/api/v1/students/${STUDENT}/certificates/preview`] = () =>
      Response.json(preview());
    stub.routes[`POST /bff/api/v1/students/${STUDENT}/certificates`] = () =>
      Response.json(issued(), { status: 201 });
    renderWithIntl(<IssueCertificateScreen studentId={STUDENT} initialType="bonafide" />);
    await screen.findByText("14/03/2012");
    await userEvent.selectOptions(screen.getByLabelText("Purpose"), "bus_pass");
    await userEvent.click(screen.getByRole("button", { name: "Issue certificate" }));
    await waitFor(() => expect(push).toHaveBeenCalledWith(`/en/certificates/${CERT}`));
    const call = stub.callsTo(`POST /bff/api/v1/students/${STUDENT}/certificates`)[0];
    expect(JSON.parse(call?.body ?? "{}")).toEqual({
      certificate_type: "bonafide",
      inputs: { purpose: "bus_pass" },
    });
    expect(call?.headers.get("idempotency-key")).toBeTruthy();
  });

  it("a transfer certificate goes for approval and warns that the student leaves the rolls", async () => {
    stub.routes["GET /bff/api/v1/me"] = () => Response.json(me([ISSUE]));
    stub.routes[`GET /bff/api/v1/students/${STUDENT}/certificates/preview`] = () =>
      Response.json(preview({ certificate_type: "transfer", requires_approval: true }));
    renderWithIntl(<IssueCertificateScreen studentId={STUDENT} initialType="transfer" />);
    expect(await screen.findByRole("button", { name: "Send for approval" })).toBeInTheDocument();
    expect(screen.getByText("The student leaves the rolls")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Send for approval" }));
    expect((await screen.findAllByText("Fill in this field.")).length).toBeGreaterThan(0);
    expect(stub.callsTo(`POST /bff/api/v1/students/${STUDENT}/certificates`)).toHaveLength(0);
  });

  it("shows why the API refused (409 certificate_blocked)", async () => {
    stub.routes["GET /bff/api/v1/me"] = () => Response.json(me([ISSUE]));
    stub.routes[`GET /bff/api/v1/students/${STUDENT}/certificates/preview`] = () =>
      Response.json(preview());
    stub.routes[`POST /bff/api/v1/students/${STUDENT}/certificates`] = () =>
      problem(409, "certificate_blocked");
    renderWithIntl(<IssueCertificateScreen studentId={STUDENT} initialType="bonafide" />);
    await screen.findByText("14/03/2012");
    await userEvent.selectOptions(screen.getByLabelText("Purpose"), "bus_pass");
    await userEvent.click(screen.getByRole("button", { name: "Issue certificate" }));
    expect(await screen.findByText("This certificate cannot be issued yet")).toBeInTheDocument();
    expect(push).not.toHaveBeenCalled();
  });
});

describe("registers (US-1106, FR-REG-004)", () => {
  it("prepares a register (step-up handled by the client) and then links to the print view", async () => {
    stub.routes["GET /bff/api/v1/me"] = () => Response.json(me(["register.read"]));
    stub.routes["GET /bff/api/v1/academic-years"] = () =>
      page([
        {
          id: YEAR,
          label: "2026-27",
          starts_on: "2026-06-01",
          ends_on: "2027-04-30",
          is_current: true,
          version: 1,
          created_at: "2026-06-01T00:00:00Z",
          updated_at: "2026-06-01T00:00:00Z",
        },
      ]);
    stub.routes["GET /bff/api/v1/registers/transfer-certificates"] = () =>
      new Response(null, { status: 204 });
    renderWithIntl(<RegistersScreen />);
    const card = (await screen.findByRole("region", {
      name: "TC register (counterfoil)",
    })) as HTMLElement;
    await userEvent.click(within(card).getByRole("button", { name: "Prepare print view" }));
    const link = await within(card).findByRole("link", { name: /Open print view/ });
    expect(link).toHaveAttribute(
      "href",
      `/bff/api/v1/registers/transfer-certificates?academic_year_id=${YEAR}`,
    );
    expect(link).toHaveAttribute("target", "_blank");
    // "Prepare" only checks (204, not audited); the print view opened from the link is the one
    // audited view, so the screen never fetches the register page itself (FR-REG-004).
    const calls = stub.callsTo("GET /bff/api/v1/registers/transfer-certificates");
    expect(calls).toHaveLength(1);
    expect(calls[0]?.url.searchParams.get("academic_year_id")).toBe(YEAR);
    expect(calls[0]?.url.searchParams.get("check")).toBe("true");
    expect(link.getAttribute("href")).not.toContain("check");
  });
});

describe("certificate letterhead (US-1108, FR-CERT-013)", () => {
  const tenant = {
    id: "0192f3a4-0000-7000-8000-000000000001",
    code: "synthetic",
    name: "Synthetic Model School",
    boards: ["icse"],
    state_code: "37",
    status: "active",
    plan_tier: "shared",
    deployment_mode: "shared",
    version: 4,
    settings: {
      languages: ["en", "te"],
      date_format: "DD/MM/YYYY",
      idle_timeout_minutes: 15,
      ai_features_enabled: true,
      ai_monthly_budget_inr: 5000,
      certificate_letterhead: {
        school_name_te: "",
        address_en: "",
        address_te: "",
        affiliation: "",
        place: "",
      },
    },
  } as unknown as Parameters<typeof LetterheadCard>[0]["tenant"];

  it("a manager saves the letterhead with If-Match", async () => {
    stub.routes["PATCH /bff/api/v1/tenant"] = () => Response.json(tenant);
    renderWithIntl(<LetterheadCard tenant={tenant} manage tenantKey={["staff", "tenant"]} />);
    await userEvent.type(screen.getByLabelText("Place"), "Guntur");
    await userEvent.type(screen.getByLabelText("School name in Telugu"), "కృత్రిమ మోడల్ పాఠశాల");
    await userEvent.click(screen.getByRole("button", { name: "Save letterhead" }));
    await waitFor(() => expect(stub.callsTo("PATCH /bff/api/v1/tenant")).toHaveLength(1));
    const call = stub.callsTo("PATCH /bff/api/v1/tenant")[0];
    expect(call?.headers.get("if-match")).toBe('W/"4"');
    expect(JSON.parse(call?.body ?? "{}")).toEqual({
      certificate_letterhead: {
        school_name_te: "కృత్రిమ మోడల్ పాఠశాల",
        address_en: "",
        address_te: "",
        affiliation: "",
        place: "Guntur",
      },
    });
    expect(
      await screen.findByText("Letterhead saved. New certificates use it."),
    ).toBeInTheDocument();
  });

  it("everyone else sees it read-only", () => {
    renderWithIntl(
      <LetterheadCard tenant={tenant} manage={false} tenantKey={["staff", "tenant"]} />,
    );
    expect(screen.queryByRole("button", { name: "Save letterhead" })).not.toBeInTheDocument();
    expect(
      screen.getByText(/The English name is the school's name: Synthetic Model School\./),
    ).toBeInTheDocument();
    expect(screen.getByText("Place")).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });
});
