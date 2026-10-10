import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { installBffStub, page, problem, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { ID, NOW, me, student } from "@/test/records-fixtures";
import { messages, renderWithIntl } from "@/test/render";
import { ApaarScreen } from "./ApaarScreen";
import { StudentConsentScreen } from "./StudentConsentScreen";
import {
  nextStatuses,
  sectionFormsHref,
  studentFormHref,
  type ConsentRow,
  type StudentConsent,
} from "./types";

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => "/apaar",
    useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
    useSearchParams: () => new URLSearchParams(),
    useParams: () => ({ locale: "en" }),
  };
});

/**
 * APAAR consent register screens (ADR-0039; US-1901..US-1903, FR-APC-001..006). Synthetic data
 * only.
 */
const am = messages.en.apaar;
const SECTION = "0192f3a4-0000-7000-8000-00000000a9a1";
const DOC = "0192f3a4-0000-7000-8000-00000000d0c1";
let stub: BffStub;

function row(extra: Partial<ConsentRow>): ConsentRow {
  return {
    student_id: ID.student,
    display_name: "Synthetica Venkata Sai",
    admission_no: "A-1",
    class_section: "IX-A",
    section_id: SECTION,
    status: "pending",
    decided_on: null,
    has_form: false,
    recorded_at: null,
    version: 0,
    has_apaar_id: false,
    ...extra,
  };
}

function consent(extra: Partial<StudentConsent> = {}): StudentConsent {
  return {
    student_id: ID.student,
    status: "pending",
    current: null,
    history: [],
    version: 0,
    ...extra,
  };
}

function staffRoutes(permissions: readonly string[]) {
  stub.routes["GET /bff/api/v1/me"] = () => Response.json(me(permissions));
  stub.routes["GET /bff/api/v1/academic-years"] = () => page([]);
  stub.routes["GET /bff/api/v1/classes"] = () => page([]);
  stub.routes["GET /bff/api/v1/sections"] = () => page([]);
  stub.routes["GET /bff/api/v1/apaar/settings"] = () =>
    Response.json({ form_language: "en", version: 0 });
}

beforeEach(() => {
  stub = installBffStub("staff");
});
afterEach(() => {
  uninstallBffStub();
});

describe("FR-APC-002 transitions offered", () => {
  it("withdrawal only after consent; never back to pending after a decision", () => {
    expect(nextStatuses("pending")).toEqual(["pending", "given", "refused"]);
    expect(nextStatuses("given")).toEqual(["given", "refused", "withdrawn"]);
    expect(nextStatuses("refused")).toEqual(["given", "refused"]);
    expect(nextStatuses("withdrawn")).toEqual(["given", "refused"]);
  });

  it("print views go through the BFF with ids and codes only", () => {
    expect(studentFormHref(ID.student, "te")).toBe(
      `/bff/api/v1/students/${ID.student}/apaar-consent/form?language=te`,
    );
    expect(sectionFormsHref(SECTION, "pending")).toBe(
      `/bff/api/v1/sections/${SECTION}/apaar-consent-forms?status=pending`,
    );
  });
});

describe("US-1903 summary and follow-up list", () => {
  it("shows counts per section, refused as a decision and print links", async () => {
    staffRoutes(["apaar.consent.read", "apaar.consent.record", "student.read_basic"]);
    stub.routes["GET /bff/api/v1/apaar/consents/summary"] = () =>
      Response.json({
        totals: { total: 3, given: 1, refused: 1, pending: 1, withdrawn: 0 },
        sections: [
          {
            section_id: SECTION,
            class_section: "IX-A",
            counts: { total: 3, given: 1, refused: 1, pending: 1, withdrawn: 0 },
          },
        ],
      });
    stub.routes["GET /bff/api/v1/apaar/consents"] = () =>
      page([
        row({ status: "refused", decided_on: "2026-07-15", version: 1 }),
        row({
          student_id: "0192f3a4-0000-7000-8000-00000000a002",
          display_name: "Synthetica Lakshmi",
          status: "given",
          has_form: true,
          version: 1,
        }),
      ]);
    renderWithIntl(<ApaarScreen />);
    expect(await screen.findByRole("heading", { name: am.summaryTitle })).toBeInTheDocument();
    const pending = await screen.findAllByRole("link", { name: new RegExp(am.printPending) });
    expect(pending[0]).toHaveAttribute("href", sectionFormsHref(SECTION, "pending"));
    expect(pending[0]).toHaveAttribute("target", "_blank");
    expect((await screen.findAllByText(am.status.refused)).length).toBeGreaterThan(0);
    expect(screen.getByRole("link", { name: "Synthetica Lakshmi" })).toHaveAttribute(
      "href",
      expect.stringContaining("/apaar/students/0192f3a4-0000-7000-8000-00000000a002"),
    );
  });

  it("asks the API for the pending list when Pending is chosen", async () => {
    staffRoutes(["apaar.consent.read"]);
    stub.routes["GET /bff/api/v1/apaar/consents/summary"] = () =>
      Response.json({
        totals: { total: 0, given: 0, refused: 0, pending: 0, withdrawn: 0 },
        sections: [],
      });
    stub.routes["GET /bff/api/v1/apaar/consents"] = () => page([]);
    const user = userEvent.setup();
    renderWithIntl(<ApaarScreen />);
    await user.click(await screen.findByRole("radio", { name: am.status.pending }));
    await waitFor(() =>
      expect(
        stub
          .callsTo("GET /bff/api/v1/apaar/consents")
          .some((call) => call.url.searchParams.get("status") === "pending"),
      ).toBe(true),
    );
    expect(await screen.findByRole("heading", { name: am.followUpTitle })).toBeInTheDocument();
  });

  it("explains when the person has no APAAR permission", async () => {
    staffRoutes(["student.read_basic"]);
    renderWithIntl(<ApaarScreen />);
    expect(await screen.findByText(am.noPermissionTitle)).toBeInTheDocument();
    expect(stub.callsTo("GET /bff/api/v1/apaar/consents")).toEqual([]);
  });
});

