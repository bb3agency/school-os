import { screen, within } from "@testing-library/react";
import type * as Navigation from "next/navigation";
import { describe, expect, it, vi } from "vitest";
import type { Locale } from "@/i18n/routing";
import { intlErrors, messages, renderWithIntl } from "@/test/render";

let pathname = "/en/welcome";

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => pathname,
    useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
    useSearchParams: () => new URLSearchParams(),
    useParams: () => ({ locale: "en" }),
  };
});

import SignedOutPage from "./[locale]/signed-out/page";
import WelcomePage from "./[locale]/welcome/page";

/**
 * Public welcome page (FR-IAM-001 entry point, NFR-I18N-001, NFR-A11Y-001): renders without a
 * session in both languages, with landmarks, heading order, sign-in links and native FAQ
 * disclosures, and no inline styles or external resources (SEC-010 CSP).
 */

const SECTIONS = ["features", "how", "security", "plans", "faq"] as const;

function render(locale: Locale, telugu = locale === "te") {
  pathname = `/${locale}/welcome`;
  return renderWithIntl(<WelcomePage />, { locale, telugu });
}

describe("welcome page (public product page)", () => {
  for (const locale of ["en", "te"] as const) {
    const m = messages[locale].welcome;

    it(`renders every section with no missing keys [${locale}]`, () => {
      render(locale);
      expect(intlErrors).toEqual([]);
      expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(m.hero.headline);
      const h2 = screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent);
      expect(h2).toEqual([
        m.features.title,
        m.how.title,
        m.security.title,
        m.plans.title,
        m.faq.title,
        m.cta.title,
      ]);
      // Six feature cards, three steps, six security points, two plans.
      const region = (name: string) => screen.getByRole("region", { name });
      expect(within(region(m.features.title)).getAllByRole("heading", { level: 3 })).toHaveLength(
        6,
      );
      expect(within(region(m.how.title)).getAllByRole("heading", { level: 3 })).toHaveLength(3);
      expect(within(region(m.security.title)).getAllByRole("heading", { level: 3 })).toHaveLength(
        6,
      );
      expect(
        within(region(m.plans.title))
          .getAllByRole("heading", { level: 3 })
          .map((h) => h.textContent),
      ).toEqual([m.plans.shared.name, m.plans.dedicated.name]);
    });

    it(`sign-in links go to staff sign-in; in-page links reach their sections [${locale}]`, () => {
      const { container } = render(locale);
      for (const name of [m.signIn, m.cta.button]) {
        for (const link of screen.getAllByRole("link", { name })) {
          expect(link).toHaveAttribute("href", "/bff/auth/login");
        }
      }
      expect(screen.getByRole("link", { name: m.hero.secondary })).toHaveAttribute("href", "#how");
      const nav = screen.getByRole("navigation", { name: m.nav.label });
      const hrefs = within(nav)
        .getAllByRole("link")
        .map((link) => link.getAttribute("href"));
      expect(hrefs).toEqual(SECTIONS.map((id) => `#${id}`));
      for (const id of SECTIONS) {
        expect(container.querySelector(`section#${id}`), id).not.toBeNull();
      }
    });
  }

  it("has landmarks, a skip link first, and a language switch that keeps the page", () => {
    // Telugu switched on explicitly (ADR-0036): the language switch exists only then.
    const { container } = render("en", true);
    const first = container.querySelector("a");
    expect(first).toHaveTextContent(messages.en.common.skipToContent);
    expect(first).toHaveAttribute("href", "#main");
    expect(screen.getByRole("main")).toHaveAttribute("id", "main");
    expect(screen.getByRole("banner")).toBeInTheDocument();
    expect(screen.getByRole("contentinfo")).toBeInTheDocument();
    const language = screen.getByRole("navigation", { name: messages.en.language.label });
    expect(within(language).getByRole("link", { name: "తెలుగు" })).toHaveAttribute(
      "href",
      "/te/welcome",
    );
  });

  it("with Telugu switched off: no language switch and no Telugu anywhere (ADR-0036)", () => {
    const { container } = render("en");
    expect(intlErrors).toEqual([]);
    expect(screen.queryByRole("navigation", { name: messages.en.language.label })).toBeNull();
    expect(container.querySelector("[hreflang='te'], [lang='te'], a[href^='/te']")).toBeNull();
    expect(container.textContent ?? "").not.toMatch(/[\u0C00-\u0C7F]|Telugu/);
    expect(screen.getByText(messages.englishOnly.welcome.faq.language.a)).toBeInTheDocument();
  });

  it("the FAQ uses native disclosures, closed by default", () => {
    const { container } = render("en");
    const details = [...container.querySelectorAll("section#faq details")];
    expect(details).toHaveLength(7);
    for (const item of details) {
      expect(item.firstElementChild?.tagName).toBe("SUMMARY");
      expect(item).not.toHaveAttribute("open");
      // Nothing interactive inside a summary (it is the button).
      expect(item.querySelector("summary a, summary button")).toBeNull();
    }
    expect(details[0]?.querySelector("summary")).toHaveTextContent(
      messages.en.welcome.faq.portal.q,
    );
  });

  it("the product visual is sample data, hidden from screen readers with a text description", () => {
    const { container } = render("en");
    const figure = container.querySelector("figure");
    expect(figure?.querySelector("figcaption")).toHaveTextContent(
      messages.en.welcome.visual.description,
    );
    const mock = figure?.querySelector('[aria-hidden="true"]');
    expect(mock).toHaveTextContent(messages.en.welcome.visual.sample);
    expect(mock?.querySelector("a, button, input, [tabindex]")).toBeNull();
  });

  it("marks features that are not built yet as planned", () => {
    render("en");
    const card = screen
      .getByRole("heading", { level: 3, name: messages.en.welcome.features.certificates.title })
      .closest("li");
    expect(card).toHaveTextContent(messages.en.welcome.features.planned);
  });

  it("is CSP-safe and self-contained: no inline styles, images, scripts or external links", () => {
    const { container } = render("en");
    expect(container.querySelectorAll("[style]")).toHaveLength(0);
    expect(container.querySelectorAll("img, script, iframe, link")).toHaveLength(0);
    for (const link of container.querySelectorAll("a")) {
      expect(link.getAttribute("href") ?? "").toMatch(/^(#|\/)/);
    }
  });

  it("makes no pricing, rating or customer-count claims", () => {
    for (const locale of ["en", "te"] as const) {
      const text = JSON.stringify(messages[locale].welcome);
      expect(text, locale).not.toMatch(/₹|\bRs\.?\s|INR|\bper (month|year)\b|%|★|testimonial/i);
      expect(text, locale).not.toMatch(/\b\d{2,}\+? (schools|students|users)\b/i);
    }
  });
});

describe("signed-out page links to the public welcome page", () => {
  it.each(["en", "te"] as const)("[%s]", async (locale) => {
    pathname = `/${locale}/signed-out`;
    renderWithIntl(await SignedOutPage({ searchParams: Promise.resolve({}) }), locale);
    // The existing sign-in button is still there.
    expect(
      screen.getByRole("link", { name: messages[locale].auth.signedOut.signIn }),
    ).toHaveAttribute("href", "/bff/auth/login");
    // useLocale() comes from the provider, so the link keeps the page's language.
    expect(
      screen.getByRole("link", { name: messages[locale].auth.signedOut.homeLink }),
    ).toHaveAttribute("href", `/${locale}/welcome`);
  });
});
