import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { describe, expect, it, vi } from "vitest";
import { renderWithIntl } from "@/test/render";
import { AiPanel, QuoteBlock } from "./AiPanel";
import { Avatar, AvatarStack, initials } from "./Avatar";
import { DeltaPill, Pill } from "./Badge";
import { IconButton } from "./Button";
import { Card } from "./Card";
import { SearchInput } from "./Input";
import { PageHeader } from "./PageHeader";
import { ProgressRing } from "./ProgressRing";
import { SegmentedControl } from "./SegmentedControl";
import { SidebarNav, type NavSection } from "./SidebarNav";
import { Sparkline, sparklinePoints } from "./Sparkline";
import { KpiCard } from "./StatCard";
import { Timeline } from "./Timeline";
import { Toggle } from "./Toggle";

const path = vi.hoisted(() => ({ current: "/students" }));
vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return { ...actual, usePathname: () => path.current };
});

describe("Toggle: role=switch (NFR-A11Y-001)", () => {
  it("is a named switch that toggles with click, Space and its label", async () => {
    const user = userEvent.setup();
    const changes: boolean[] = [];
    renderWithIntl(
      <Toggle
        label="Answer from documents"
        description="Staff can ask questions."
        onCheckedChange={(value) => changes.push(value)}
      />,
    );
    const toggle = screen.getByRole("switch", { name: "Answer from documents" });
    expect(toggle).toHaveAttribute("aria-checked", "false");
    expect(toggle).toHaveAccessibleDescription("Staff can ask questions.");
    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-checked", "true");
    toggle.focus();
    await user.keyboard(" ");
    expect(toggle).toHaveAttribute("aria-checked", "false");
    await user.click(screen.getByText("Answer from documents"));
    expect(toggle).toHaveAttribute("aria-checked", "true");
    expect(changes).toEqual([true, false, true]);
  });

  it("controlled, disabled and form value", async () => {
    const user = userEvent.setup();
    const { container } = renderWithIntl(
      <form>
        <Toggle label="Locked" checked disabled name="locked" />
      </form>,
    );
    const toggle = screen.getByRole("switch", { name: "Locked" });
    expect(toggle).toBeDisabled();
    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-checked", "true");
    expect(container.querySelector('input[type="hidden"][name="locked"]')).toHaveValue("on");
  });
});

describe("SegmentedControl: native radio group", () => {
  const options = [
    { value: "both", label: "Both" },
    { value: "office", label: "Office" },
    { value: "parent", label: "Parent" },
  ];

  it("is a named group of radios; arrow keys move the choice", async () => {
    const user = userEvent.setup();
    const values: string[] = [];
    renderWithIntl(
      <SegmentedControl
        legend="Show messages from"
        options={options}
        onValueChange={(value) => values.push(value)}
      />,
    );
    const group = screen.getByRole("group", { name: "Show messages from" });
    const radios = within(group).getAllByRole("radio");
    expect(radios).toHaveLength(3);
    expect(radios[0]).toBeChecked();
    await user.tab();
    expect(radios[0]).toHaveFocus();
    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("radio", { name: "Office" })).toBeChecked();
    expect(screen.getByRole("radio", { name: "Office" })).toHaveFocus();
    await user.click(screen.getByText("Parent"));
    expect(screen.getByRole("radio", { name: "Parent" })).toBeChecked();
    expect(values).toEqual(["office", "parent"]);
  });

  it("radios share one name so a form submits the choice", () => {
    renderWithIntl(<SegmentedControl legend="Speaker" name="speaker" options={options} />);
    for (const radio of screen.getAllByRole("radio")) {
      expect(radio).toHaveAttribute("name", "speaker");
    }
  });
});

