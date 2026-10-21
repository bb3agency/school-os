import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { notificationHref } from "@/features/notifications/data";
import { installBffStub, page, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { ID, NOW, fakeAadhaar, me } from "@/test/records-fixtures";
import { messages, renderWithIntl } from "@/test/render";
import { NewRequestScreen, buildRequestSchema, toCreateBody } from "./NewRequestScreen";
import { PrivacyContactFooter } from "./PrivacyContactFooter";
import { privacyContactSchema } from "./PrivacySettingsScreen";
import { RequestDetailScreen, workPatch } from "./RequestDetailScreen";
import { RequestsScreen } from "./RequestsScreen";
import {
  chosenCount,
  letterHref,
  parseTab,
  searchBody,
  type ErasurePlanRow,
  type PrivacyRequest,
  type PrivacyRequestRow,
} from "./types";

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => "/privacy/requests",
    useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
    useSearchParams: () => new URLSearchParams(),
    useParams: () => ({ locale: "en" }),
  };
});

/** Privacy requests and complaints (R3-1A; FR-DPDP-001..018). Synthetic data only. */
const pm = messages.en.privacy;
const REQUEST = "0192f3a4-0000-7000-8000-00000000e001";
const READ = ["privacy.read"];
const MANAGE = ["privacy.read", "privacy.request.manage", "student.read_basic"];
const DECIDE = [...MANAGE, "privacy.request.decide"];
let stub: BffStub;

function row(extra: Partial<PrivacyRequestRow> = {}): PrivacyRequestRow {
  return {
    id: REQUEST,
    reference: "PR-2026-0001",
    kind: "access",
    subject_kind: "student",
    subject_label: "Synthetica Venkata Sai",
    status: "received",
    received_on: "2026-10-18",
    due_on: "2026-10-25",
    days_left: 4,
    overdue: false,
    ...extra,
  };
}

function detail(extra: Partial<PrivacyRequest> = {}): PrivacyRequest {
  return {
    id: REQUEST,
    reference: "PR-2026-0001",
    kind: "access",
    status: "in_progress",
    subject_kind: "student",
    subject_label: "Synthetica Venkata Sai",
    student_id: ID.student,
    guardian_id: null,
    membership_id: null,
    requester_relationship: "father",
    requester_name: "Synthetica Ramana",
    received_on: "2026-10-18",
    due_on: "2026-10-25",
    days_left: 4,
    overdue: false,
    request_text: "What do you hold about my son?",
    notes: null,
    response_text: null,
    responded_on: null,
    verification_method: "in_person_document",
    verified_at: NOW,
    events: [
      {
        seq: 1,
        event: "logged",
        at: NOW,
        actor_name: "Office Clerk",
        detail: {},
      },
    ],
    links: [],
    report: {
      export_id: "0192f3a4-0000-7000-8000-00000000e101",
      status: "ready",
      created_at: NOW,
      finished_at: NOW,
      expires_at: null,
      error_code: null,
    },
    report_released_at: null,
    nominee: null,
    can_decide: true,
    logged_by_me: false,
    logged_by_name: "Office Clerk",
    decided_at: null,
    decided_by_name: null,
    closed_at: null,
    version: 2,
    ...extra,
  };
}

function plan(): ErasurePlanRow[] {
  return [
    {
      class_key: "ask_history",
      label: "Ask questions and answers",
      outcome: "erasable",
      reason: "Working data the school may erase.",
      count: 12,
      manual_steps: [],
    },
    {
      class_key: "admission_register",
      label: "Admission register",
      outcome: "refused_official_record",
      reason: "The admission register is the school's official record.",
      count: 1,
      manual_steps: [],
    },
    {
      class_key: "fee_receipts",
      label: "Fee receipts",
      outcome: "retention_running",
      reason: "Accounts are kept for eight years.",
      until: "2035-03-31",
      count: 4,
      manual_steps: [],
    },
  ];
}

function staff(permissions: readonly string[]) {
  stub.routes["GET /bff/api/v1/me"] = () => Response.json(me(permissions));
}

beforeEach(() => {
  stub = installBffStub("staff");
});
afterEach(() => {
  uninstallBffStub();
});

