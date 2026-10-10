import { screen, within } from "@testing-library/react";
import type * as Navigation from "next/navigation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { installBffStub, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { intlErrors, renderWithIntl } from "@/test/render";
import { me, SECTION, STUDENT, structureRoutes } from "@/test/school-fixtures";
import { ReadinessScreen } from "./ReadinessScreen";
import { ReadinessStudentScreen } from "./ReadinessStudentScreen";
import { readyShare, slipHref, type ReadinessDetail, type ReadinessSummary } from "./types";

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => "/findings/readiness",
    useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
    useSearchParams: () => new URLSearchParams(),
    useParams: () => ({ locale: "en" }),
  };
});

const SSC = "bseap-ssc-2027";
const READ = ["dq.readiness.read", "dq.findings.read", "student.read_basic"];
let stub: BffStub;

const PROFILE = {
  key: SSC,
  version: 1,
  label_en: "AP SSC 2027 (BSEAP)",
  label_te: "AP SSC 2027 (BSEAP) పరీక్షలు",
  source: ["https://example.org/press-note"],
  verified: false,
  classes: ["IX", "X"],
  fields: ["full_name", "dob", "gender"],
};

function summary(): ReadinessSummary {
  return {
    profile: PROFILE,
    totals: { students: 160, ready: 142, needs_parent: 10, needs_school: 6, blocked: 2 },
    sections: [
      {
        section_id: SECTION,
        class_id: "0192f3a4-0000-7000-8000-0000000000c9",
        students: 160,
        ready: 142,
        needs_parent: 10,
        needs_school: 6,
        blocked: 2,
      },
    ],
    last_run: null,
  };
}

function detail(overrides: Partial<ReadinessDetail> = {}): ReadinessDetail {
  return {
    profile: PROFILE,
    student: { id: STUDENT, display_name: "Kommineni Venkata Sai Kumar", admission_no: "A-17" },
    section_id: SECTION,
    applies: true,
    status: "needs_parent",
    values_shown: true,
    fields: [
      {
        attribute_key: "full_name",
        reference: "admission_register",
        values: [
          {
            source: "admission_register",
            value: "Kommineni Venkata Sai Kumar",
            masked: "K••• V••• S••• K•••",
            sensitive: false,
          },
          {
            source: "aadhaar_as_printed",
            value: "Kommineni Venkata SaiKumar",
            masked: "K••• V••• S•••",
            sensitive: true,
          },
          { source: "udise_plus", value: null, masked: null, sensitive: false },
        ],
        items: [
          {
            reason: "mismatch",
            owner: "parent_aadhaar",
            status: "needs_parent",
            source: "aadhaar_as_printed",
            against: "admission_register",
            sources: ["aadhaar_as_printed", "admission_register"],
            kinds: ["spacing"],
            advisory: false,
            waived: false,
            severity: "blocker",
            explanation: {
              code: "DQ-030-PARENT",
              en: "Full name on Aadhaar differs from the right value (spaces differ). The parent must correct Aadhaar at an Aadhaar centre.",
              te: "",
            },
            owner_label: {
              code: "parent_aadhaar",
              en: "Parent: correct Aadhaar at an Aadhaar centre",
              te: "",
            },
            kinds_text: { code: "kinds", en: "spaces differ", te: "" },
            segments: [
              { op: "equal", reference: "Kommineni Venkata Sai", other: "Kommineni Venkata Sai" },
              { op: "delete", reference: " ", other: "" },
              { op: "equal", reference: "Kumar", other: "Kumar" },
            ],
            changes: [{ code: "space_missing", en: "space missing after “Sai”", te: "" }],
            finding_id: "0192f3a4-0000-7000-8000-00000000f001",
            finding_status: "open",
          },
        ],
      },
    ],
    ...overrides,
  };
}

function common(permissions: string[]) {
  Object.assign(stub.routes, structureRoutes());
  stub.routes["GET /bff/api/v1/me"] = () => Response.json(me(permissions));
  stub.routes["GET /bff/api/v1/dq/profiles"] = () =>
    Response.json([
      {
        key: SSC,
        version: 1,
        label_en: PROFILE.label_en,
        label_te: PROFILE.label_te,
        required_fields: ["full_name"],
        needs_apaar: false,
        source: PROFILE.source,
        verified: false,
        readiness: true,
      },
      {
        key: "udise-plus",
        version: 1,
        label_en: "UDISE+ student profile",
        label_te: "UDISE+ విద్యార్థి ప్రొఫైల్",
        required_fields: ["full_name"],
        needs_apaar: true,
        source: [],
        verified: false,
        readiness: false,
      },
    ]);
  stub.routes["GET /bff/api/v1/attributes"] = () =>
    Response.json([
      {
        key: "full_name",
        data_type: "text",
        classification: "C2",
        is_identity: true,
        label_en: "Full name",
        label_te: "పూర్తి పేరు",
        sort_order: 1,
        allowed_sources: null,
        allowed_values: null,
        precedence: [],
        is_global: true,
      },
    ]);
  stub.routes[`GET /bff/api/v1/dq/readiness/${SSC}`] = () => Response.json(summary());
}

beforeEach(() => {
  stub = installBffStub("staff");
});
afterEach(() => {
  uninstallBffStub();
  expect(intlErrors).toEqual([]);
});

