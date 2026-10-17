import type { components } from "@schoolos/api-client";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { installBffStub, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { intlErrors, renderWithIntl } from "@/test/render";
import { me, STUDENT } from "@/test/school-fixtures";
import { CertificateFormatCard } from "./CertificateFormatCard";
import { IssueCertificateScreen } from "./IssueCertificateScreen";
import { VerificationCard } from "./VerificationCard";

/**
 * R2-1A screens (ADR-0045; US-2001, US-2002, US-2004; FR-CERT-015..023): the verification card
 * on a certificate, the TC format card in settings and a TC prepared in the AP format.
 * Synthetic data only (CLAUDE.md §6.11).
 */

type Schemas = components["schemas"];

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => "/certificates",
    useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
    useSearchParams: () => new URLSearchParams(),
    useParams: () => ({ locale: "en" }),
  };
});

const CERT = "0192f3a4-0000-7000-8000-0000000ce101";
const VERIFY_PATH = `/bff/api/v1/certificates/${CERT}/verification`;

function verification(overrides: Partial<Schemas["VerificationOut"]> = {}) {
  return {
    certificate_id: CERT,
    status: "active",
    code: "7K4M-Q2XP-9HD3-RT6W",
    url: "https://verify.synthetic.example/v/7K4MQ2XP9HD3RT6W",
    created_reason: "issued",
    created_at: "2026-10-17T05:00:00Z",
    check_count: 3,
    last_checked_at: "2026-10-17T06:00:00Z",
    withdrawn_at: null,
    withdraw_reason: null,
    version: 2,
    can_add: false,
    can_withdraw: true,
    ...overrides,
  } satisfies Schemas["VerificationOut"];
}

let stub: BffStub;

beforeEach(() => {
  stub = installBffStub("staff");
  stub.routes["GET /bff/api/v1/me"] = () =>
    Response.json(me(["certificate.read", "certificate.approve"]));
});
afterEach(() => {
  uninstallBffStub();
  expect(intlErrors).toEqual([]);
});

