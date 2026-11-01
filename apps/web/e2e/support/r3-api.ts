/**
 * Canned API for the R3 journeys (e2e/r3.spec.ts; roadmap R3-2F "E2E (Playwright)"): privacy
 * requests with the access report and the erasure plan (FR-DPDP-005..016, ADR-0048), the breach
 * register and its duty clocks (FR-DPDP-030..035, ADR-0050), the CBSE disclosure and its public
 * page (FR-DISC-001..012, ADR-0051), the portal calendar (US-3101..US-3104, ADR-0053), consents
 * for optional purposes (FR-DPDP-020..024, ADR-0049) and SchoolOS incidents (platform).
 *
 * Answered only for the stand-in IdP subjects below, so the other journeys and their /me stay as
 * they were. The two school subjects are the maker and the checker of the privacy decisions
 * (the API refuses a decision by the person who logged the request; this stand-in does too).
 * Everything else these subjects ask for (readiness, students, guardians) is answered by the R1
 * office stand-in (r1-api.ts), see `R3_DELEGATE`.
 *
 * Stateful where a journey needs it; `resetR3()` (POST /__e2e/reset) starts again, and
 * POST /__e2e/r3/clock {"now": ISO} moves the stand-in's clock for the breach duty clocks.
 * Synthetic data only ("Synthetica …" names, SYN-… admission numbers).
 */
import type { components } from "@schoolos/api-client";

type Schemas = components["schemas"];
type Body = Record<string, unknown>;

/** The office admin who logs requests (the maker). */
export const DPO_OFFICE = "dpo-office";
/** The principal who decides (the checker). */
export const DPO_PRINCIPAL = "dpo-principal";
/** A SchoolOS operator who can see incidents (platform sign-in). */
export const SECURITY_OPERATOR = "security-op";
const SCHOOL_SUBJECTS = [DPO_OFFICE, DPO_PRINCIPAL];
/** The R1 subject whose canned answers (readiness, students, guardians) these subjects share. */
export const R3_DELEGATE = "office";

const T1 = "0192f3a4-0000-7000-8000-000000000001";
const YEAR_ID = "0192f3a4-0000-7000-8000-0000000000a1";
const SECTION_A = "0192f3a4-0000-7000-8000-000000000500";

export const R3_IDS = {
  /** R1's student "Synthetica Ravi Sai Kumar" (r1-api.ts R1_IDS.student). */
  student: "0192f3a4-0000-7000-8000-00000000f601",
  /** A seeded erasure request, logged by the office admin. */
  erasureRequest: "0192f3a4-0000-7000-8000-0000000003e1",
  /** A seeded breach for the register and detail pages (axe). */
  breach: "0192f3a4-0000-7000-8000-0000000003b1",
  purpose: "0192f3a4-0000-7000-8000-0000000003c1",
  snapshot: "0192f3a4-0000-7000-8000-0000000003d1",
  incident: "0192f3a4-0000-7000-8000-0000000003f1",
} as const;

export const R3_STUDENT = {
  name: "Synthetica Ravi Sai Kumar",
  admissionNo: "SYN-2026-301",
  classSection: "X-A",
} as const;

/** A disclosure link key: 22 base64url characters, made up for the stand-in. */
export const DISCLOSURE_KEY = "e2eDisclosureKey000001";
export const DISCLOSURE_SCHOOL = "Sri Saraswati High School";
export const PURPOSE_LABEL = "Photos on the school website";
export const CALENDAR_EVENT = {
  key: "bseap-ssc-nominal-roll",
  label: "AP SSC March 2027 nominal roll",
} as const;

const PERMISSIONS = [
  "support.ticket.create",
  "student.read_basic",
  "dq.findings.read",
  "dq.readiness.read",
  "dq.readiness.manage",
  "readiness.calendar.manage",
  "document.read",
  "tenant.settings.manage",
  "privacy.read",
  "privacy.request.manage",
  "privacy.request.decide",
  "privacy.breach.read",
  "privacy.breach.manage",
  "notice.draft",
  "consent.read",
  "consent.record",
  "disclosure.edit",
  "disclosure.publish",
];

const OPERATOR_PERMISSIONS = [
  "platform.tenants.read",
  "platform.incidents.read",
  "platform.incidents.manage",
];

const MEMBERS: Record<string, { user: string; membership: string; name: string; role: string }> = {
  [DPO_OFFICE]: {
    user: "0192f3a4-0000-7000-8000-0000000003a1",
    membership: "0192f3a4-0000-7000-8000-0000000003a2",
    name: "Synthetica Office Admin",
    role: "office_admin",
  },
  [DPO_PRINCIPAL]: {
    user: "0192f3a4-0000-7000-8000-0000000003a3",
    membership: "0192f3a4-0000-7000-8000-0000000003a4",
    name: "Synthetica Principal",
    role: "principal",
  },
};

const page = <T>(data: T[]) => ({ data, next_cursor: null });
const problem = (status: number, code: string): [number, unknown] => [
  status,
  { type: "about:blank", title: code, status, code },
];
const notFound = problem(404, "not_found");
const HOUR = 3_600_000;
const DAY = 24 * HOUR;
const isoDate = (ms: number) =>
  new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" }).format(new Date(ms));

