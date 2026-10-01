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

/** The internal page a prefix-less URL is rendered from (next-intl rewrite), path + query. */
function rewrittenTo(response: Response): string | null {
  const target = response.headers.get("x-middleware-rewrite");
  if (!target) return null;
  const url = new URL(target);
  return `${url.pathname}${url.search}`;
}

/** The NEXT_LOCALE value a response sets, or null when it sets none. */
function localeCookieSet(response: Response): string | null {
  const cookie = response.headers.get("set-cookie") ?? "";
  return /(?:^|,\s*)NEXT_LOCALE=([^;]*)/.exec(cookie)?.[1] ?? null;
}

describe("proxy (SEC-010, NFR-I18N-001)", () => {
  it("serves / without a locale in the URL and still sets security headers", () => {
    const response = proxy(request("/"));
    expect(response.status).toBe(200);
    expect(response.headers.get("location")).toBeNull();
    expect(rewrittenTo(response)).toBe("/en");
    expect(response.headers.get("content-security-policy")).toContain("'strict-dynamic'");
    expect(response.headers.get("x-content-type-options")).toBe("nosniff");
  });

  it("serves every page without a prefix, query kept, and names no alternate by path", () => {
    for (const path of [
      "/students",
      "/ask",
      "/ask/c/0192f3a4-0000-7000-8000-00000000c001",
      "/platform/schools",
      "/dev/sign-in",
      "/welcome",
      "/features",
      "/signed-out?kind=operator&error=signin_failed",
      "/settings/users?page=2",
    ]) {
      const response = proxy(request(path));
      expect(response.status, path).toBe(200);
      expect(response.headers.get("location"), path).toBeNull();
      expect(rewrittenTo(response), path).toBe(`/en${path}`);
      expect(response.headers.get("link"), path).toBeNull();
      expect(localeCookieSet(response), path).toBeNull();
    }
  });

  it("negotiates Telugu from Accept-Language when Telugu is switched on (ADR-0036)", () => {
    vi.stubEnv("SOS_TELUGU_ENABLED", "true");
    const response = proxy(request("/", { "accept-language": "te-IN,te;q=0.9,en;q=0.5" }));
    expect(response.headers.get("location")).toBeNull();
    expect(rewrittenTo(response)).toBe("/te");
    const students = proxy(request("/students", { "accept-language": "te" }));
    expect(rewrittenTo(students)).toBe("/te/students");
    expect(students.headers.get("link")).toBeNull();
  });

  it("takes the language from the NEXT_LOCALE cookie first when Telugu is on (ADR-0036)", () => {
    vi.stubEnv("SOS_TELUGU_ENABLED", "true");
    const telugu = proxy(
      request("/students", { cookie: "NEXT_LOCALE=te", "accept-language": "en" }),
    );
    expect(rewrittenTo(telugu)).toBe("/te/students");
    const english = proxy(
      request("/students", { cookie: "NEXT_LOCALE=en", "accept-language": "te" }),
    );
    expect(rewrittenTo(english)).toBe("/en/students");
    // An unknown cookie value falls back to Accept-Language, then English.
    expect(rewrittenTo(proxy(request("/students", { cookie: "NEXT_LOCALE=fr" })))).toBe(
      "/en/students",
    );
  });

  it("redirects old /en URLs with 308 to the same path without the prefix, query kept", () => {
    for (const [path, target] of [
      ["/en", "/"],
      ["/en/", "/"],
      ["/en/students", "/students"],
      ["/en/settings/users?page=2", "/settings/users?page=2"],
      [
        "/en/ask/c/0192f3a4-0000-7000-8000-00000000c001",
        "/ask/c/0192f3a4-0000-7000-8000-00000000c001",
      ],
      ["/en/platform/schools/abc?tab=billing", "/platform/schools/abc?tab=billing"],
      ["/EN/students", "/students"],
    ] as const) {
      const response = proxy(request(path));
      expect(response.status, path).toBe(308);
      expect(response.headers.get("location"), path).toBe(`https://office.school.example${target}`);
      expect(response.headers.get("content-security-policy"), path).toContain("default-src 'self'");
      expect(response.headers.get("x-content-type-options"), path).toBe("nosniff");
      expect(response.headers.get("strict-transport-security"), path).toContain("max-age=");
      expect(localeCookieSet(response), path).toBeNull();
    }
  });

  it("with Telugu on, /te/x stores Telugu in the cookie and redirects with 308 to /x (ADR-0036)", () => {
    vi.stubEnv("SOS_TELUGU_ENABLED", "true");
    for (const [path, target] of [
      ["/te", "/"],
      ["/te/students?page=2", "/students?page=2"],
      ["/te/platform/schools", "/platform/schools"],
    ] as const) {
      const response = proxy(request(path));
      expect(response.status, path).toBe(308);
      expect(response.headers.get("location"), path).toBe(`https://office.school.example${target}`);
      expect(localeCookieSet(response), path).toBe("te");
      const cookie = response.headers.get("set-cookie") ?? "";
      expect(cookie, path).toMatch(/Path=\//i);
      expect(cookie, path).toMatch(/SameSite=lax/i);
      expect(cookie, path).toMatch(/Max-Age=31536000/i);
      expect(response.headers.get("content-security-policy"), path).toContain("default-src 'self'");
    }
    // An old /en link names English: stored too, so the page it lands on is English.
    const english = proxy(request("/en/students", { cookie: "NEXT_LOCALE=te" }));
    expect(english.status).toBe(308);
    expect(localeCookieSet(english)).toBe("en");
  });

  it("never turns an old prefix into an open redirect", () => {
    for (const path of [
      "/en//evil.example",
      "/en///evil.example/x",
      "/te//evil.example",
      "/en/%2F%2Fevil.example",
      "/en/\\evil.example",
      "/en/%5Cevil.example",
    ]) {
      const response = proxy(request(path));
      const location = response.headers.get("location");
      if (location === null) continue; // not a redirect at all: nothing to follow
      const target = new URL(location);
      expect(target.origin, path).toBe("https://office.school.example");
      expect(target.pathname.startsWith("//"), path).toBe(false);
      // Resolved as a browser would (relative to the page), it stays on this site.
      expect(new URL(location, "https://office.school.example/x").host, path).toBe(
        "office.school.example",
      );
    }
    expect(proxy(request("/en//evil.example")).headers.get("location")).toBe(
      "https://office.school.example/evil.example",
    );
  });

  it("forwards a fresh nonce to rendering via the request CSP header", () => {
    const first = proxy(request("/platform"));
    const second = proxy(request("/platform"));
    const responseCsp = first.headers.get("content-security-policy");
    const nonce = nonceOf(responseCsp);
    expect(nonce).toBeDefined();
    expect(nonceOf(second.headers.get("content-security-policy"))).not.toBe(nonce);
    // Next.js request-header override mechanism (what NextResponse.next({ request }) sets).
    expect(first.headers.get("x-middleware-request-content-security-policy")).toBe(responseCsp);
    expect(first.headers.get("x-middleware-request-x-nonce")).toBe(nonce);
  });

  it("sets HSTS by default and every documented header", () => {
    const headers = proxy(request("/")).headers;
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
    const headers = proxy(request("/")).headers;
    expect(headers.get("strict-transport-security")).toBeNull();
    expect(headers.get("content-security-policy")).not.toContain("upgrade-insecure-requests");
  });

  it("passes the current path to layouts for the sign-in return, ignoring a client value", () => {
    const response = proxy(
      request("/settings/users?page=2", { "x-sos-path": "https://evil.example/" }),
    );
    expect(response.headers.get("x-middleware-request-x-sos-path")).toBe("/settings/users?page=2");
  });

  it("serves the signed-out page where it is, keeping its query", () => {
    const response = proxy(request("/signed-out?kind=operator&error=signin_failed"));
    expect(response.status).toBe(200);
    expect(response.headers.get("location")).toBeNull();
    expect(rewrittenTo(response)).toBe("/en/signed-out?kind=operator&error=signin_failed");
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
  it("redirects every /te URL with 308 to the same path without a prefix, query kept, no cookie", () => {
    for (const [path, target] of [
      ["/te", "/"],
      ["/te/", "/"],
      ["/te/students", "/students"],
      ["/te/settings/users?page=2", "/settings/users?page=2"],
      ["/te/platform/schools/abc", "/platform/schools/abc"],
    ] as const) {
      const response = proxy(request(path, { cookie: "NEXT_LOCALE=te" }));
      expect(response.status, path).toBe(308);
      expect(response.headers.get("location"), path).toBe(`https://office.school.example${target}`);
      expect(response.headers.get("content-security-policy"), path).toContain("default-src 'self'");
      expect(response.headers.get("x-content-type-options"), path).toBe("nosniff");
      expect(localeCookieSet(response), path).toBeNull();
    }
  });

  it("treats an unrecognised or empty switch value as off", () => {
    for (const value of ["", "false", "0", "no", "off", "maybe"]) {
      vi.stubEnv("SOS_TELUGU_ENABLED", value);
      const redirected = proxy(request("/te/students"));
      expect(redirected.headers.get("location"), value).toBe(
        "https://office.school.example/students",
      );
      expect(localeCookieSet(redirected), value).toBeNull();
      expect(rewrittenTo(proxy(request("/students", { cookie: "NEXT_LOCALE=te" }))), value).toBe(
        "/en/students",
      );
    }
  });

  it("never selects Telugu from Accept-Language or a stored locale cookie", () => {
    const fromHeader = proxy(request("/", { "accept-language": "te-IN,te;q=0.9" }));
    expect(fromHeader.headers.get("location")).toBeNull();
    expect(rewrittenTo(fromHeader)).toBe("/en");
    const fromCookie = proxy(request("/", { cookie: "NEXT_LOCALE=te" }));
    expect(rewrittenTo(fromCookie)).toBe("/en");
    const both = proxy(request("/students", { cookie: "NEXT_LOCALE=te", "accept-language": "te" }));
    expect(both.headers.get("location")).toBeNull();
    expect(rewrittenTo(both)).toBe("/en/students");
    // The stored choice is left alone for when Telugu returns (no Set-Cookie while off).
    expect(localeCookieSet(both)).toBeNull();
  });

  it("serves pages in English and names no alternate at all", () => {
    const response = proxy(request("/students", { "accept-language": "te" }));
    expect(response.headers.get("location")).toBeNull();
    expect(response.headers.get("link")).toBeNull();
  });

  it("does not treat paths that merely start with en or te as prefixed", () => {
    for (const path of ["/tests", "/teachers", "/entries", "/students/en", "/students/te"]) {
      const response = proxy(request(path));
      expect(response.headers.get("location"), path).toBeNull();
      expect(rewrittenTo(response), path).toBe(`/en${path}`);
    }
  });
});
