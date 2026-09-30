import { act, cleanup } from "@testing-library/react";
import type * as Navigation from "next/navigation";
import { isValidElement, type ReactElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PlatformShell } from "@/components/shell/PlatformShell";
import { SchoolShell } from "@/components/shell/SchoolShell";
import { renderWithIntl } from "@/test/render";

/**
 * ADR-0036 (English first, Telugu hidden): with SOS_TELUGU_ENABLED off (the default) no page
 * shows Telugu. Every page under app/[locale] is rendered inside its shell, with the school's
 * languages set to Telugu and English, the API answering 404 (empty and error states), and
 * the DOM is scanned for Telugu script (U+0C00–U+0C7F), the word "Telugu", `lang="te"`,
 * `hreflang="te"` and links to /te. Pages that need a live session (server layouts are
 * covered in layouts.node.test.tsx) are skipped here, and the count of pages scanned is pinned.
 */

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => "/en/students",
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
  headers: async () => new Headers({ "x-sos-path": "/en" }),
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

const TELUGU_SCRIPT = /[ఀ-౿]/;

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json({ status: 404, code: "not_found" }, { status: 404 })),
  );
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
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

function offenders(root: HTMLElement): string[] {
  const found: string[] = [];
  const text = root.textContent ?? "";
  if (TELUGU_SCRIPT.test(text)) found.push(`script: ${text.match(/.{0,20}[ఀ-౿]+/)?.[0]}`);
  if (/Telugu/i.test(text)) found.push(`word: ${text.match(/.{0,40}Telugu.{0,20}/i)?.[0]}`);
  const attrs = root.querySelectorAll("[lang='te'], [hreflang='te'], a[href^='/te']");
  if (attrs.length > 0) found.push(`attributes: ${attrs.length}`);
  for (const input of root.querySelectorAll<HTMLInputElement>("input, textarea, option")) {
    if (TELUGU_SCRIPT.test(input.value ?? "") || /_te$/.test(input.name ?? "")) {
      found.push(`field: ${input.name || input.value}`);
    }
  }
  return found;
}

describe("English only while Telugu is switched off (ADR-0036)", () => {
  it("no page under app/[locale] shows Telugu, a Telugu field or a language switch", async () => {
    const paths = Object.keys(modules).sort();
    expect(paths.length).toBeGreaterThan(60);
    const scanned: string[] = [];
    const failures: Record<string, string[]> = {};
    for (const path of paths) {
      const page = await element(path);
      if (!page) continue;
      const shell = path.includes("/platform/") ? (
        <PlatformShell permissions={null}>{page}</PlatformShell>
      ) : (
        <SchoolShell permissions={null} languages={["te", "en"]}>
          {page}
        </SchoolShell>
      );
      const { container } = renderWithIntl(shell);
      // Let queries settle into their error or empty states.
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 0));
      });
      const found = offenders(container);
      if (found.length > 0) failures[path] = found;
      expect(container.querySelector("nav[aria-label='Language']"), path).toBeNull();
      scanned.push(path);
      cleanup();
    }
    expect(failures).toEqual({});
    // Most pages render without a session; pinned so a broken import cannot empty the scan.
    expect(scanned.length).toBeGreaterThan(75);
  }, 120_000);

  it("the scan finds Telugu when it is switched on (the check itself works)", async () => {
    const page = await element("./[locale]/welcome/page.tsx");
    expect(page).not.toBeNull();
    const { container } = renderWithIntl(
      <SchoolShell permissions={null} languages={["te", "en"]}>
        {page}
      </SchoolShell>,
      { telugu: true },
    );
    expect(offenders(container).length).toBeGreaterThan(0);
  });
});
