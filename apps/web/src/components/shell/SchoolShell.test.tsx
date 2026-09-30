import { screen, within } from "@testing-library/react";
import type * as Navigation from "next/navigation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { messages, renderWithIntl } from "@/test/render";
import { SchoolShell } from "./SchoolShell";

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return { ...actual, usePathname: () => "/en" };
});

function navLinks() {
  return within(screen.getByRole("navigation", { name: "Main" }))
    .getAllByRole("link")
    .map((link) => link.textContent);
}

describe("school navigation (UX only; the API checks every call)", () => {
  it("shows data quality, correction requests and support access by permission", () => {
    renderWithIntl(
      <SchoolShell
        permissions={["dq.findings.read", "student.identity_change.approve", "breakglass.approve"]}
      >
        <p>x</p>
      </SchoolShell>,
    );
    expect(navLinks()).toEqual(
      expect.arrayContaining(["Check before submitting", "Correction requests", "Support access"]),
    );
  });

  it("hides them without the permissions (either correction permission is enough)", () => {
    renderWithIntl(
      <SchoolShell permissions={["student.identity_change.request"]}>
        <p>x</p>
      </SchoolShell>,
    );
    const links = navLinks();
    expect(links).toContain("Correction requests");
    expect(links).not.toContain("Check before submitting");
    expect(links).not.toContain("Support access");
  });

  it("shows the Tally items only while the school's connector is on (M6, ADR-0032)", () => {
    const { unmount } = renderWithIntl(
      <SchoolShell permissions={["finance.read", "tally.configure"]}>
        <p>x</p>
      </SchoolShell>,
    );
    expect(navLinks()).not.toContain("Fee dues");
    expect(navLinks()).not.toContain("Tally connector");
    unmount();
    renderWithIntl(
      <SchoolShell permissions={["finance.read", "tally.configure"]} features={{ tally: true }}>
        <p>x</p>
      </SchoolShell>,
    );
    expect(navLinks()).toEqual(expect.arrayContaining(["Fee dues", "Tally connector"]));
  });

  it("hides the Tally items without a Tally permission even when the connector is on", () => {
    renderWithIntl(
      <SchoolShell permissions={["student.read_basic"]} features={{ tally: true }}>
        <p>x</p>
      </SchoolShell>,
    );
    expect(navLinks()).not.toContain("Fee dues");
    expect(navLinks()).not.toContain("Tally connector");
  });

  it("shows the M5 classroom group by permission, and drops a group left empty", () => {
    renderWithIntl(
      <SchoolShell permissions={["attendance.read"]}>
        <p>x</p>
      </SchoolShell>,
    );
    const nav = screen.getByRole("navigation", { name: "Main" });
    const classroom = within(nav).getByRole("list", { name: "Classroom" });
    expect(
      within(classroom)
        .getAllByRole("link")
        .map((link) => link.textContent),
    ).toEqual(["Attendance"]);
    // Nothing in "Ask" for this user: no heading without items.
    expect(within(nav).queryByText("Ask")).toBeNull();
  });
});

describe("school sidebar: school, account and top bar (docs/17 §5.2)", () => {
  beforeEach(() => {
    // The idle warning asks the BFF for the session once; signed out here.
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json({ authenticated: false, kind: "staff" })),
    );
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function sidebar(): HTMLElement {
    const node = document.querySelector<HTMLElement>("[data-sidebar-mode='inline']");
    if (!node) throw new Error("no sidebar");
    return node;
  }

  it("names the active school at the top and offers 'Switch school' only to multi-school users", () => {
    const { unmount } = renderWithIntl(
      <SchoolShell schoolName="Sample Model School" canSwitchSchool>
        <p>x</p>
      </SchoolShell>,
    );
    const top = within(sidebar());
    expect(top.getByText("Current school")).toBeInTheDocument();
    expect(top.getByText("Sample Model School")).toBeInTheDocument();
    const change = top.getByRole("link", { name: "Switch school" });
    expect(change).toHaveAttribute("href", "/en/choose-school");
    // One place only: not in the top bar any more.
    expect(
      within(screen.getByRole("banner")).queryByRole("link", { name: "Switch school" }),
    ).toBeNull();
    unmount();

    renderWithIntl(
      <SchoolShell schoolName="Sample Model School">
        <p>x</p>
      </SchoolShell>,
    );
    expect(screen.queryByRole("link", { name: "Switch school" })).toBeNull();
  });

  it("the account area shows who is signed in, their role and Lock now; the idle warning once", () => {
    renderWithIntl(
      <SchoolShell
        account={{
          kind: "staff",
          displayName: "Sample Clerk",
          roles: ["office_staff", "custom_role_x"],
        }}
      >
        <p>x</p>
      </SchoolShell>,
    );
    const account = within(sidebar()).getByRole("region", { name: "Your account" });
    expect(within(account).getByText("Signed in as Sample Clerk")).toBeInTheDocument();
    // System roles by name; a custom role key is never shown raw.
    expect(within(account).getByText("Office staff")).toBeInTheDocument();
    expect(account).not.toHaveTextContent("custom_role_x");
    expect(within(account).getByRole("button", { name: "Lock now" })).toBeInTheDocument();
    // Nothing duplicated: one account area, the idle warning outside the sidebar.
    expect(screen.getAllByRole("region", { name: "Your account" })).toHaveLength(1);
    const dialogs = Array.from(document.querySelectorAll("dialog"));
    expect(dialogs.filter((dialog) => !dialog.classList.contains("drawer"))).toHaveLength(1);
    expect(sidebar().querySelector("dialog")).toBeNull();
  });

  it("the top bar keeps the bell and the language switch; the sidebar does not repeat them", () => {
    renderWithIntl(
      <SchoolShell topbarActions={<button type="button">Notifications</button>}>
        <p>x</p>
      </SchoolShell>,
      // Telugu switched on explicitly (ADR-0036): the language switch exists only then.
      { telugu: true },
    );
    const bar = within(screen.getByRole("banner"));
    expect(bar.getByRole("button", { name: "Notifications" })).toBeInTheDocument();
    expect(bar.getByRole("navigation", { name: "Language" })).toBeInTheDocument();
    expect(within(sidebar()).queryByRole("navigation", { name: "Language" })).toBeNull();
    expect(screen.getAllByRole("navigation", { name: "Language" })).toHaveLength(1);
  });

  it("Telugu: menu, headings and account in Telugu; long labels wrap, never truncate", () => {
    renderWithIntl(
      <SchoolShell
        permissions={["user.manage", "dq.findings.read", "attendance.read"]}
        account={{ kind: "staff", displayName: "Sample Clerk", roles: ["principal"] }}
        schoolName="Sample Model School"
      >
        <p>x</p>
      </SchoolShell>,
      "te",
    );
    const te = messages.te;
    const nav = within(sidebar()).getByRole("navigation", { name: te.school.nav.label });
    expect(within(nav).getByRole("link", { name: te.school.nav.users })).toBeInTheDocument();
    expect(within(nav).getByRole("link", { name: te.attendance.nav })).toBeInTheDocument();
    expect(
      within(nav).getByRole("list", { name: te.school.nav.sections.checks }),
    ).toBeInTheDocument();
    expect(within(sidebar()).getByText(te.shell.currentSchool)).toBeInTheDocument();
    const account = within(sidebar()).getByRole("region", { name: te.shell.account });
    expect(within(account).getByText(te.school.users.roles.principal)).toBeInTheDocument();
    expect(within(sidebar()).getByRole("button", { name: te.shell.collapse })).toBeInTheDocument();
    expect(sidebar().querySelector(".truncate")).toBeNull();
  });
});
