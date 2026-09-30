import { screen, within } from "@testing-library/react";
import type * as Navigation from "next/navigation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { randomBytes } from "node:crypto";
import type { SessionKind } from "@/server/config";
import { setAuthRuntimeForTesting } from "@/server/runtime";
import { sessionCookieName } from "@/server/session/cookies";
import { createHarness, HARNESS_TENANT, type Harness } from "@/test/bff-harness";
import { messages, renderWithIntl } from "@/test/render";

/**
 * Layout auth wiring with the real session store (FR-IAM-001, SEC-004): no session →
 * redirect to the right sign-in; with a session → shell with session controls, and the
 * rendered output (what the RSC payload carries) contains no token or secret.
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
    usePathname: () => requestPath,
    useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
    useSearchParams: () => new URLSearchParams(),
    useParams: () => ({ locale: "en" }),
  };
});

import ChooseSchoolPage from "./[locale]/choose-school/page";
import PlatformLayout from "./[locale]/platform/layout";
import SchoolLayout from "./[locale]/(school)/layout";
import { isSchoolHomePath, requireOperator, requireStaff } from "@/server/session/rsc";

let h: Harness;
const en = Promise.resolve({ locale: "en" });

/** A JWT-shaped token (the layout must never render any of these). */
function fakeJwt(): string {
  const part = (value: object) => Buffer.from(JSON.stringify(value)).toString("base64url");
  return `${part({ alg: "RS256", typ: "JWT" })}.${part({ sub: randomBytes(8).toString("hex") })}.${randomBytes(32).toString("base64url")}`;
}

/**
 * Create a session straight in the store (jsdom cannot run openid-client's WebCrypto
 * calls; the full OIDC flow is covered by src/server/auth/handlers.test.ts).
 */
async function signInDirect(
  kind: SessionKind,
  user: { sub: string; name?: string },
  activeTenantId: string | null = kind === "staff" ? HARNESS_TENANT : null,
) {
  const created = await h.runtime.store.create({
    kind,
    subject: user.sub,
    issuer: h.runtime.config[kind].issuer.href,
    displayName: user.name ?? null,
    authTime: Math.floor(Date.now() / 1000),
    mfa: kind === "operator",
    tokens: {
      accessToken: fakeJwt(),
      refreshToken: `rt-${randomBytes(16).toString("hex")}`,
      idToken: fakeJwt(),
    },
    accessExpiresAt: Date.now() + 600_000,
    activeTenantId,
  });
  cookieJar.set(sessionCookieName(kind, true), created.cookieValue);
  return created;
}

beforeEach(async () => {
  h = await createHarness();
  setAuthRuntimeForTesting(h.runtime);
  cookieJar.clear();
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json({ authenticated: false, kind: "staff" })),
  );
});

