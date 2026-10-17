/* eslint-disable no-restricted-properties -- these tests read and seed the one value the app
   keeps in browser storage: the compact-sidebar preference (no tokens, no personal data). */
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { NavSection } from "@/components/ui/SidebarNav";
import { messages, renderWithIntl } from "@/test/render";
import { AppShell, type AppShellProps } from "./AppShell";
import { NEUTRAL_CANVAS_PATHS, canvasFor } from "./canvas";
import {
  DENSITY_ATTRIBUTE,
  DENSITY_STORAGE_KEY,
  SHELL_STATE_SCRIPT,
  SIDEBAR_ATTRIBUTE,
  SIDEBAR_STATE_SCRIPT,
  SIDEBAR_STORAGE_KEY,
} from "./sidebar-script";

/**
 * The one sidebar (docs/17 §5.2; NFR-A11Y-001, NFR-I18N-001). jsdom applies no CSS, so the
 * wide-screen sidebar (hidden below lg) and the drawer are both in the DOM here; what CSS
 * decides (which one shows at which width, no horizontal scroll) is checked by
 * e2e/responsive.spec.ts and e2e/a11y.spec.ts in a real browser.
 */

const path = vi.hoisted(() => ({ current: "/students" }));
const router = vi.hoisted(() => ({
  push: vi.fn(),
  replace: vi.fn(),
  refresh: vi.fn(),
  prefetch: vi.fn(),
}));
vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return { ...actual, usePathname: () => path.current, useRouter: () => router };
});

const SECTIONS: NavSection[] = [
  {
    id: "overview",
    label: "Overview",
    items: [{ href: "/", label: "Home", exact: true, icon: "home" }],
  },
  {
    id: "records",
    label: "Records",
    items: [
      { href: "/students", label: "Students", icon: "users" },
      { href: "/imports", label: "Imports", icon: "upload" },
    ],
  },
  { id: "empty", label: "Empty", items: [] },
];

function shell(props: Partial<AppShellProps> = {}) {
  return (
    <AppShell
      homeHref="/"
      navLabel="Main"
      sections={SECTIONS}
      context={<p>Sample School</p>}
      account={<button type="button">Lock now</button>}
      topbarActions={<button type="button">Notifications</button>}
      session={<p>idle warning host</p>}
      {...props}
    >
      <h1>Page</h1>
    </AppShell>
  );
}

/** The wide-screen sidebar (the drawer is the <dialog>). */
function inlineSidebar(): HTMLElement {
  const node = document.querySelector<HTMLElement>("[data-sidebar-mode='inline']");
  if (!node) throw new Error("no inline sidebar");
  return node;
}

beforeEach(() => {
  path.current = "/students";
  document.documentElement.removeAttribute(SIDEBAR_ATTRIBUTE);
  document.documentElement.removeAttribute(DENSITY_ATTRIBUTE);
  window.localStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
  document.documentElement.removeAttribute(SIDEBAR_ATTRIBUTE);
  window.localStorage.clear();
});

