// @vitest-environment node
import { randomBytes } from "node:crypto";
import type * as Navigation from "next/navigation";
import type { ReactElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { SessionKind } from "@/server/config";
import { setAuthRuntimeForTesting } from "@/server/runtime";
import { sessionCookieName } from "@/server/session/cookies";
import {
  createHarness,
  defaultApi,
  HARNESS_ME,
  HARNESS_TENANT,
  type Harness,
} from "@/test/bff-harness";

/**
 * Layouts and the school picker call the API from the server (RSC) with the user's tokens
 * (FR-IAM-013, SEC-004). Node environment: the service token is signed with WebCrypto.
 * The returned elements are inspected (not rendered): props carry data, never tokens.
 */

const cookieJar = new Map<string, string>();
let requestPath = "/settings/users";

vi.mock("next/headers", () => ({
  cookies: async () => ({
    get: (name: string) => (cookieJar.has(name) ? { name, value: cookieJar.get(name) } : undefined),
  }),
  headers: async () => new Headers({ "x-sos-path": requestPath }),
}));

class RedirectSignal extends Error {
  constructor(readonly url: string) {
    super(`redirect ${url}`);
  }
}

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    redirect: (url: string) => {
      throw new RedirectSignal(url);
    },
    notFound: () => {
      throw new RedirectSignal("not-found");
    },
  };
});

import ChooseSchoolPage from "./[locale]/choose-school/page";
import PlatformLayout from "./[locale]/platform/layout";
import SchoolLayout from "./[locale]/(school)/layout";

const OTHER = "0192f3a4-0000-7000-8000-000000000002";
const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

let h: Harness;

async function signInDirect(kind: SessionKind, activeTenantId: string | null) {
  const created = await h.runtime.store.create({
    kind,
    subject: `${kind}-${randomBytes(4).toString("hex")}`,
    issuer: h.runtime.config[kind].issuer.href,
    displayName: "Synthetic User",
    authTime: Math.floor(Date.now() / 1000),
    mfa: true,
    tokens: { accessToken: "access-token-synthetic", refreshToken: null, idToken: null },
    accessExpiresAt: Date.now() + 600_000,
    activeTenantId,
  });
  cookieJar.set(sessionCookieName(kind, h.runtime.config.secureCookies), created.cookieValue);
}

async function redirectOf(render: () => Promise<unknown>): Promise<string | null> {
  try {
    await render();
    return null;
  } catch (error) {
    if (error instanceof RedirectSignal) return error.url;
    throw error;
  }
}

beforeEach(async () => {
  h = await createHarness();
  setAuthRuntimeForTesting(h.runtime);
  cookieJar.clear();
  vi.stubEnv("APP_BASE_URL", h.runtime.config.appBaseUrl.origin);
});

afterEach(() => {
  setAuthRuntimeForTesting(null);
  vi.unstubAllEnvs();
});

type ShellProps = { permissions: readonly string[] | null; canSwitchSchool: boolean };

