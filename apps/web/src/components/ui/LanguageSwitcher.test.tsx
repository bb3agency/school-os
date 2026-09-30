import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SchoolShell } from "@/components/shell/SchoolShell";
import { localeCookieString } from "@/i18n/locale-cookie";
import { renderWithIntl } from "@/test/render";
import { LanguageSwitcher, offeredLanguages } from "./LanguageSwitcher";

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return { ...actual, usePathname: () => "/students" };
});

afterEach(() => {
  document.cookie = "NEXT_LOCALE=; path=/; max-age=0";
  vi.clearAllMocks();
});

function offered(): (string | null)[] {
  const nav = screen.queryByRole("navigation", { name: "Language" });
  if (!nav) return [];
  return within(nav)
    .getAllByRole("button")
    .map((button) => button.getAttribute("lang"));
}

/** Telugu switched on explicitly (ADR-0036): the switcher exists only then. */
const telugu = { telugu: true } as const;

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
    renderWithIntl(<LanguageSwitcher />, telugu);
    expect(offered()).toEqual(["en", "te"]);
  });

  it("offers both languages in the school's order", () => {
    renderWithIntl(<LanguageSwitcher languages={["te", "en"]} />, telugu);
    expect(offered()).toEqual(["te", "en"]);
  });

  it("offers only the school's language when it uses one", () => {
    renderWithIntl(<LanguageSwitcher languages={["te"]} />, { ...telugu, locale: "en" });
    expect(offered()).toEqual(["te"]);
  });

  it("shows nothing when the only language is the one already in use", () => {
    renderWithIntl(<LanguageSwitcher languages={["en"]} />, { ...telugu, locale: "en" });
    expect(offered()).toEqual([]);
  });

  it("the school shell passes the school's languages to the switcher", () => {
    renderWithIntl(
      <SchoolShell languages={["te"]}>
        <p>x</p>
      </SchoolShell>,
      telugu,
    );
    expect(offered()).toEqual(["te"]);
  });
});

describe("the switcher keeps the URL free of a locale (ADR-0036 note, 2026-09-30)", () => {
  it("renders buttons, not links: no href and no /te or /en anywhere", () => {
    renderWithIntl(<LanguageSwitcher />, telugu);
    const nav = screen.getByRole("navigation", { name: "Language" });
    expect(within(nav).queryAllByRole("link")).toEqual([]);
    expect(nav.querySelector("[href], [hreflang]")).toBeNull();
    expect(within(nav).getByRole("button", { name: "English" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(within(nav).getByRole("button", { name: "తెలుగు" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
  });

  it("stores the choice in the NEXT_LOCALE cookie and reloads the same page", async () => {
    const user = userEvent.setup();
    const reload = vi.fn();
    const before = window.location.href;
    renderWithIntl(<LanguageSwitcher reload={reload} />, telugu);
    await user.click(screen.getByRole("button", { name: "తెలుగు" }));
    expect(document.cookie).toContain("NEXT_LOCALE=te");
    expect(reload).toHaveBeenCalledTimes(1);
    expect(window.location.href).toBe(before);
  });

  it("does nothing for the language already in use (keyboard too)", async () => {
    const user = userEvent.setup();
    const reload = vi.fn();
    renderWithIntl(<LanguageSwitcher reload={reload} />, telugu);
    screen.getByRole("button", { name: "English" }).focus();
    await user.keyboard("{Enter}");
    expect(reload).not.toHaveBeenCalled();
    expect(document.cookie).not.toContain("NEXT_LOCALE");
  });

  it("writes a site-wide, year-long, SameSite=Lax cookie (Secure over https)", () => {
    expect(localeCookieString("te", false)).toBe(
      "NEXT_LOCALE=te; path=/; max-age=31536000; samesite=lax",
    );
    expect(localeCookieString("en", true)).toBe(
      "NEXT_LOCALE=en; path=/; max-age=31536000; samesite=lax; secure",
    );
  });
});

describe("no language switcher while Telugu is switched off (ADR-0036, the default)", () => {
  it("offers only switched-on languages", () => {
    expect(offeredLanguages(undefined, ["en"])).toEqual(["en"]);
    expect(offeredLanguages(["te", "en"], ["en"])).toEqual(["en"]);
    expect(offeredLanguages(["te"], ["en"])).toEqual(["en"]);
  });

  it("renders nothing, whatever the school's languages", () => {
    for (const languages of [null, ["en", "te"], ["te", "en"], ["te"], ["en"]]) {
      const { unmount } = renderWithIntl(<LanguageSwitcher languages={languages} />);
      expect(offered(), JSON.stringify(languages)).toEqual([]);
      expect(document.body.textContent ?? "").not.toMatch(/[\u0C00-\u0C7F]|Telugu/);
      unmount();
    }
  });

  it("the school shell shows no switcher even for a school set to Telugu", () => {
    renderWithIntl(
      <SchoolShell languages={["te", "en"]}>
        <p>x</p>
      </SchoolShell>,
    );
    expect(offered()).toEqual([]);
    expect(document.querySelector("[hreflang='te'], [lang='te']")).toBeNull();
  });
});
