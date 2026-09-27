import { vi } from "vitest";
import { forgetSessionInfo } from "@/lib/bff/session-client";
import { setNavigateForTesting } from "@/lib/bff/query";

/**
 * Browser-side BFF double for screen tests: routes keyed "METHOD /path" (path without the
 * query), a signed-in session with a CSRF token, and every request recorded (body text too).
 * Unknown routes answer 404 problem+json. Synthetic data only.
 */
export type StubRoute = (request: Request, url: URL) => Response | Promise<Response>;

export interface StubCall {
  method: string;
  url: URL;
  headers: Headers;
  body: string;
}

export interface BffStub {
  routes: Record<string, StubRoute>;
  calls: StubCall[];
  navigate: ReturnType<typeof vi.fn>;
  /** Calls to one route ("POST /bff/api/v1/platform/tenants"). */
  callsTo: (key: string) => StubCall[];
}

export const CSRF = "csrf-test-token";

export function page<T>(data: readonly T[]) {
  return Response.json({ data, next_cursor: null });
}

export function problem(status: number, code: string, extra: Record<string, unknown> = {}) {
  return Response.json(
    { type: "about:blank", title: code, status, code, request_id: "req_test", ...extra },
    { status, headers: { "content-type": "application/problem+json" } },
  );
}

export function installBffStub(kind: "staff" | "operator" = "operator"): BffStub {
  forgetSessionInfo();
  const navigate = vi.fn();
  setNavigateForTesting(navigate);
  const stub: BffStub = {
    routes: {},
    calls: [],
    navigate,
    callsTo: (key) => stub.calls.filter((call) => `${call.method} ${call.url.pathname}` === key),
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: Request | string, init?: RequestInit) => {
      const request =
        typeof input === "string" ? new Request(new URL(input, "http://localhost"), init) : input;
      const url = new URL(request.url);
      const body = request.method === "GET" ? "" : await request.clone().text();
      stub.calls.push({ method: request.method, url, headers: request.headers, body });
      if (url.pathname === "/bff/auth/session") {
        return Response.json({
          authenticated: true,
          kind,
          display_name: "Test User",
          active_tenant_id: null,
          mfa: true,
          csrf_token: CSRF,
          idle_timeout_ms: 900_000,
          idle_expires_at: new Date(Date.now() + 900_000).toISOString(),
          absolute_expires_at: new Date(Date.now() + 3_600_000).toISOString(),
          expires_in_ms: 900_000,
        });
      }
      const route = stub.routes[`${request.method} ${url.pathname}`];
      return route ? route(request, url) : problem(404, "not_found");
    }),
  );
  return stub;
}

export function uninstallBffStub(): void {
  setNavigateForTesting(undefined);
  vi.unstubAllGlobals();
}
