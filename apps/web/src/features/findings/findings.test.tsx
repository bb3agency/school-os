import { screen, waitFor, within } from "@testing-library/react";
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
import { intlErrors, renderWithIntl } from "@/test/render";
import { finding, me, SECTION, STUDENT, structureRoutes } from "@/test/school-fixtures";
import { FindingDetailScreen } from "./FindingDetailScreen";
import { FindingsScreen } from "./FindingsScreen";
import { findingsQuery, parseFindingFilters } from "./filters";
import { RulesScreen } from "./RulesScreen";
import { RunChecksDialog, runPollDelay } from "./RunChecks";

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => "/en/findings",
    useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
    useSearchParams: () => new URLSearchParams(),
    useParams: () => ({ locale: "en" }),
  };
});

const READ = ["dq.findings.read", "student.read_basic"];
let stub: BffStub;

function common(permissions: string[]) {
  Object.assign(stub.routes, structureRoutes());
  stub.routes["GET /bff/api/v1/me"] = () => Response.json(me(permissions));
  stub.routes["GET /bff/api/v1/dq/rules"] = () => Response.json([]);
  stub.routes["GET /bff/api/v1/dq/profiles"] = () =>
    Response.json([
      {
        key: "cisce-registration-2026",
        version: 1,
        label_en: "CISCE registration 2026",
        label_te: "CISCE నమోదు 2026",
        required_fields: ["full_name"],
        needs_apaar: true,
      },
    ]);
  stub.routes["GET /bff/api/v1/attributes"] = () =>
    Response.json([
      {
        key: "date_of_birth",
        data_type: "date",
        classification: "C3",
        is_identity: true,
        label_en: "Date of birth",
        label_te: "పుట్టిన తేదీ",
        sort_order: 2,
        allowed_sources: null,
        allowed_values: null,
        precedence: [],
        is_global: true,
      },
    ]);
  stub.routes["GET /bff/api/v1/dq/summary"] = () =>
    Response.json({
      profile_key: null,
      blockers: 1,
      warnings: 1,
      students_with_blockers: 1,
      by_severity: {},
      by_rule: [],
      last_run: null,
    });
}

beforeEach(() => {
  stub = installBffStub("staff");
});
afterEach(() => {
  uninstallBffStub();
  // Every string the screens showed exists in the catalogue (NFR-I18N-001).
  expect(intlErrors).toEqual([]);
});

describe("findings filters (US-501)", () => {
  it("defaults to unresolved findings and drops unknown values", () => {
    const filters = parseFindingFilters({
      status: "bogus",
      severity: ["blocker", "nope"],
      rule_id: "DQ-999",
      section_id: "not-a-uuid",
      student_id: STUDENT,
    });
    expect(filters).toEqual({
      status: ["open", "reopened"],
      severity: ["blocker"],
      ruleId: null,
      profileKey: null,
      sectionId: null,
      studentId: STUDENT,
    });
    expect(findingsQuery(filters)).toEqual({
      status: ["open", "reopened"],
      severity: ["blocker"],
      student_id: STUDENT,
    });
  });
});