describe("school layout reads /me from the server (FR-IAM-013)", () => {
  it("passes effective permissions to the shell and sends the active school", async () => {
    await signInDirect("staff", HARNESS_TENANT);
    const element = (await SchoolLayout({ children: "x" })) as ReactElement<ShellProps>;
    expect(element.props.permissions).toEqual(HARNESS_ME.permissions);
    expect(element.props.canSwitchSchool).toBe(false);
    const call = h.apiCalls.find((r) => new URL(r.url).pathname === "/api/v1/me");
    expect(call?.headers.get("x-active-tenant")).toBe(HARNESS_TENANT);
    expect(call?.headers.get("authorization")).toBe("Bearer access-token-synthetic");
    expect(call?.headers.get("x-service-token")).toMatch(/^ey/);
    expect(JSON.stringify(element.props)).not.toContain("access-token-synthetic");
  });

  it("names the active school in the sidebar and passes who is signed in (docs/17 §5.2)", async () => {
    await signInDirect("staff", HARNESS_TENANT);
    type Props = ShellProps & {
      schoolName: string | null;
      account: { kind: string; displayName: string | null; roles: readonly string[] | null };
    };
    const element = (await SchoolLayout({ children: "x" })) as ReactElement<Props>;
    expect(element.props.schoolName).toBe("Sample School");
    expect(element.props.account).toEqual({
      kind: "staff",
      displayName: HARNESS_ME.display_name,
      roles: HARNESS_ME.roles,
    });
    const call = h.apiCalls.find((r) => new URL(r.url).pathname === "/api/v1/me/schools");
    expect(call?.headers.get("authorization")).toBe("Bearer access-token-synthetic");

    // The list cannot be read: no name, the console still works.
    h.setApi((request) =>
      new URL(request.url).pathname === "/api/v1/me/schools"
        ? json({ status: 503, code: "unavailable" }, 503)
        : defaultApi(request),
    );
    const without = (await SchoolLayout({ children: "x" })) as ReactElement<Props>;
    expect(without.props.schoolName).toBeNull();
    expect(without.props.permissions).toEqual(HARNESS_ME.permissions);
  });

  it("offers 'Switch school' to people in several schools", async () => {
    await signInDirect("staff", HARNESS_TENANT);
    h.setApi((request) =>
      new URL(request.url).pathname === "/api/v1/me"
        ? json({ ...HARNESS_ME, tenant_ids: [HARNESS_TENANT, OTHER] })
        : defaultApi(request),
    );
    const element = (await SchoolLayout({ children: "x" })) as ReactElement<ShellProps>;
    expect(element.props.canSwitchSchool).toBe(true);
  });

  it("shows the Tally menu only while the school's connector answers (M6, ADR-0032)", async () => {
    await signInDirect("staff", HARNESS_TENANT);
    const tallyUser = { ...HARNESS_ME, permissions: ["finance.read"] };
    let connector = 404;
    h.setApi((request) => {
      const path = new URL(request.url).pathname;
      if (path === "/api/v1/me") return json(tallyUser);
      if (path === "/api/v1/tally/status") {
        return connector === 200
          ? json({ devices_active: 1, silent: false })
          : json({ status: 404, code: "not_found" }, 404);
      }
      return defaultApi(request);
    });
    type Features = ShellProps & { features: { tally?: boolean } };
    const off = (await SchoolLayout({ children: "x" })) as ReactElement<Features>;
    expect(off.props.features).toEqual({ tally: false });
    connector = 200;
    const on = (await SchoolLayout({ children: "x" })) as ReactElement<Features>;
    expect(on.props.features).toEqual({ tally: true });
  });

  it("does not ask for the Tally status for people without a Tally permission", async () => {
    await signInDirect("staff", HARNESS_TENANT);
    const element = (await SchoolLayout({ children: "x" })) as ReactElement<
      ShellProps & { features: { tally?: boolean } }
    >;
    expect(element.props.features).toEqual({ tally: false });
    expect(h.apiCalls.some((r) => new URL(r.url).pathname === "/api/v1/tally/status")).toBe(false);
  });

  it("goes back to the picker when the API says the school must be chosen again", async () => {
    requestPath = "/audit?page=2";
    await signInDirect("staff", HARNESS_TENANT);
    h.setApi((request) =>
      new URL(request.url).pathname === "/api/v1/me"
        ? json({ status: 409, code: "active_tenant_required" }, 409)
        : defaultApi(request),
    );
    expect(await redirectOf(() => SchoolLayout({ children: "x" }))).toBe(
      "/choose-school?next=%2Faudit%3Fpage%3D2",
    );
  });

  it("the picker address carries no locale with Telugu on or off (ADR-0036 note)", async () => {
    for (const telugu of ["true", "false"]) {
      vi.stubEnv("SOS_TELUGU_ENABLED", telugu);
      requestPath = "/audit";
      await signInDirect("staff", HARNESS_TENANT);
      h.setApi((request) =>
        new URL(request.url).pathname === "/api/v1/me"
          ? json({ status: 409, code: "active_tenant_required" }, 409)
          : defaultApi(request),
      );
      expect(await redirectOf(() => SchoolLayout({ children: "x" })), telugu).toBe(
        "/choose-school?next=%2Faudit",
      );
    }
  });
});