describe("FR-DPDP-005 helpers", () => {
  it("tabs map to statuses and only the tab goes in a URL", () => {
    expect(parseTab("overdue")).toBe("overdue");
    expect(parseTab(["closed"])).toBe("closed");
    expect(parseTab("anything")).toBe("open");
    expect(searchBody("closed", "")).toMatchObject({ status: ["closed", "withdrawn"] });
    expect(searchBody("overdue", "erasure")).toMatchObject({ overdue: true, kind: ["erasure"] });
    expect(letterHref(REQUEST, "acknowledgement")).toBe(
      `/bff/api/v1/privacy/requests/${REQUEST}/letters/acknowledgement`,
    );
  });

  it("counts only the ticked erasable records", () => {
    expect(chosenCount(plan(), ["ask_history", "admission_register"])).toBe(12);
  });

  it("sends only what changed on the notes form", () => {
    const current = { status: "in_progress" as const, verification_method: null, notes: "a" };
    expect(
      workPatch(current, { status: "in_progress", verification_method: "", notes: "a" }),
    ).toEqual({});
    expect(
      workPatch(current, {
        status: "waiting_for_requester",
        verification_method: "staff_sign_in",
        notes: "",
      }),
    ).toEqual({
      status: "waiting_for_requester",
      verification_method: "staff_sign_in",
      notes: null,
    });
  });

  it("the daily reminder opens the requests list", () => {
    expect(notificationHref({ resource_type: "privacy_request_list", resource_id: null })).toBe(
      "/privacy/requests",
    );
  });

  it("checks the privacy contact like the API (target 1-7 days)", () => {
    expect(
      privacyContactSchema.safeParse({
        name: "The Principal",
        phone: "",
        email: "",
        privacy_request_target_days: "8",
      }).success,
    ).toBe(false);
    expect(
      privacyContactSchema.parse({
        name: "The Principal",
        phone: "9876543210",
        email: "",
        privacy_request_target_days: "5",
      }),
    ).toEqual({
      name: "The Principal",
      phone: "9876543210",
      email: null,
      privacy_request_target_days: 5,
    });
  });
});

describe("US-2601 requests and complaints list", () => {
  it("lists requests with days left, overdue in words, and searches by POST", async () => {
    staff(MANAGE);
    stub.routes["POST /bff/api/v1/privacy/requests/search"] = () =>
      page([
        row(),
        row({
          id: "0192f3a4-0000-7000-8000-00000000e002",
          reference: "PR-2026-0002",
          kind: "grievance",
          days_left: -1,
          overdue: true,
        }),
      ]);
    renderWithIntl(<RequestsScreen tab="open" />);
    expect((await screen.findAllByText("PR-2026-0001")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("4 days left").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Overdue by 1 day").length).toBeGreaterThan(0);
    expect(screen.getAllByText(pm.kinds.grievance.label).length).toBeGreaterThan(0);
    expect(screen.getByRole("link", { name: new RegExp(pm.requests.logRequest) })).toHaveAttribute(
      "href",
      expect.stringContaining("/privacy/requests/new"),
    );
    const [call] = stub.callsTo("POST /bff/api/v1/privacy/requests/search");
    expect(call?.url.search).toBe("");
    expect(JSON.parse(call?.body ?? "{}")).toMatchObject({
      overdue: false,
      status: ["received", "in_progress", "waiting_for_requester", "decided"],
    });
  });

  it("shows the empty state and hides logging from readers", async () => {
    staff(READ);
    stub.routes["POST /bff/api/v1/privacy/requests/search"] = () => page([]);
    renderWithIntl(<RequestsScreen tab="open" />);
    expect(await screen.findByText(pm.requests.empty.open.title)).toBeInTheDocument();
    expect(screen.getByText(pm.requests.empty.open.body)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: new RegExp(pm.requests.logRequest) })).toBeNull();
  });
});

describe("US-2602 log a request", () => {
  it("says what is missing before sending anything", async () => {
    staff(MANAGE);
    const user = userEvent.setup();
    renderWithIntl(<NewRequestScreen />);
    await user.click(await screen.findByRole("button", { name: pm.new.submit }));
    expect((await screen.findAllByText(pm.validation.chooseKind)).length).toBeGreaterThan(0);
    expect(screen.getAllByText(pm.validation.chooseSubject).length).toBeGreaterThan(0);
    expect(screen.getAllByText(pm.validation.chooseRelationship).length).toBeGreaterThan(0);
    expect(stub.callsTo("POST /bff/api/v1/privacy/requests")).toEqual([]);
  });

  it("refuses an Aadhaar number in what they asked", () => {
    const parsed = buildRequestSchema("2026-10-21").safeParse({
      kind: "access",
      student_id: ID.student,
      requester_relationship: "father",
      received_on: "21/10/2026",
      request_text: `My Aadhaar is ${fakeAadhaar()}`,
    });
    expect(parsed.success).toBe(false);
  });

  it("builds the body with the chosen subject only", () => {
    const data = buildRequestSchema("2026-10-21").parse({
      kind: "access",
      subject_kind: "student",
      student_id: ID.student,
      guardian_id: ID.guardian,
      requester_relationship: "mother",
      received_on: "20/10/2026",
      verification_method: "not_checked",
    });
    expect(toCreateBody(data)).toEqual({
      kind: "access",
      subject: { student_id: ID.student },
      requester_relationship: "mother",
      received_on: "2026-10-20",
    });
  });

  it("logs a request found by a student search and offers the acknowledgement", async () => {
    staff(MANAGE);
    stub.routes["POST /bff/api/v1/students/search"] = () =>
      page([
        {
          id: ID.student,
          display_name: "Synthetica Venkata Sai",
          admission_no: "A-1",
          class_section: "IX-A",
          section_id: ID.section,
          status: "active",
          match: { field: "name", score: 1 },
        },
      ]);
    stub.routes["POST /bff/api/v1/privacy/requests"] = () =>
      Response.json(detail({ status: "received" }), { status: 201 });
    const user = userEvent.setup();
    renderWithIntl(<NewRequestScreen />);
    await user.click(await screen.findByRole("radio", { name: new RegExp(pm.kinds.access.label) }));
    await user.type(screen.getByRole("combobox", { name: pm.new.subject.searchLabel }), "Synth");
    await user.click(await screen.findByRole("option", { name: /Synthetica Venkata Sai/ }));
    await user.selectOptions(screen.getByLabelText(pm.new.relationship), "father");
    await user.click(screen.getByRole("button", { name: pm.new.submit }));
    expect(
      await screen.findByText(pm.new.savedTitle.replace("{reference}", "PR-2026-0001")),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: pm.new.printAcknowledgement })).toHaveAttribute(
      "href",
      letterHref(REQUEST, "acknowledgement"),
    );
    const [search] = stub.callsTo("POST /bff/api/v1/students/search");
    expect(search?.url.search).toBe("");
    const [call] = stub.callsTo("POST /bff/api/v1/privacy/requests");
    expect(call?.headers.get("idempotency-key")).toBeTruthy();
    expect(JSON.parse(call?.body ?? "{}")).toMatchObject({
      kind: "access",
      subject: { student_id: ID.student },
      requester_relationship: "father",
    });
  });
});

