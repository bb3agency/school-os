import { cleanup, render } from "@testing-library/react";
import type * as Navigation from "next/navigation";
import { describe, expect, it, vi } from "vitest";
import { PlatformShell } from "@/components/shell/PlatformShell";
import { SchoolShell } from "@/components/shell/SchoolShell";
import { renderWithIntl } from "@/test/render";

/**
 * No URL carries a locale, in any language (product owner 2026-09-30; ADR-0036 note). Every
 * page is scanned with Telugu off in english-only.test.tsx (the same page scan, so the pages
 * are rendered once); here the shells (every menu link and the language switch) are checked
 * with Telugu on, in English and in Telugu.
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
