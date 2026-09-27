/**
 * Stand-ins for the e2e run with sign-in (E2E_STAND_IN=1): a scripted OIDC provider (the
 * in-process fake from src/test/fake-idp.ts served over HTTP, with a tiny login form) and a
 * canned API that answers with the generated OpenAPI shapes. Synthetic data only; never
 * used outside tests. The real API and the dev OIDC stub are exercised manually
 * (apps/web/README.md).
 */
import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import { createFakeIdp, type FakeIdp } from "../../src/test/fake-idp";

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
        permissions: ["tenant.billing.read", "support.ticket.create", "audit.read", "user.manage"],
        scopes: [{ type: "school", ref: null }],
        mfa: true,
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
  if (path === "/api/v1/platform/tenants" && method === "GET") return [200, page([TENANT_SUMMARY])];
  if (path === "/api/v1/platform/plans") return [200, page([PLAN])];
  if (path === "/api/v1/platform/invoices") return [200, page([INVOICE])];
  if (path.startsWith("/api/v1/")) return [200, page([])];
  return [404, { type: "about:blank", title: "Not found", status: 404, code: "not_found" }];
}

async function startApi(): Promise<Server> {
  const server = createServer(async (request, response) => {
    const url = new URL(request.url ?? "/", `http://localhost:${API_PORT}`);
    if (request.method !== "GET") await readBody(request);
    const [status, body] = apiAnswer(request.method ?? "GET", url.pathname, subjectOf(request));
    send(response, status, body, status >= 400 ? "application/problem+json" : "application/json");
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
