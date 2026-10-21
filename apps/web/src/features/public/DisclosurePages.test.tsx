import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { PublicDisclosure } from "@/server/public/disclosure";
import { DisclosureBusy, DisclosureNotFound, DisclosurePage } from "./DisclosurePages";

/**
 * The CBSE disclosure page on the public origin (R3-1C; ADR-0051; FR-DISC-009; docs/17 §5.10).
 * Synthetic school only.
 */

const PAGE: PublicDisclosure = {
  school_name: "Sri Venkateswara Synthetic Residential English Medium High School",
  title: "Mandatory public disclosure (CBSE Appendix IX)",
  published_at: "2026-10-21T05:00:00Z",
  format_version: 1,
  sections: [
    {
      key: "general",
      title: "A. General information",
      items: [
        { key: "affiliation_no", label: "Affiliation number", value: "1130001", documents: [] },
        { key: "school_code", label: "School code", documents: [] },
      ],
    },
    {
      key: "academics",
      title: "C. Results and academics",
      items: [
        {
          key: "board_results",
          label: "Board results",
          documents: [],
          table: {
            columns: ["Year", "Class", "Appeared", "Passed", "Pass percentage"],
            rows: [["2025-26", "Class X", "fewer than 10", "fewer than 10", ""]],
          },
        },
        {
          key: "fire_safety",
          label: "Fire safety certificate",
          documents: [{ n: 1, title: "Fire NOC 2026" }],
        },
        {
          key: "infrastructure_video",
          label: "Video",
          documents: [],
          link: "https://video.example/tour",
        },
      ],
    },
  ],
};

describe("disclosure page (FR-DISC-009)", () => {
  it("shows the school, the sections, documents and the published line", () => {
    const { container } = render(<DisclosurePage disclosure={PAGE} docBase="/d/KEY" />);
    expect(
      screen.getByRole("heading", { level: 1, name: /Mandatory public disclosure/ }),
    ).toBeTruthy();
    expect(screen.getByRole("heading", { level: 2, name: "A. General information" })).toBeTruthy();
    expect(screen.getByText("1130001")).toBeTruthy();
    expect(screen.getByText("Not provided")).toBeTruthy();
    expect(screen.getAllByText("fewer than 10")).toHaveLength(2);
    const doc = screen.getByRole("link", { name: /Fire NOC 2026/ });
    expect(doc.getAttribute("href")).toBe("/d/KEY/documents/1");
    const video = screen.getByRole("link", { name: "https://video.example/tour" });
    expect(video.getAttribute("rel")).toContain("noreferrer");
    expect(screen.getAllByText(/Published by .* on 21\/10\/2026\./)).toHaveLength(2);
    expect(screen.getByText("Prepared with SchoolOS.")).toBeTruthy();
    // The public CSP allows no inline styles and no scripts.
    expect(container.querySelector("[style]")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
  });

  it("has one not found and one busy page", () => {
    render(<DisclosureNotFound />);
    expect(screen.getByRole("heading", { name: "Page not found" })).toBeTruthy();
    render(<DisclosureBusy />);
    expect(screen.getByRole("heading", { name: "Please try again in a few minutes" })).toBeTruthy();
  });
});