afterEach(() => {
  setAuthRuntimeForTesting(null);
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

async function redirectOf(render: () => Promise<unknown>): Promise<string | null> {
  try {
    await render();
    return null;
  } catch (error) {
    if (error instanceof RedirectSignal) return error.url;
    throw error;
  }
}

describe("school layout", () => {
  it("sends visitors without a staff session to staff sign-in, returning here", async () => {
    requestPath = "/en/settings/users?page=2";
    expect(await redirectOf(() => SchoolLayout({ children: "x", params: en }))).toBe(
      "/bff/auth/login?next=%2Fen%2Fsettings%2Fusers%3Fpage%3D2",
    );
  });

  it("does not accept an operator session", async () => {
    await signInDirect("operator", { sub: "op-1" });
    expect(await redirectOf(() => SchoolLayout({ children: "x", params: en }))).toMatch(
      /^\/bff\/auth\/login/,
    );
  });

  it("renders the shell with Lock now, and nothing secret reaches the page", async () => {
    const { cookieValue, session } = await signInDirect("staff", {
      sub: "staff-1",
      name: "Office Clerk",
    });
    const tokens = (await h.runtime.store.tokens(session.id))!.tokens;

    const { container } = renderWithIntl(await SchoolLayout({ children: <p>page</p>, params: en }));
    // Account area at the foot of the one sidebar (docs/17 §5.2); the school's name from
    // GET /me/schools is covered by layouts.node.test.tsx.
    const account = screen.getByRole("region", { name: messages.en.shell.account });
    expect(within(account).getByRole("button", { name: messages.en.auth.lockNow })).toBeVisible();
    expect(within(account).getByText("Signed in as Office Clerk")).toBeInTheDocument();

    const html = container.innerHTML;
    for (const secret of [
      tokens.accessToken,
      tokens.refreshToken ?? "-",
      tokens.idToken ?? "-",
      session.csrfToken,
      cookieValue,
    ]) {
      expect(html).not.toContain(secret);
    }
    expect(html).not.toMatch(/eyJ[A-Za-z0-9_-]{10,}/);
  });
});

describe("school home without a session: public welcome page (FR-IAM-001)", () => {
  it.each([
    ["/en", "/en/welcome"],
    ["/en/", "/en/welcome"],
    ["/te", "/te/welcome"],
    ["/te/", "/te/welcome"],
    ["/", "/en/welcome"],
    ["/te?from=bookmark", "/te/welcome"],
  ])("a signed-out visitor to %s sees %s instead of the sign-in page", async (path, welcome) => {
    requestPath = path;
    expect(await redirectOf(() => SchoolLayout({ children: "x", params: en }))).toBe(welcome);
    expect(await redirectOf(() => requireStaff())).toBe(welcome);
  });

  it.each([
    ["/en/students", "/bff/auth/login?next=%2Fen%2Fstudents"],
    ["/te/ask?q=1", "/bff/auth/login?next=%2Fte%2Fask%3Fq%3D1"],
    ["/en/welcome-back", "/bff/auth/login?next=%2Fen%2Fwelcome-back"],
    ["/english", "/bff/auth/login?next=%2Fenglish"],
    ["/en//", "/bff/auth/login?next=%2Fen%2F%2F"],
  ])("a deep link %s still goes straight to sign-in, returning there", async (path, login) => {
    requestPath = path;
    expect(await redirectOf(() => requireStaff())).toBe(login);
  });

  it("a signed-in staff member on the school home gets the console, not the welcome page", async () => {
    requestPath = "/en";
    await signInDirect("staff", { sub: "staff-1", name: "Office Clerk" });
    const session = await requireStaff();
    expect(session.kind).toBe("staff");
  });

  it("a SchoolOS support session on the school home still opens the console (ADR-0023)", async () => {
    requestPath = "/te";
    await signInDirect("support", { sub: "op-1" });
    const session = await requireStaff();
    expect(session.kind).toBe("support");
  });

  it("the operator panel never sends visitors to the school welcome page", async () => {
    requestPath = "/en";
    expect(await redirectOf(() => requireOperator())).toBe("/bff/auth/platform/login?next=%2Fen");
  });

  it("recognises only the bare locale home as the school home", () => {
    expect(["/", "/en", "/te", "/en/", "/te/", "/en?x=1"].map(isSchoolHomePath)).toEqual([
      "en",
      "en",
      "te",
      "en",
      "te",
      "en",
    ]);
    for (const path of ["/fr", "/en/students", "/en//", "//en", "/te/welcome", "", "en"]) {
      expect(isSchoolHomePath(path), path).toBeNull();
    }
  });
});

describe("school layout: active school and permissions (FR-IAM-013)", () => {
  it("without an active school, sends the user to the school picker, returning here", async () => {
    requestPath = "/en/settings/users";
    await signInDirect("staff", { sub: "staff-1" }, null);
    expect(await redirectOf(() => SchoolLayout({ children: "x", params: en }))).toBe(
      "/en/choose-school?next=%2Fen%2Fsettings%2Fusers",
    );
  });

  it("shows every item when /me cannot be read (the API still checks each call)", async () => {
    await signInDirect("staff", { sub: "staff-1" });
    h.setApi(() => {
      throw new TypeError("fetch failed");
    });
    renderWithIntl(await SchoolLayout({ children: <p>page</p>, params: en }));
    const nav = screen.getByRole("navigation", { name: messages.en.school.nav.label });
    expect(
      within(nav).getByRole("link", { name: messages.en.school.nav.audit }),
    ).toBeInTheDocument();
  });
});

describe("school picker page (FR-IAM-013, ADR-0019)", () => {
  it("needs a staff session", async () => {
    requestPath = "/en/choose-school";
    expect(
      await redirectOf(() => ChooseSchoolPage({ params: en, searchParams: Promise.resolve({}) })),
    ).toBe("/bff/auth/login?next=%2Fen%2Fchoose-school");
  });
});

describe("platform layout", () => {
  it("sends visitors to the operator sign-in", async () => {
    requestPath = "/te/platform/schools";
    expect(await redirectOf(() => PlatformLayout({ children: "x" }))).toBe(
      "/bff/auth/platform/login?next=%2Fte%2Fplatform%2Fschools",
    );
  });

  it("does not accept a staff session", async () => {
    requestPath = "/en/platform";
    await signInDirect("staff", { sub: "staff-1" });
    expect(await redirectOf(() => PlatformLayout({ children: "x" }))).toBe(
      "/bff/auth/platform/login?next=%2Fen%2Fplatform",
    );
  });

  it("renders for an operator", async () => {
    await signInDirect("operator", { sub: "op-1", name: "Ops One" });
    renderWithIntl(await PlatformLayout({ children: <p>panel</p> }));
    expect(screen.getAllByText(messages.en.platform.badge)[0]).toBeVisible();
    // The account area at the foot of the sidebar: who is signed in and "Sign out".
    const account = screen.getByRole("region", { name: messages.en.shell.account });
    expect(within(account).getByText("Signed in as Ops One")).toBeInTheDocument();
    expect(
      within(account).getByRole("button", { name: messages.en.auth.signOut }),
    ).toBeInTheDocument();
  });

  it("is switched off on dedicated hosts", async () => {
    vi.stubEnv("SOS_DEPLOYMENT_MODE", "dedicated");
    expect(await redirectOf(() => PlatformLayout({ children: "x" }))).toBe("not-found");
  });
});