function me(subject: string): Schemas["app__identity__schemas__MeOut"] {
  const member = MEMBERS[subject] ?? MEMBERS[DPO_OFFICE]!;
  return {
    user_id: member.user,
    membership_id: member.membership,
    tenant_id: T1,
    tenant_ids: [T1],
    display_name: member.name,
    preferred_language: "en",
    roles: [member.role],
    permissions: PERMISSIONS,
    scopes: [{ type: "school", ref: null }],
    mfa: true,
    tenant_status: "active",
    settings: { idle_timeout_minutes: 15, date_format: "DD/MM/YYYY", languages: ["en", "te"] },
  };
}

/* ------------------------------------------------------------------ state */

interface StoredRequest {
  request: Schemas["PrivacyRequestOut"];
  loggedBy: string;
  /** GETs since the report was asked for; the second finds it ready. */
  reportPolls: number;
}

interface R3State {
  now: number | null;
  requests: Map<string, StoredRequest>;
  nextRequest: number;
  breaches: Map<string, Schemas["BreachDetailOut"]>;
  published: Schemas["SnapshotOut"] | null;
  draftVersion: number;
  calendar: Schemas["CalendarEventOut"];
  consents: Schemas["PurposeConsentEntryOut"][];
  consentVersion: number;
}

const now = () => state.now ?? Date.now();

function seededErasure(): StoredRequest {
  const received = Date.now() - 2 * DAY;
  return {
    loggedBy: DPO_OFFICE,
    reportPolls: 0,
    request: {
      id: R3_IDS.erasureRequest,
      reference: "PR-2026-0007",
      kind: "erasure",
      status: "in_progress",
      subject_kind: "student",
      subject_label: `${R3_STUDENT.name} · ${R3_STUDENT.admissionNo}`,
      student_id: R3_IDS.student,
      guardian_id: null,
      membership_id: null,
      requester_relationship: "mother",
      requester_name: "Synthetica Lakshmi",
      received_on: isoDate(received),
      due_on: isoDate(received + 7 * DAY),
      days_left: 5,
      overdue: false,
      request_text: "Please erase the photos and optional records of my child.",
      response_text: null,
      responded_on: null,
      notes: null,
      verification_method: "in_person_document",
      verified_at: new Date(received).toISOString(),
      logged_by_me: false,
      logged_by_name: MEMBERS[DPO_OFFICE]!.name,
      can_decide: false,
      decided_at: null,
      decided_by_name: null,
      closed_at: null,
      events: [
        {
          seq: 1,
          event: "logged",
          at: new Date(received).toISOString(),
          actor_name: MEMBERS[DPO_OFFICE]!.name,
          detail: {},
        },
      ],
      links: [],
      nominee: null,
      report: null,
      report_released_at: null,
      version: 2,
    },
  };
}

/** Erasure plan (FR-DPDP-012): what can go, and what the school must keep and why. */
export const ERASURE_PLAN: Schemas["PrivacyErasurePlanRowOut"][] = [
  {
    class_key: "consents",
    label: "Optional consents",
    outcome: "erasable",
    count: 2,
    reason: "Given for an optional purpose; nothing requires the school to keep it.",
    manual_steps: [],
    until: null,
  },
  {
    class_key: "ask_memory",
    label: "Saved notes in Ask the school",
    outcome: "erasable",
    count: 1,
    reason: "Working notes only.",
    manual_steps: ["Remove the printed copy from the office file."],
    until: null,
  },
  {
    class_key: "admission_register",
    label: "Admission register entry",
    outcome: "refused_official_record",
    count: 1,
    reason: "The admission register is the school's legal record and is never erased.",
    manual_steps: [],
    until: null,
  },
  {
    class_key: "fee_ledger",
    label: "Fee receipts",
    outcome: "refused_legal_obligation",
    count: 4,
    reason: "Accounts must be kept for eight years under tax law.",
    manual_steps: [],
    until: null,
  },
  {
    class_key: "attendance",
    label: "Daily attendance",
    outcome: "retention_running",
    count: 180,
    reason: "Kept until the end of the retention period.",
    manual_steps: [],
    until: "2029-04-30",
  },
];

function seededBreach(): Schemas["BreachDetailOut"] {
  const noticed = Date.now() - 30 * HOUR;
  return breachOut({
    id: R3_IDS.breach,
    noticed_at: new Date(noticed).toISOString(),
    description: "A printed class list was left on the bus; it was returned the same day.",
    categories: ["identity", "contact"],
    approx_children: 40,
    approx_guardians: 40,
    approx_staff: 0,
    containment: "The list was collected and shredded.",
    created_at: new Date(noticed + HOUR).toISOString(),
  });
}

