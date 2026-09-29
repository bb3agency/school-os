import { screen, within } from "@testing-library/react";
import type * as Navigation from "next/navigation";
import { describe, expect, it, vi } from "vitest";
import { renderWithIntl } from "@/test/render";
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
});