describe("verification card (US-2002, US-2004)", () => {
  it("shows the printed code, the link and how often it was checked", async () => {
    stub.routes[`GET ${VERIFY_PATH}`] = () => Response.json(verification());
    renderWithIntl(<VerificationCard certificateId={CERT} />);
    expect(await screen.findByText("7K4M-Q2XP-9HD3-RT6W")).toBeInTheDocument();
    expect(screen.getByText("Checked 3 times")).toBeInTheDocument();
    expect(screen.getByText("Made when the certificate was issued")).toBeInTheDocument();
    expect(
      screen.getByText("https://verify.synthetic.example/v/7K4MQ2XP9HD3RT6W"),
    ).toBeInTheDocument();
  });

  it("the principal withdraws a misused code with a reason and If-Match", async () => {
    stub.routes[`GET ${VERIFY_PATH}`] = () => Response.json(verification());
    stub.routes[`POST ${VERIFY_PATH}/withdraw`] = () =>
      Response.json(verification({ status: "withdrawn", can_withdraw: false, version: 3 }));
    renderWithIntl(<VerificationCard certificateId={CERT} />);
    await userEvent.click(await screen.findByRole("button", { name: "Withdraw code" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText("Reason"), "short");
    await userEvent.click(within(dialog).getByRole("button", { name: "Withdraw code" }));
    expect(stub.callsTo(`POST ${VERIFY_PATH}/withdraw`)).toHaveLength(0);
    await userEvent.type(within(dialog).getByLabelText("Reason"), " and posted online by mistake");
    await userEvent.click(within(dialog).getByRole("button", { name: "Withdraw code" }));
    await waitFor(() => expect(stub.callsTo(`POST ${VERIFY_PATH}/withdraw`)).toHaveLength(1));
    const call = stub.callsTo(`POST ${VERIFY_PATH}/withdraw`)[0];
    expect(call?.headers.get("if-match")).toBe('W/"2"');
    expect(JSON.parse(call?.body ?? "{}")).toEqual({
      reason: "short and posted online by mistake",
    });
  });

  it("a certificate from before R2 offers adding a code; a withdrawn one says so", async () => {
    stub.routes[`GET ${VERIFY_PATH}`] = () =>
      Response.json(
        verification({
          status: "none",
          code: null,
          url: null,
          created_reason: null,
          check_count: 0,
          last_checked_at: null,
          can_add: true,
          can_withdraw: false,
          version: 0,
        }),
      );
    const { unmount } = renderWithIntl(<VerificationCard certificateId={CERT} />);
    expect(await screen.findByText("No verification code")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add verification code" })).toBeInTheDocument();
    unmount();
    stub.routes[`GET ${VERIFY_PATH}`] = () =>
      Response.json(
        verification({
          status: "withdrawn",
          withdrawn_at: "2026-10-17T07:00:00Z",
          withdraw_reason: "Posted on a group by mistake",
          can_withdraw: false,
        }),
      );
    renderWithIntl(<VerificationCard certificateId={CERT} />);
    expect(await screen.findByText("This code was withdrawn")).toBeInTheDocument();
    expect(screen.getByText("Posted on a group by mistake")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Withdraw code" })).not.toBeInTheDocument();
  });
});

const FORMATS: Schemas["CertificateFormatOut"][] = [
  {
    key: "generic-v1",
    version: 1,
    certificate_type: "transfer",
    label_en: "Generic transfer certificate",
    label_te: "",
    builtin: true,
    verified: true,
    sources: [],
    notes: "",
    lines: [],
    inputs: [],
  },
  {
    key: "ap-tc-2027",
    version: 1,
    certificate_type: "transfer",
    label_en: "Andhra Pradesh transfer certificate",
    label_te: "",
    builtin: false,
    verified: false,
    sources: [{ title: "Synthetic source", url: null }],
    notes: "",
    lines: [],
    inputs: [],
  },
];

describe("TC format card (FR-CERT-015, FR-CERT-018)", () => {
  const tenant = {
    id: "0192f3a4-0000-7000-8000-000000000001",
    name: "Synthetic Model School",
    version: 4,
    settings: { certificate_format: { transfer: "ap-tc-2027" }, print_restricted_tc_fields: false },
  } as unknown as Parameters<typeof CertificateFormatCard>[0]["tenant"];

  it("says an unverified format is unconfirmed and switches back with If-Match", async () => {
    stub.routes["GET /bff/api/v1/certificates/formats"] = () => Response.json(FORMATS);
    stub.routes["PATCH /bff/api/v1/tenant"] = () => Response.json(tenant);
    renderWithIntl(
      <CertificateFormatCard tenant={tenant} manage tenantKey={["staff", "tenant"]} />,
    );
    expect(await screen.findByText(/not yet confirmed with a school/)).toBeInTheDocument();
    expect(screen.getByText("Andhra Pradesh TC")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Use SchoolOS standard" }));
    await waitFor(() => expect(stub.callsTo("PATCH /bff/api/v1/tenant")).toHaveLength(1));
    const call = stub.callsTo("PATCH /bff/api/v1/tenant")[0];
    expect(call?.headers.get("if-match")).toBe('W/"4"');
    expect(JSON.parse(call?.body ?? "{}")).toEqual({
      certificate_format: { transfer: "generic-v1" },
    });
  });

  it("religion and caste stay blank by default; printing them is an explicit choice", async () => {
    stub.routes["GET /bff/api/v1/certificates/formats"] = () => Response.json(FORMATS);
    stub.routes["PATCH /bff/api/v1/tenant"] = () => Response.json(tenant);
    renderWithIntl(
      <CertificateFormatCard tenant={tenant} manage tenantKey={["staff", "tenant"]} />,
    );
    expect(await screen.findByText(/Left blank on the certificate/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Print religion and caste" }));
    await waitFor(() => expect(stub.callsTo("PATCH /bff/api/v1/tenant")).toHaveLength(1));
    const call = stub.callsTo("PATCH /bff/api/v1/tenant")[0];
    expect(JSON.parse(call?.body ?? "{}")).toEqual({ print_restricted_tc_fields: true });
  });

  it("is read-only without tenant.settings.manage", async () => {
    stub.routes["GET /bff/api/v1/certificates/formats"] = () => Response.json(FORMATS);
    renderWithIntl(
      <CertificateFormatCard tenant={tenant} manage={false} tenantKey={["staff", "tenant"]} />,
    );
    expect(await screen.findByText("Andhra Pradesh TC")).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});

describe("a TC in the AP format (US-2001)", () => {
  const AP_TYPES: Schemas["CertificateTypeOut"][] = [
    {
      key: "transfer",
      label_en: "Transfer certificate",
      label_te: "",
      requires_approval: true,
      ends_enrolment: true,
      printed: ["full_name"],
      format_key: "ap-tc-2027",
      format_version: 1,
      format_verified: false,
      inputs: [
        {
          key: "identification_mark_1",
          label_en: "Personal marks of identification (1)",
          label_te: "",
          kind: "text",
          required: true,
          max_length: 120,
          choices: [],
        },
        {
          key: "working_days",
          label_en: "Number of working days up to the date of leaving",
          label_te: "",
          kind: "number",
          required: false,
          max_length: null,
          max_value: 366,
          choices: [],
        },
      ],
    },
  ];

  it("shows the format warning, the API's input labels and where each line comes from", async () => {
    stub.routes["GET /bff/api/v1/me"] = () => Response.json(me(["certificate.issue"]));
    stub.routes["GET /bff/api/v1/certificates/types"] = () => Response.json(AP_TYPES);
    stub.routes[`GET /bff/api/v1/students/${STUDENT}`] = () =>
      Response.json({ id: STUDENT, status: "active", canonical: {}, values: {}, version: 1 });
    stub.routes[`GET /bff/api/v1/students/${STUDENT}/certificates/preview`] = () =>
      Response.json({
        student_id: STUDENT,
        certificate_type: "transfer",
        requires_approval: true,
        format_key: "ap-tc-2027",
        format_version: 1,
        fields: [
          {
            key: "full_name",
            label_en: "Name of the pupil",
            label_te: "",
            value: "Synthetica Asha Tset",
            source: "admission_register",
            verified: true,
            provisional: false,
          },
          {
            key: "identification_mark_1",
            label_en: "Personal marks of identification (1)",
            label_te: "",
            value: null,
            source: null,
            verified: false,
            provisional: false,
            fill: "input",
          },
          {
            key: "religion",
            label_en: "Religion",
            label_te: "",
            value: null,
            source: null,
            verified: false,
            provisional: false,
            fill: "restricted",
          },
          {
            key: "apaar_id",
            label_en: "APAAR ID",
            label_te: "",
            value: null,
            source: null,
            verified: false,
            provisional: false,
            fill: "identifier",
          },
        ],
        class_label: "Class IX A",
        academic_year_label: "2026-27",
        blockers: [],
        warnings: [],
        can_issue: true,
      } satisfies Schemas["CertificatePreview"]);
    renderWithIntl(<IssueCertificateScreen studentId={STUDENT} initialType="transfer" />);
    expect(await screen.findByText("Format: Andhra Pradesh TC")).toBeInTheDocument();
    expect(screen.getByText(/not yet confirmed with a school/)).toBeInTheDocument();
    const table = screen.getByRole("table");
    expect(within(table).getByText("Typed below")).toBeInTheDocument();
    expect(within(table).getByText("Blank, filled by hand")).toBeInTheDocument();
    expect(within(table).getByText("Not printed")).toBeInTheDocument();
    expect(screen.getByLabelText("Personal mark of identification (1)")).toBeInTheDocument();
    expect(screen.getByLabelText(/Working days up to the date of leaving/)).toHaveAttribute(
      "inputmode",
      "numeric",
    );
  });
});
