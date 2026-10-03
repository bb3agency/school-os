/**
 * Stand-ins for the e2e run with sign-in (E2E_STAND_IN=1): a scripted OIDC provider (the
 * in-process fake from src/test/fake-idp.ts served over HTTP, with a tiny login form) and a
 * canned API that answers with the generated OpenAPI shapes (the M1 journeys' stateful part
 * lives in journey-api.ts; POST /__e2e/reset starts it again). Synthetic data only; never
 * used outside tests. The real API and the dev OIDC stub are exercised manually
 * (apps/web/README.md).
 */
import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import { createFakeIdp, type FakeIdp } from "../../src/test/fake-idp";
import { askAnswer, askEvents, resetAsk } from "./ask-api";
import { FILES_PREFIX, journeyAnswer, resetJourney } from "./journey-api";
import { resetSheets, sheetAnswer } from "./sheet-api";

export const IDP_PORT = Number(process.env.E2E_IDP_PORT ?? 8089);
export const API_PORT = Number(process.env.E2E_API_PORT ?? 8099);
export const IDP_BASE = `http://localhost:${IDP_PORT}`;
/** Same placeholder secrets as playwright.config.ts (dev-only, accepted only on localhost). */
const devOnly = (name: string) => `dev-only-e2e-${name}-${"x".repeat(32)}`;
export const CLIENTS = {
  staff: { id: "schoolos-web", secret: devOnly("staff-client") },
  operator: { id: "schoolos-platform", secret: devOnly("operator-client") },
} as const;

const T1 = "0192f3a4-0000-7000-8000-000000000001";
const T2 = "0192f3a4-0000-7000-8000-000000000002";
const SUB = "0192f3a4-0000-7000-8000-00000000b001";
/** A school whose provisioning stopped (platform school detail, FR-PLT-002). */
const T3 = "0192f3a4-0000-7000-8000-000000000003";
const USER_ID = "0192f3a4-0000-7000-8000-0000000000d1";
const DOC_ID = "0192f3a4-0000-7000-8000-00000000d001";
const YEAR_ID = "0192f3a4-0000-7000-8000-0000000000a1";
const CLASS_ID = "0192f3a4-0000-7000-8000-0000000000c6";
const NEXT_YEAR_ID = "0192f3a4-0000-7000-8000-0000000000a2";

async function readBody(request: IncomingMessage): Promise<string> {
  const chunks: Buffer[] = [];
  for await (const chunk of request) chunks.push(chunk as Buffer);
  return Buffer.concat(chunks).toString("utf8");
}

function send(response: ServerResponse, status: number, body: unknown, type = "application/json") {
  response.writeHead(status, { "content-type": type, "cache-control": "no-store" });
  response.end(typeof body === "string" ? body : JSON.stringify(body));
}

function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
}

