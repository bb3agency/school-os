import { screen, within } from "@testing-library/react";
import type * as Navigation from "next/navigation";
import { describe, expect, it, vi } from "vitest";
import { SchoolShell } from "@/components/shell/SchoolShell";
import { renderWithIntl } from "@/test/render";
import { LanguageSwitcher, offeredLanguages } from "./LanguageSwitcher";

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return { ...actual, usePathname: () => "/en/students" };
});

function offered(): (string | null)[] {
  const nav = screen.queryByRole("navigation", { name: "Language" });
  if (!nav) return [];
  return within(nav)
    .getAllByRole("link")
    .map((link) => link.getAttribute("lang"));
}

describe("language switcher follows the school's languages (FR-TEN-012, NFR-I18N-001)", () => {
  it("keeps the school's order (first is the default), drops unknown codes and duplicates", () => {
    expect(offeredLanguages(["te", "en"])).toEqual(["te", "en"]);
    expect(offeredLanguages(["te", "xx", "te"])).toEqual(["te"]);
    expect(offeredLanguages(["en"])).toEqual(["en"]);
  });

  it("keeps English when the list is empty or unusable", () => {
    expect(offeredLanguages([])).toEqual(["en"]);
    expect(offeredLanguages(["fr"])).toEqual(["en"]);
  });

  it("offers every language outside a school (sign-in pages, platform)", () => {
    expect(offeredLanguages(undefined)).toEqual(["en", "te"]);
    expect(offeredLanguages(null)).toEqual(["en", "te"]);
    renderWithIntl(<LanguageSwitcher />);
    expect(offered()).toEqual(["en", "te"]);
  });

  it("offers both languages in the school's order", () => {
    renderWithIntl(<LanguageSwitcher languages={["te", "en"]} />);
    expect(offered()).toEqual(["te", "en"]);
  });

  it("offers only the school's language when it uses one", () => {
    renderWithIntl(<LanguageSwitcher languages={["te"]} />, "en");
    expect(offered()).toEqual(["te"]);
  });

  it("shows nothing when the only language is the one already in use", () => {
    renderWithIntl(<LanguageSwitcher languages={["en"]} />, "en");
    expect(offered()).toEqual([]);
  });

  it("the school shell passes the school's languages to the switcher", () => {
    renderWithIntl(
      <SchoolShell languages={["te"]}>
        <p>x</p>
      </SchoolShell>,
    );
    expect(offered()).toEqual(["te"]);
  });
});
