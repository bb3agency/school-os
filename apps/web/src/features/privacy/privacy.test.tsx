import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { notificationHref } from "@/features/notifications/data";
import { installBffStub, page, problem, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { ID, NOW, fakeAadhaar, me } from "@/test/records-fixtures";
import { messages, renderWithIntl } from "@/test/render";
import { NewRequestScreen, buildRequestSchema, toCreateBody } from "./NewRequestScreen";
import { NoticesScreen, buildNoticeSchema, recordNotice } from "./NoticesScreen";
import { PrivacyContactFooter } from "./PrivacyContactFooter";
import { privacyContactSchema } from "./PrivacySettingsScreen";
import { RequestDetailScreen, canWithdraw, workPatch } from "./RequestDetailScreen";
import { RequestsScreen } from "./RequestsScreen";
import {
  chosenCount,
  letterHref,
  parseTab,
  searchBody,
  type ErasurePlanRow,
  type PrivacyFile,
  type PrivacyNotice,
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

  it("FR-DPDP-005 refuses before sending what the API refuses (nomination, verification, old date)", () => {
    const base = {
      kind: "access",
      student_id: ID.student,
      membership_id: ID.student,
      requester_relationship: "father",
      received_on: "21/10/2026",
    };
    const issues = (values: Record<string, string>) => {
      const parsed = buildRequestSchema("2026-10-21").safeParse({ ...base, ...values });
      return parsed.success
        ? []
        : parsed.error.issues.map((i) => `${i.path.join(".")}:${i.message}`);
    };
    expect(issues({ kind: "nomination", nominee_name: "N" })).toContain(
      "kind:nominationAdultsOnly",
    );
    expect(issues({ verification_method: "staff_sign_in" })).toContain(
      "verification_method:staffSignInStaffOnly",
    );
    expect(
      issues({ subject_kind: "member", verification_method: "guardian_phone_on_record" }),
    ).toContain("verification_method:guardianPhoneGuardiansOnly");
    expect(issues({ received_on: "19/10/2025" })).toContain("received_on:dateTooOld");
    expect(issues({ received_on: "20/10/2025" })).toEqual([]);
    expect(issues({ subject_kind: "member", verification_method: "staff_sign_in" })).toEqual([]);
    for (const key of [
      "nominationAdultsOnly",
      "staffSignInStaffOnly",
      "guardianPhoneGuardiansOnly",
      "dateTooOld",
    ]) {
      expect(pm.validation).toHaveProperty(key);
    }
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

describe("D60 FR-DPDP-005 mark a request as withdrawn", () => {
  const withdrawCall = `PATCH /bff/api/v1/privacy/requests/${REQUEST}`;
  const can = (permissions: readonly string[]) => (permission: string) =>
    permissions.includes(permission);

  it("is offered only while open, not decided, and with privacy.request.manage", () => {
    for (const status of ["received", "in_progress", "waiting_for_requester"] as const) {
      expect(canWithdraw({ status }, can(MANAGE))).toBe(true);
      expect(canWithdraw({ status }, can(READ))).toBe(false);
    }
    for (const status of ["decided", "closed", "withdrawn"] as const) {
      expect(canWithdraw({ status }, can(DECIDE))).toBe(false);
    }
  });

  it("hides the button from readers and on decided or withdrawn requests", async () => {
    staff(READ);
    stub.routes[`GET /bff/api/v1/privacy/requests/${REQUEST}`] = () =>
      Response.json(detail({ kind: "other", report: null }));
    const first = renderWithIntl(<RequestDetailScreen requestId={REQUEST} />);
    expect(await screen.findByText(pm.detail.requestTitle)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: pm.withdraw.action })).toBeNull();
    first.unmount();

    staff(MANAGE);
    stub.routes[`GET /bff/api/v1/privacy/requests/${REQUEST}`] = () =>
      Response.json(detail({ kind: "other", report: null, status: "decided" }));
    const second = renderWithIntl(<RequestDetailScreen requestId={REQUEST} />);
    expect(await screen.findByText(pm.detail.requestTitle)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: pm.withdraw.action })).toBeNull();
    second.unmount();

    stub.routes[`GET /bff/api/v1/privacy/requests/${REQUEST}`] = () =>
      Response.json(detail({ kind: "other", report: null, status: "withdrawn" }));
    renderWithIntl(<RequestDetailScreen requestId={REQUEST} />);
    expect(await screen.findByText(pm.detail.requestTitle)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: pm.withdraw.action })).toBeNull();
  });

  it("confirms, sends status withdrawn with If-Match, and reloads the request", async () => {
    staff(MANAGE);
    let status: PrivacyRequest["status"] = "in_progress";
    stub.routes[`GET /bff/api/v1/privacy/requests/${REQUEST}`] = () =>
      Response.json(detail({ kind: "other", report: null, status }));
    stub.routes[withdrawCall] = () => {
      status = "withdrawn";
      return Response.json(detail({ kind: "other", report: null, status, version: 3 }));
    };
    const user = userEvent.setup();
    renderWithIntl(<RequestDetailScreen requestId={REQUEST} />);
    await user.click(await screen.findByRole("button", { name: pm.withdraw.action }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(pm.withdraw.consequence)).toBeInTheDocument();
    expect(stub.callsTo(withdrawCall)).toHaveLength(0);
    await user.click(within(dialog).getByRole("button", { name: pm.withdraw.confirm }));
    await waitFor(() => expect(stub.callsTo(withdrawCall)).toHaveLength(1));
    const call = stub.callsTo(withdrawCall)[0];
    expect(call?.headers.get("If-Match")).toBe('W/"2"');
    expect(JSON.parse(call?.body ?? "{}")).toEqual({ status: "withdrawn" });
    // Refreshed: the request now shows as withdrawn and the button is gone.
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: pm.withdraw.action })).toBeNull(),
    );
    expect(screen.getAllByText(pm.status.withdrawn).length).toBeGreaterThan(0);
  });

  it("explains a 409 request_decided and a 412 in the dialog", async () => {
    staff(MANAGE);
    stub.routes[`GET /bff/api/v1/privacy/requests/${REQUEST}`] = () =>
      Response.json(detail({ kind: "other", report: null }));
    stub.routes[withdrawCall] = () => problem(409, "request_decided");
    const user = userEvent.setup();
    renderWithIntl(<RequestDetailScreen requestId={REQUEST} />);
    await user.click(await screen.findByRole("button", { name: pm.withdraw.action }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: pm.withdraw.confirm }));
    expect(await within(dialog).findByText(pm.errors.request_decided.title)).toBeInTheDocument();

    stub.routes[withdrawCall] = () => problem(412, "precondition_failed");
    await user.click(within(dialog).getByRole("button", { name: pm.withdraw.confirm }));
    expect(
      await within(dialog).findByText(messages.en.errors.api.precondition_failed.title),
    ).toBeInTheDocument();
  });
});