describe("US-1901 recording a decision", () => {
  function studentRoutes(current: StudentConsent) {
    stub.routes[`GET /bff/api/v1/students/${ID.student}/apaar-consent`] = () =>
      Response.json(current);
    stub.routes[`GET /bff/api/v1/students/${ID.student}`] = () => Response.json(student());
    stub.routes[`GET /bff/api/v1/students/${ID.student}/guardians`] = () => Response.json([]);
  }

  it("records a refusal without a form, with If-Match and an idempotency key", async () => {
    staffRoutes(["apaar.consent.read", "apaar.consent.record", "student.read_basic"]);
    studentRoutes(consent());
    stub.routes[`POST /bff/api/v1/students/${ID.student}/apaar-consent`] = () =>
      Response.json(
        consent({
          status: "refused",
          version: 1,
          current: {
            id: DOC,
            seq: 1,
            status: "refused",
            relationship: "father",
            guardian_id: null,
            decided_on: "2026-07-15",
            form_language: null,
            evidence_document_id: null,
            note: null,
            recorded_by: ID.user,
            recorded_by_name: "Test User",
            recorded_at: NOW,
          },
        }),
        { status: 201 },
      );
    const user = userEvent.setup();
    renderWithIntl(<StudentConsentScreen studentId={ID.student} />);
    await user.click(await screen.findByRole("radio", { name: am.record.decisions.refused }));
    await user.selectOptions(screen.getByLabelText(am.record.relationship), "father");
    await user.type(screen.getByLabelText(am.record.decidedOn), "15/07/2026");
    await user.click(screen.getByRole("button", { name: am.record.submit }));
    await screen.findByText(am.record.savedTitle);
    const [call] = stub.callsTo(`POST /bff/api/v1/students/${ID.student}/apaar-consent`);
    expect(call?.headers.get("if-match")).toBe('W/"0"');
    expect(call?.headers.get("idempotency-key")).toBeTruthy();
    expect(JSON.parse(call?.body ?? "{}")).toMatchObject({
      status: "refused",
      relationship: "father",
      decided_on: "2026-07-15",
      evidence_document_id: null,
    });
  });

  it("refuses consent without the signed form before sending anything", async () => {
    staffRoutes(["apaar.consent.read", "apaar.consent.record", "student.read_basic"]);
    studentRoutes(consent());
    const user = userEvent.setup();
    renderWithIntl(<StudentConsentScreen studentId={ID.student} />);
    await user.click(await screen.findByRole("radio", { name: am.record.decisions.given }));
    await user.selectOptions(screen.getByLabelText(am.record.relationship), "mother");
    await user.type(screen.getByLabelText(am.record.decidedOn), "15/07/2026");
    await user.click(screen.getByRole("button", { name: am.record.submit }));
    expect(await screen.findByText(am.validation.formRequired)).toBeInTheDocument();
    expect(stub.callsTo(`POST /bff/api/v1/students/${ID.student}/apaar-consent`)).toEqual([]);
  });

  it("offers withdrawal only after consent, and only readers see no form", async () => {
    staffRoutes(["apaar.consent.read", "student.read_basic"]);
    studentRoutes(consent({ status: "given", version: 1 }));
    renderWithIntl(<StudentConsentScreen studentId={ID.student} />);
    expect(await screen.findByText(am.status.given)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: am.record.submit })).not.toBeInTheDocument();
    const print = screen.getByRole("link", { name: am.student.printForm });
    expect(print).toHaveAttribute("href", studentFormHref(ID.student));
  });

  it("explains a refused transition from the API", async () => {
    staffRoutes(["apaar.consent.read", "apaar.consent.record", "student.read_basic"]);
    studentRoutes(consent({ status: "given", version: 1 }));
    stub.routes[`POST /bff/api/v1/students/${ID.student}/apaar-consent`] = () =>
      problem(409, "consent_not_given");
    const user = userEvent.setup();
    renderWithIntl(<StudentConsentScreen studentId={ID.student} />);
    const decision = await screen.findByRole("group", { name: am.record.decision });
    await user.click(within(decision).getByRole("radio", { name: am.record.decisions.withdrawn }));
    await user.selectOptions(screen.getByLabelText(am.record.relationship), "mother");
    await user.type(screen.getByLabelText(am.record.decidedOn), "15/07/2026");
    await user.click(screen.getByRole("button", { name: am.record.submit }));
    expect(await screen.findByText(am.errors.consent_not_given.title)).toBeInTheDocument();
  });
});
