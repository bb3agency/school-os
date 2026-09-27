import { describe, expect, it } from "vitest";
import {
  buildContentSecurityPolicy,
  generateNonce,
  isHttpsDeployment,
  securityHeaders,
} from "./security-headers";

function directives(csp: string): Map<string, string[]> {
  return new Map(
    csp
      .split(";")
      .map((part) => part.trim())
      .filter(Boolean)
      .map((part) => {
        const [name, ...values] = part.split(/\s+/);
        return [name ?? "", values] as const;
      }),
  );
}

const prod = (overrides: Partial<Parameters<typeof buildContentSecurityPolicy>[0]> = {}) =>
  buildContentSecurityPolicy({
    nonce: "abc123==",
    isDev: false,
    upgradeInsecureRequests: true,
    ...overrides,
  });

describe("SEC-010 content security policy (docs/07 §11)", () => {
  it("uses the per-request nonce with strict-dynamic and no unsafe-inline for scripts", () => {
    const csp = directives(prod());
    expect(csp.get("script-src")).toEqual(["'self'", "'nonce-abc123=='", "'strict-dynamic'"]);
    expect(csp.get("script-src")).not.toContain("'unsafe-inline'");
    expect(csp.get("script-src")).not.toContain("'unsafe-eval'");
  });

  it("matches the documented directives in production", () => {
    const csp = directives(prod());
    expect(csp.get("default-src")).toEqual(["'self'"]);
    expect(csp.get("style-src")).toEqual(["'self'"]);
    expect(csp.get("img-src")).toEqual(["'self'", "data:", "blob:"]);
    expect(csp.get("connect-src")).toEqual(["'self'"]);
    expect(csp.get("font-src")).toEqual(["'self'"]);
    expect(csp.get("object-src")).toEqual(["'none'"]);
    expect(csp.get("base-uri")).toEqual(["'none'"]);
    expect(csp.get("frame-ancestors")).toEqual(["'none'"]);
    expect(csp.get("form-action")).toEqual(["'self'"]);
    expect(csp.has("upgrade-insecure-requests")).toBe(true);
  });

  it("allows the files origin for images only when it is an https origin", () => {
    expect(
      directives(prod({ filesOrigin: "https://files.schoolos.example/some/path" })).get("img-src"),
    ).toContain("https://files.schoolos.example");
    expect(directives(prod({ filesOrigin: "http://files.example" })).get("img-src")).toEqual([
      "'self'",
      "data:",
      "blob:",
    ]);
    expect(prod({ filesOrigin: "https://x.example; script-src *" })).not.toContain("script-src *");
  });

  it("never adds the files origin to connect-src (docs/07 §11: connect-src 'self')", () => {
    const csp = directives(prod({ filesOrigin: "https://files.schoolos.example/bucket" }));
    expect(csp.get("connect-src")).toEqual(["'self'"]);
  });

  it("relaxes only what next dev needs, and never adds unsafe-inline to scripts", () => {
    const csp = directives(prod({ isDev: true }));
    expect(csp.get("script-src")).toContain("'unsafe-eval'");
    expect(csp.get("script-src")).not.toContain("'unsafe-inline'");
  });

  it("omits upgrade-insecure-requests for plain-http local runs", () => {
    expect(
      directives(prod({ upgradeInsecureRequests: false })).has("upgrade-insecure-requests"),
    ).toBe(false);
  });
});

describe("SEC-010 security headers", () => {
  it("sets every documented header, with HSTS on https", () => {
    const headers = securityHeaders({ csp: prod(), hsts: true });
    expect(headers).toMatchObject({
      "Strict-Transport-Security": "max-age=63072000; includeSubDomains; preload",
      "X-Content-Type-Options": "nosniff",
      "Referrer-Policy": "strict-origin-when-cross-origin",
      "Permissions-Policy": "camera=(self), microphone=(), geolocation=(), payment=()",
      "Cross-Origin-Opener-Policy": "same-origin",
      "Cross-Origin-Resource-Policy": "same-origin",
    });
    expect(headers["Content-Security-Policy"]).toContain("frame-ancestors 'none'");
  });

  it("does not send HSTS over plain http", () => {
    expect(securityHeaders({ csp: prod(), hsts: false })).not.toHaveProperty(
      "Strict-Transport-Security",
    );
  });

  it("treats a missing APP_BASE_URL as https (secure by default)", () => {
    expect(isHttpsDeployment(undefined)).toBe(true);
    expect(isHttpsDeployment("https://office.school.edu.in")).toBe(true);
    expect(isHttpsDeployment("http://localhost:3000")).toBe(false);
  });
});

describe("generateNonce", () => {
  it("returns 128-bit base64 values that differ per call", () => {
    const nonces = new Set(Array.from({ length: 50 }, () => generateNonce()));
    expect(nonces.size).toBe(50);
    for (const nonce of nonces) {
      expect(nonce).toMatch(/^[A-Za-z0-9+/]{22}==$/);
    }
  });
});
