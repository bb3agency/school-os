import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { BusyLink, ExpiredLink, LinkFrame, type LinkEnvelope } from "./PublicPages";

/** Parent page shells (ADR-0044, ADR-0046; FR-PUB-008; docs/17 §5.8). Synthetic school. */

const TELUGU = /[ఀ-౿]/;
const base: LinkEnvelope = {
  kind: "parent_notice",
  expires_at: "2026-11-16T10:00:00+00:00",
  language: "en",
  school_name: "Synthetic Model School",
};

describe("parent page shell", () => {
  it("shows the school, the kind, the expiry and the forwarding warning in English", () => {
    const { container } = render(<LinkFrame envelope={base} />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Notice from the school");
    expect(container.textContent).toContain("Synthetic Model School");
    expect(container.textContent).toContain("16/11/2026");
    expect(container.textContent).toContain("Do not forward it.");
    expect(container.textContent).toContain("sent through SchoolOS");
    expect(TELUGU.test(container.textContent ?? "")).toBe(false);
    expect(container.querySelector('link[rel="stylesheet"]')).toBeNull();
    expect(container.querySelector("main")?.getAttribute("lang")).toBe("en");
  });

  it("is Telugu, with the self-hosted Telugu font, when the parent language is te", () => {
    const { container } = render(
      <LinkFrame envelope={{ ...base, kind: "parent_receipt", language: "te" }} />,
    );
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("ఫీజు రసీదు");
    expect(container.querySelector("main")?.getAttribute("lang")).toBe("te");
    const font = document.querySelector('link[rel="stylesheet"]');
    expect(font?.getAttribute("href")).toBe("/p/fonts/telugu/noto-sans-telugu.css");
  });

  it("the expired and busy pages name no school", () => {
    const expired = render(<ExpiredLink />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
      "This link has expired or is not valid",
    );
    expect(expired.container.textContent).not.toContain("Synthetic");
    expired.unmount();
    render(<BusyLink />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("try again");
  });
});