describe("D60 FR-DPDP-003 the notice-issue dialog's optional PDF", () => {
  const FILE = "0192f3a4-0000-7000-8000-00000000e201";
  const notice: PrivacyNotice = {
    id: "0192f3a4-0000-7000-8000-00000000e301",
    template_key: "notice_parents",
    template_version: "1.0",
    audience: "parents",
    language: "en",
    issued_on: "2026-10-20",
    channel: "paper",
    document_id: null,
    recorded_by_name: "Office Clerk",
    recorded_at: NOW,
  };
  const file = (status: PrivacyFile["status"]): PrivacyFile => ({
    export_id: FILE,
    status,
    error_code: null,
    created_at: NOW,
    finished_at: status === "ready" ? NOW : null,
    expires_at: null,
  });

  it("reads the tick as a boolean", () => {
    const base = {
      template_key: "notice_parents",
      template_version: "1.0",
      issued_on: "20/10/2026",
      channel: "paper",
    };
    const ticked = buildNoticeSchema("2026-10-21").safeParse({ ...base, make_pdf: "on" });
    expect(ticked.success && ticked.data.make_pdf).toBe(true);
    const plain = buildNoticeSchema("2026-10-21").safeParse(base);
    expect(plain.success && plain.data.make_pdf).toBe(false);
  });

  it("keeps the recorded notice when the PDF request fails", async () => {
    const plain = await recordNotice(() => Promise.resolve(notice), null);
    expect(plain).toEqual({ notice, pdfAsked: false, file: null, pdfError: undefined });
    const made = await recordNotice(
      () => Promise.resolve(notice),
      () => Promise.resolve(file("queued")),
    );
    expect(made.file?.export_id).toBe(FILE);
    const failure = new Error("offline");
    const failed = await recordNotice(
      () => Promise.resolve(notice),
      () => Promise.reject(failure),
    );
    expect(failed).toEqual({ notice, pdfAsked: true, file: null, pdfError: failure });
    // A notice that was not recorded asks for no PDF at all.
    const pdf = vi.fn(() => Promise.resolve(file("queued")));
    await expect(recordNotice(() => Promise.reject(failure), pdf)).rejects.toBe(failure);
    expect(pdf).not.toHaveBeenCalled();
  });

  function noticeRoutes() {
    stub.routes["GET /bff/api/v1/privacy/templates"] = () =>
      Response.json([
        {
          key: "notice_parents",
          template_version: "1.0",
          audience: "parents",
          languages: ["en"],
          placeholders: [],
          unfilled: [],
          last_issued_version: null,
          newer_version_available: false,
        },
      ]);
    stub.routes["GET /bff/api/v1/privacy/notices"] = () => Response.json([]);
    stub.routes["POST /bff/api/v1/privacy/notices"] = () => Response.json(notice, { status: 201 });
    stub.routes["POST /bff/api/v1/privacy/templates/notice_parents/pdf"] = () =>
      Response.json(file("queued"), { status: 202 });
    stub.routes[`GET /bff/api/v1/privacy/files/${FILE}`] = () => Response.json(file("ready"));
  }

  async function recordWith(tick: boolean) {
    const user = userEvent.setup();
    renderWithIntl(<NoticesScreen />);
    await user.click(await screen.findByRole("button", { name: pm.notices.record }));
    const dialog = await screen.findByRole("dialog");
    await user.selectOptions(within(dialog).getByLabelText(pm.notices.fieldChannel), "paper");
    if (tick) await user.click(within(dialog).getByRole("checkbox", { name: /PDF/ }));
    await user.click(within(dialog).getByRole("button", { name: pm.notices.recordConfirm }));
    return dialog;
  }

  it("records the notice, asks for its PDF and offers the download when ready", async () => {
    staff(MANAGE);
    noticeRoutes();
    const dialog = await recordWith(true);
    expect(await within(dialog).findByText(pm.notices.recordedTitle)).toBeInTheDocument();
    expect(stub.callsTo("POST /bff/api/v1/privacy/notices")).toHaveLength(1);
    expect(stub.callsTo("POST /bff/api/v1/privacy/templates/notice_parents/pdf")).toHaveLength(1);
    expect(
      await within(dialog).findByRole("button", { name: new RegExp(pm.pdf.download) }),
    ).toBeInTheDocument();
  });

  it("asks for no PDF when the box is not ticked", async () => {
    staff(MANAGE);
    noticeRoutes();
    const dialog = await recordWith(false);
    expect(await within(dialog).findByText(pm.notices.recordedBody)).toBeInTheDocument();
    expect(stub.callsTo("POST /bff/api/v1/privacy/notices")).toHaveLength(1);
    expect(stub.callsTo("POST /bff/api/v1/privacy/templates/notice_parents/pdf")).toHaveLength(0);
  });

  it("says when the PDF could not be asked for, and offers to make it", async () => {
    staff(MANAGE);
    noticeRoutes();
    stub.routes["POST /bff/api/v1/privacy/templates/notice_parents/pdf"] = () =>
      problem(409, "conflict");
    const dialog = await recordWith(true);
    expect(await within(dialog).findByText(pm.notices.recordedTitle)).toBeInTheDocument();
    expect(within(dialog).getByText(messages.en.errors.api.conflict.title)).toBeInTheDocument();
    expect(
      within(dialog).getByRole("button", { name: new RegExp(pm.pdf.make) }),
    ).toBeInTheDocument();
  });
});

