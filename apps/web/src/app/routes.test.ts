// @vitest-environment node
import { describe, expect, it } from "vitest";
import { GET as bffGet, POST as bffPost } from "./bff/api/v1/[...path]/route";
import { GET as health } from "./healthz/route";

describe("route handlers", () => {
  it("GET /healthz returns ok without caching", async () => {
    const response = health();
    expect(response.status).toBe(200);
    expect(response.headers.get("cache-control")).toBe("no-store");
    await expect(response.json()).resolves.toEqual({ status: "ok" });
  });

  it("the BFF stub answers 501 problem+json and echoes a safe request id", async () => {
    const response = await bffPost(
      new Request("https://office.school.example/bff/api/v1/students?q=x", {
        method: "POST",
        headers: { "x-request-id": "req_abc-123" },
      }),
    );
    expect(response.status).toBe(501);
    expect(response.headers.get("content-type")).toContain("application/problem+json");
    expect(response.headers.get("x-request-id")).toBe("req_abc-123");
    await expect(response.json()).resolves.toMatchObject({
      status: 501,
      code: "not_implemented",
      instance: "/bff/api/v1/students",
      request_id: "req_abc-123",
    });
  });

  it("replaces a malformed request id instead of echoing it", async () => {
    const response = await bffGet(
      new Request("https://office.school.example/bff/api/v1/me", {
        headers: { "x-request-id": "<script>alert(1)</script>" },
      }),
    );
    expect(response.headers.get("x-request-id")).toMatch(/^req_[0-9a-f-]{36}$/);
  });
});
