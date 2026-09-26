// @vitest-environment node
import { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { proxy } from "./proxy";

function request(path: string, headers: Record<string, string> = {}) {
  return new NextRequest(new URL(path, "https://office.school.example"), { headers });
}

function nonceOf(csp: string | null): string | undefined {
  return /'nonce-([^']+)'/.exec(csp ?? "")?.[1];
}

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("proxy (SEC-010, NFR-I18N-001)", () => {
  it("redirects / to the default locale and still sets security headers", () => {
    const response = proxy(request("/"));
    expect(response.status).toBe(307);
    expect(response.headers.get("location")).toMatch(/\/en$/);
    expect(response.headers.get("content-security-policy")).toContain("'strict-dynamic'");
    expect(response.headers.get("x-content-type-options")).toBe("nosniff");
  });

  it("negotiates Telugu from Accept-Language", () => {
    const response = proxy(request("/", { "accept-language": "te-IN,te;q=0.9,en;q=0.5" }));
    expect(response.headers.get("location")).toMatch(/\/te$/);
  });

  it("forwards a fresh nonce to rendering via the request CSP header", () => {
    const first = proxy(request("/en/platform"));
    const second = proxy(request("/en/platform"));
    const responseCsp = first.headers.get("content-security-policy");
    const nonce = nonceOf(responseCsp);
    expect(nonce).toBeDefined();
    expect(nonceOf(second.headers.get("content-security-policy"))).not.toBe(nonce);
    // Next.js request-header override mechanism (what NextResponse.next({ request }) sets).
    expect(first.headers.get("x-middleware-request-content-security-policy")).toBe(responseCsp);
    expect(first.headers.get("x-middleware-request-x-nonce")).toBe(nonce);
  });

  it("sets HSTS by default and every documented header", () => {
    const headers = proxy(request("/en")).headers;
    expect(headers.get("strict-transport-security")).toBe(
      "max-age=63072000; includeSubDomains; preload",
    );
    expect(headers.get("referrer-policy")).toBe("strict-origin-when-cross-origin");
    expect(headers.get("permissions-policy")).toBe(
      "camera=(self), microphone=(), geolocation=(), payment=()",
    );
    expect(headers.get("cross-origin-opener-policy")).toBe("same-origin");
    expect(headers.get("cross-origin-resource-policy")).toBe("same-origin");
  });

  it("skips HSTS and upgrade-insecure-requests for plain-http local runs", () => {
    vi.stubEnv("APP_BASE_URL", "http://localhost:3000");
    const headers = proxy(request("/en")).headers;
    expect(headers.get("strict-transport-security")).toBeNull();
    expect(headers.get("content-security-policy")).not.toContain("upgrade-insecure-requests");
  });

  it("does not localise the BFF or health check but still sets headers", () => {
    for (const path of ["/healthz", "/bff/api/v1/me", "/bff/api/v1/files/report.pdf"]) {
      const response = proxy(request(path));
      expect(response.status, path).toBe(200);
      expect(response.headers.get("location"), path).toBeNull();
      expect(response.headers.get("content-security-policy"), path).toContain("default-src 'self'");
    }
  });
});
