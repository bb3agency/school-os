import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { installBffStub, problem, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { intlErrors, renderWithIntl } from "@/test/render";
import { me, SECTION, STUDENT, structureRoutes } from "@/test/school-fixtures";
import { ReadinessScreen } from "./ReadinessScreen";
import { ReadinessStudentScreen } from "./ReadinessStudentScreen";
import { readyShare, slipHref, type ReadinessDetail, type ReadinessSummary } from "./types";

const router = vi.hoisted(() => ({ replace: vi.fn() }));

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => "/findings/readiness",
    useRouter: () => ({
      push: vi.fn(),
      replace: router.replace,
      refresh: vi.fn(),
      prefetch: vi.fn(),
    }),
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
              code: "DQ-031-PARENT",
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
  router.replace.mockReset();
});
afterEach(() => {
  uninstallBffStub();
  expect(intlErrors).toEqual([]);
});

describe("readiness helpers (FR-DQ-045)", () => {
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

describe("board readiness screen (US-504, US-506)", () => {
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

describe("board readiness actions and states (FR-DQ-044, FR-DQ-048)", () => {
  it("a school without the default check opens its first readiness check instead", async () => {
    common(READ);
    renderWithIntl(<ReadinessScreen profileKey="apaar" sectionId={null} chosen={false} />);
    await waitFor(() =>
      expect(router.replace).toHaveBeenCalledWith(`/findings/readiness?profile=${SSC}`),
    );
  });

  it("a check chosen in the address is kept even when it is not listed", async () => {
    common(READ);
    renderWithIntl(<ReadinessScreen profileKey="apaar" sectionId={null} />);
    expect(await screen.findByRole("navigation", { name: "Board or portal" })).toBeVisible();
    expect(router.replace).not.toHaveBeenCalled();
  });

  it("Check now starts a check of the section shown with an Idempotency-Key", async () => {
    common([...READ, "dq.readiness.manage"]);
    stub.routes[`GET /bff/api/v1/dq/readiness/${SSC}/students`] = () => Response.json([]);
    stub.routes[`POST /bff/api/v1/dq/readiness/${SSC}/runs`] = () =>
      Response.json(
        {
          id: "0192f3a4-0000-7000-8000-00000000ab02",
          trigger: "manual",
          event_type: null,
          status: "completed",
          profile_key: SSC,
          scope: { section_ids: [SECTION] },
          stats: { students: 40, blockers: 2, warnings: 5, new: 3, reopened: 1, cleared: 0 },
          created_at: "2026-10-10T05:00:00Z",
          started_at: "2026-10-10T05:00:00Z",
          finished_at: "2026-10-10T05:00:02Z",
          error_code: null,
        },
        { status: 202 },
      );
    renderWithIntl(<ReadinessScreen profileKey={SSC} sectionId={SECTION} />);
    await userEvent.click(await screen.findByRole("button", { name: "Check now" }));
    const dialog = screen.getByRole("dialog", { name: "Check readiness now" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Start the check" }));
    expect(
      await within(dialog).findByText("40 students checked: 2 blockers and 5 warnings."),
    ).toBeInTheDocument();
    const call = stub.callsTo(`POST /bff/api/v1/dq/readiness/${SSC}/runs`)[0];
    expect(call?.headers.get("idempotency-key")).toMatch(/^[0-9a-f-]{36}$/);
    expect(JSON.parse(call?.body ?? "{}")).toEqual({ scope: { section_ids: [SECTION] } });
    // The finished check reloads the summary.
    await waitFor(() =>
      expect(stub.callsTo(`GET /bff/api/v1/dq/readiness/${SSC}`).length).toBeGreaterThan(1),
    );
  });

  it("an unavailable summary says what to do, never shows raw API text", async () => {
    common(READ);
    stub.routes[`GET /bff/api/v1/dq/readiness/${SSC}`] = () =>
      problem(503, "service_unavailable", { detail: "upstream pool exhausted" });
    renderWithIntl(<ReadinessScreen profileKey={SSC} sectionId={null} />);
    expect(await screen.findByRole("alert")).toBeVisible();
    expect(screen.queryByText(/upstream pool exhausted/)).toBeNull();
    expect(screen.queryByRole("table", { name: "Sections" })).toBeNull();
  });
});

describe("student readiness (US-505)", () => {
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
