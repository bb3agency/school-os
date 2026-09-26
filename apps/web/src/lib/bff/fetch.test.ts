import { beforeEach, describe, expect, it, vi } from "vitest";
import { AuthRedirectError, createBffClient, createBffFetch } from "./fetch";
import { ACTIVITY_EVENT, forgetSessionInfo } from "./session-client";

const CSRF = "c".repeat(43);

function sessionResponse() {
  return Response.json({
    authenticated: true,
    kind: "staff",
    display_name: "Office Clerk",
    active_tenant_id: null,
    mfa: false,
    csrf_token: CSRF,
    idle_timeout_ms: 900_000,
    idle_expires_at: "2026-09-26T05:00:00.000Z",
    absolute_expires_at: "2026-09-26T16:00:00.000Z",
    expires_in_ms: 900_000,
  });
}

/** Global fetch (session info) + the BFF call under test. */
function setup(api: (request: Request) => Response | Promise<Response>) {
  const calls: Request[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input instanceof Request ? input.url : input);
      if (url.includes("/bff/auth/session")) return sessionResponse();
      throw new Error(`unexpected global fetch ${url}`);
    }),
  );
  const navigate = vi.fn();
  const bffFetch = createBffFetch({
    kind: "staff",
    locale: "te",
    navigate,
    fetchImpl: async (request) => {
      calls.push(request.clone());
      return api(request);
    },
  });
  return { calls, navigate, bffFetch };
}

beforeEach(() => {
  forgetSessionInfo();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/te/settings/users?page=2");
});

describe("browser BFF client (SEC-004)", () => {
  it("sends the CSRF token on state-changing calls only, and the UI language", async () => {
    const { calls, bffFetch } = setup(() => Response.json({}));
    await bffFetch(new Request("http://localhost:3000/bff/api/v1/classes"));
    await bffFetch(
      new Request("http://localhost:3000/bff/api/v1/classes", { method: "POST", body: "{}" }),
    );
    expect(calls[0]?.headers.get("x-csrf-token")).toBeNull();
    expect(calls[0]?.headers.get("accept-language")).toBe("te");
    expect(calls[1]?.headers.get("x-csrf-token")).toBe(CSRF);
    expect(calls[1]?.credentials).toBe("same-origin");
  });

  it("goes to sign-in on 401 and comes back to the current page", async () => {
    const { navigate, bffFetch } = setup(() =>
      Response.json({ code: "unauthenticated" }, { status: 401 }),
    );
    await expect(
      bffFetch(new Request("http://localhost:3000/bff/api/v1/users")),
    ).rejects.toBeInstanceOf(AuthRedirectError);
    expect(navigate).toHaveBeenCalledWith(
      "/bff/auth/login?next=%2Fte%2Fsettings%2Fusers%3Fpage%3D2",
    );
  });

  it("goes to the step-up URL on 428", async () => {
    const { navigate, bffFetch } = setup(() =>
      Response.json(
        {
          code: "step_up_required",
          step_up_url: "/bff/auth/step-up?next=%2Fte%2Fsettings%2Fusers",
        },
        { status: 428 },
      ),
    );
    await expect(bffFetch(new Request("http://localhost:3000/bff/api/v1/users"))).rejects.toThrow();
    expect(navigate).toHaveBeenCalledWith("/bff/auth/step-up?next=%2Fte%2Fsettings%2Fusers");
  });

  it("ignores a step_up_url that points anywhere else", async () => {
    const { navigate, bffFetch } = setup(() =>
      Response.json(
        { code: "step_up_required", step_up_url: "https://evil.example/" },
        { status: 428 },
      ),
    );
    await expect(bffFetch(new Request("http://localhost:3000/bff/api/v1/users"))).rejects.toThrow();
    expect(navigate).toHaveBeenCalledWith(
      "/bff/auth/step-up?next=%2Fte%2Fsettings%2Fusers%3Fpage%3D2",
    );
  });

  it("re-reads the CSRF token once after csrf_failed and retries with the same body", async () => {
    let first = true;
    const { calls, bffFetch } = setup(() => {
      if (first) {
        first = false;
        return Response.json({ code: "csrf_failed" }, { status: 403 });
      }
      return Response.json({ ok: true });
    });
    const response = await bffFetch(
      new Request("http://localhost:3000/bff/api/v1/classes", { method: "POST", body: '{"a":1}' }),
    );
    expect(response.status).toBe(200);
    expect(calls).toHaveLength(2);
    await expect(calls[1]?.text()).resolves.toBe('{"a":1}');
  });

  it("reports activity after a successful call (idle timer slides)", async () => {
    const { bffFetch } = setup(() => Response.json({}));
    const listener = vi.fn();
    window.addEventListener(ACTIVITY_EVENT, listener);
    await bffFetch(new Request("http://localhost:3000/bff/api/v1/classes"));
    window.removeEventListener(ACTIVITY_EVENT, listener);
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it("the typed client calls same-origin /bff/api/v1 paths", async () => {
    const seen: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: Request) => {
        seen.push(input.url);
        return Response.json({ data: [], next_cursor: null });
      }),
    );
    const client = createBffClient({ kind: "operator", locale: "en", navigate: vi.fn() });
    await client.GET("/api/v1/platform/tenants", { params: { query: { q: "sri" } } });
    expect(seen).toEqual(["http://localhost:3000/bff/api/v1/platform/tenants?q=sri"]);
  });
});