describe("AppShell: one sidebar, no rail and no second list panel", () => {
  it("skip link first, one Main navigation, the main landmark; no 'Sections' rail", () => {
    renderWithIntl(shell());
    const skip = screen.getByRole("link", { name: "Skip to main content" });
    expect(skip).toHaveAttribute("href", "#main");
    // The skip link is the first focusable element of the page.
    expect(document.querySelector("a[href], button")).toBe(skip);
    expect(screen.getByRole("main")).toHaveAttribute("id", "main");
    // One menu; the top bar adds only the "Where you are" trail.
    expect(screen.getAllByRole("navigation", { name: "Main" })).toHaveLength(1);
    expect(screen.getAllByRole("navigation")).toHaveLength(2);
    expect(screen.getByRole("navigation", { name: "Where you are" })).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Sections" })).toBeNull();
    // The drawer renders nothing while closed (no hidden second menu to tab into).
    const drawer = document.querySelector("dialog");
    expect(drawer).not.toHaveAttribute("open");
    expect(drawer?.querySelector("a, button")).toBeNull();
    // The session host (idle warning) is mounted once, outside the sidebar.
    expect(screen.getAllByText("idle warning host")).toHaveLength(1);
    expect(inlineSidebar()).not.toContainElement(screen.getByText("idle warning host"));
  });

  it("anatomy top to bottom: brand, context, grouped navigation, account", () => {
    renderWithIntl(shell());
    const sidebar = inlineSidebar();
    const brand = within(sidebar).getByRole("link", { name: "SchoolOS" });
    expect(brand).toHaveAttribute("href", "/");
    const context = within(sidebar).getByText("Sample School");
    const nav = within(sidebar).getByRole("navigation", { name: "Main" });
    const account = within(sidebar).getByRole("button", { name: "Lock now" });
    const order = [brand, context, nav, account];
    for (let i = 1; i < order.length; i += 1) {
      expect(
        (order[i - 1] as Node).compareDocumentPosition(order[i] as Node) &
          Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy();
    }
    // Empty groups are dropped; the others are lists named by their headings.
    expect(
      within(nav)
        .getAllByRole("list")
        .map((list) => list.getAttribute("aria-labelledby")),
    ).toHaveLength(2);
    expect(within(nav).getByRole("list", { name: "Records" })).toBeInTheDocument();
    expect(within(nav).queryByText("Empty")).toBeNull();
    // Sticky, full height, the menu scrolls on its own; hidden in print.
    expect(sidebar).toHaveClass("sticky", "top-0", "h-viewport", "hidden", "lg:block");
    expect(sidebar).toHaveAttribute("data-print", "hide");
    expect(nav).toHaveClass("overflow-y-auto", "min-h-0", "flex-1");
    // Top bar: page-wide tools; the menu button only below lg.
    const banner = screen.getByRole("banner");
    expect(within(banner).getByRole("button", { name: "Notifications" })).toBeInTheDocument();
    expect(within(banner).getByRole("button", { name: "Menu" }).parentElement).toHaveClass(
      "lg:hidden",
    );
  });

  it("the current page is marked once with aria-current='page'", () => {
    path.current = "/imports";
    renderWithIntl(shell());
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(within(nav).getByRole("link", { name: "Imports" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(within(nav).getAllByRole("link", { current: "page" })).toHaveLength(1);
    expect(within(nav).getByRole("link", { name: "Home" })).not.toHaveAttribute("aria-current");
  });

  it("school theme: sidebar on the canvas tone with a hairline; platform: dark neutral with the amber ring", () => {
    const { unmount } = renderWithIntl(shell());
    expect(inlineSidebar()).toHaveClass(
      "bg-canvas",
      "border-e",
      "border-border",
      "w-60",
      "collapsed:w-14",
    );
    expect(inlineSidebar()).not.toHaveClass("platform-chrome");
    expect(screen.getByRole("banner")).not.toHaveClass("bg-platform");
    unmount();
    renderWithIntl(shell({ theme: "platform", brandBadge: <span>Platform admin</span> }));
    expect(inlineSidebar()).toHaveClass("platform-chrome", "bg-platform", "text-platform-ink");
    expect(screen.getByRole("banner")).toHaveClass("platform-chrome", "bg-platform");
    expect(document.querySelector("dialog")).toHaveClass(
      "drawer",
      "platform-chrome",
      "bg-platform",
    );
  });
});

describe("AppShell: compact sidebar (collapse toggle)", () => {
  it("collapses and expands with a keyboard-operable button and remembers the choice", async () => {
    const user = userEvent.setup();
    renderWithIntl(shell());
    const toggle = within(inlineSidebar()).getByRole("button", { name: "Collapse menu" });
    toggle.focus();
    await user.keyboard("{Enter}");
    expect(document.documentElement).toHaveAttribute(SIDEBAR_ATTRIBUTE, "collapsed");
    expect(window.localStorage.getItem(SIDEBAR_STORAGE_KEY)).toBe("collapsed");
    // Same button, new action name; focus stays on it.
    expect(toggle).toHaveAccessibleName("Expand menu");
    expect(toggle).toHaveFocus();
    // Labels stay the accessible names in compact mode.
    const nav = within(inlineSidebar()).getByRole("navigation", { name: "Main" });
    expect(within(nav).getByRole("link", { name: "Students" })).toBeInTheDocument();

    await user.keyboard(" ");
    expect(document.documentElement).not.toHaveAttribute(SIDEBAR_ATTRIBUTE);
    expect(window.localStorage.getItem(SIDEBAR_STORAGE_KEY)).toBe("expanded");
    expect(toggle).toHaveAccessibleName("Collapse menu");
  });

  it("a stored choice is applied on load; the head script sets it before the first paint", () => {
    window.localStorage.setItem(SIDEBAR_STORAGE_KEY, "collapsed");
    // The root layout runs this inline in <head> (tested here as plain code).
    new Function(SIDEBAR_STATE_SCRIPT)();
    expect(document.documentElement).toHaveAttribute(SIDEBAR_ATTRIBUTE, "collapsed");
    renderWithIntl(shell());
    expect(within(inlineSidebar()).getByRole("button", { name: "Expand menu" })).toBeVisible();
  });

  it("without the head script (client-only render) the stored choice still applies", () => {
    window.localStorage.setItem(SIDEBAR_STORAGE_KEY, "collapsed");
    renderWithIntl(shell());
    expect(document.documentElement).toHaveAttribute(SIDEBAR_ATTRIBUTE, "collapsed");
    expect(within(inlineSidebar()).getByRole("button", { name: "Expand menu" })).toBeVisible();
  });

  it("works when browser storage is blocked: nothing throws, the choice lasts for the page", async () => {
    vi.spyOn(window, "localStorage", "get").mockImplementation(() => {
      throw new DOMException("blocked", "SecurityError");
    });
    expect(() => new Function(SIDEBAR_STATE_SCRIPT)()).not.toThrow();
    const user = userEvent.setup();
    renderWithIntl(shell());
    const toggle = within(inlineSidebar()).getByRole("button", { name: "Collapse menu" });
    await user.click(toggle);
    expect(document.documentElement).toHaveAttribute(SIDEBAR_ATTRIBUTE, "collapsed");
    expect(toggle).toHaveAccessibleName("Expand menu");
  });

  it("compact: an item's label shows beside it on focus and hover; Escape hides it (WCAG 1.4.13)", async () => {
    const user = userEvent.setup();
    renderWithIntl(shell());
    await user.click(within(inlineSidebar()).getByRole("button", { name: "Collapse menu" }));
    const tip = document.querySelector<HTMLElement>(".sidebar-tip");
    expect(tip).toHaveAttribute("aria-hidden", "true");
    expect(tip).toHaveAttribute("hidden");

    const students = within(inlineSidebar()).getByRole("link", { name: "Students" });
    act(() => students.focus());
    expect(tip).not.toHaveAttribute("hidden");
    expect(tip).toHaveTextContent("Students");
    // Placed with the CSSOM (CSP forbids style attributes in markup, not this).
    expect(tip?.style.top).toMatch(/px$/);
    await user.keyboard("{Escape}");
    expect(tip).toHaveAttribute("hidden");

    fireEvent.pointerOver(within(inlineSidebar()).getByRole("link", { name: "Imports" }));
    expect(tip).not.toHaveAttribute("hidden");
    expect(tip).toHaveTextContent("Imports");
    // Moving onto the label keeps it (hoverable); leaving it hides it.
    fireEvent.pointerLeave(inlineSidebar(), { relatedTarget: tip });
    expect(tip).not.toHaveAttribute("hidden");
    fireEvent.pointerLeave(tip as HTMLElement);
    expect(tip).toHaveAttribute("hidden");
  });

  it("expanded: no tooltips (the labels are visible)", () => {
    renderWithIntl(shell());
    act(() => within(inlineSidebar()).getByRole("link", { name: "Students" }).focus());
    expect(document.querySelector(".sidebar-tip")).toHaveAttribute("hidden");
  });
});

describe("AppShell: the same sidebar as a drawer below lg (NFR-A11Y-001)", () => {
  it("the menu button opens a named modal dialog; focus moves in, Escape closes and returns it", async () => {
    const user = userEvent.setup();
    renderWithIntl(shell());
    const menu = screen.getByRole("button", { name: "Menu" });
    const drawer = document.getElementById(menu.getAttribute("aria-controls") ?? "");
    expect(drawer?.tagName).toBe("DIALOG");
    expect(menu).toHaveAttribute("aria-haspopup", "dialog");
    expect(menu).toHaveAttribute("aria-expanded", "false");
    // Touch target: the menu button is 44px tall (min-h-11), above the 24px minimum.
    expect(menu).toHaveClass("min-h-11");

    // Keyboard: Enter on the menu button opens the drawer and puts focus on its close button.
    menu.focus();
    await user.keyboard("{Enter}");
    const dialog = screen.getByRole("dialog", { name: "Menu" });
    expect(dialog).toBe(drawer);
    expect(dialog).toHaveAttribute("open");
    expect(menu).toHaveAttribute("aria-expanded", "true");
    const close = within(dialog).getByRole("button", { name: "Close menu" });
    expect(close).toHaveFocus();
    expect(close).toHaveClass("size-11");
    // The same sidebar: context, grouped navigation with the current page, account.
    const nav = within(dialog).getByRole("navigation", { name: "Main" });
    expect(within(nav).getByRole("link", { name: "Students" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(within(nav).getByRole("list", { name: "Records" })).toBeInTheDocument();
    expect(within(dialog).getByText("Sample School")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Lock now" })).toBeInTheDocument();
    // The drawer is never compact and has no collapse toggle; its wordmark is not a link,
    // so the close button is the first focus stop.
    expect(dialog.querySelector("[data-sidebar-mode]")).toBeNull();
    expect(within(dialog).queryByRole("button", { name: "Collapse menu" })).toBeNull();
    expect(dialog.querySelector("a[href], button")).toBe(close);

    await user.keyboard("{Escape}");
    expect(dialog).not.toHaveAttribute("open");
    expect(menu).toHaveAttribute("aria-expanded", "false");
    expect(menu).toHaveFocus();
    expect(within(dialog).queryByRole("navigation")).toBeNull();
  });

  it("the close button and a tap on the dimmed page close it; focus goes back to Menu", async () => {
    const user = userEvent.setup();
    renderWithIntl(shell());
    const menu = screen.getByRole("button", { name: "Menu" });
    await user.click(menu);
    const dialog = screen.getByRole("dialog", { name: "Menu" });
    await user.click(within(dialog).getByRole("button", { name: "Close menu" }));
    expect(dialog).not.toHaveAttribute("open");
    expect(menu).toHaveFocus();

    // A tap on the dimmed page (the <dialog> box itself, outside the sheet) closes it too.
    await user.click(menu);
    expect(dialog).toHaveAttribute("open");
    await user.click(dialog);
    expect(dialog).not.toHaveAttribute("open");
    expect(menu).toHaveFocus();
  });

  it("the drawer stays expanded even when the wide sidebar is compact", async () => {
    const user = userEvent.setup();
    renderWithIntl(shell());
    await user.click(within(inlineSidebar()).getByRole("button", { name: "Collapse menu" }));
    await user.click(screen.getByRole("button", { name: "Menu" }));
    const dialog = screen.getByRole("dialog", { name: "Menu" });
    // collapsed: styles only match inside [data-sidebar-mode="inline"].
    expect(dialog.closest("[data-sidebar-mode='inline']")).toBeNull();
    expect(dialog.querySelector("[data-sidebar-mode='inline']")).toBeNull();
  });

  it("following a link closes it", async () => {
    const user = userEvent.setup();
    // Providers as a wrapper, so a re-render keeps them.
    const wrapper = ({ children }: { children: ReactNode }) => (
      <NextIntlClientProvider locale="en" messages={messages.en} timeZone="Asia/Kolkata">
        {children}
      </NextIntlClientProvider>
    );
    const { rerender } = render(shell(), { wrapper });
    const menu = screen.getByRole("button", { name: "Menu" });
    await user.click(menu);
    const dialog = screen.getByRole("dialog", { name: "Menu" });
    expect(dialog).toHaveAttribute("open");
    // The router changes the path (same tree, new pathname).
    path.current = "/imports";
    rerender(shell());
    expect(dialog).not.toHaveAttribute("open");
    expect(menu).toHaveAttribute("aria-expanded", "false");
  });
});

describe("AppShell: one flat canvas (docs/17 §3.2 v1.0, owner decision 2026-10-18)", () => {
  function canvas(): HTMLElement {
    const node = document.querySelector<HTMLElement>("[data-canvas]");
    if (!node) throw new Error("no canvas");
    return node;
  }

  it("every console page, dashboards included, is on the same flat canvas", () => {
    for (const route of ["/", "/students", "/ask", "/settings/users"]) {
      path.current = route;
      const { unmount } = renderWithIntl(shell());
      expect(canvas(), route).toHaveAttribute("data-canvas", "neutral");
      unmount();
    }
    path.current = "/platform";
    renderWithIntl(shell({ theme: "platform", homeHref: "/platform" }));
    expect(canvas()).toHaveAttribute("data-canvas", "neutral");
  });

  it("canvasFor always answers the flat canvas; the old prop still compiles", () => {
    expect(canvasFor("school", "/")).toBe("neutral");
    expect(canvasFor("platform", "/platform")).toBe("neutral");
    expect(NEUTRAL_CANVAS_PATHS.school).toContain("/");
    path.current = "/";
    renderWithIntl(shell({ canvas: "neutral" }));
    expect(canvas()).toHaveAttribute("data-canvas", "neutral");
  });
});

describe("AppShell: top bar (breadcrumb, palette, preferences)", () => {
  it("shows where you are from the same navigation: section, then the page", () => {
    path.current = "/students";
    const { unmount } = renderWithIntl(shell());
    const trail = screen.getByRole("navigation", { name: "Where you are" });
    expect(trail).toHaveTextContent("Records");
    expect(within(trail).getByText("Students")).toHaveAttribute("aria-current", "page");
    unmount();
    // On a sub-page the nav item becomes a link back to its list.
    path.current = "/students/0193";
    renderWithIntl(shell());
    const deeper = screen.getByRole("navigation", { name: "Where you are" });
    expect(within(deeper).getByRole("link", { name: "Students" })).toHaveAttribute(
      "href",
      "/students",
    );
  });

  it("no trail for a page outside the menu", () => {
    path.current = "/notifications";
    renderWithIntl(shell());
    expect(screen.queryByRole("navigation", { name: "Where you are" })).toBeNull();
  });

  it("Ctrl+K opens the palette with the menu's pages and the actions; Enter goes there", async () => {
    const user = userEvent.setup();
    router.push.mockClear();
    renderWithIntl(
      shell({ actions: [{ id: "add", label: "Add a student", href: "/students/new" }] }),
    );
    await user.keyboard("{Control>}k{/Control}");
    const palette = screen.getByRole("dialog", { name: "Search or jump to" });
    expect(palette).toHaveAttribute("open");
    const box = within(palette).getByRole("combobox", { name: "Search or jump to" });
    expect(box).toHaveFocus();
    // Every page the sidebar shows, and nothing it hides (the same filtered sections).
    const pages = within(palette).getByRole("group", { name: "Go to" });
    expect(
      within(pages)
        .getAllByRole("option")
        .map((option) => option.textContent),
    ).toEqual(["HomeOverview", "StudentsRecords", "ImportsRecords"]);
    expect(within(palette).getByRole("group", { name: "Actions" })).toHaveTextContent(
      "Add a student",
    );
    await user.type(box, "imp");
    const options = within(palette).getAllByRole("option");
    expect(options[0]).toHaveTextContent("Imports");
    expect(box).toHaveAttribute("aria-activedescendant", options[0]?.id);
    await user.keyboard("{Enter}");
    expect(router.push).toHaveBeenCalledWith("/imports");
    expect(palette).not.toHaveAttribute("open");
  });

  it("the search button opens it too; arrows move, Escape closes", async () => {
    const user = userEvent.setup();
    renderWithIntl(shell());
    await user.click(screen.getByRole("button", { name: /^Search or jump to…/ }));
    const palette = screen.getByRole("dialog", { name: "Search or jump to" });
    const box = within(palette).getByRole("combobox");
    const first = within(palette).getAllByRole("option")[0];
    expect(first).toHaveAttribute("aria-selected", "true");
    await user.keyboard("{ArrowDown}");
    expect(within(palette).getAllByRole("option")[1]).toHaveAttribute("aria-selected", "true");
    await user.keyboard("{ArrowUp}{ArrowUp}");
    const all = within(palette).getAllByRole("option");
    expect(all[all.length - 1]).toHaveAttribute("aria-selected", "true");
    await user.type(box, "zzzz");
    expect(within(palette).getByText("Nothing matches. Try another word.")).toBeInTheDocument();
    fireEvent(palette, new Event("close"));
    expect(screen.queryByRole("combobox")).toBeNull();
  });

  it("density: the preferences menu switches compact rows and remembers it", async () => {
    const user = userEvent.setup();
    renderWithIntl(shell());
    const prefs = screen.getByRole("button", { name: "Your preferences" });
    expect(prefs).toHaveAttribute("aria-haspopup", "menu");
    prefs.focus();
    await user.keyboard("{Enter}");
    const menu = screen.getByRole("menu", { name: "Your preferences" });
    const compact = within(menu).getByRole("menuitemradio", { name: "Compact" });
    expect(within(menu).getByRole("menuitemradio", { name: "Comfortable" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    await user.click(compact);
    expect(document.documentElement).toHaveAttribute(DENSITY_ATTRIBUTE, "compact");
    expect(window.localStorage.getItem(DENSITY_STORAGE_KEY)).toBe("compact");
    expect(prefs).toHaveFocus();
    // The head script restores it before the first paint.
    document.documentElement.removeAttribute(DENSITY_ATTRIBUTE);
    new Function(SHELL_STATE_SCRIPT)();
    expect(document.documentElement).toHaveAttribute(DENSITY_ATTRIBUTE, "compact");
  });

  it("Ctrl+\\ switches the compact sidebar", async () => {
    const user = userEvent.setup();
    renderWithIntl(shell());
    await user.keyboard("{Control>}\\{/Control}");
    expect(document.documentElement).toHaveAttribute(SIDEBAR_ATTRIBUTE, "collapsed");
    await user.keyboard("{Control>}\\{/Control}");
    expect(document.documentElement).not.toHaveAttribute(SIDEBAR_ATTRIBUTE);
  });

  it("phones: the bottom bar lists its destinations and marks the current one", () => {
    path.current = "/students";
    renderWithIntl(
      shell({
        bottomBar: [
          { href: "/", label: "Home", exact: true, icon: "home" },
          { href: "/students", label: "Students", icon: "users" },
        ],
      }),
    );
    const bar = screen.getByRole("navigation", { name: "Quick links" });
    expect(bar).toHaveClass("md:hidden");
    expect(within(bar).getByRole("link", { name: "Students" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(within(bar).getByRole("link", { name: "Home" })).not.toHaveAttribute("aria-current");
  });
});

describe("AppShell in Telugu (NFR-I18N-001)", () => {
  it("names its controls in Telugu", async () => {
    const user = userEvent.setup();
    renderWithIntl(shell(), "te");
    expect(screen.getByRole("link", { name: messages.te.common.skipToContent })).toBeVisible();
    expect(
      within(inlineSidebar()).getByRole("button", { name: messages.te.shell.collapse }),
    ).toBeVisible();
    await user.click(screen.getByRole("button", { name: messages.te.shell.menu }));
    const dialog = screen.getByRole("dialog", { name: messages.te.shell.menu });
    expect(within(dialog).getByRole("button", { name: messages.te.shell.closeMenu })).toHaveFocus();
  });
});