function loginForm(issuerPath: string, authorizeUrl: string): string {
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Stand-in sign-in</title></head>
<body><main><h1>Stand-in sign-in (${escapeHtml(issuerPath)})</h1>
<form method="post" action="/${escapeHtml(issuerPath)}/login">
<input type="hidden" name="authorize" value="${escapeHtml(authorizeUrl)}">
<label for="sub">Subject</label> <input id="sub" name="sub" value="clerk">
<label><input type="checkbox" name="mfa" value="true" checked> MFA</label>
<button type="submit">Sign in</button></form></main></body></html>`;
}

async function startIdp(): Promise<Server> {
  const idps: Record<string, FakeIdp> = {
    schoolos: await createFakeIdp({
      issuer: `${IDP_BASE}/schoolos`,
      clients: { [CLIENTS.staff.id]: CLIENTS.staff.secret },
    }),
    platform: await createFakeIdp({
      issuer: `${IDP_BASE}/platform`,
      clients: { [CLIENTS.operator.id]: CLIENTS.operator.secret },
    }),
  };
  const server = createServer(async (request, response) => {
    const url = new URL(request.url ?? "/", IDP_BASE);
    const [, issuerPath = "", rest = ""] = url.pathname.split("/");
    const idp = idps[issuerPath];
    if (!idp) return send(response, 404, { error: "not_found" });
    if (rest === "authorize")
      return send(response, 200, loginForm(issuerPath, url.href), "text/html");
    if (rest === "login" && request.method === "POST") {
      const form = new URLSearchParams(await readBody(request));
      const back = idp.authorize(form.get("authorize") ?? "", {
        sub: form.get("sub") ?? "clerk",
        name: `Synthetic ${form.get("sub") ?? "user"}`,
        mfa: form.get("mfa") === "true",
      });
      response.writeHead(302, { location: back.href });
      return response.end();
    }
    if (rest === "endsession") {
      const target = url.searchParams.get("post_logout_redirect_uri") ?? "/";
      response.writeHead(302, { location: target });
      return response.end();
    }
    const body = request.method === "POST" ? await readBody(request) : undefined;
    const options = {
      method: request.method ?? "GET",
      headers: new Headers(),
      redirect: "manual",
      ...(body !== undefined ? { body } : {}),
    } as unknown as Parameters<FakeIdp["fetch"]>[1];
    const answer = await idp.fetch(url.href, options);
    send(
      response,
      answer.status,
      await answer.text(),
      answer.headers.get("content-type") ?? "text/plain",
    );
  });
  await new Promise<void>((resolve) => server.listen(IDP_PORT, resolve));
  return server;
}

function subjectOf(request: IncomingMessage): string {
  const token = (request.headers.authorization ?? "").replace(/^Bearer /, "");
  try {
    const payload = JSON.parse(Buffer.from(token.split(".")[1] ?? "", "base64url").toString()) as {
      sub?: string;
    };
    return payload.sub ?? "";
  } catch {
    return "";
  }
}

const page = (data: unknown[]) => ({ data, next_cursor: null });

const TENANT_SUMMARY = {
  tenant_id: T1,
  school_name: "Sri Saraswati High School",
  code: "sshs",
  tier: "shared",
  tenant_status: "active",
  subscription_status: "trial",
  plan_code: "standard",
  deployment_status: "healthy",
  app_version: "2026.09.1",
  last_heartbeat_at: "2026-09-26T04:30:00Z",
  created_at: "2026-06-01T04:30:00Z",
};

const PLAN = {
  id: "0192f3a4-0000-7000-8000-00000000a001",
  code: "standard",
  name: "Standard",
  version: 1,
  tier: "shared",
  status: "published",
  billing_period: "monthly",
  pricing_model: "flat",
  base_price_inr: "4999.00",
  per_student_price_inr: null,
  included_students: null,
  gst_rate: "18.00",
  sac_code: "998314",
  trial_days: 30,
  limits: { students: 1500 },
  features: {},
  published_at: "2026-06-01T00:00:00Z",
  created_at: "2026-06-01T00:00:00Z",
};

const INVOICE = {
  id: "0192f3a4-0000-7000-8000-00000000c001",
  tenant_id: T1,
  subscription_id: SUB,
  invoice_number: "SOS/2026-27/000123",
  financial_year: "2026-27",
  status: "issued",
  period_start: "2026-09-01",
  period_end: "2026-09-30",
  issue_date: "2026-09-01",
  due_date: "2026-09-15",
  supplier_legal_name: "SchoolOS",
  supplier_gstin: "37AAAAA0000A1Z5",
  supplier_state_code: "37",
  recipient_legal_name: "Sample Education Society",
  recipient_gstin: null,
  place_of_supply_state_code: "37",
  tax_type: "cgst_sgst",
  taxable_value_inr: "4999.00",
  cgst_inr: "449.91",
  sgst_inr: "449.91",
  igst_inr: "0.00",
  total_inr: "5898.82",
  amount_paid_inr: "0.00",
  tds_inr: "0.00",
  balance_due_inr: "5898.82",
  notes: null,
  void_reason: null,
  version: 1,
};

const STAMP = {
  version: 1,
  created_at: "2026-06-01T04:30:00Z",
  updated_at: "2026-06-01T04:30:00Z",
};

/** School console fixtures for the screens added on 2026-09-27 (synthetic only). */
const STRUCTURE = {
  years: [
    {
      id: YEAR_ID,
      label: "2026-27",
      starts_on: "2026-06-01",
      ends_on: "2027-04-30",
      is_current: true,
      ...STAMP,
    },
    {
      id: NEXT_YEAR_ID,
      label: "2027-28",
      starts_on: "2027-06-01",
      ends_on: "2028-04-30",
      is_current: false,
      archived_at: null,
      ...STAMP,
    },
  ],
  classes: [
    {
      id: CLASS_ID,
      code: "6",
      display_en: "Class 6",
      display_te: "6వ తరగతి",
      sort_order: 6,
      ...STAMP,
    },
  ],
  sections: ["A", "B"].map((name, index) => ({
    id: `0192f3a4-0000-7000-8000-0000000005${index}0`,
    academic_year_id: YEAR_ID,
    class_id: CLASS_ID,
    name,
    class_teacher_membership_id: null,
    ...STAMP,
  })),
};

/** Staff directory (class teacher picker) and a promotion preview (FR-TEN-011). */
const STAFF = [
  {
    membership_id: "0192f3a4-0000-7000-8000-0000000000e2",
    display_name: "Synthetic Teacher",
    roles: ["class_teacher"],
  },
];
const PROMOTIONS_PATH = `/api/v1/academic-years/${YEAR_ID}/promotions`;
const PROMOTION_PREVIEW = {
  from_academic_year_id: YEAR_ID,
  to_academic_year_id: NEXT_YEAR_ID,
  counts: { promoted: 1, held_back: 0, graduated: 0, skipped: 0 },
  groups: [
    {
      from_section_id: STRUCTURE.sections[0]?.id,
      from_label: "6-A",
      outcome: "promoted",
      to_section_id: null,
      to_label: null,
      count: 1,
    },
  ],
  problems: [
    {
      code: "no_target_section",
      from_section_id: STRUCTURE.sections[0]?.id,
      from_label: "6-A",
      target_class_id: CLASS_ID,
      count: 1,
    },
  ],
  students: [
    {
      student_id: "0192f3a4-0000-7000-8000-00000000d001",
      enrollment_id: "0192f3a4-0000-7000-8000-00000000e101",
      from_section_id: STRUCTURE.sections[0]?.id,
      outcome: "promoted",
      to_section_id: null,
      reason: "no_target_section",
    },
  ],
  plan_fingerprint: "a".repeat(64),
  can_commit: false,
};

const TENANT = {
  id: T1,
  code: "sshs",
  name: "Sri Saraswati High School",
  boards: ["STATE_AP", "CBSE"],
  state_code: "37",
  status: "active",
  plan_tier: "shared",
  deployment_mode: "shared",
  settings: {
    languages: ["en", "te"],
    date_format: "DD/MM/YYYY",
    idle_timeout_minutes: 15,
    ai_features_enabled: true,
    ai_monthly_budget_inr: 5000,
  },
  version: 7,
};

const ROLES = [
  { key: "office_admin", name_en: "Office admin", name_te: "ఆఫీసు అడ్మిన్", scoped: false },
  { key: "class_teacher", name_en: "Class teacher", name_te: "తరగతి ఉపాధ్యాయులు", scoped: true },
].map((role, index) => ({
  id: `0192f3a4-0000-7000-8000-0000000007${index}0`,
  ...role,
  is_system: true,
  permissions: ["student.read_basic"],
  grantable: true,
}));

const USER = {
  id: USER_ID,
  membership_id: "0192f3a4-0000-7000-8000-0000000000e1",
  display_name: "Synthetic Teacher",
  email: "teacher@school.example",
  preferred_language: "te",
  status: "active",
  expires_at: null,
  roles: ["class_teacher"],
  scopes: [{ type: "section", ref: STRUCTURE.sections[0]?.id }],
  last_login_at: "2026-09-20T04:30:00Z",
  created_at: "2026-06-01T04:30:00Z",
  version: 3,
};

const DOC_VERSION = {
  id: "0192f3a4-0000-7000-8000-00000000d101",
  version_no: 1,
  mime_type: "application/pdf",
  size_bytes: 204_800,
  status: "ready",
  error: null,
  created_at: "2026-09-20T05:00:00Z",
};
const DOCUMENT = {
  id: DOC_ID,
  purpose: "circular",
  doc_type: "circular",
  title: "Dasara holidays circular 2026",
  issuer: "Synthetic school",
  issued_on: "2026-09-15",
  academic_year_id: null,
  language: "en",
  sensitivity: "C1",
  status: "active",
  current_version: DOC_VERSION,
  acl: [],
  created_by: USER_ID,
  uploaded_by: {
    membership_id: "0192f3a4-0000-7000-8000-0000000000e1",
    display_name: "Synthetic Teacher",
  },
  uploaded_by_me: false,
  created_at: "2026-09-20T05:00:00Z",
  updated_at: "2026-09-21T05:00:00Z",
  version: 3,
};

/**
 * Ask the school (docs/06 §5.1): a synthetic cited answer about the holiday circular. The events
 * (meta with the conversation, status steps, preview deltas, the validated final, citation,
 * follow-ups, done) and the stored conversations and memory live in ask-api.ts.
 */
const ASK_QUERY_ID = "0192f3a4-0000-7000-8000-00000000e001";
const ASK_SOURCE = `sos://doc/${DOC_ID}/v1#p1`;
const ASK_CITATION = {
  index: 1,
  source: ASK_SOURCE,
  title: "Dasara holidays circular 2026",
  snippet: "Holidays from 02/10/2026 to 12/10/2026; school reopens on 13/10/2026.",
};
const VERIFIED_ANSWER = {
  id: "0192f3a4-0000-7000-8000-00000000e101",
  question: "When are the Dasara holidays?",
  language: "en",
  answer_text: "From 02/10/2026 to 12/10/2026. School reopens on 13/10/2026.",
  citations: [{ source: ASK_SOURCE, cited_text: "Holidays from 02/10/2026 to 12/10/2026" }],
  status: "active",
  verified_by: "0192f3a4-0000-7000-8000-0000000000e1",
  verified_by_name: "Synthetic Principal",
  verified_at: "2026-09-27T05:30:00Z",
  review_due: null,
  version: 1,
  created_at: "2026-09-27T05:30:00Z",
};

/**
 * Stream a canned answer as Server-Sent Events, one event at a time (FR-KB-008). A question
 * containing "slowly" waits 1.5 s between events, so the e2e run can press Stop mid-answer.
 */
/* M5 (US-1701..US-1709): synthetic flags, counts, rules and exams. */
const FLAG_ID = "0192f3a4-0000-7000-8000-00000000f501";
const STAFF_REF = {
  membership_id: "0192f3a4-0000-7000-8000-0000000000e1",
  display_name: "Synthetic Teacher",
};
const INSIGHT_FLAG = {
  id: FLAG_ID,
  student: {
    id: "0192f3a4-0000-7000-8000-00000000e501",
    full_name: "Synthetica Rao",
    admission_no: "SYN-0001",
    section_label: "VI-A",
  },
  indicator: "attendance",
  rule: "attendance_streak",
  evidence: { days: 3, from: "2026-09-22", to: "2026-09-24", threshold: 3 },
  status: "in_progress",
  owner: STAFF_REF,
  raised_on: "2026-09-24",
  due_on: "2026-10-01",
  overdue: false,
  actioned: true,
  first_action_at: "2026-09-25T05:00:00Z",
  closed_at: null,
  close_reason: null,
  raised_by: null,
  version: 2,
};
const INSIGHT_FLAG_DETAIL = {
  ...INSIGHT_FLAG,
  actions: [
    {
      id: "0192f3a4-0000-7000-8000-00000000f601",
      kind: "raised",
      acted_on: "2026-09-24",
      note: null,
      by: null,
      created_at: "2026-09-24T12:10:00Z",
    },
    {
      id: "0192f3a4-0000-7000-8000-00000000f602",
      kind: "called_parent",
      acted_on: "2026-09-25",
      note: "Synthetic note: fever, back on Monday.",
      by: STAFF_REF,
      created_at: "2026-09-25T05:00:00Z",
    },
  ],
};
const INSIGHT_SUMMARY = {
  since: "2026-08-30",
  raised: 4,
  actioned_on_time: 3,
  actioned_late: 0,
  not_actioned: 1,
  overdue: 1,
  open: 2,
};
const rule = (key: string, indicator: string, threshold: number, min: number, max: number) => ({
  key,
  indicator,
  enabled: true,
  can_disable: key !== "attendance_streak",
  threshold,
  default: threshold,
  min,
  max,
  window: key === "attendance_rate" || key === "behaviour_concerns" ? 30 : null,
  min_days: key === "attendance_rate" ? 10 : null,
});
const INSIGHT_SETTINGS = {
  rules: [
    rule("attendance_streak", "attendance", 3, 2, 5),
    rule("attendance_rate", "attendance", 75, 60, 90),
    rule("course_low", "course", 35, 25, 50),
    rule("course_decline", "course", 15, 10, 30),
    rule("behaviour_concerns", "behaviour", 3, 2, 5),
  ],
  rules_version: 1,
  due_days: 7,
  version: 0,
  updated_at: null,
};
const EXAMS = [
  {
    id: "0192f3a4-0000-7000-8000-00000000ea01",
    academic_year_id: YEAR_ID,
    name: "Formative assessment 1",
    held_on: "2026-08-10",
    version: 1,
  },
];

function streamAnswer(
  response: ServerResponse,
  events: Array<[string, unknown]>,
  slow: boolean,
  onEnd: () => void = () => undefined,
): void {
  response.writeHead(200, {
    "content-type": "text/event-stream; charset=utf-8",
    "cache-control": "no-store",
    "x-accel-buffering": "no",
  });
  let i = 0;
  let closed = false;
  // The browser pressed Stop (the BFF aborts this request): write nothing more.
  response.on("close", () => {
    closed = true;
  });
  const next = () => {
    if (closed) return;
    const item = events[i];
    if (!item) {
      onEnd();
      return void response.end();
    }
    const [event, data] = item;
    response.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
    i += 1;
    setTimeout(next, slow ? 1500 : 60);
  };
  next();
}

/** Platform school detail while provisioning is stopped (docs/16 §5.4). */
const PROVISIONING_DETAIL = {
  ...provisioningSummary(),
  boards: ["CBSE"],
  tenant_status_reason: null,
  offboard_requested_at: null,
  offboard_approved_at: null,
  subscription: null,
  counts: { users: 0, active_memberships: 0, academic_years: 0, sections: 0 },
  open_tickets: 0,
  invoices: [],
  flag_overrides: {},
  provisioning: {
    state: "failed",
    failed_step: "initialise",
    last_error: "unexpected_error",
    attempts: 2,
    in_progress: false,
    resumable: true,
    updated_at: "2026-09-27T04:30:00Z",
  },
};

function provisioningSummary() {
  return {
    ...TENANT_SUMMARY,
    tenant_id: T3,
    school_name: "Sample Model School",
    code: "sms",
    tenant_status: "provisioning",
    subscription_status: "trial",
    deployment_status: "healthy",
    app_version: null,
    last_heartbeat_at: null,
  };
}

const OPERATOR_PERMISSIONS = [
  "platform.tenants.read",
  "platform.tenants.provision",
  "platform.tenants.suspend",
  "platform.plans.manage",
  "platform.subscriptions.read",
  "platform.subscriptions.manage",
  "platform.invoices.read",
  "platform.invoices.manage",
  "platform.usage.read",
  "platform.fleet.read",
  "platform.support.read",
  "platform.support.manage",
  "platform.announcements.manage",
  "platform.audit.read",
];

function apiAnswer(method: string, path: string, subject: string): [number, unknown] {
  const multi = subject === "multi";
  const schools = [
    { tenant_id: T1, name: "Sri Saraswati High School", code: "sshs", status: "active" },
    ...(multi ? [{ tenant_id: T2, name: "Vidya Nilayam", code: "vn", status: "suspended" }] : []),
  ];
  if (path === "/api/v1/me/accept-invitations") return [200, { accepted: [] }];
  if (path === "/api/v1/me/schools") return [200, { data: schools }];
  if (path === "/api/v1/me/login-event") return [200, { recorded: true, tenant_id: T1 }];
  if (path === "/api/v1/me/active-tenant") return [200, {}];
  if (path === "/api/v1/me")
    return [
      200,
      {
        user_id: "0192f3a4-0000-7000-8000-0000000000d1",
        membership_id: "0192f3a4-0000-7000-8000-0000000000e1",
        tenant_id: T1,
        tenant_ids: schools.map((school) => school.tenant_id),
        display_name: "Synthetic Principal",
        preferred_language: "en",
        roles: ["principal"],
        permissions: [
          "tenant.billing.read",
          "support.ticket.create",
          "audit.read",
          "user.manage",
          "role.assign",
          "student.read_basic",
          "tenant.structure.manage",
          "tenant.settings.manage",
          "document.read",
          "document.upload",
          "document.manage_acl",
          "kb.ask",
          "kb.verified_answer.manage",
          "certificate.read",
          "certificate.issue",
          "certificate.approve",
          "register.read",
          "attendance.read",
          "attendance.record",
          "exam.manage",
          "marks.read",
          "marks.record",
          "insights.read",
          "insights.note",
          "insights.act",
          "insights.manage",
        ],
        scopes: [{ type: "school", ref: null }],
        mfa: true,
        settings: { idle_timeout_minutes: 15, date_format: "DD/MM/YYYY", languages: ["en", "te"] },
      },
    ];
  if (path === "/api/v1/announcements")
    return [
      200,
      [
        {
          id: "0192f3a4-0000-7000-8000-00000000a501",
          title_en: "Maintenance on Sunday",
          title_te: "ఆదివారం నిర్వహణ",
          body_en: "SchoolOS is unavailable from 06:00 to 07:00.",
          body_te: "06:00 నుండి 07:00 వరకు SchoolOS అందుబాటులో ఉండదు.",
          severity: "maintenance",
          starts_at: "2026-09-26T00:00:00Z",
          ends_at: "2099-09-27T01:30:00Z",
        },
      ],
    ];
  if (path === "/api/v1/tenant/billing")
    return [
      200,
      {
        available: true,
        plan_code: "standard",
        plan_name: "Standard",
        tier: "shared",
        billing_period: "monthly",
        status: "trial",
        current_period_start: "2026-09-01",
        current_period_end: "2026-09-30",
        trial_ends_at: "2026-10-01T00:00:00Z",
        past_due_since: null,
        grace_ends_on: null,
        cancel_at_period_end: false,
        usage_date: "2026-09-25",
        usage: [
          { metric: "students", used: "1210", limit: "1500", percent: 80 },
          { metric: "storage_gb", used: "3.25", limit: "50", percent: 6 },
        ],
        amount_due_inr: "0.00",
      },
    ];
  if (path === "/api/v1/tenant/billing/invoices") return [200, page([])];
  if (path === "/api/v1/tenant") return [200, TENANT];
  if (path === "/api/v1/academic-years") return [200, page(STRUCTURE.years)];
  if (path === "/api/v1/classes") return [200, page(STRUCTURE.classes)];
  if (path === "/api/v1/sections") return [200, page(STRUCTURE.sections)];
  if (path === "/api/v1/staff") return [200, page(STAFF)];
  if (path === `/api/v1/academic-years/${YEAR_ID}`) return [200, STRUCTURE.years[0]];
  if (path === PROMOTIONS_PATH) return [200, []];
  if (path === `${PROMOTIONS_PATH}:preview` && method === "POST") return [200, PROMOTION_PREVIEW];
  if (path === "/api/v1/roles") return [200, page(ROLES)];
  if (path === "/api/v1/users") return [200, page([USER])];
  if (path === `/api/v1/users/${USER_ID}`) return [200, USER];
  if (path === "/api/v1/documents") return [200, page([DOCUMENT])];
  if (path === `/api/v1/documents/${DOC_ID}`)
    return [200, { ...DOCUMENT, versions: [DOC_VERSION] }];
  if (path === "/api/v1/knowledge/verified-answers" && method === "GET")
    return [200, page([VERIFIED_ANSWER])];
  if (/^\/api\/v1\/knowledge\/queries\/[^/]+\/feedback$/.test(path) && method === "POST")
    return [
      200,
      {
        query_id: ASK_QUERY_ID,
        feedback: "helpful",
        reason: null,
        recorded_at: "2026-09-28T05:00:00Z",
      },
    ];
  if (path === "/api/v1/knowledge/search" && method === "POST")
    return [
      200,
      {
        data: [
          {
            source: ASK_SOURCE,
            document_id: DOC_ID,
            version_no: 1,
            page_from: 1,
            page_to: 1,
            doc_type: "circular",
            title: "Dasara holidays circular 2026",
            issued_on: "2026-09-15",
            snippet: "Holidays from 02/10/2026 to 12/10/2026; school reopens on 13/10/2026.",
            score: 0.82,
          },
        ],
      },
    ];
  if (path === "/api/v1/audit/verify")
    return [200, { ok: true, checked: 1234, first_bad_seq: null, reason: null }];
  if (path === `/api/v1/platform/tenants/${T3}`) return [200, PROVISIONING_DETAIL];
  if (path === "/api/v1/platform/me")
    return [
      200,
      {
        operator_id: "0192f3a4-0000-7000-8000-0000000000f1",
        roles: ["platform_owner"],
        permissions: OPERATOR_PERMISSIONS,
        step_up_fresh: true,
      },
    ];
  if (path === "/api/v1/platform/dashboard")
    return [
      200,
      {
        mrr_inr: "125000.00",
        arr_inr: "1500000.00",
        schools_by_status: { active: 12, suspended: 1 },
        schools_by_tier: { shared: 11, dedicated: 2 },
        trials_running: 3,
        trials_ending_14d: 1,
        past_due_count: 1,
        past_due_amount_inr: "5898.82",
        oldest_overdue_due_date: "2026-09-15",
        fleet_by_status: { healthy: 4, degraded: 1 },
        fleet_versions: { "2026.09.1": 5 },
        ai_spend_mtd_inr: "3200.50",
        open_tickets_by_priority: { p2: 1, p3: 2 },
        tickets_sla_breached: 0,
      },
    ];
  if (path === "/api/v1/platform/tenants" && method === "GET")
    return [200, page([TENANT_SUMMARY, provisioningSummary()])];
  if (path === "/api/v1/platform/plans") return [200, page([PLAN])];
  if (path === "/api/v1/platform/invoices") return [200, page([INVOICE])];
  if (path === "/api/v1/insights/flags") return [200, page([INSIGHT_FLAG])];
  if (path === `/api/v1/insights/flags/${FLAG_ID}`) return [200, INSIGHT_FLAG_DETAIL];
  if (path === `/api/v1/insights/flags/${FLAG_ID}/owners`)
    return [200, [{ ...STAFF_REF, display_name: "Synthetic Teacher", roles: ["class_teacher"] }]];
  if (path === "/api/v1/insights/summary") return [200, INSIGHT_SUMMARY];
  if (path === "/api/v1/insights/settings") return [200, INSIGHT_SETTINGS];
  if (path === "/api/v1/exams") return [200, EXAMS];
  if (path.startsWith("/api/v1/")) return [200, page([])];
  return [404, { type: "about:blank", title: "Not found", status: 404, code: "not_found" }];
}

async function startApi(): Promise<Server> {
  const server = createServer(async (request, response) => {
    const url = new URL(request.url ?? "/", `http://localhost:${API_PORT}`);
    const method = request.method ?? "GET";
    const raw = method !== "GET" ? await readBody(request) : "";
    // Test control and the files behind canned download links (not API paths).
    if (url.pathname === "/__e2e/reset" && method === "POST") {
      resetJourney();
      resetAsk();
      resetSheets();
      return send(response, 204, "");
    }
    if (url.pathname.startsWith(FILES_PREFIX)) {
      const name = url.pathname.slice(FILES_PREFIX.length).replace(/[^a-z0-9.-]/g, "");
      response.writeHead(200, {
        "content-type": "application/octet-stream",
        "content-disposition": `attachment; filename="${name}"`,
        "cache-control": "no-store",
      });
      return response.end("Synthetic pre-check file (e2e stand-in)");
    }
    let body: Record<string, unknown> = {};
    try {
      body = raw ? (JSON.parse(raw) as Record<string, unknown>) : {};
    } catch {
      body = {};
    }
    if (url.pathname === "/api/v1/knowledge/ask" && method === "POST") {
      const question = typeof body.question === "string" ? body.question : "";
      const answer = askEvents(body, ASK_CITATION, question.includes("budget"));
      if (!answer) {
        // A conversation that is not (or no longer) there: 404, as the API answers.
        const problem = { type: "about:blank", title: "Not found", status: 404, code: "not_found" };
        return send(response, 404, problem, "application/problem+json");
      }
      return streamAnswer(response, answer.events, question.includes("slowly"), answer.commit);
    }
    // Sheet editor (sheet-api.ts): ETags, If-Match and file downloads.
    const ifMatch = request.headers["if-match"];
    const sheet = sheetAnswer(method, url, body, typeof ifMatch === "string" ? ifMatch : undefined);
    if (sheet?.kind === "file") {
      response.writeHead(200, {
        "content-type": sheet.type,
        "content-disposition": `attachment; filename="${sheet.name}"`,
        "cache-control": "no-store",
        "x-content-type-options": "nosniff",
      });
      return response.end(sheet.content);
    }
    if (sheet) {
      if (sheet.etag) response.setHeader("ETag", sheet.etag);
      return send(
        response,
        sheet.status,
        sheet.body,
        sheet.status >= 400 ? "application/problem+json" : "application/json",
      );
    }
    const subject = subjectOf(request);
    const [status, answer] =
      askAnswer(method, url.pathname, body) ??
      journeyAnswer(method, url, subject, body) ??
      apiAnswer(method, url.pathname, subject);
    if (status === 204) {
      response.writeHead(204, { "cache-control": "no-store" });
      return void response.end();
    }
    send(response, status, answer, status >= 400 ? "application/problem+json" : "application/json");
  });
  await new Promise<void>((resolve) => server.listen(API_PORT, resolve));
  return server;
}

export async function startStandIns(): Promise<() => Promise<void>> {
  const servers = [await startIdp(), await startApi()];
  return async () => {
    await Promise.all(
      servers.map((server) => new Promise<void>((resolve) => server.close(() => resolve()))),
    );
  };
}