describe("the notes form saves once per submit (XP-02, FR-DPDP-006)", () => {
  it("two synchronous submits send one PATCH", async () => {
    staff(MANAGE);
    stub.routes[`GET /bff/api/v1/privacy/requests/${REQUEST}`] = () => Response.json(detail());
    stub.routes[`PATCH /bff/api/v1/privacy/requests/${REQUEST}`] = () =>
      Response.json(detail({ notes: "Called the father.", version: 3 }));
    const user = userEvent.setup();
    renderWithIntl(<RequestDetailScreen requestId={REQUEST} />);
    const notes = await screen.findByLabelText(pm.work.notes);
    await user.type(notes, "Called the father.");
    const form = notes.closest("form");
    expect(form).not.toBeNull();
    act(() => {
      fireEvent.submit(form!);
      fireEvent.submit(form!);
    });
    const key = `PATCH /bff/api/v1/privacy/requests/${REQUEST}`;
    await waitFor(() => expect(stub.callsTo(key)).toHaveLength(1));
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(stub.callsTo(key)).toHaveLength(1);
  });
});

describe("after an erasure the requests list is read again (XP-04, FR-DPDP-012)", () => {
  it("the list on the same page shows the decided status", async () => {
    staff(DECIDE);
    let decided = false;
    stub.routes[`GET /bff/api/v1/privacy/requests/${REQUEST}`] = () =>
      Response.json(
        detail({ kind: "erasure", report: null, ...(decided ? { status: "decided" } : {}) }),
      );
    stub.routes[`GET /bff/api/v1/privacy/requests/${REQUEST}/erasure-plan`] = () =>
      Response.json({ request_id: REQUEST, rows: plan() });
    stub.routes["POST /bff/api/v1/privacy/requests/search"] = () =>
      page([row({ kind: "erasure", ...(decided ? { status: "decided" } : {}) })]);
    stub.routes[`POST /bff/api/v1/privacy/requests/${REQUEST}/erasure`] = () => {
      decided = true;
      return Response.json({
        request_id: REQUEST,
        erased: [{ class_key: "ask_history", count: 12 }],
        manual_steps: [],
      });
    };
    const user = userEvent.setup();
    renderWithIntl(
      <>
        <RequestsScreen tab="open" />
        <RequestDetailScreen requestId={REQUEST} />
      </>,
    );
    const box = await screen.findByRole("checkbox", { name: "Erase Ask questions and answers" });
    await waitFor(() =>
      expect(stub.callsTo("POST /bff/api/v1/privacy/requests/search")).toHaveLength(1),
    );
    await user.click(box);
    await user.click(screen.getByRole("button", { name: pm.erasure.erase }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: pm.erasure.erase }));
    expect(await screen.findByText(pm.erasure.doneTitle)).toBeInTheDocument();
    await waitFor(() =>
      expect(stub.callsTo("POST /bff/api/v1/privacy/requests/search").length).toBeGreaterThan(1),
    );
  });
});

describe("the access report is built once per click (XP-02, FR-DPDP-008)", () => {
  it("two synchronous clicks send one POST", async () => {
    staff(MANAGE);
    stub.routes[`GET /bff/api/v1/privacy/requests/${REQUEST}`] = () =>
      Response.json(detail({ report: null }));
    stub.routes[`POST /bff/api/v1/privacy/requests/${REQUEST}/report`] = () =>
      Response.json(detail(), { status: 202 });
    renderWithIntl(<RequestDetailScreen requestId={REQUEST} />);
    const build = await screen.findByRole("button", { name: pm.access.build });
    act(() => {
      fireEvent.click(build);
      fireEvent.click(build);
    });
    const key = `POST /bff/api/v1/privacy/requests/${REQUEST}/report`;
    await waitFor(() => expect(stub.callsTo(key)).toHaveLength(1));
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(stub.callsTo(key)).toHaveLength(1);
  });
});