describe("findings list (US-501 AC1/AC2, FR-DQ-006)", () => {
  it("separates blockers from warnings, shows masked values, the explanation and the fix", async () => {
    common(READ);
    stub.routes["GET /bff/api/v1/dq/findings"] = () =>
      page([
        finding(),
        finding({
          id: "0192f3a4-0000-7000-8000-00000000f002",
          severity: "low",
          blocker: false,
          rule_id: "DQ-007",
          explanation: {
            code: "X",
            en: "Initials used instead of the surname.",
            te: "ఇంటి పేరు బదులు పొడి అక్షరాలు.",
          },
          routes: [],
          values: [
            {
              attribute_key: "full_name",
              source: "udise_plus",
              value_id: "0192f3a4-0000-7000-8000-00000000a009",
              masked: "A. T.",
              value: "A. Test",
              sensitive: false,
            },
          ],
        }),
      ]);
    renderWithIntl(<FindingsScreen filters={parseFindingFilters({})} />);
    const blockers = await screen.findByRole("region", { name: "1 blocker" });
    expect(within(blockers).getByText("Asha Test")).toBeInTheDocument();
    expect(within(blockers).getByText("••/••/2012")).toBeInTheDocument();
    expect(within(blockers).getAllByText(/hidden to protect personal details/).length).toBe(2);
    expect(
      within(blockers).getByText("How to fix: Correct the school record with a change request."),
    ).toBeInTheDocument();
    const warnings = screen.getByRole("region", { name: "1 warning" });
    expect(within(warnings).getByText("A. Test")).toBeInTheDocument();
    // Status filter defaults to unresolved (the API's default too).
    const call = stub.callsTo("GET /bff/api/v1/dq/findings")[0];
    expect(call?.url.searchParams.getAll("status")).toEqual(["open", "reopened"]);
  });

  it("shows the explanation in Telugu for Telugu readers", async () => {
    common(READ);
    stub.routes["GET /bff/api/v1/dq/findings"] = () => page([finding()]);
    renderWithIntl(<FindingsScreen filters={parseFindingFilters({})} />, "te");
    expect(
      await screen.findByText("రిజిస్టర్‌లో మరియు జనన ధృవీకరణ పత్రంలో పుట్టిన తేదీ వేరుగా ఉంది."),
    ).toBeInTheDocument();
  });

  it("shows only the counts the API returns, and filters one severity at a time (NFR-A11Y-001)", async () => {
    common(READ);
    stub.routes["GET /bff/api/v1/dq/summary"] = () =>
      Response.json({
        profile_key: null,
        blockers: 1,
        warnings: 1,
        students_with_blockers: 1,
        by_severity: { blocker: 1, low: 1 },
        by_rule: [],
        last_run: null,
      });
    stub.routes["GET /bff/api/v1/dq/findings"] = () => page([finding()]);
    renderWithIntl(<FindingsScreen filters={parseFindingFilters({ severity: "blocker" })} />);
    const blockers = await screen.findByRole("group", { name: "Blockers" });
    await waitFor(() => expect(within(blockers).getByText("1")).toBeInTheDocument());
    expect(await screen.findByText("By severity:")).toBeInTheDocument();
    // Severities with no findings are not listed (no invented zero rows).
    expect(screen.queryByText("Medium", { selector: "p span" })).toBeNull();
    expect(screen.getByText("Not checked yet")).toBeInTheDocument();
    const severity = screen.getByRole("group", { name: "Severity" });
    expect(within(severity).getByRole("radio", { name: "Blocker" })).toBeChecked();
    expect(within(severity).getByRole("radio", { name: "All" })).not.toBeChecked();
  });

  it("says what to do when nothing is found", async () => {
    common(READ);
    stub.routes["GET /bff/api/v1/dq/findings"] = () => page([]);
    renderWithIntl(<FindingsScreen filters={parseFindingFilters({})} />);
    expect(await screen.findByText("No problems found")).toBeInTheDocument();
  });
});