function breachOut(fields: {
  id: string;
  noticed_at: string;
  description: string;
  categories: Schemas["BreachCreate"]["categories"];
  approx_children: number;
  approx_guardians: number;
  approx_staff: number;
  containment: string | null;
  created_at: string;
}): Schemas["BreachDetailOut"] {
  const t0 = new Date(fields.noticed_at).getTime();
  const duties: Schemas["DutyOut"][] = [
    { key: "board_notice", due_at: new Date(t0 + 24 * HOUR).toISOString() },
    { key: "board_report", due_at: new Date(t0 + 72 * HOUR).toISOString() },
    { key: "people_notified", due_at: null },
  ].map((duty) => ({
    key: duty.key as Schemas["DutyOut"]["key"],
    due_at: duty.due_at,
    done_at: null,
    method: null,
    default_rule: true,
    overdue: false,
  }));
  return {
    ...fields,
    origin: "school",
    status: "open",
    closed_at: null,
    close_reason: null,
    platform_incident_id: null,
    processor_facts: null,
    recorded_by_name: MEMBERS[DPO_PRINCIPAL]!.name,
    affected: { students: 0, guardians: 0, staff: 0 },
    duties,
    next_duty: null,
    version: 1,
  };
}

/** The duty clocks as the API works them out at the stand-in's "now" (whole hours). */
function withClocks<T extends Schemas["BreachOut"]>(breach: T): T {
  const at = now();
  const duties = breach.duties.map((duty) => ({
    ...duty,
    overdue: !duty.done_at && duty.due_at !== null && new Date(duty.due_at).getTime() < at,
  }));
  const next = duties.find((duty) => !duty.done_at && duty.due_at) ?? null;
  let hours: number | null = null;
  if (next?.due_at) {
    const left = new Date(next.due_at).getTime() - at;
    hours = left >= 0 ? Math.floor(left / HOUR) : -Math.max(1, Math.floor(-left / HOUR));
  }
  return {
    ...breach,
    duties,
    next_duty: next ? { key: next.key, due_at: next.due_at, hours_left: hours } : null,
  };
}

function calendarEvent(): Schemas["CalendarEventOut"] {
  return {
    event_key: CALENDAR_EVENT.key,
    academic_year_id: YEAR_ID,
    label_en: CALENDAR_EVENT.label,
    label_te: CALENDAR_EVENT.label,
    portal: "bseap",
    boards: ["STATE_AP"],
    classes: ["10"],
    state: "expected",
    confirmed: false,
    confirmed_at: null,
    confirmed_by: null,
    opens_on: "2026-12-01",
    closes_on: "2027-01-31",
    opens_approximate: true,
    closes_approximate: true,
    expected: {
      opens_on: "2026-12-01",
      closes_on: "2027-01-31",
      opens_approximate: true,
      closes_approximate: true,
    },
    days_left: 91,
    document_id: null,
    lead_days: [30, 14, 7, 1],
    own: false,
    owner: null,
    profile_keys: ["bseap-ssc-2027"],
    readiness: [
      { profile_key: "bseap-ssc-2027", label_en: "AP SSC 2027 (BSEAP)", ready: 1, students: 3 },
    ],
    source_kind: null,
    sources: ["BSEAP circular (earlier years)"],
    verified: false,
    version: null,
  };
}

function fresh(): R3State {
  return {
    now: null,
    requests: new Map([[R3_IDS.erasureRequest, seededErasure()]]),
    nextRequest: 11,
    breaches: new Map([[R3_IDS.breach, seededBreach()]]),
    published: null,
    draftVersion: 1,
    calendar: calendarEvent(),
    consents: [],
    consentVersion: 1,
  };
}

let state: R3State = fresh();

export function resetR3(): void {
  state = fresh();
}

/** POST /__e2e/r3/clock: the time the stand-in uses for the breach clocks (null: real time). */
export function setR3Clock(at: string | null): void {
  state.now = at ? new Date(at).getTime() : null;
}

/* ------------------------------------------------------------------ privacy requests */

function view(stored: StoredRequest, subject: string): Schemas["PrivacyRequestOut"] {
  const request = stored.request;
  const mine = stored.loggedBy === subject;
  return {
    ...request,
    logged_by_me: mine,
    can_decide: !mine && request.verification_method !== null,
  };
}

function addEvent(stored: StoredRequest, event: string, subject: string) {
  const events = stored.request.events;
  events.push({
    seq: events.length + 1,
    event,
    at: new Date().toISOString(),
    actor_name: MEMBERS[subject]?.name ?? null,
    detail: {},
  });
  stored.request.version += 1;
}

function row(stored: StoredRequest): Schemas["PrivacyRequestRowOut"] {
  const r = stored.request;
  return {
    id: r.id,
    reference: r.reference,
    kind: r.kind,
    status: r.status,
    subject_kind: r.subject_kind,
    subject_label: r.subject_label,
    received_on: r.received_on,
    due_on: r.due_on,
    days_left: r.days_left,
    overdue: r.overdue,
  };
}

