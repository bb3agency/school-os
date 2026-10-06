import { fireEvent, screen } from "@testing-library/react";
import type * as Navigation from "next/navigation";
import { describe, expect, it, vi } from "vitest";
import { renderWithIntl } from "@/test/render";
import { CitationChip } from "./Citations";

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return { ...actual, usePathname: () => "/ask" };
});

/** Citations on touch screens (docs/17 §5.3, §5.7; FR-KB-012): first tap previews. */
const CITATION = {
  index: 1,
  source: "sos://doc/0192f3a4-0000-7000-8000-00000000d001/v1#p1",
  title: "Synthetic admission guide",
  snippet: "Synthetic passage.",
};

function chip() {
  renderWithIntl(<CitationChip citation={CITATION} prefix="a1" />);
  return screen.getByRole("link", { name: /Synthetic admission guide/ });
}

describe("CitationChip on touch", () => {
  it("the first tap opens the preview instead of jumping; the second follows the link", () => {
    const link = chip();
    fireEvent.pointerDown(link, { pointerType: "touch" });
    fireEvent.focus(link);
    const first = fireEvent.click(link);
    expect(first).toBe(false); // default prevented: no jump yet
    expect(link).toHaveAttribute("aria-describedby");

    fireEvent.pointerDown(link, { pointerType: "touch" });
    const second = fireEvent.click(link);
    expect(second).toBe(true); // the link is followed to the source card
    expect(link).not.toHaveAttribute("aria-describedby");
  });

  it("touch enter and leave do not open or close it; a tap outside closes it", () => {
    const link = chip();
    fireEvent.pointerEnter(link, { pointerType: "touch" });
    expect(link).not.toHaveAttribute("aria-describedby");
    fireEvent.pointerDown(link, { pointerType: "touch" });
    fireEvent.click(link);
    fireEvent.pointerLeave(link, { pointerType: "touch" });
    expect(link).toHaveAttribute("aria-describedby");
    fireEvent.pointerDown(document.body, { pointerType: "touch" });
    expect(link).not.toHaveAttribute("aria-describedby");
  });

  it("a mouse still opens it on hover and keyboard focus still opens it", () => {
    const link = chip();
    fireEvent.pointerEnter(link, { pointerType: "mouse" });
    expect(link).toHaveAttribute("aria-describedby");
    fireEvent.pointerDown(document.body, { pointerType: "mouse" });
    expect(link).not.toHaveAttribute("aria-describedby");
    fireEvent.focus(link);
    expect(link).toHaveAttribute("aria-describedby");
  });
});
