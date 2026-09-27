/**
 * Test harness for the BFF route handlers: real config parsing, real openid-client against
 * an in-process fake IdP, in-memory Valkey stand-in, a scriptable fake API and a cookie jar.
 */
import type { CustomFetch } from "@/server/auth/oidc";
import { loadAuthConfig, type SessionKind } from "@/server/config";
import { createAuthRuntime, type AuthRuntime } from "@/server/runtime";
import { MemoryKeyValue } from "@/server/session/kv";
import { createFakeIdp, type FakeIdp, type FakeUser } from "./fake-idp";
import { testEnv } from "./server-env";

export type ApiHandler = (request: Request) => Response | Promise<Response>;

export interface Harness {
  runtime: AuthRuntime;
  kv: MemoryKeyValue;
  idp: Record<SessionKind, FakeIdp>;
  apiCalls: Request[];
  setApi(handler: ApiHandler): void;
  jar: Map<string, string>;
  /** Build a request to the app with the jar's cookies. */
  request(path: string, init?: RequestInit & { cookies?: boolean }): Request;
  /** Store Set-Cookie headers from a response in the jar. */
  absorb(response: Response): Response;
  signIn(kind: SessionKind, user: FakeUser, next?: string): Promise<Response>;
  csrf(kind?: SessionKind): Promise<string>;
  base: string;
}

/** The school every harness user belongs to by default (synthetic). */
export const HARNESS_TENANT = "0192f3a4-0000-7000-8000-000000000001";

export const HARNESS_ME = {
  user_id: "0192f3a4-0000-7000-8000-0000000000d1",
  membership_id: "0192f3a4-0000-7000-8000-0000000000e1",
  tenant_id: HARNESS_TENANT,
  tenant_ids: [HARNESS_TENANT],
  display_name: "Office Clerk",
  preferred_language: "en",
  roles: ["office_staff"],
  permissions: ["session.authenticated"],
  scopes: [{ type: "school", ref: null }],
  mfa: false,
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

/**
 * Default fake API: a staff user with one active school (so sign-in lands on `next`),
 * nothing to accept, and empty pages for every list.
 */
export function defaultApi(request: Request): Response {
  const path = new URL(request.url).pathname;
  if (path === "/api/v1/me/schools") {
    return json({
      data: [
        { tenant_id: HARNESS_TENANT, name: "Sample School", code: "sample", status: "active" },
      ],
    });
  }
  if (path === "/api/v1/me/accept-invitations") return json({ accepted: [] });
  if (path === "/api/v1/me/login-event") return json({ recorded: true, tenant_id: HARNESS_TENANT });
  if (path === "/api/v1/me") return json(HARNESS_ME);
  return json({ data: [], next_cursor: null });
}

export async function createHarness(
  envOverrides: Record<string, string | undefined> = {},
): Promise<Harness> {
  const env = testEnv(envOverrides);
  const config = loadAuthConfig(env);
  const kv = new MemoryKeyValue();
  // The support client lives in the operator pool (ADR-0023): one IdP, two app clients.
  const operatorIdp = await createFakeIdp({
    issuer: config.operator.issuer.href,
    clients: {
      [config.operator.clientId]: config.operator.clientSecret,
      ...(config.supportEnabled ? { [config.support.clientId]: config.support.clientSecret } : {}),
    },
  });
  const idp = {
    staff: await createFakeIdp({
      issuer: config.staff.issuer.href,
      clients: { [config.staff.clientId]: config.staff.clientSecret },
    }),
    operator: operatorIdp,
    support: operatorIdp,
  };
  const oidcFetch: CustomFetch = (url, init) =>
    url.startsWith(config.operator.issuer.href)
      ? idp.operator.fetch(url, init)
      : idp.staff.fetch(url, init);

  const apiCalls: Request[] = [];
  let api: ApiHandler = defaultApi;
  const apiFetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = new Request(input, init);
    apiCalls.push(request.clone());
    return api(request);
  }) as typeof fetch;

  const runtime = createAuthRuntime(config, { kv, oidcFetch, apiFetch, refreshPollMs: 2 });
  const jar = new Map<string, string>();
  const base = config.appBaseUrl.origin;

  const harness: Harness = {
    runtime,
    kv,
    idp,
    apiCalls,
    jar,
    base,
    setApi(handler) {
      api = handler;
    },
    request(path, init = {}) {
      const headers = new Headers(init.headers);
      if (init.cookies !== false && jar.size > 0) {
        headers.set("cookie", [...jar].map(([name, value]) => `${name}=${value}`).join("; "));
      }
      const { cookies: _unused, ...rest } = init;
      void _unused;
      return new Request(new URL(path, base), { ...rest, headers });
    },
    absorb(response) {
      for (const cookie of response.headers.getSetCookie()) {
        const [pair, ...attributes] = cookie.split(";").map((part) => part.trim());
        const [name, value] = (pair ?? "").split(/=(.*)/s) as [string, string];
        if (attributes.some((attribute) => attribute.toLowerCase() === "max-age=0"))
          jar.delete(name);
        else jar.set(name, value);
      }
      return response;
    },
    async signIn(kind, user, next) {
      const loginPath = kind === "operator" ? "/bff/auth/platform/login" : "/bff/auth/login";
      const { handleLogin, handleCallback } = await import("@/server/auth/handlers");
      const login = harness.absorb(
        await handleLogin(
          harness.request(`${loginPath}${next ? `?next=${encodeURIComponent(next)}` : ""}`),
          runtime,
          kind,
        ),
      );
      const back = idp[kind].authorize(login.headers.get("location") ?? "", user);
      return harness.absorb(await handleCallback(harness.request(back.href), runtime, kind));
    },
    async csrf(kind = "staff") {
      const { handleSessionInfo } = await import("@/server/auth/handlers");
      const response = await handleSessionInfo(
        harness.request(`/bff/auth/session?kind=${kind}`),
        runtime,
      );
      return ((await response.json()) as { csrf_token: string }).csrf_token;
    },
  };
  return harness;
}
