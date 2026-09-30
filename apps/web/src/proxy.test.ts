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

  it("negotiates Telugu from Accept-Language when Telugu is switched on (ADR-0036)", () => {
    vi.stubEnv("SOS_TELUGU_ENABLED", "true");
    const response = proxy(request("/", { "accept-language": "te-IN,te;q=0.9,en;q=0.5" }));
    expect(response.headers.get("location")).toMatch(/\/te$/);
  });

  it("serves /te pages when Telugu is switched on (ADR-0036)", () => {
    vi.stubEnv("SOS_TELUGU_ENABLED", "true");
    const response = proxy(request("/te/students"));
    expect(response.headers.get("location")).toBeNull();
    expect(response.headers.get("link") ?? "").toContain('hreflang="te"');
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

  it("passes the current path to layouts for the sign-in return, ignoring a client value", () => {
    const response = proxy(
      request("/en/settings/users?page=2", { "x-sos-path": "https://evil.example/" }),
    );
    expect(response.headers.get("x-middleware-request-x-sos-path")).toBe(
      "/en/settings/users?page=2",
    );
  });

  it("localises the signed-out page and keeps its query", () => {
    const response = proxy(request("/signed-out?kind=operator&error=signin_failed"));
    expect(response.status).toBe(307);
    expect(response.headers.get("location")).toMatch(
      /\/en\/signed-out\?kind=operator&error=signin_failed$/,
    );
  });

  it("does not localise the BFF or health check but still sets headers", () => {
    for (const path of ["/healthz", "/bff/api/v1/me", "/bff/api/v1/files/report.pdf"]) {
      const response = proxy(request(path));
      expect(response.status, path).toBe(200);
      expect(response.headers.get("location"), path).toBeNull();
      expect(response.headers.get("content-security-policy"), path).toContain("default-src 'self'");
    }
  });

  it("leaves the correction memo's CSP to the BFF handler, and only that path (FR-CR-005)", () => {
    const memo = proxy(
      request("/bff/api/v1/change-requests/0192f3a4-0000-7000-8000-00000000c001/memo"),
    );
    expect(memo.headers.get("content-security-policy")).toBeNull();
    expect(memo.headers.get("x-content-type-options")).toBe("nosniff");
    expect(memo.headers.get("cross-origin-opener-policy")).toBe("same-origin");
    for (const path of [
      "/bff/api/v1/change-requests/0192f3a4-0000-7000-8000-00000000c001",
      "/bff/api/v1/change-requests/0192f3a4-0000-7000-8000-00000000c001/memo/x",
      "/bff/api/v1/change-requests/../memo",
    ]) {
      expect(proxy(request(path)).headers.get("content-security-policy"), path).toContain(
        "default-src 'self'",
      );
    }
  });

  it("leaves certificate and register print views' CSP to the API, and only those paths (FR-CERT-011, FR-REG-004)", () => {
    for (const path of [
      "/bff/api/v1/certificates/0192f3a4-0000-7000-8000-0000000ce001/print",
      "/bff/api/v1/registers/transfer-certificates",
      "/bff/api/v1/registers/certificates",
      "/bff/api/v1/registers/admission-withdrawal",
    ]) {
      const response = proxy(request(path));
      expect(response.headers.get("content-security-policy"), path).toBeNull();
      expect(response.headers.get("x-content-type-options"), path).toBe("nosniff");
    }
    for (const path of [
      "/bff/api/v1/certificates/0192f3a4-0000-7000-8000-0000000ce001",
      "/bff/api/v1/certificates/0192f3a4-0000-7000-8000-0000000ce001/print/x",
      "/bff/api/v1/certificates/../print",
      "/bff/api/v1/registers/other",
      "/bff/api/v1/registers/certificates/x",
    ]) {
      expect(proxy(request(path)).headers.get("content-security-policy"), path).toContain(
        "default-src 'self'",
      );
    }
  });
});

describe("proxy with Telugu switched off (ADR-0036, the default)", () => {
  it("redirects every /te URL to the same /en page, keeping the query", () => {
    for (const [path, target] of [
      ["/te", "/en"],
      ["/te/", "/en/"],
      ["/te/students", "/en/students"],
      ["/te/settings/users?page=2", "/en/settings/users?page=2"],
      ["/te/platform/schools/abc", "/en/platform/schools/abc"],
    ] as const) {
      const response = proxy(request(path));
      expect(response.status, path).toBe(307);
      expect(response.headers.get("location"), path).toBe(`https://office.school.example${target}`);
      expect(response.headers.get("content-security-policy"), path).toContain("default-src 'self'");
      expect(response.headers.get("x-content-type-options"), path).toBe("nosniff");
    }
  });

  it("treats an unrecognised or empty switch value as off", () => {
    for (const value of ["", "false", "0", "no", "off", "maybe"]) {
      vi.stubEnv("SOS_TELUGU_ENABLED", value);
      expect(proxy(request("/te/students")).headers.get("location"), value).toMatch(
        /\/en\/students$/,
      );
    }
  });

  it("never selects Telugu from Accept-Language or a stored locale cookie", () => {
    const fromHeader = proxy(request("/", { "accept-language": "te-IN,te;q=0.9" }));
    expect(fromHeader.headers.get("location")).toMatch(/\/en$/);
    const fromCookie = proxy(request("/", { cookie: "NEXT_LOCALE=te" }));
    expect(fromCookie.headers.get("location")).toMatch(/\/en$/);
    const both = proxy(request("/students", { cookie: "NEXT_LOCALE=te", "accept-language": "te" }));
    expect(both.headers.get("location")).toMatch(/\/en\/students$/);
  });

  it("serves /en pages and names no Telugu alternate", () => {
    const response = proxy(request("/en/students", { "accept-language": "te" }));
    expect(response.headers.get("location")).toBeNull();
    expect(response.headers.get("link") ?? "").not.toMatch(/hreflang="te"|\/te(\/|>)/);
  });

  it("does not treat paths that merely start with te as Telugu", () => {
    const response = proxy(request("/en/tests"));
    expect(response.headers.get("location")).toBeNull();
    expect(proxy(request("/teachers")).headers.get("location")).toMatch(/\/en\/teachers$/);
  });
});