describe("ProgressRing, Sparkline and KpiCard: text alternatives", () => {
  it("ProgressRing is a named progressbar with the value as text", () => {
    renderWithIntl(<ProgressRing value={86.4} label="Records checked" />);
    const ring = screen.getByRole("progressbar", { name: "Records checked" });
    expect(ring).toHaveAttribute("aria-valuenow", "86");
    expect(ring).toHaveAttribute("aria-valuetext", "86%");
    expect(ring.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
  });

  it("ProgressRing clamps out-of-range values", () => {
    renderWithIntl(<ProgressRing value={140} label="Over" />);
    expect(screen.getByRole("progressbar", { name: "Over" })).toHaveAttribute(
      "aria-valuenow",
      "100",
    );
  });

  it("Sparkline hides the drawing and reads the label", () => {
    const { container } = renderWithIntl(
      <Sparkline values={[3, 5, 4, 8]} label="Rising from 3 to 8" />,
    );
    expect(container.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
    expect(screen.getByText("Rising from 3 to 8")).toHaveClass("sr-only");
    expect(sparklinePoints([1, 1])).toBe("2.0,30.0 118.0,30.0");
    expect(sparklinePoints([])).toBe("");
  });

  it("KpiCard groups the number with its label; the delta reads its label", () => {
    renderWithIntl(
      <KpiCard
        label="Students on roll"
        value="1,245"
        unavailableLabel="Not available"
        delta={{ value: "+2.7%", direction: "up", label: "up 2.7% on last month" }}
        comparison="1,212 last month"
      />,
    );
    const group = screen.getByRole("group", { name: "Students on roll" });
    expect(group).toHaveTextContent("1,245");
    expect(within(group).getByText("up 2.7% on last month")).toHaveClass("sr-only");
    expect(within(group).getByText("+2.7%")).toHaveAttribute("aria-hidden", "true");
  });

  it("KpiCard without a value says 'Not available' and hides the delta", () => {
    renderWithIntl(
      <KpiCard
        label="Fees collected"
        value={null}
        unavailableLabel="Not available"
        delta={{ value: "+1%" }}
      />,
    );
    const group = screen.getByRole("group", { name: "Fees collected" });
    expect(within(group).getByText("Not available")).toHaveClass("sr-only");
    expect(within(group).queryByText("+1%")).toBeNull();
  });
});

describe("pills, avatars and timeline", () => {
  it("DeltaPill without a label reads the value", () => {
    renderWithIntl(<DeltaPill value="-1.2%" direction="down" />);
    expect(screen.getByText("-1.2%")).toBeInTheDocument();
  });

  it("status pills always carry text", () => {
    renderWithIntl(<Pill variant="done">Done</Pill>);
    expect(screen.getByText("Done")).toHaveClass("pill-gradient-teal");
  });

  it("initials work for Latin and Telugu names", () => {
    expect(initials("Sample Staff A")).toBe("SA");
    expect(initials("  sample  ")).toBe("S");
    expect(initials("")).toBe("?");
    expect(initials("నమూనా ఉపాధ్యాయుడు")).toBe("నఉ");
  });

  it("Avatar is a named image unless decorative; AvatarStack has one name", () => {
    renderWithIntl(
      <>
        <Avatar name="Sample Staff A" />
        <AvatarStack
          names={["Sample Staff A", "Sample Staff B", "Sample Staff C"]}
          max={2}
          label="Assigned to 3 people"
        />
      </>,
    );
    expect(screen.getByRole("img", { name: "Sample Staff A" })).toHaveTextContent("SA");
    const stack = screen.getByRole("img", { name: "Assigned to 3 people" });
    expect(stack).toHaveTextContent("+1");
    expect(within(stack).queryAllByRole("img")).toHaveLength(0);
  });

  it("Timeline is a named ordered list with status text for screen readers", () => {
    renderWithIntl(
      <Timeline
        label="Import history"
        items={[
          { id: "1", title: "File uploaded", time: "10:02", statusLabel: "Done:" },
          { id: "2", title: "Checking rows", status: "current", statusLabel: "In progress:" },
        ]}
      />,
    );
    const list = screen.getByRole("list", { name: "Import history" });
    const items = within(list).getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(items[1]).toHaveTextContent("In progress: Checking rows");
  });
});

describe("page chrome primitives", () => {
  it("PageHeader has one h1 and a breadcrumb with the current page marked", () => {
    renderWithIntl(
      <PageHeader
        title="Sample student A"
        eyebrow="Student"
        breadcrumb={[
          { label: "Home", href: "/" },
          { label: "Students", href: "/students" },
          { label: "Sample student A" },
        ]}
      />,
    );
    expect(screen.getByRole("heading", { level: 1, name: "Sample student A" })).toBeVisible();
    const crumbs = screen.getByRole("navigation", { name: "You are here" });
    expect(within(crumbs).getByRole("link", { name: "Students" })).toHaveAttribute(
      "href",
      "/students",
    );
    expect(within(crumbs).getByText("Sample student A")).toHaveAttribute("aria-current", "page");
  });

  it("Card with an eyebrow is still named by its title", () => {
    renderWithIntl(
      <Card title="Mismatches" eyebrow="Checks">
        x
      </Card>,
    );
    expect(screen.getByRole("region", { name: "Mismatches" })).toHaveTextContent("Checks");
  });

  it("SearchInput and IconButton have accessible names", () => {
    renderWithIntl(
      <>
        <SearchInput label="Search students" name="q" />
        <IconButton label="Add student" dot>
          +
        </IconButton>
      </>,
    );
    expect(screen.getByRole("searchbox", { name: "Search students" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add student" })).toHaveAttribute("type", "button");
  });

  it("AiPanel is a named region with suggestion links and buttons", async () => {
    const user = userEvent.setup();
    const picked: string[] = [];
    renderWithIntl(
      <AiPanel
        greeting="Good morning"
        eyebrow="Ask the school"
        suggestionsLabel="Try asking"
        suggestions={[
          { id: "a", label: "Fee rules", href: "/ask?q=fees" },
          { id: "b", label: "Transfer certificate steps", onSelect: () => picked.push("b") },
        ]}
      >
        <QuoteBlock label="Suggested reply">Sample text.</QuoteBlock>
      </AiPanel>,
    );
    const panel = screen.getByRole("region", { name: "Good morning" });
    const list = within(panel).getByRole("list", { name: "Try asking" });
    expect(within(list).getByRole("link", { name: "Fee rules" })).toHaveAttribute(
      "href",
      "/ask?q=fees",
    );
    await user.click(within(list).getByRole("button", { name: "Transfer certificate steps" }));
    expect(picked).toEqual(["b"]);
  });
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
      { href: "/imports", label: "Imports" },
      { href: "/imports/history", label: "History", nested: true },
    ],
  },
];

describe("grouped sidebar navigation (the AppShell tests are in shell/AppShell.test.tsx)", () => {
  it("sections are lists named by their headings; one current page with an accent bar", () => {
    path.current = "/students/abc";
    renderWithIntl(<SidebarNav label="Main" sections={SECTIONS} />);
    const nav = screen.getByRole("navigation", { name: "Main" });
    const records = within(nav).getByRole("list", { name: "Records" });
    const students = within(records).getByRole("link", { name: "Students" });
    expect(students).toHaveAttribute("aria-current", "page");
    expect(within(nav).getAllByRole("link", { current: "page" })).toHaveLength(1);
    // The accent bar (decorative) marks the current item only, so the state is not colour
    // alone; the row is also bolder.
    expect(students.querySelectorAll("span.absolute[aria-hidden='true']")).toHaveLength(1);
    expect(students).toHaveClass("font-semibold");
    for (const other of within(nav)
      .getAllByRole("link")
      .filter((link) => link !== students)) {
      expect(other.querySelector("span.absolute[aria-hidden='true']")).toBeNull();
    }
    // Every item has a compact-mode tooltip text equal to its label.
    for (const link of within(nav).getAllByRole("link")) {
      expect(link).toHaveAttribute("data-tooltip", link.textContent);
    }
  });

  it("rows are at least 40px tall (WCAG 2.5.8) and labels wrap instead of clipping", () => {
    path.current = "/";
    renderWithIntl(<SidebarNav label="Main" sections={SECTIONS} />);
    for (const link of screen.getAllByRole("link")) {
      expect(link).toHaveClass("min-h-10");
      const label = link.querySelector("span:not([aria-hidden])");
      expect(label).not.toHaveClass("truncate");
      expect(label).toHaveClass("break-words");
      // In the compact sidebar the label is hidden visually but stays the accessible name.
      expect(label).toHaveClass("collapsed:sr-only");
    }
  });

  it("a sub-entry without its own icon gets the corner arrow, so compact mode shows it", () => {
    path.current = "/imports/history";
    renderWithIntl(<SidebarNav label="Main" sections={SECTIONS} />);
    const history = screen.getByRole("link", { name: "History" });
    expect(history).toHaveAttribute("aria-current", "page");
    expect(history.querySelector("svg")).not.toBeNull();
    expect(screen.getByRole("link", { name: "Imports" })).not.toHaveAttribute("aria-current");
    expect(screen.getByRole("link", { name: "Imports" }).querySelector("svg")).toBeNull();
  });

  it("platform theme: the active marker is the yellow accent on the dark chrome", () => {
    path.current = "/students";
    renderWithIntl(<SidebarNav label="Platform" sections={SECTIONS} theme="platform" />);
    const current = screen.getByRole("link", { current: "page" });
    expect(current).toHaveClass("bg-platform-hover", "text-platform-ink");
    expect(current.querySelector("span.absolute")).toHaveClass("bg-platform-accent");
    expect(screen.getByRole("link", { name: "Home" })).toHaveClass("text-platform-muted");
  });
});
