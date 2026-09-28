import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SignedOutView } from "@/features/auth/SignedOutView";
import { renderWithIntl } from "@/test/render";
import { SupportAccessBanner } from "./SupportAccessBanner";

describe("SchoolOS support access in the school app (ADR-0023, SEC-021)", () => {
  it("shows a read-only, audited banner in English and Telugu", () => {
    const { unmount } = renderWithIntl(<SupportAccessBanner />, "en");
    const region = screen.getByRole("region", { name: "SchoolOS support access (read only)" });
    expect(region).toHaveTextContent("every page you open is recorded in the school's audit log");
    unmount();
    renderWithIntl(<SupportAccessBanner />, "te");
    expect(
      screen.getByRole("region", { name: "SchoolOS సపోర్ట్ యాక్సెస్ (చూడటానికి మాత్రమే)" }),
    ).toBeInTheDocument();
  });

  it("signed-out page for an ended support session links back to the admin panel only", () => {
    renderWithIntl(
      <SignedOutView operator={false} support idle={false} error="support_ended" devSignIn />,
      "en",
    );
    expect(screen.getByRole("alert")).toHaveTextContent("This support access has ended");
    const back = screen.getByRole("link", { name: "Back to break-glass requests" });
    expect(back).toHaveAttribute("href", "/en/platform/break-glass");
    expect(screen.queryByRole("link", { name: /Sign in to the school office/ })).toBeNull();
    const hrefs = screen.getAllByRole("link").map((link) => link.getAttribute("href") ?? "");
    expect(hrefs.some((href) => href.startsWith("/bff/auth/") || href.includes("/dev/"))).toBe(
      false,
    );
  });

  it("explains a refused support sign-in in Telugu", () => {
    renderWithIntl(
      <SignedOutView operator={false} support idle={false} error="support_not_allowed" />,
      "te",
    );
    expect(screen.getByRole("alert")).toHaveTextContent("సపోర్ట్ యాక్సెస్ తెరవలేకపోయాం");
  });
});
