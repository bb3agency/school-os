import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { screen } from "@testing-library/react";
import type * as Navigation from "next/navigation";
import { afterEach, describe, expect, it, vi } from "vitest";
import { intlErrors, renderWithIntl } from "@/test/render";

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => "/en/dev/ui",
    useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
    useSearchParams: () => new URLSearchParams(),
    useParams: () => ({ locale: "en" }),
    notFound: () => {
      throw new Error("NEXT_NOT_FOUND");
    },
  };
});

import DevUiPage, { generateMetadata } from "./[locale]/dev/ui/page";

const REAL_ISSUER = "https://cognito-idp.ap-south-1.amazonaws.com/ap-south-1_SYNTHETIC";

afterEach(() => {
  vi.unstubAllEnvs();
});

/**
 * The design-system reference page is local development only, with the dev sign-in guard
 * (SEC-004): a 404 in production builds and with a real issuer. When shown it is CSP-safe
 * (SEC-010: no style attributes, images or scripts) and complete in both languages
 * (NFR-I18N-001).
 */
describe("dev UI reference (/[locale]/dev/ui)", () => {
  it("is a 404 in a production build, even with the local stub issuer", async () => {
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("OIDC_ISSUER", "http://localhost:8080/schoolos");
    expect(() => DevUiPage()).toThrow("NEXT_NOT_FOUND");
    await expect(generateMetadata({ params: Promise.resolve({ locale: "en" }) })).resolves.toEqual(
      {},
    );
  });

  it("is a 404 under next dev with a real issuer", () => {
    vi.stubEnv("NODE_ENV", "development");
    vi.stubEnv("OIDC_ISSUER", REAL_ISSUER);
    expect(() => DevUiPage()).toThrow("NEXT_NOT_FOUND");
  });

  for (const locale of ["en", "te"] as const) {
    it(`renders every section, CSP-safe, no missing keys [${locale}]`, () => {
      vi.stubEnv("NODE_ENV", "development");
      vi.stubEnv("OIDC_ISSUER", "http://localhost:8080/schoolos");
      const { container } = renderWithIntl(DevUiPage(), locale);
      expect(intlErrors).toEqual([]);
      expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
      expect(screen.getAllByRole("heading", { level: 2 }).length).toBeGreaterThanOrEqual(14);
      expect(screen.getByRole("main")).toHaveAttribute("id", "main");
      expect(container.querySelectorAll("[style]")).toHaveLength(0);
      expect(container.querySelectorAll("img, script, iframe, link")).toHaveLength(0);
      const ids = [...container.querySelectorAll("[id]")].map((element) => element.id);
      expect(new Set(ids).size).toBe(ids.length);
    });
  }

  it("nothing links to it", () => {
    const src = resolve(dirname(fileURLToPath(import.meta.url)), "..");
    const files = (dir: string): string[] =>
      readdirSync(dir).flatMap((name) => {
        const path = join(dir, name);
        return statSync(path).isDirectory() ? files(path) : [path];
      });
    const linkers = files(src)
      .filter((file) => /\.tsx?$/.test(file) && !/\.test\.tsx?$/.test(file))
      // Any string literal with the path (href, redirect, fetch); prose comments are fine.
      .filter((file) => /["'`][^"'`\n]*\/dev\/ui/.test(readFileSync(file, "utf8")))
      .map((file) => relative(src, file).replaceAll("\\", "/"));
    expect(linkers).toEqual([]);
  });
});