describe("US-2604 releasing an access report", () => {
  it("is disabled for the person who logged it, with the reason", async () => {
    staff(DECIDE);
    stub.routes[`GET /bff/api/v1/privacy/requests/${REQUEST}`] = () =>
      Response.json(detail({ logged_by_me: true, can_decide: false }));
    renderWithIntl(<RequestDetailScreen requestId={REQUEST} />);
    const release = await screen.findByRole("button", { name: pm.access.release });
    expect(release).toBeDisabled();
    expect(screen.getByText(pm.detail.someoneElse.release)).toBeInTheDocument();
  });

  it("another decider releases it after the consequence line", async () => {
    staff(DECIDE);
    stub.routes[`GET /bff/api/v1/privacy/requests/${REQUEST}`] = () => Response.json(detail());
    stub.routes[`POST /bff/api/v1/privacy/requests/${REQUEST}/report/release`] = () =>
      Response.json(detail({ report_released_at: NOW }));
    const user = userEvent.setup();
    renderWithIntl(<RequestDetailScreen requestId={REQUEST} />);
    await user.click(await screen.findByRole("button", { name: pm.access.release }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(pm.access.releaseConsequence)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: pm.access.release }));
    await waitFor(() =>
      expect(
        stub.callsTo(`POST /bff/api/v1/privacy/requests/${REQUEST}/report/release`),
      ).toHaveLength(1),
    );
  });

  it("hides the release from people without the decide permission", async () => {
    staff(MANAGE);
    stub.routes[`GET /bff/api/v1/privacy/requests/${REQUEST}`] = () =>
      Response.json(detail({ can_decide: false }));
    renderWithIntl(<RequestDetailScreen requestId={REQUEST} />);
    expect((await screen.findAllByText(pm.detail.decideRole)).length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: pm.access.release })).toBeNull();
  });
});

describe("US-2606 erasure plan", () => {
  it("offers checkboxes only on erasable rows and names the count", async () => {
    staff(DECIDE);
    stub.routes[`GET /bff/api/v1/privacy/requests/${REQUEST}`] = () =>
      Response.json(detail({ kind: "erasure", report: null }));
    stub.routes[`GET /bff/api/v1/privacy/requests/${REQUEST}/erasure-plan`] = () =>
      Response.json({ request_id: REQUEST, rows: plan() });
    const user = userEvent.setup();
    renderWithIntl(<RequestDetailScreen requestId={REQUEST} />);
    expect((await screen.findAllByText("Admission register")).length).toBeGreaterThan(0);
    const boxes = screen.getAllByRole("checkbox", { name: /Erase / });
    expect(new Set(boxes.map((box) => box.getAttribute("aria-label")))).toEqual(
      new Set(["Erase Ask questions and answers"]),
    );
    expect(screen.getAllByText(pm.erasure.outcome.refused_official_record).length).toBeGreaterThan(
      0,
    );
    expect(screen.getAllByText("Kept until 31/03/2035").length).toBeGreaterThan(0);
    const erase = screen.getByRole("button", { name: pm.erasure.erase });
    expect(erase).toBeDisabled();
    await user.click(boxes[0] as HTMLElement);
    expect(erase).toBeEnabled();
    await user.click(erase);
    const dialog = await screen.findByRole("dialog");
    expect(
      within(dialog).getByText(/12 records in 1 kind of record will be erased for good/),
    ).toBeInTheDocument();
  });
});

describe("FR-DPDP-004 privacy contact in the footer", () => {
  it("shows the contact when set and nothing otherwise", () => {
    const first = renderWithIntl(
      <PrivacyContactFooter contact={{ name: "The Principal", phone: "9876543210" }} />,
    );
    expect(screen.getByText(pm.footer.label)).toBeInTheDocument();
    expect(screen.getByText("The Principal · 9876543210")).toBeInTheDocument();
    first.unmount();
    renderWithIntl(<PrivacyContactFooter contact={null} />);
    expect(screen.queryByText(pm.footer.label)).toBeNull();
  });
});
