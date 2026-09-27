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
let requestPath = "/en/settings/users";

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
const en = Promise.resolve({ locale: "en" });
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
    const element = (await SchoolLayout({ children: "x", params: en })) as ReactElement<ShellProps>;
    expect(element.props.permissions).toEqual(HARNESS_ME.permissions);
    expect(element.props.canSwitchSchool).toBe(false);
    const call = h.apiCalls.find((r) => new URL(r.url).pathname === "/api/v1/me");
    expect(call?.headers.get("x-active-tenant")).toBe(HARNESS_TENANT);
    expect(call?.headers.get("authorization")).toBe("Bearer access-token-synthetic");
    expect(call?.headers.get("x-service-token")).toMatch(/^ey/);
    expect(JSON.stringify(element.props)).not.toContain("access-token-synthetic");
  });

  it("offers 'Switch school' to people in several schools", async () => {
    await signInDirect("staff", HARNESS_TENANT);
    h.setApi((request) =>
      new URL(request.url).pathname === "/api/v1/me"
        ? json({ ...HARNESS_ME, tenant_ids: [HARNESS_TENANT, OTHER] })
        : defaultApi(request),
    );
    const element = (await SchoolLayout({ children: "x", params: en })) as ReactElement<ShellProps>;
    expect(element.props.canSwitchSchool).toBe(true);
  });

  it("goes back to the picker when the API says the school must be chosen again", async () => {
    requestPath = "/te/audit";
    await signInDirect("staff", HARNESS_TENANT);
    h.setApi((request) =>
      new URL(request.url).pathname === "/api/v1/me"
        ? json({ status: 409, code: "active_tenant_required" }, 409)
        : defaultApi(request),
    );
    expect(
      await redirectOf(() =>
        SchoolLayout({ children: "x", params: Promise.resolve({ locale: "te" }) }),
      ),
    ).toBe("/te/choose-school?next=%2Fte%2Faudit");
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
    const element = (await PlatformLayout({ children: "x" })) as ReactElement<ShellProps>;
    expect(element.props.permissions).toEqual(["platform.invoices.read"]);
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
      params: en,
      searchParams: Promise.resolve({ next: "https://evil.example/phish" }),
    })) as Shell;
    expect(element.props.children.props.schools.map((s) => s.status)).toEqual([
      "active",
      "suspended",
    ]);
    expect(element.props.children.props.next).toBe("/en");
    const call = h.apiCalls.find((r) => new URL(r.url).pathname === "/api/v1/me/schools");
    expect(call?.headers.has("x-active-tenant")).toBe(false);
  });

  it("keeps a same-origin next and sends people with no school to 'no access yet'", async () => {
    await signInDirect("staff", null);
    const element = (await ChooseSchoolPage({
      params: en,
      searchParams: Promise.resolve({ next: "/en/settings/users" }),
    })) as Shell;
    expect(element.props.children.props.next).toBe("/en/settings/users");

    h.setApi((request) =>
      new URL(request.url).pathname === "/api/v1/me/schools"
        ? json({ data: [] })
        : defaultApi(request),
    );
    expect(
      await redirectOf(() =>
        ChooseSchoolPage({
          params: Promise.resolve({ locale: "te" }),
          searchParams: Promise.resolve({}),
        }),
      ),
    ).toBe("/te/no-access");
  });
});
