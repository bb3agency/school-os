import { screen } from "@testing-library/react";
import type * as Navigation from "next/navigation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { randomBytes } from "node:crypto";
import type { SessionKind } from "@/server/config";
import { setAuthRuntimeForTesting } from "@/server/runtime";
import { sessionCookieName } from "@/server/session/cookies";
import { createHarness, type Harness } from "@/test/bff-harness";
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

import PlatformLayout from "./[locale]/platform/layout";
import SchoolLayout from "./[locale]/(school)/layout";

let h: Harness;

/** A JWT-shaped token (the layout must never render any of these). */
function fakeJwt(): string {
  const part = (value: object) => Buffer.from(JSON.stringify(value)).toString("base64url");
  return `${part({ alg: "RS256", typ: "JWT" })}.${part({ sub: randomBytes(8).toString("hex") })}.${randomBytes(32).toString("base64url")}`;
}

/**
 * Create a session straight in the store (jsdom cannot run openid-client's WebCrypto
 * calls; the full OIDC flow is covered by src/server/auth/handlers.test.ts).
 */
async function signInDirect(kind: SessionKind, user: { sub: string; name?: string }) {
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
    expect(await redirectOf(() => SchoolLayout({ children: "x" }))).toBe(
      "/bff/auth/login?next=%2Fen%2Fsettings%2Fusers%3Fpage%3D2",
    );
  });

  it("does not accept an operator session", async () => {
    await signInDirect("operator", { sub: "op-1" });
    expect(await redirectOf(() => SchoolLayout({ children: "x" }))).toMatch(/^\/bff\/auth\/login/);
  });

  it("renders the shell with Lock now, and nothing secret reaches the page", async () => {
    const { cookieValue, session } = await signInDirect("staff", {
      sub: "staff-1",
      name: "Office Clerk",
    });
    const tokens = (await h.runtime.store.tokens(session.id))!.tokens;

    const { container } = renderWithIntl(await SchoolLayout({ children: <p>page</p> }));
    expect(screen.getByRole("button", { name: messages.en.auth.lockNow })).toBeVisible();
    expect(screen.getByText("Signed in as Office Clerk")).toBeInTheDocument();

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
    expect(screen.getByText(messages.en.platform.badge)).toBeVisible();
    expect(screen.getByRole("button", { name: messages.en.auth.signOut })).toBeInTheDocument();
  });

  it("is switched off on dedicated hosts", async () => {
    vi.stubEnv("SOS_DEPLOYMENT_MODE", "dedicated");
    expect(await redirectOf(() => PlatformLayout({ children: "x" }))).toBe("not-found");
  });
});
