import { screen, within } from "@testing-library/react";
import type * as Navigation from "next/navigation";
import { describe, expect, it, vi } from "vitest";
import { SchoolShell } from "@/components/shell/SchoolShell";
import { messages, renderWithIntl } from "@/test/render";
import { activeHref, type NavItem } from "./SidebarNav";

const path = vi.hoisted(() => ({ current: "/" }));
vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return { ...actual, usePathname: () => path.current };
});

const ITEMS: NavItem[] = [
  { href: "/", label: "Home", exact: true },
  { href: "/settings/structure", label: "Structure" },
  {
    href: "/settings/structure/promotions",
    label: "Promotions",
    nested: true,
    activePattern: "^/settings/structure/years/[^/]+/promotions/?$",
  },
];

describe("sidebar: one current page, the most specific (FR-TEN-011 menu entry)", () => {
  it("marks the longest matching entry, or the one whose pattern matches", () => {
    expect(activeHref("/", ITEMS)).toBe("/");
    expect(activeHref("/settings/structure", ITEMS)).toBe("/settings/structure");
    expect(activeHref("/settings/structure/promotions", ITEMS)).toBe(
      "/settings/structure/promotions",
    );
    expect(activeHref("/settings/structure/years/abc/promotions", ITEMS)).toBe(
      "/settings/structure/promotions",
    );
    expect(activeHref("/settings/structurex", ITEMS)).toBeNull();
    expect(activeHref("/students", ITEMS)).toBeNull();
  });

  it("school menu: Promotions sits under School structure for structure managers only", () => {
    path.current = "/settings/structure/years/0192f3a4-0000-7000-8000-0000000000a1/promotions";
    const { unmount } = renderWithIntl(
      <SchoolShell permissions={["tenant.structure.manage"]}>
        <p>x</p>
      </SchoolShell>,
    );
    const nav = screen.getByRole("navigation", { name: "Main" });
    const promotions = within(nav).getByRole("link", { name: messages.en.school.nav.promotions });
    expect(promotions).toHaveAttribute("href", "/settings/structure/promotions");
    expect(promotions).toHaveAttribute("aria-current", "page");
    expect(within(nav).getByRole("link", { name: "School structure" })).not.toHaveAttribute(
      "aria-current",
    );
    expect(within(nav).getAllByRole("link", { current: "page" })).toHaveLength(1);
    unmount();

    path.current = "/settings/structure";
    renderWithIntl(
      <SchoolShell permissions={["student.read_basic"]}>
        <p>x</p>
      </SchoolShell>,
    );
    const menu = screen.getByRole("navigation", { name: "Main" });
    expect(
      within(menu).queryByRole("link", { name: messages.en.school.nav.promotions }),
    ).toBeNull();
    expect(within(menu).getByRole("link", { name: "School structure" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });
});