function createRequest(body: Body, subject: string): [number, unknown] {
  const kind = String(body.kind ?? "") as Schemas["PrivacyRequestOut"]["kind"];
  const subjectIn = (body.subject ?? {}) as Body;
  const studentId = typeof subjectIn.student_id === "string" ? subjectIn.student_id : null;
  if (!kind || !studentId) return problem(422, "validation_failed");
  const id = `0192f3a4-0000-7000-8000-0000000003${String(state.nextRequest).padStart(2, "0")}`;
  const reference = `PR-2026-${String(state.nextRequest).padStart(4, "0")}`;
  state.nextRequest += 1;
  const received = typeof body.received_on === "string" ? body.received_on : isoDate(Date.now());
  const method =
    typeof body.verification_method === "string"
      ? (body.verification_method as Schemas["PrivacyRequestOut"]["verification_method"])
      : null;
  const stored: StoredRequest = {
    loggedBy: subject,
    reportPolls: 0,
    request: {
      id,
      reference,
      kind,
      status: "received",
      subject_kind: "student",
      subject_label: `${R3_STUDENT.name} · ${R3_STUDENT.admissionNo}`,
      student_id: studentId,
      guardian_id: null,
      membership_id: null,
      requester_relationship: (body.requester_relationship ??
        "mother") as Schemas["PrivacyRequestOut"]["requester_relationship"],
      requester_name: typeof body.requester_name === "string" ? body.requester_name : null,
      received_on: received,
      due_on: isoDate(new Date(received).getTime() + 7 * DAY),
      days_left: 7,
      overdue: false,
      request_text: typeof body.request_text === "string" ? body.request_text : null,
      response_text: null,
      responded_on: null,
      notes: null,
      verification_method: method,
      verified_at: method ? new Date().toISOString() : null,
      logged_by_me: true,
      logged_by_name: MEMBERS[subject]?.name ?? null,
      can_decide: false,
      decided_at: null,
      decided_by_name: null,
      closed_at: null,
      events: [],
      links: [],
      nominee: null,
      report: null,
      report_released_at: null,
      version: 0,
    },
  };
  addEvent(stored, "logged", subject);
  state.requests.set(id, stored);
  return [201, view(stored, subject)];
}

function privacyAnswer(
  method: string,
  url: URL,
  subject: string,
  body: Body,
): [number, unknown] | undefined {
  const path = url.pathname;
  if (path === "/api/v1/privacy/requests" && method === "POST") return createRequest(body, subject);
  if (path === "/api/v1/privacy/requests/search" && method === "POST") {
    const statuses = Array.isArray(body.status) ? (body.status as string[]) : [];
    const rows = [...state.requests.values()]
      .filter((stored) => statuses.length === 0 || statuses.includes(stored.request.status))
      .filter((stored) => body.overdue !== true || stored.request.overdue)
      .map(row);
    return [200, page(rows)];
  }
  const match = /^\/api\/v1\/privacy\/requests\/([0-9a-f-]{36})(\/.*)?$/.exec(path);
  if (!match) return undefined;
  const stored = state.requests.get(match[1] ?? "");
  if (!stored) return notFound;
  const rest = match[2] ?? "";
  const request = stored.request;
  if (rest === "" && method === "GET") {
    if (request.report?.status === "queued") {
      stored.reportPolls += 1;
      if (stored.reportPolls >= 2) {
        request.report = {
          ...request.report,
          status: "ready",
          finished_at: new Date().toISOString(),
          expires_at: new Date(Date.now() + DAY).toISOString(),
        };
        addEvent(stored, "report_built", stored.loggedBy);
      }
    }
    return [200, view(stored, subject)];
  }
  if (rest === "" && method === "PATCH") {
    if (typeof body.verification_method === "string") {
      request.verification_method =
        body.verification_method as Schemas["PrivacyRequestOut"]["verification_method"];
      request.verified_at = new Date().toISOString();
      addEvent(stored, "verified", subject);
    }
    if (typeof body.status === "string") {
      request.status = body.status as Schemas["PrivacyRequestOut"]["status"];
      addEvent(stored, "status_changed", subject);
    }
    if ("notes" in body) {
      request.notes = typeof body.notes === "string" ? body.notes : null;
      addEvent(stored, "notes_changed", subject);
    }
    return [200, view(stored, subject)];
  }
  if (rest === "/report" && method === "POST") {
    stored.reportPolls = 0;
    request.report = {
      export_id: "0192f3a4-0000-7000-8000-0000000003e9",
      status: "queued",
      created_at: new Date().toISOString(),
      finished_at: null,
      expires_at: null,
      error_code: null,
    };
    request.report_released_at = null;
    if (request.status === "received") request.status = "in_progress";
    return [202, view(stored, subject)];
  }
  if (rest === "/report/release" && method === "POST") {
    if (stored.loggedBy === subject) return problem(403, "maker_checker");
    if (!request.verification_method) return problem(409, "not_verified");
    if (request.report?.status !== "ready") return problem(409, "report_not_ready");
    request.report_released_at = new Date().toISOString();
    request.status = "decided";
    request.decided_at = request.report_released_at;
    request.decided_by_name = MEMBERS[subject]?.name ?? null;
    addEvent(stored, "report_released", subject);
    return [200, view(stored, subject)];
  }
  if (rest === "/report/download-url" && method === "GET") {
    if (!request.report_released_at) return problem(409, "not_released");
    addEvent(stored, "report_downloaded", subject);
    return [
      200,
      {
        url: `${url.origin}/e2e-files/access-report-${request.reference.toLowerCase()}.pdf`,
        filename: `access-report-${request.reference}.pdf`,
        content_type: "application/pdf",
        expires_at: new Date(Date.now() + 5 * 60_000).toISOString(),
      } satisfies Schemas["PrivacyDownloadOut"],
    ];
  }
  if (rest === "/erasure-plan" && method === "GET") {
    return [
      200,
      { request_id: request.id, rows: ERASURE_PLAN } satisfies Schemas["PrivacyErasurePlanOut"],
    ];
  }
  if (rest === "/erasure" && method === "POST") {
    if (stored.loggedBy === subject) return problem(403, "maker_checker");
    const keys = Array.isArray(body.class_keys) ? (body.class_keys as string[]) : [];
    const erased = ERASURE_PLAN.filter(
      (plan) => plan.outcome === "erasable" && keys.includes(plan.class_key),
    );
    addEvent(stored, "erasure_executed", subject);
    return [
      200,
      {
        request_id: request.id,
        erased: erased.map((plan) => ({
          class_key: plan.class_key,
          count: plan.count ?? 0,
          manual_steps: plan.manual_steps ?? [],
        })),
        manual_steps: erased.flatMap((plan) => plan.manual_steps ?? []),
      } satisfies Schemas["PrivacyErasureResultOut"],
    ];
  }
  return undefined;
}