describe("readiness helpers (FR-DQ-035)", () => {
  it("links slips through the BFF and computes the ready share", () => {
    expect(slipHref(SSC, { studentId: STUDENT })).toBe(
      `/bff/api/v1/dq/readiness/${SSC}/slips?student_id=${STUDENT}`,
    );
    expect(slipHref(SSC, { sectionId: SECTION })).toBe(
      `/bff/api/v1/dq/readiness/${SSC}/slips?section_id=${SECTION}`,
    );
    expect(readyShare({ students: 160, ready: 142 })).toBeCloseTo(0.8875);
    expect(readyShare({ students: 0, ready: 0 })).toBe(0);
  });
});

describe("board readiness screen (US-503, US-505)", () => {
  it("shows 142 of 160 ready, the unverified format and the sections, without a run button for readers", async () => {
    common(READ);
    renderWithIntl(<ReadinessScreen profileKey={SSC} sectionId={null} />);
    expect(await screen.findByRole("heading", { name: "Board readiness", level: 1 })).toBeVisible();
    expect(await screen.findByText("Format not yet confirmed")).toBeVisible();
    const table = await screen.findByRole("table", { name: "Sections" });
    const bar = within(table).getByRole("progressbar", { name: "142 of 160 ready" });
    expect(bar).toHaveAttribute("value", "142");
    expect(within(table).getByRole("link", { name: /Open section/ })).toHaveAttribute(
      "href",
      `/findings/readiness?profile=${SSC}&section=${SECTION}`,
    );
    // Only profiles with a readiness check are offered.
    const picker = screen.getByRole("navigation", { name: "Board or portal" });
    expect(within(picker).getByRole("link", { name: PROFILE.label_en })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(within(picker).queryByText("UDISE+ student profile")).toBeNull();
    expect(screen.queryByRole("button", { name: "Check now" })).toBeNull();
  });

  it("lists a section's students with who must act and slip links", async () => {
    common([...READ, "dq.readiness.manage"]);
    stub.routes[`GET /bff/api/v1/dq/readiness/${SSC}/students`] = () =>
      Response.json([
        {
          student: {
            id: STUDENT,
            display_name: "Kommineni Venkata Sai Kumar",
            admission_no: "A-17",
          },
          section_id: SECTION,
          status: "needs_parent",
          owners: ["parent_aadhaar"],
          attribute_keys: ["full_name"],
          open_items: 1,
        },
      ]);
    renderWithIntl(<ReadinessScreen profileKey={SSC} sectionId={SECTION} />);
    expect(await screen.findByRole("button", { name: "Check now" })).toBeVisible();
    const table = await screen.findByRole("table", { name: "Students in Class 9 · A" });
    expect(within(table).getByText("Parent must act")).toBeVisible();
    expect(within(table).getByText("Parent: Aadhaar")).toBeVisible();
    const slip = within(table).getByRole("link", { name: /Print parent slip/ });
    expect(slip).toHaveAttribute("href", slipHref(SSC, { studentId: STUDENT }));
    expect(slip).toHaveAttribute("target", "_blank");
    expect(slip).toHaveAttribute("rel", "noopener noreferrer");
    expect(screen.getByRole("link", { name: "Print slips for this section" })).toHaveAttribute(
      "href",
      slipHref(SSC, { sectionId: SECTION }),
    );
  });
});

describe("student readiness (US-504)", () => {
  it("highlights the exact characters and says who fixes it", async () => {
    common(READ);
    stub.routes[`GET /bff/api/v1/dq/readiness/${SSC}/students/${STUDENT}`] = () =>
      Response.json(detail());
    renderWithIntl(<ReadinessStudentScreen profileKey={SSC} studentId={STUDENT} />);
    expect(await screen.findByText("space missing after “Sai”")).toBeVisible();
    const removed = document.querySelector("del");
    expect(removed?.textContent).toContain("␣");
    expect(screen.getByText("Parent: correct Aadhaar at an Aadhaar centre")).toBeVisible();
    expect(screen.getByText("Right value")).toBeVisible();
    expect(screen.getByText("To correct")).toBeVisible();
    expect(screen.getByText("Not recorded")).toBeVisible();
    expect(screen.getByRole("link", { name: "Open the finding" })).toHaveAttribute(
      "href",
      "/findings/0192f3a4-0000-7000-8000-00000000f001",
    );
    expect(screen.queryByText(/Aadhaar values are hidden/)).toBeNull();
  });

  it("keeps Aadhaar values masked without permission", async () => {
    common(READ);
    const masked = detail({ values_shown: false });
    const field = masked.fields[0]!;
    masked.fields = [
      {
        ...field,
        values: field.values.map((v) => (v.sensitive ? { ...v, value: null } : v)),
        items: field.items.map((i) => ({ ...i, segments: null, changes: null })),
      },
    ];
    stub.routes[`GET /bff/api/v1/dq/readiness/${SSC}/students/${STUDENT}`] = () =>
      Response.json(masked);
    renderWithIntl(<ReadinessStudentScreen profileKey={SSC} studentId={STUDENT} />);
    expect(await screen.findByText(/Aadhaar values are hidden/)).toBeVisible();
    expect(screen.getByText("K••• V••• S•••")).toBeVisible();
    expect(screen.queryByText("Kommineni Venkata SaiKumar")).toBeNull();
    expect(document.querySelector("del")).toBeNull();
  });

  it("renders in Telugu (NFR-I18N-001)", async () => {
    common(READ);
    stub.routes[`GET /bff/api/v1/dq/readiness/${SSC}/students/${STUDENT}`] = () =>
      Response.json(detail());
    renderWithIntl(<ReadinessStudentScreen profileKey={SSC} studentId={STUDENT} />, "te");
    expect(await screen.findByText("సరైన విలువ")).toBeVisible();
    expect(screen.getByText("తల్లిదండ్రులు: ఆధార్")).toBeVisible();
  });
});
