// @vitest-environment node
import { afterEach, describe, expect, it } from "vitest";
import { setAuthRuntimeForTesting } from "@/server/runtime";
import { createHarness } from "@/test/bff-harness";
import { GET as bffGet, POST as bffPost } from "./bff/api/v1/[...path]/route";
import { GET as sessionGet } from "./bff/auth/session/route";
import { GET as health } from "./healthz/route";

afterEach(() => {
  setAuthRuntimeForTesting(null);
});

describe("route handlers", () => {
  it("GET /healthz returns ok without caching", async () => {
    const response = health();
    expect(response.status).toBe(200);
    expect(response.headers.get("cache-control")).toBe("no-store");
    await expect(response.json()).resolves.toEqual({ status: "ok" });
  });

  it("the BFF answers 503 problem+json when it is not configured, leaking nothing", async () => {
    const response = await bffPost(
      new Request("https://office.school.example/bff/api/v1/students?q=x", {
        method: "POST",
        headers: { "x-request-id": "req_abc-123" },
      }),
    );
    expect(response.status).toBe(503);
    expect(response.headers.get("content-type")).toContain("application/problem+json");
    expect(response.headers.get("x-request-id")).toBe("req_abc-123");
    const body = (await response.json()) as Record<string, unknown>;
    expect(body).toMatchObject({
      status: 503,
      code: "service_unavailable",
      request_id: "req_abc-123",
    });
    expect(JSON.stringify(body)).not.toMatch(/SESSION_SECRET|REDIS_URL|OIDC/);
  });

  it("replaces a malformed request id instead of echoing it", async () => {
    const response = await bffGet(
      new Request("https://office.school.example/bff/api/v1/me", {
        headers: { "x-request-id": "<script>alert(1)</script>" },
      }),
    );
    expect(response.headers.get("x-request-id")).toMatch(/^req_[0-9a-f-]{36}$/);
  });

  it("route files use the process-wide runtime", async () => {
    const h = await createHarness();
    setAuthRuntimeForTesting(h.runtime);
    const signedOut = await bffGet(new Request("https://office.school.example/bff/api/v1/me"));
    expect(signedOut.status).toBe(401);
    await h.signIn("staff", { sub: "s-1" });
    const info = await sessionGet(h.request("/bff/auth/session"));
    await expect(info.json()).resolves.toMatchObject({ authenticated: true, kind: "staff" });
  });
});