const TEMPLATES: Schemas["PrivacyTemplateOut"][] = [
  {
    key: "notice_parents",
    audience: "parents",
    languages: ["en"],
    template_version: "2026-10",
    last_issued_version: "2026-10",
    newer_version_available: false,
    placeholders: ["school_name", "contact_name"],
    unfilled: [],
  },
  {
    key: "notice_staff",
    audience: "staff",
    languages: ["en"],
    template_version: "2026-10",
    last_issued_version: null,
    newer_version_available: false,
    placeholders: ["school_name"],
    unfilled: [],
  },
];

const NOTICES: Schemas["PrivacyNoticeOut"][] = [
  {
    id: "0192f3a4-0000-7000-8000-0000000003a9",
    template_key: "notice_parents",
    template_version: "2026-10",
    audience: "parents",
    channel: "paper",
    language: "en",
    issued_on: "2026-10-15",
    document_id: null,
    recorded_at: "2026-10-15T05:00:00Z",
    recorded_by_name: "Synthetica Office Admin",
  },
];

/* ------------------------------------------------------------------ breaches */

function breachAnswer(method: string, path: string, body: Body): [number, unknown] | undefined {
  if (path === "/api/v1/privacy/breaches" && method === "GET") {
    return [200, [...state.breaches.values()].reverse().map((breach) => withClocks(breach))];
  }
  if (path === "/api/v1/privacy/breaches" && method === "POST") {
    const id = `0192f3a4-0000-7000-8000-0000000003b${state.breaches.size + 1}`;
    const count = (key: string) => (typeof body[key] === "number" ? (body[key] as number) : 0);
    const created = breachOut({
      id,
      noticed_at: typeof body.noticed_at === "string" ? body.noticed_at : new Date().toISOString(),
      description: typeof body.description === "string" ? body.description : "",
      categories: Array.isArray(body.categories)
        ? (body.categories as Schemas["BreachCreate"]["categories"])
        : [],
      approx_children: count("approx_children"),
      approx_guardians: count("approx_guardians"),
      approx_staff: count("approx_staff"),
      containment: typeof body.containment === "string" ? body.containment : null,
      created_at: new Date().toISOString(),
    });
    state.breaches.set(id, created);
    return [201, withClocks(created)];
  }
  const match = /^\/api\/v1\/privacy\/breaches\/([0-9a-f-]{36})$/.exec(path);
  if (match && method === "GET") {
    const found = state.breaches.get(match[1] ?? "");
    return found ? [200, withClocks(found)] : notFound;
  }
  if (/^\/api\/v1\/privacy\/breaches\/[0-9a-f-]{36}\/affected$/.test(path) && method === "GET") {
    return [
      200,
      {
        breach_id: path.split("/")[5] ?? "",
        student_ids: [],
        guardian_ids: [],
        membership_ids: [],
      } satisfies Schemas["AffectedOut"],
    ];
  }
  return undefined;
}

/* ------------------------------------------------------------------ disclosure */

const FORMAT: Schemas["DisclosureFormatOut"] = {
  key: "cbse-appendix-ix",
  title: "Mandatory public disclosure",
  version: 1,
  verified: false,
  sources: [{ title: "CBSE affiliation bye-laws, Appendix IX", url: null }],
  sections: [
    {
      key: "general",
      title: "General information",
      items: [
        {
          key: "school_name",
          label: "Name of the school",
          fill: "school",
          type: "text",
          required: true,
          contact: false,
          takes_documents: false,
          max_length: null,
          max_rows: null,
          max_value: null,
        },
        {
          key: "affiliation_no",
          label: "Affiliation number",
          fill: "input",
          type: "text",
          required: true,
          contact: false,
          takes_documents: false,
          max_length: 20,
          max_rows: null,
          max_value: null,
        },
      ],
    },
  ],
};

function draft(): Schemas["DisclosureDraftOut"] {
  return {
    format_key: FORMAT.key,
    format_version: FORMAT.version,
    from_schoolos: { school_name: { value: DISCLOSURE_SCHOOL, table: null } },
    inputs: { affiliation_no: "SYN0000001" },
    documents: {},
    missing: [],
    updated_at: "2026-10-30T05:00:00Z",
    updated_by_name: "Synthetica Principal",
    version: state.draftVersion,
  };
}