describe("platform layout reads /platform/me", () => {
  it("passes the operator's permissions to the shell", async () => {
    await signInDirect("operator", null);
    h.setApi((request) =>
      new URL(request.url).pathname === "/api/v1/platform/me"
        ? json({
            operator_id: "0192f3a4-0000-7000-8000-0000000000f1",
            roles: ["billing_admin"],
            permissions: ["platform.invoices.read"],
            step_up_fresh: false,
          })
        : defaultApi(request),
    );
    const element = (await PlatformLayout({ children: "x" })) as ReactElement<
      ShellProps & { account: { displayName: string | null; roles: readonly string[] | null } }
    >;
    expect(element.props.permissions).toEqual(["platform.invoices.read"]);
    expect(element.props.account).toEqual({
      displayName: "Synthetic User",
      roles: ["billing_admin"],
    });
    const call = h.apiCalls.find((r) => new URL(r.url).pathname === "/api/v1/platform/me");
    expect(call?.headers.has("x-active-tenant")).toBe(false);
  });
});

describe("school picker page (ADR-0019)", () => {
  type PickerProps = { schools: Array<{ tenant_id: string; status: string }>; next: string };
  type Shell = ReactElement<{ children: ReactElement<PickerProps> }>;

  it("lists the user's schools (no X-Active-Tenant) and keeps a safe next", async () => {
    await signInDirect("staff", null);
    h.setApi((request) =>
      new URL(request.url).pathname === "/api/v1/me/schools"
        ? json({
            data: [
              { tenant_id: HARNESS_TENANT, name: "A", code: "a", status: "active" },
              { tenant_id: OTHER, name: "B", code: "b", status: "suspended" },
            ],
          })
        : defaultApi(request),
    );
    const element = (await ChooseSchoolPage({
      searchParams: Promise.resolve({ next: "https://evil.example/phish" }),
    })) as Shell;
    expect(element.props.children.props.schools.map((s) => s.status)).toEqual([
      "active",
      "suspended",
    ]);
    expect(element.props.children.props.next).toBe("/");
    const call = h.apiCalls.find((r) => new URL(r.url).pathname === "/api/v1/me/schools");
    expect(call?.headers.has("x-active-tenant")).toBe(false);
  });

  it("keeps a same-origin next and sends people with no school to 'no access yet'", async () => {
    // Telugu switched on explicitly (ADR-0036): the language is in the cookie, not the URL.
    vi.stubEnv("SOS_TELUGU_ENABLED", "true");
    await signInDirect("staff", null);
    const element = (await ChooseSchoolPage({
      searchParams: Promise.resolve({ next: "/settings/users" }),
    })) as Shell;
    expect(element.props.children.props.next).toBe("/settings/users");
    // An old prefixed return address loses its prefix; the picker itself is never "next".
    const legacy = (await ChooseSchoolPage({
      searchParams: Promise.resolve({ next: "/te/settings/users" }),
    })) as Shell;
    expect(legacy.props.children.props.next).toBe("/settings/users");
    const loop = (await ChooseSchoolPage({
      searchParams: Promise.resolve({ next: "/choose-school?next=%2F" }),
    })) as Shell;
    expect(loop.props.children.props.next).toBe("/");

    h.setApi((request) =>
      new URL(request.url).pathname === "/api/v1/me/schools"
        ? json({ data: [] })
        : defaultApi(request),
    );
    expect(await redirectOf(() => ChooseSchoolPage({ searchParams: Promise.resolve({}) }))).toBe(
      "/no-access",
    );
  });

  it("with Telugu switched off, 'no access yet' is the same prefix-less page (ADR-0036)", async () => {
    await signInDirect("staff", null);
    h.setApi((request) =>
      new URL(request.url).pathname === "/api/v1/me/schools"
        ? json({ data: [] })
        : defaultApi(request),
    );
    expect(await redirectOf(() => ChooseSchoolPage({ searchParams: Promise.resolve({}) }))).toBe(
      "/no-access",
    );
  });
});
