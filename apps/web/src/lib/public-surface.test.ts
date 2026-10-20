// @vitest-environment node
import { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { proxy } from "@/proxy";
import {
  buildPublicCsp,
  parentCookieName,
  publicOrigins,
  publicSecurityHeaders,
  routeSurface,
} from "./public-surface";

/**
 * The parent pages on their own origin (ADR-0046; FR-PUB-001, FR-PUB-010; SEC-036): which host
 * serves what, and the public pages' headers through the real proxy.
 */

const STAFF = "office.school.example";
const PUBLIC = "my.school.example";
const TOKEN = "Abcdefghijklmnopqrstuvwxyz0123456789_-ABCDE";
const ENV = { PUBLIC_BASE_URL: `https://${PUBLIC}`, APP_BASE_URL: `https://${STAFF}` };

function on(host: string, path: string) {
  return new NextRequest(new URL(path, `https://${host}`), { headers: { host } });
}

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("public origin (FR-PUB-001)", () => {
  it("accepts an https origin or a loopback http origin, nothing else", () => {
    expect(publicOrigins(ENV)).toEqual({
      publicHost: PUBLIC,
      appHost: STAFF,
      shared: false,
      secure: true,
    });
    for (const bad of [
      "http://my.school.example",
      "https://my.school.example/p",
      "https://my.school.example?x=1",
      "ftp://my.school.example",
      "not a url",
      "",
    ]) {
      expect(publicOrigins({ ...ENV, PUBLIC_BASE_URL: bad }), bad).toBeNull();
    }
    // One origin for both surfaces: only a local http run.
    expect(
      publicOrigins({ PUBLIC_BASE_URL: `https://${STAFF}`, APP_BASE_URL: `https://${STAFF}` }),
    ).toBeNull();
    expect(
      publicOrigins({
        PUBLIC_BASE_URL: "http://localhost:3000",
        APP_BASE_URL: "http://localhost:3000",
      }),
    ).toMatchObject({ shared: true, secure: false });
  });

  it("serves parent pages only on the public host and staff pages only on the staff host", () => {
    expect(routeSurface(PUBLIC, `/p/${TOKEN}`, ENV)).toEqual({ kind: "public", served: true });
    expect(routeSurface(PUBLIC, "/p/view", ENV)).toEqual({ kind: "public", served: true });
    expect(routeSurface(PUBLIC, "/p/a/b/c", ENV)).toEqual({ kind: "public", served: false });
    expect(routeSurface(PUBLIC, "/students", ENV)).toEqual({ kind: "not_found" });
    expect(routeSurface(PUBLIC, "/bff/api/v1/me", ENV)).toEqual({ kind: "not_found" });
    expect(routeSurface(STAFF, `/p/${TOKEN}`, ENV)).toEqual({ kind: "not_found" });
    expect(routeSurface(STAFF, "/students", ENV)).toEqual({ kind: "staff" });
    // Public pages switched off (no PUBLIC_BASE_URL): /p is nowhere.
    expect(routeSurface(STAFF, "/p/view", { APP_BASE_URL: ENV.APP_BASE_URL })).toEqual({
      kind: "not_found",
    });
  });

  it("names the cookie __Host- on https only", () => {
    expect(parentCookieName(true)).toBe("__Host-sos_parent");
    expect(parentCookieName(false)).toBe("sos_parent");
  });

  it("has a strict CSP without third-party origins and the no-leak headers", () => {
    const csp = buildPublicCsp({ nonce: "bm9uY2U=", isDev: false, https: true });
    expect(csp).toContain("default-src 'self'");
    expect(csp).toContain("frame-ancestors 'none'");
    expect(csp).toContain("object-src 'none'");
    // The only third-party origins: Razorpay's hosted pages as form targets (R2-3H, FR-PUB-010).
    const directives = new Map(
      csp.split("; ").map((d) => [d.split(" ")[0], d.split(" ").slice(1)] as const),
    );
    expect(directives.get("form-action")).toEqual([
      "'self'",
      "https://rzp.io",
      "https://*.rzp.io",
      "https://razorpay.com",
      "https://*.razorpay.com",
    ]);
    const others = csp
      .split("; ")
      .filter((d) => !d.startsWith("form-action"))
      .join("; ");
    expect(others).not.toMatch(/https?:\/\//);
    expect(csp).not.toContain("unsafe");
    expect(csp).not.toContain("example.test");
    // The verification pages take no third-party form target at all.
    const verify = buildPublicCsp({ nonce: "bm9uY2U=", isDev: false, https: true, pay: false });
    expect(verify).not.toMatch(/https?:\/\//);
    const headers = publicSecurityHeaders(csp, true);
    expect(headers["Referrer-Policy"]).toBe("no-referrer");
    expect(headers["Cache-Control"]).toBe("private, no-store");
    expect(headers["X-Robots-Tag"]).toBe("noindex, nofollow");
  });
});

describe("proxy on the public host (SEC-036)", () => {
  it("gives parent pages their own headers and no locale rewrite", () => {
    vi.stubEnv("PUBLIC_BASE_URL", ENV.PUBLIC_BASE_URL);
    vi.stubEnv("APP_BASE_URL", ENV.APP_BASE_URL);
    for (const path of [`/p/${TOKEN}`, "/p/view", "/p/expired"]) {
      const response = proxy(on(PUBLIC, path));
      expect(response.status, path).toBe(200);
      expect(response.headers.get("x-middleware-rewrite"), path).toBeNull();
      expect(response.headers.get("referrer-policy"), path).toBe("no-referrer");
      expect(response.headers.get("cache-control"), path).toBe("private, no-store");
      expect(response.headers.get("x-robots-tag"), path).toBe("noindex, nofollow");
      expect(response.headers.get("content-security-policy"), path).toContain(
        "frame-ancestors 'none'",
      );
    }
    const odd = proxy(on(PUBLIC, "/p/a/b"));
    expect(new URL(odd.headers.get("x-middleware-rewrite") ?? "").pathname).toBe("/p/expired");
  });

  it("answers 404 for staff pages on the public host and parent pages on the staff host", () => {
    vi.stubEnv("PUBLIC_BASE_URL", ENV.PUBLIC_BASE_URL);
    vi.stubEnv("APP_BASE_URL", ENV.APP_BASE_URL);
    for (const [host, path] of [
      [PUBLIC, "/"],
      [PUBLIC, "/students"],
      [PUBLIC, "/bff/auth/login"],
      [STAFF, `/p/${TOKEN}`],
      [STAFF, "/p/view"],
    ] as const) {
      const response = proxy(on(host, path));
      expect(response.status, `${host}${path}`).toBe(404);
      expect(response.headers.get("x-middleware-rewrite")).toBeNull();
    }
    // The staff app itself still works on its own host.
    expect(proxy(on(STAFF, "/students")).status).toBe(200);
  });

  it("shares one loopback origin locally", () => {
    vi.stubEnv("PUBLIC_BASE_URL", "http://localhost:3000");
    vi.stubEnv("APP_BASE_URL", "http://localhost:3000");
    const parent = proxy(on("localhost:3000", "/p/view"));
    expect(parent.headers.get("referrer-policy")).toBe("no-referrer");
    const staff = proxy(on("localhost:3000", "/students"));
    expect(new URL(staff.headers.get("x-middleware-rewrite") ?? "").pathname).toBe("/en/students");
  });
});
