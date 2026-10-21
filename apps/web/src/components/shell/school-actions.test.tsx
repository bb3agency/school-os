import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { describe, expect, it, vi } from "vitest";
import type { NavSection } from "@/components/ui/SidebarNav";
import { renderWithIntl } from "@/test/render";
import { SchoolShell } from "./SchoolShell";
import { SCHOOL_ACTIONS, bottomBarFor } from "./school-actions";

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => "/",
    useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
  };
});

const SECTIONS: NavSection[] = [
  { id: "o", label: "Overview", items: [{ href: "/", label: "Home", exact: true, icon: "home" }] },
  {
    id: "c",
    label: "Classroom",
    items: [
      { href: "/attendance", label: "Attendance", icon: "check" },
      { href: "/marks", label: "Marks", icon: "chart" },
    ],
  },
];

describe("palette actions and bottom bars come from the same permission rule (docs/17 §5.9)", () => {
  it("every quick action points at an existing school page", () => {
    const pages = new Set([
      "/students/new",
      "/fees/collect",
      "/attendance",
      "/marks",
      "/change-requests/new",
      "/documents/new",
      "/ask",
      "/exports/new/student-list",
      "/settings/users/new",
    ]);
    for (const action of SCHOOL_ACTIONS) expect(pages.has(action.href), action.href).toBe(true);
  });

  it("teachers get Home, Attendance and Marks; only pages their menu shows", () => {
    expect(bottomBarFor(["class_teacher"], SECTIONS).map((item) => item.href)).toEqual([
      "/",
      "/attendance",
      "/marks",
    ]);
    // Office staff have no phone bar; a bar with fewer than two pages is dropped.
    expect(bottomBarFor(["office_staff"], SECTIONS)).toEqual([]);
    expect(bottomBarFor(["principal"], SECTIONS.slice(0, 1))).toEqual([]);
  });

  it("the palette offers only the actions the person's permissions allow", async () => {
    const user = userEvent.setup();
    renderWithIntl(
      <SchoolShell
        permissions={["attendance.read", "attendance.record", "kb.ask"]}
        account={{ kind: "staff", displayName: "Sample Teacher", roles: ["class_teacher"] }}
      >
        <h1>Today</h1>
      </SchoolShell>,
    );
    await user.keyboard("{Control>}k{/Control}");
    const palette = screen.getByRole("dialog", { name: "Search or jump to" });
    const actions = within(palette).getByRole("group", { name: "Actions" });
    expect(
      within(actions)
        .getAllByRole("option")
        .map((option) => option.textContent),
    ).toEqual(["Take attendance", "Ask the school"]);
    const pages = within(palette).getByRole("group", { name: "Go to" });
    expect(pages).not.toHaveTextContent("Students");
    expect(screen.getByRole("navigation", { name: "Quick links" })).toHaveTextContent("Attendance");
  });
});

describe("the school menu", () => {
  it("has each section once and each page once, with every permission and feature on", () => {
    // The sidebar keys sections by id: two waves each adding a "privacy" section gave React a
    // duplicate key and two Privacy headings (R3-1A + R3-1B merge).
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    renderWithIntl(
      <SchoolShell
        permissions={null}
        features={{ tally: true, messaging: true, fees: true, disclosure: true }}
        account={{ kind: "staff", displayName: "Sample Principal", roles: ["principal"] }}
      >
        <h1>Today</h1>
      </SchoolShell>,
    );
    const duplicateKeys = errors.mock.calls.filter((call) => String(call[0]).includes("same key"));
    errors.mockRestore();
    expect(duplicateKeys).toEqual([]);
    const menu = screen.getByRole("navigation", { name: "Main" });
    const hrefs = within(menu)
      .getAllByRole("link")
      .map((link) => link.getAttribute("href"));
    expect(hrefs.filter((href, i) => hrefs.indexOf(href) !== i)).toEqual([]);
    expect(within(menu).getAllByText("Privacy")).toHaveLength(1);
  });
});