/** What the public page shows (always published here, so /d/{key} opens on its own). */
export const PUBLIC_DISCLOSURE: Schemas["PublicDisclosureOut"] = {
  title: "Mandatory public disclosure",
  school_name: DISCLOSURE_SCHOOL,
  format_version: 1,
  published_at: "2026-10-31T05:00:00Z",
  sections: [
    {
      key: "general",
      title: "General information",
      items: [
        { key: "school_name", label: "Name of the school", value: DISCLOSURE_SCHOOL },
        { key: "affiliation_no", label: "Affiliation number", value: "SYN0000001" },
        {
          key: "results_x",
          label: "Class X board results",
          table: {
            columns: ["Year", "Appeared", "Passed", "Pass percentage"],
            rows: [
              ["2025-26", "48", "47", "97.92"],
              ["2024-25", "fewer than 10", "fewer than 10", "100.00"],
            ],
          },
        },
        {
          key: "contact_email",
          label: "E-mail address of the school, a long one that must wrap on a phone",
          value: "principal.office.sri-saraswati-high-school@school.example",
        },
      ],
    },
  ],
};

function publicOrigin(): string {
  return (
    process.env.E2E_PUBLIC_BASE_URL ??
    `http://localhost:${Number(process.env.E2E_PORT ?? 3000) + 4}`
  ).replace(/\/$/, "");
}

function disclosureAnswer(method: string, url: URL): [number, unknown] | undefined {
  const path = url.pathname;
  if (path === "/api/v1/disclosure/format" && method === "GET") return [200, FORMAT];
  if (path === "/api/v1/disclosure/draft" && method === "GET") return [200, draft()];
  if (path === "/api/v1/disclosure/draft" && method === "PUT") {
    state.draftVersion += 1;
    return [200, draft()];
  }
  if (path === "/api/v1/disclosure/snapshots" && method === "GET") {
    return [200, state.published ? [state.published] : []];
  }
  if (path === "/api/v1/disclosure/link" && method === "GET") {
    return [
      200,
      {
        url: state.published ? `${publicOrigin()}/d/${DISCLOSURE_KEY}` : null,
        created_at: state.published?.published_at ?? null,
      } satisfies Schemas["LinkOut"],
    ];
  }
  if (path === "/api/v1/disclosure/publish" && method === "POST") {
    state.published = {
      id: R3_IDS.snapshot,
      format_key: FORMAT.key,
      format_version: FORMAT.version,
      published_at: new Date().toISOString(),
      published_by_name: "Synthetica Principal",
      live: true,
      documents: 0,
      html_status: "ready",
      pdf_status: "ready",
      views_total: 0,
      withdrawn_at: null,
      withdrawn_by_name: null,
      withdraw_reason: null,
    };
    return [201, state.published];
  }
  return undefined;
}

/** The public routes (no user; the BFF's service token only). */
function publicDisclosureAnswer(method: string, path: string): [number, unknown] | undefined {
  const match = /^\/api\/v1\/public\/disclosure\/([A-Za-z0-9_-]+)(\/documents\/\d+)?$/.exec(path);
  if (!match || method !== "GET") return undefined;
  if (match[1] !== DISCLOSURE_KEY) return notFound;
  if (match[2]) return notFound;
  return [200, PUBLIC_DISCLOSURE];
}

/* ------------------------------------------------------------------ portal calendar */

function calendarAnswer(method: string, url: URL, body: Body): [number, unknown] | undefined {
  const path = url.pathname;
  if (path === "/api/v1/dq/calendar" && method === "GET") {
    return [
      200,
      {
        academic_year_id: YEAR_ID,
        academic_year_label: "2026-27",
        events: [state.calendar],
      } satisfies Schemas["CalendarOut"],
    ];
  }
  if (path === "/api/v1/dq/calendar/assignees" && method === "GET") {
    return [
      200,
      [
        {
          membership_id: MEMBERS[DPO_OFFICE]!.membership,
          display_name: MEMBERS[DPO_OFFICE]!.name,
          roles: ["office_admin"],
        },
      ] satisfies Schemas["CalendarAssigneeOut"][],
    ];
  }
  if (path === `/api/v1/dq/calendar/${CALENDAR_EVENT.key}` && method === "PUT") {
    const opens = String(body.opens_on ?? "");
    const closes = String(body.closes_on ?? "");
    if (!/^\d{4}-\d{2}-\d{2}$/.test(opens) || !/^\d{4}-\d{2}-\d{2}$/.test(closes)) {
      return problem(422, "validation_failed");
    }
    const today = new Date(`${isoDate(Date.now())}T00:00:00Z`).getTime();
    const owner = typeof body.owner_membership_id === "string" ? body.owner_membership_id : null;
    state.calendar = {
      ...state.calendar,
      state: new Date(`${opens}T00:00:00Z`).getTime() <= today ? "open" : "confirmed",
      confirmed: true,
      confirmed_at: new Date().toISOString(),
      confirmed_by: { membership_id: MEMBERS[DPO_OFFICE]!.membership, display_name: "Synthetica" },
      opens_on: opens,
      closes_on: closes,
      opens_approximate: false,
      closes_approximate: false,
      days_left: Math.round((new Date(`${closes}T00:00:00Z`).getTime() - today) / DAY),
      source_kind: (body.source_kind ?? "circular") as Schemas["CalendarEventOut"]["source_kind"],
      owner: owner
        ? { membership_id: owner, display_name: MEMBERS[DPO_OFFICE]!.name }
        : state.calendar.owner,
      version: (state.calendar.version ?? 0) + 1,
    };
    return [200, state.calendar];
  }
  return undefined;
}

