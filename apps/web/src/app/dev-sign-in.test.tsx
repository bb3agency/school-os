import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { afterEach, describe, expect, it, vi } from "vitest";
import { isDevSignInEnabled } from "@/features/dev-sign-in/enabled";
import { intlErrors, renderWithIntl } from "@/test/render";

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => "/en/dev/sign-in",
    useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
    useSearchParams: () => new URLSearchParams(),
    useParams: () => ({ locale: "en" }),
    notFound: () => {
      throw new Error("NEXT_NOT_FOUND");
    },
  };
});

import DevSignInPage, { generateMetadata } from "./[locale]/dev/sign-in/page";
import SignedOutPage from "./[locale]/signed-out/page";

const LOCAL_ISSUERS = [
  "http://localhost:8080/schoolos",
  "http://127.0.0.1:8080/schoolos",
  "http://[::1]:8080/schoolos",
  "http://oidc.localhost:8080/schoolos",
];
const REAL_ISSUER = "https://cognito-idp.ap-south-1.amazonaws.com/ap-south-1_SYNTHETIC";

function devEnv(nodeEnv: string, issuer: string | undefined) {
  vi.stubEnv("NODE_ENV", nodeEnv);
  vi.stubEnv("OIDC_ISSUER", issuer);
}

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("dev sign-in guard (local development only; SEC-004, SEC-005)", () => {
  it.each(LOCAL_ISSUERS)("is enabled under next dev with the local stub issuer %s", (issuer) => {
    expect(isDevSignInEnabled({ NODE_ENV: "development", OIDC_ISSUER: issuer })).toBe(true);
  });

  it.each([
    ["production build, local issuer", "production", "http://localhost:8080/schoolos"],
    ["test run, local issuer", "test", "http://localhost:8080/schoolos"],
    ["next dev, real issuer", "development", REAL_ISSUER],
    ["next dev, look-alike host", "development", "https://localhost.example.com/schoolos"],
    ["next dev, bare .localhost", "development", "http://.localhost/schoolos"],
    ["next dev, not a URL", "development", "localhost"],
    ["next dev, other scheme", "development", "ftp://localhost/schoolos"],
    ["next dev, no issuer", "development", undefined],
    ["no NODE_ENV", undefined, "http://localhost:8080/schoolos"],
  ])("is disabled: %s", (_name, nodeEnv, issuer) => {
    expect(isDevSignInEnabled({ NODE_ENV: nodeEnv, OIDC_ISSUER: issuer })).toBe(false);
  });

  it("returns 404 in a production build even with the local stub issuer", async () => {
    devEnv("production", "http://localhost:8080/schoolos");
    expect(() => DevSignInPage()).toThrow("NEXT_NOT_FOUND");
    await expect(generateMetadata({ params: Promise.resolve({ locale: "en" }) })).resolves.toEqual(
      {},
    );
  });

  it("returns 404 under next dev when the issuer is not the local stub", () => {
    devEnv("development", REAL_ISSUER);
    expect(() => DevSignInPage()).toThrow("NEXT_NOT_FOUND");
  });

  it("lists synth-a and synth-b staff per role, with copy and the normal sign-in link", async () => {
    devEnv("development", "http://oidc.localhost:8080/schoolos");
    const user = userEvent.setup();
    const writeText = vi.fn<(text: string) => Promise<void>>(() => Promise.resolve());
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });

    renderWithIntl(DevSignInPage());
    expect(screen.getByRole("heading", { level: 1, name: "Dev sign-in" })).toBeInTheDocument();
    for (const code of ["synth-a", "synth-b"]) {
      const table = screen.getByRole("table", { name: `Synthetic staff of school ${code}` });
      for (const role of ["owner", "principal", "office_admin", "teacher", "class_teacher"]) {
        expect(within(table).getByText(`synthetic|${code}|${role}|1`)).toBeInTheDocument();
      }
    }
    const signIn = screen.getByRole("link", { name: "Sign in as Owner of synth-a" });
    expect(signIn).toHaveAttribute("href", "/bff/auth/login");

    const ownerRow = screen.getByText("synthetic|synth-a|owner|1").closest("tr");
    expect(ownerRow).not.toBeNull();
    await user.click(within(ownerRow as HTMLElement).getByRole("button", { name: "Copy" }));
    expect(writeText).toHaveBeenCalledWith("synthetic|synth-a|owner|1");
    expect(within(ownerRow as HTMLElement).getByRole("status")).toHaveTextContent("Copied");

    const before = Math.floor(Date.now() / 1000);
    await user.click(screen.getByRole("button", { name: "Copy step-up claims" }));
    const claims = JSON.parse(String(writeText.mock.calls.at(-1)?.[0])) as { auth_time: number };
    expect(Object.keys(claims)).toEqual(["auth_time"]);
    expect(claims.auth_time).toBeGreaterThanOrEqual(before);
    expect(intlErrors).toEqual([]);
  });

  it("renders in Telugu without missing keys", () => {
    devEnv("development", "http://localhost:8080/schoolos");
    renderWithIntl(DevSignInPage(), "te");
    expect(screen.getByRole("heading", { level: 1, name: "డెవ్ సైన్-ఇన్" })).toBeInTheDocument();
    expect(intlErrors).toEqual([]);
  });
});