describe("finding detail (US-502, FR-DQ-020)", () => {
  function detail(permissions: string[], overrides = {}) {
    common(permissions);
    stub.routes["GET /bff/api/v1/dq/findings/0192f3a4-0000-7000-8000-00000000f001"] = () =>
      Response.json(finding(overrides));
    stub.routes["GET /bff/api/v1/change-requests"] = () => page([]);
  }

  it("resolving needs a note or a request, and sends If-Match with the finding's version", async () => {
    detail([...READ, "dq.findings.resolve"]);
    stub.routes["POST /bff/api/v1/dq/findings/0192f3a4-0000-7000-8000-00000000f001/resolve"] = () =>
      Response.json(finding({ status: "resolved" }));
    renderWithIntl(<FindingDetailScreen findingId="0192f3a4-0000-7000-8000-00000000f001" />);
    await userEvent.click(await screen.findByRole("button", { name: "Resolve" }));
    const dialog = screen.getByRole("dialog", { name: "Resolve this problem" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Resolve" }));
    expect(within(dialog).getByLabelText("What was done")).toHaveAttribute("aria-invalid", "true");
    expect(
      stub.callsTo("POST /bff/api/v1/dq/findings/0192f3a4-0000-7000-8000-00000000f001/resolve"),
    ).toHaveLength(0);

    await userEvent.type(within(dialog).getByLabelText("What was done"), "Checked the register.");
    await userEvent.click(within(dialog).getByRole("button", { name: "Resolve" }));
    await waitFor(() =>
      expect(
        stub.callsTo("POST /bff/api/v1/dq/findings/0192f3a4-0000-7000-8000-00000000f001/resolve"),
      ).toHaveLength(1),
    );
    const call = stub.callsTo(
      "POST /bff/api/v1/dq/findings/0192f3a4-0000-7000-8000-00000000f001/resolve",
    )[0];
    expect(call?.headers.get("if-match")).toBe('W/"3"');
    expect(call?.headers.get("x-csrf-token")).toBe(CSRF);
    expect(JSON.parse(call?.body ?? "{}")).toEqual({
      note: "Checked the register.",
      change_request_id: null,
    });
  });

  it("offers no actions without permission, and never a way to edit the record", async () => {
    detail(READ);
    renderWithIntl(<FindingDetailScreen findingId="0192f3a4-0000-7000-8000-00000000f001" />);
    expect(await screen.findByText("Problem DQ-003")).toBeInTheDocument();
    await waitFor(() => expect(stub.callsTo("GET /bff/api/v1/me")).toHaveLength(1));
    expect(screen.queryByRole("button", { name: "Resolve" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Accept as it is" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Request a correction" })).not.toBeInTheDocument();
  });

  it("links to a change request for school-record problems when the user may request one", async () => {
    detail([...READ, "student.identity_change.request"]);
    renderWithIntl(<FindingDetailScreen findingId="0192f3a4-0000-7000-8000-00000000f001" />);
    const link = await screen.findByRole("link", { name: "Request a correction" });
    expect(link.getAttribute("href")).toBe(
      `/en/change-requests/new?student_id=${STUDENT}&finding_id=0192f3a4-0000-7000-8000-00000000f001&attribute_key=date_of_birth`,
    );
  });

  it("accepting (waive) warns about step-up and explains finding_not_open", async () => {
    detail([...READ, "dq.findings.waive"]);
    stub.routes["POST /bff/api/v1/dq/findings/0192f3a4-0000-7000-8000-00000000f001/waive"] = () =>
      problem(409, "finding_not_open");
    renderWithIntl(<FindingDetailScreen findingId="0192f3a4-0000-7000-8000-00000000f001" />);
    await userEvent.click(await screen.findByRole("button", { name: "Accept as it is" }));
    const dialog = screen.getByRole("dialog", { name: "Accept this problem as it is" });
    expect(within(dialog).getByText(/you may be asked to sign in again/)).toBeInTheDocument();
    await userEvent.type(
      within(dialog).getByLabelText("Why is it right as it is?"),
      "Known spelling",
    );
    await userEvent.click(within(dialog).getByRole("button", { name: "Accept as it is" }));
    expect(await within(dialog).findByText("This problem is already closed")).toBeInTheDocument();
  });
});

describe("run checks (FR-DQ-002)", () => {
  it("backs off while polling, up to 15 seconds", () => {
    expect(runPollDelay(0)).toBe(2000);
    expect(runPollDelay(1)).toBe(3000);
    expect(runPollDelay(20)).toBe(15_000);
  });

  it("starts a check for the ticked sections with an Idempotency-Key", async () => {
    common(READ);
    stub.routes["POST /bff/api/v1/dq/runs"] = () =>
      Response.json(
        {
          id: "0192f3a4-0000-7000-8000-00000000ab01",
          trigger: "manual",
          event_type: null,
          status: "completed",
          profile_key: null,
          scope: {},
          stats: { students: 40, blockers: 2, warnings: 5, new: 3, reopened: 1, cleared: 0 },
          created_at: "2026-09-26T05:00:00Z",
          started_at: "2026-09-26T05:00:00Z",
          finished_at: "2026-09-26T05:00:02Z",
          error_code: null,
        },
        { status: 202 },
      );
    renderWithIntl(<RunChecksDialog />);
    await userEvent.click(screen.getByRole("button", { name: "Check now" }));
    const dialog = screen.getByRole("dialog", { name: "Check student records" });
    await userEvent.click(await within(dialog).findByRole("checkbox", { name: "Class 9 · A" }));
    await userEvent.click(within(dialog).getByRole("button", { name: "Start check" }));
    expect(
      await within(dialog).findByText("40 students checked: 2 blockers and 5 warnings."),
    ).toBeInTheDocument();
    const call = stub.callsTo("POST /bff/api/v1/dq/runs")[0];
    expect(call?.headers.get("idempotency-key")).toMatch(/[0-9a-f-]{36}/);
    expect(JSON.parse(call?.body ?? "{}")).toEqual({
      scope: { section_ids: [SECTION] },
      profile_key: null,
    });
  });
});

describe("rules (FR-DQ-001)", () => {
  it("lists the checks in the reader's language", async () => {
    common(READ);
    stub.routes["GET /bff/api/v1/dq/rules"] = () =>
      Response.json([
        {
          id: "DQ-003",
          version: 1,
          check: "cross_source",
          scope: "student",
          attribute_keys: ["date_of_birth"],
          sources: ["admission_register", "birth_certificate"],
          requires_profile: false,
          severity: { mode: "fixed", level: "blocker", floor: null, cap: null },
          explanation: { code: "DQ-003", en: "Dates of birth agree.", te: "పుట్టిన తేదీలు ఒకటే." },
          routes: [],
        },
      ]);
    renderWithIntl(<RulesScreen />, "te");
    expect(await screen.findByText("పుట్టిన తేదీలు ఒకటే.")).toBeInTheDocument();
    expect(screen.getByText("పుట్టిన తేదీ")).toBeInTheDocument();
  });
});