/** The readiness summary's "Next window" (FR-RDY-009), from the calendar above. */
function nextWindow(): Schemas["PortalWindowOut"] {
  const event = state.calendar;
  return {
    event_key: event.event_key,
    academic_year_id: event.academic_year_id,
    label_en: event.label_en,
    label_te: event.label_te,
    portal: event.portal,
    state: event.state,
    confirmed: event.confirmed,
    opens_on: event.opens_on,
    closes_on: event.closes_on,
    opens_approximate: event.opens_approximate,
    closes_approximate: event.closes_approximate,
    days_left: event.days_left,
  };
}

/* ------------------------------------------------------------------ consents */

const PURPOSE: Schemas["PurposeOut"] = {
  id: R3_IDS.purpose,
  key: "website_photos",
  label_en: PURPOSE_LABEL,
  label_te: null,
  description: "Class and event photos on the school's own website. Not needed for admission.",
  active: true,
  answered: 0,
  current_version_answered: false,
  form_version: 1,
  suggested_key: "website_photos",
  version: 1,
};

function purposeState(): Schemas["PurposeStateOut"] {
  const current = state.consents.at(-1) ?? null;
  return {
    purpose_id: PURPOSE.id,
    key: PURPOSE.key,
    label_en: PURPOSE.label_en,
    active: true,
    form_version: 1,
    answer: current?.status ?? "not_answered",
    state: current?.status === "given" ? "given" : "not_given",
    current,
    history: [...state.consents].reverse(),
  };
}

function counts(): Schemas["AnswerCounts"] {
  const answer = purposeState().answer;
  const base = { given: 0, refused: 0, withdrawn: 0, pending: 0, not_answered: 2, total: 3 };
  return { ...base, [answer]: base[answer] + 1 };
}

function consentAnswer(
  method: string,
  url: URL,
  subject: string,
  body: Body,
): [number, unknown] | undefined {
  const path = url.pathname;
  if (path === "/api/v1/consent-purposes" && method === "GET") {
    return [
      200,
      {
        data: [{ ...PURPOSE, answered: state.consents.length > 0 ? 1 : 0 }],
        suggestions: [
          {
            key: "website_photos",
            label_en: PURPOSE_LABEL,
            label_te: null,
            description: PURPOSE.description,
            enabled: true,
          },
          {
            key: "newsletter",
            label_en: "School newsletter by post",
            label_te: null,
            description: "A printed newsletter posted home each term.",
            enabled: false,
          },
        ],
      } satisfies Schemas["PurposeListOut"],
    ];
  }
  if (path === "/api/v1/consents/summary" && method === "GET") {
    const totals = counts();
    return [
      200,
      {
        purposes: [
          {
            purpose_id: PURPOSE.id,
            key: PURPOSE.key,
            label_en: PURPOSE.label_en,
            totals,
            sections: [{ section_id: SECTION_A, class_section: "X-A", counts: totals }],
          },
        ],
      } satisfies Schemas["PurposeConsentSummaryOut"],
    ];
  }
  const student = /^\/api\/v1\/students\/([0-9a-f-]{36})\/(consents|consent-view)$/.exec(path);
  if (!student) return undefined;
  if (student[1] !== R3_IDS.student) return notFound;
  if (student[2] === "consent-view" && method === "GET") {
    return [
      200,
      {
        student_id: R3_IDS.student,
        purposes: [purposeState()],
        apaar: null,
        whatsapp: null,
      } satisfies Schemas["ConsentViewOut"],
    ];
  }
  const consents = (): Schemas["StudentConsentsOut"] => ({
    student_id: R3_IDS.student,
    purposes: [purposeState()],
    version: state.consentVersion,
  });
  if (student[2] === "consents" && method === "GET") return [200, consents()];
  if (student[2] === "consents" && method === "POST") {
    const status = String(body.status ?? "") as Schemas["PurposeConsentEntryOut"]["status"];
    if (!["given", "refused", "pending", "withdrawn"].includes(status)) {
      return problem(422, "validation_failed");
    }
    state.consents.push({
      id: `0192f3a4-0000-7000-8000-0000000003c${state.consents.length + 2}`,
      seq: state.consents.length + 1,
      purpose_id: PURPOSE.id,
      status,
      relationship: (body.relationship ??
        null) as Schemas["PurposeConsentEntryOut"]["relationship"],
      guardian_id: typeof body.guardian_id === "string" ? body.guardian_id : null,
      decided_on: typeof body.decided_on === "string" ? body.decided_on : null,
      form_language: null,
      form_version: 1,
      evidence_document_id: null,
      note: typeof body.note === "string" ? body.note : null,
      privacy_request_id: null,
      recorded_at: new Date().toISOString(),
      recorded_by: MEMBERS[subject]?.user ?? MEMBERS[DPO_OFFICE]!.user,
      recorded_by_name: MEMBERS[subject]?.name ?? null,
    });
    state.consentVersion += 1;
    return [201, consents()];
  }
  return undefined;
}

