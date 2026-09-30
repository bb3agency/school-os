import { act, cleanup, render } from "@testing-library/react";
import type * as Navigation from "next/navigation";
import { isValidElement, type ReactElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PlatformShell } from "@/components/shell/PlatformShell";
import { SchoolShell } from "@/components/shell/SchoolShell";
import { renderWithIntl } from "@/test/render";

/**
 * No URL carries a locale, in any language (product owner 2026-09-30; ADR-0036 note). Every
 * page under app/[locale] is rendered inside its shell (Telugu off, the product default; the
 * API answering 404) and every link, form and resource address is checked; the shells (every
 * menu link and the language switch) are checked again with Telugu on, in English and Telugu.
 */

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => "/students",
    useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
    useSearchParams: () => new URLSearchParams(),
    useParams: () => ({ locale: "en" }),
    notFound: () => {
      throw new Error("NEXT_NOT_FOUND");
    },
    redirect: (url: string) => {
      throw new Error(`NEXT_REDIRECT ${url}`);
    },
  };
});

vi.mock("next/headers", () => ({
  cookies: async () => ({ get: () => undefined, getAll: () => [] }),
  headers: async () => new Headers({ "x-sos-path": "/" }),
}));

const UUID = "0192f3a4-0000-7000-8000-00000000c101";
/** Any route parameter the page asks for: `locale` is English, every id a synthetic UUID. */
const params = () =>
  Promise.resolve(
    new Proxy({ locale: "en" } as Record<string, string>, {
      get: (target, key) => (typeof key === "string" ? (target[key] ?? UUID) : undefined),
    }),
  );

type PageModule = { default: (props: object) => unknown };
const modules = import.meta.glob("./\\[locale\\]/**/page.tsx") as Record<
  string,
  () => Promise<PageModule>
>;

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json({ status: 404, code: "not_found" }, { status: 404 })),
  );
});
afterEach(() => {
  vi.unstubAllGlobals();
});

async function element(path: string): Promise<ReactElement | null> {
  const load = modules[path];
  if (!load) return null;
  const Page = (await load()).default;
  try {
    const result = await Page({ params: params(), searchParams: Promise.resolve({}) });
    return isValidElement(result) ? result : null;
  } catch {
    return null;
  }
}

const PREFIXED = /^\/(en|te)(\/|$|\?|#)/i;

/** Links, forms and resources whose address names a locale (`/en`, `/te`, `/en/...`). */
function localePrefixed(root: HTMLElement): string[] {
  const found: string[] = [];
  for (const node of root.querySelectorAll("[href], [action], [src]")) {
    for (const attr of ["href", "action", "src"]) {
      const value = node.getAttribute(attr);
      if (value && PREFIXED.test(value)) {
        found.push(`${node.tagName.toLowerCase()} ${attr}=${value}`);
      }
    }
  }
  return found;
}

describe("no locale in any URL (product owner 2026-09-30, ADR-0036 note)", () => {
  it("no page under app/[locale] links to a /en or /te address", async () => {
    const paths = Object.keys(modules).sort();
    const failures: Record<string, string[]> = {};
    let scanned = 0;
    let links = 0;
    for (const path of paths) {
      const page = await element(path);
      if (!page) continue;
      const shell = path.includes("/platform/") ? (
        <PlatformShell permissions={null}>{page}</PlatformShell>
      ) : (
        <SchoolShell permissions={null}>{page}</SchoolShell>
      );
      const { container } = renderWithIntl(shell);
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 0));
      });
      const found = localePrefixed(container);
      if (found.length > 0) failures[path] = found;
      links += container.querySelectorAll("a[href^='/']").length;
      scanned += 1;
      cleanup();
    }
    expect(failures).toEqual({});
    // Pinned so a broken import cannot empty the scan, and it really looked at in-app links.
    expect(scanned).toBeGreaterThan(75);
    expect(links).toBeGreaterThan(50);
  }, 120_000);

  it.each([
    ["English", { telugu: true }],
    ["Telugu", { locale: "te", telugu: true }],
  ] as const)("with Telugu on, the menus link prefix-less paths (%s)", (_label, options) => {
    for (const shell of [
      <SchoolShell key="school" permissions={null} languages={["te", "en"]}>
        <p>x</p>
      </SchoolShell>,
      <PlatformShell key="platform" permissions={null}>
        <p>x</p>
      </PlatformShell>,
    ]) {
      const { container } = renderWithIntl(shell, options);
      expect(localePrefixed(container)).toEqual([]);
      expect(container.querySelectorAll("a[href^='/']").length).toBeGreaterThan(5);
      // The language switch (shown while Telugu is on) sets a cookie: buttons, no links.
      const choices = [...container.querySelectorAll("nav [lang]")];
      expect(choices.map((node) => node.tagName)).toEqual(["BUTTON", "BUTTON"]);
      cleanup();
    }
  });

  it("the check itself finds a prefixed link", () => {
    const prefixed = "/en/students";
    const { container } = render(<a href={prefixed}>x</a>);
    expect(localePrefixed(container)).toEqual(["a href=/en/students"]);
  });
});