describe("signed-out page link to dev sign-in", () => {
  const render = async () =>
    renderWithIntl(await SignedOutPage({ searchParams: Promise.resolve({}) }));

  it("is shown only under next dev with the local stub issuer", async () => {
    devEnv("development", "http://localhost:8080/schoolos");
    await render();
    expect(screen.getByRole("link", { name: "Dev sign-in (local only)" })).toHaveAttribute(
      "href",
      "/en/dev/sign-in",
    );
  });

  it.each([
    ["production", "http://localhost:8080/schoolos"],
    ["development", REAL_ISSUER],
  ])("is absent with NODE_ENV=%s and issuer %s", async (nodeEnv, issuer) => {
    devEnv(nodeEnv, issuer);
    await render();
    expect(screen.queryByRole("link", { name: /dev sign-in/i })).toBeNull();
  });
});

describe("dev sign-in is reachable from nowhere else", () => {
  const src = resolve(dirname(fileURLToPath(import.meta.url)), "..");
  const files = (dir: string): string[] =>
    readdirSync(dir).flatMap((name) => {
      const path = join(dir, name);
      return statSync(path).isDirectory() ? files(path) : [path];
    });

  it("only the dev pages and the signed-out page import it", () => {
    const importers = files(src)
      .filter((file) => /\.tsx?$/.test(file) && !/\.test\.tsx?$/.test(file))
      .filter(
        (file) => !relative(src, file).replaceAll("\\", "/").startsWith("features/dev-sign-in/"),
      )
      .filter((file) => readFileSync(file, "utf8").includes("features/dev-sign-in/"))
      .map((file) => relative(src, file).replaceAll("\\", "/"))
      .sort();
    expect(importers).toEqual([
      "app/[locale]/dev/sign-in/page.tsx",
      // The UI reference reuses the same guard (isDevSignInEnabled) and nothing else.
      "app/[locale]/dev/ui/page.tsx",
      "app/[locale]/signed-out/page.tsx",
    ]);
  });

  it("no source file links to the dev page path except the guarded signed-out view", () => {
    const linkers = files(src)
      .filter((file) => /\.tsx?$/.test(file) && !/\.test\.tsx?$/.test(file))
      .filter((file) => readFileSync(file, "utf8").includes("/dev/sign-in"))
      .map((file) => relative(src, file).replaceAll("\\", "/"))
      .sort();
    expect(linkers).toEqual(["features/auth/SignedOutView.tsx"]);
  });
});