/* ------------------------------------------------------------------ incidents (platform) */

const INCIDENT: Schemas["IncidentDetailOut"] = {
  id: R3_IDS.incident,
  summary: "Synthetic: a misconfigured backup bucket was readable for two hours.",
  severity: "P2",
  status: "contained",
  opened_at: "2026-10-30T04:30:00Z",
  created_at: "2026-10-30T04:40:00Z",
  created_by: "0192f3a4-0000-7000-8000-0000000000f1",
  lead_operator_id: "0192f3a4-0000-7000-8000-0000000000f1",
  confirm_by: null,
  confirmed_at: "2026-10-30T05:00:00Z",
  confirmed_by: "0192f3a4-0000-7000-8000-0000000000f2",
  first_confirmed_at: "2026-10-30T04:50:00Z",
  second_confirmed_by: "0192f3a4-0000-7000-8000-0000000000f2",
  cert_in_required: true,
  cert_in_due_at: "2026-10-30T10:30:00Z",
  cert_in_reported_at: "2026-10-30T08:00:00Z",
  schools_count: 1,
  schools_notified: 1,
  schools: [
    {
      tenant_id: T1,
      school_name: "Sri Saraswati High School",
      tenant_code: "sshs",
      mode: "shared",
      approx_people: 120,
      categories: ["contact"],
      window_from: "2026-10-30T02:00:00Z",
      window_to: "2026-10-30T04:00:00Z",
      school_notified_at: "2026-10-30T06:00:00Z",
      notice_due_at: "2026-10-31T04:30:00Z",
      pulled_at: "2026-10-30T06:05:00Z",
    },
  ],
  version: 4,
};

function incidentAnswer(method: string, path: string): [number, unknown] | undefined {
  if (path === "/api/v1/platform/me" && method === "GET") {
    return [
      200,
      {
        operator_id: "0192f3a4-0000-7000-8000-0000000000f1",
        roles: ["platform_security"],
        permissions: OPERATOR_PERMISSIONS,
        step_up_fresh: true,
      },
    ];
  }
  if (path === "/api/v1/platform/incidents" && method === "GET") {
    const summary: Schemas["IncidentOut"] = Object.fromEntries(
      Object.entries(INCIDENT).filter(([key]) => key !== "schools"),
    ) as Schemas["IncidentOut"];
    return [200, page([summary])];
  }
  if (path === `/api/v1/platform/incidents/${R3_IDS.incident}` && method === "GET") {
    return [200, INCIDENT];
  }
  return undefined;
}

/* ------------------------------------------------------------------ entry points */

export function r3Answer(
  method: string,
  url: URL,
  subject: string,
  body: Body,
): [number, unknown] | undefined {
  const path = url.pathname;
  const publicAnswer = publicDisclosureAnswer(method, path);
  if (publicAnswer) return publicAnswer;
  if (subject === SECURITY_OPERATOR) return incidentAnswer(method, path);
  if (!SCHOOL_SUBJECTS.includes(subject)) return undefined;
  if (path === "/api/v1/me" && method === "GET") return [200, me(subject)];
  if (path === "/api/v1/students/search" && method === "POST") {
    const query = typeof body.query === "string" ? body.query.toLowerCase() : "";
    const hit = R3_STUDENT.name.toLowerCase().includes(query);
    return [
      200,
      page(
        hit
          ? [
              {
                id: R3_IDS.student,
                display_name: R3_STUDENT.name,
                admission_no: R3_STUDENT.admissionNo,
                class_section: R3_STUDENT.classSection,
                section_id: SECTION_A,
                status: "active",
                match: { field: "name", score: 1 },
              } satisfies Schemas["StudentSummary"],
            ]
          : [],
      ),
    ];
  }
  if (path === "/api/v1/privacy/templates" && method === "GET") return [200, TEMPLATES];
  if (path === "/api/v1/privacy/notices" && method === "GET") return [200, NOTICES];
  return (
    privacyAnswer(method, url, subject, body) ??
    breachAnswer(method, path, body) ??
    disclosureAnswer(method, url) ??
    calendarAnswer(method, url, body) ??
    consentAnswer(method, url, subject, body)
  );
}

/** Whether a stand-in subject is one of the R3 school subjects (answered by R1 otherwise). */
export function isR3SchoolSubject(subject: string): boolean {
  return SCHOOL_SUBJECTS.includes(subject);
}

/**
 * The R1 readiness summary with the calendar's "Next window" (FR-RDY-009), for the R3 subjects
 * only (the R1 journeys see R1's answer unchanged).
 */
export function r3Decorate(
  method: string,
  path: string,
  subject: string,
  answer: [number, unknown],
): [number, unknown] {
  if (!isR3SchoolSubject(subject) || method !== "GET") return answer;
  const [status, payload] = answer;
  if (status !== 200 || !/^\/api\/v1\/dq\/readiness\/[a-z0-9-]+$/.test(path)) return answer;
  return [status, { ...(payload as object), next_window: nextWindow() }];
}
