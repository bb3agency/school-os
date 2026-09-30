import { screen, within } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type * as Navigation from "next/navigation";
import type { ReactElement } from "react";
import { renderToString } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { readMarketingSettings } from "@/features/marketing/settings";
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

import AboutPage from "./[locale]/about/page";
import FeaturesPage from "./[locale]/features/page";
import PricingPage from "./[locale]/pricing/page";
import SecurityPage from "./[locale]/security/page";
import SignedOutPage from "./[locale]/signed-out/page";
import WelcomePage from "./[locale]/welcome/page";

/**
 * Public marketing pages (FR-IAM-001 public entry, NFR-A11Y-001, NFR-I18N-001, SEC-010):
 * home, features, security, pricing and about render without a session, with one h1,
 * landmarks and the right calls to action; contact details only when configured; 404 on a
 * dedicated host; CSP-safe (no style attributes, no external resources) and honest (no prices,
 * ratings, testimonials or counts).
 */

const m = messages.en.marketing;
const PAGES = {
  welcome: { Page: WelcomePage, h1: `${m.home.headlineLead} ${m.home.headlineAccent}` },
  features: { Page: FeaturesPage, h1: m.features.headline },
  security: { Page: SecurityPage, h1: m.security.headline },
  pricing: { Page: PricingPage, h1: m.pricing.headline },
  about: { Page: AboutPage, h1: m.about.headline },
} as const;
type PageName = keyof typeof PAGES;
const NAMES = Object.keys(PAGES) as PageName[];

/** The href without the locale prefix (links are locale-free paths; the prefix may go). */
const bare = (href: string | null) => (href ?? "").replace(/^\/(en|te)(?=\/|$)/, "") || "/";

function render(name: PageName) {
  pathname = `/en/${name}`;
  const { Page } = PAGES[name];
  return renderWithIntl(Page() as ReactElement);
}

const CONTACT = {
  SOS_PUBLIC_CONTACT_EMAIL: "hello@schoolos.example",
  SOS_PUBLIC_COMPANY_NAME: "Sample Company Private Limited",
  SOS_PUBLIC_COMPANY_ADDRESS: "1 Sample Road|Vijayawada 520001",
};
function withContact() {
  for (const [key, value] of Object.entries(CONTACT)) vi.stubEnv(key, value);
}

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("marketing pages render with structure and calls to action (FR-IAM-001)", () => {
  for (const name of NAMES) {
    it(`${name}: one h1, landmarks, skip link first, header links to every page`, () => {
      const { container } = render(name);
      expect(intlErrors).toEqual([]);
      const h1 = screen.getAllByRole("heading", { level: 1 });
      expect(h1).toHaveLength(1);
      expect(h1[0]).toHaveTextContent(PAGES[name].h1);
      const first = container.querySelector("a");
      expect(first).toHaveTextContent(messages.en.common.skipToContent);
      expect(first).toHaveAttribute("href", "#main");
      expect(screen.getByRole("main")).toHaveAttribute("id", "main");
      const banner = screen.getByRole("banner");
      expect(screen.getByRole("contentinfo")).toBeInTheDocument();
      const nav = within(banner).getByRole("navigation", { name: m.nav.label });
      expect(
        within(nav)
          .getAllByRole("link")
          .map((a) => bare(a.getAttribute("href"))),
      ).toEqual(["/features", "/security", "/pricing", "/about"]);
      // The current page is marked (never the others).
      const current = container.querySelectorAll('[aria-current="page"]');
      expect(current).toHaveLength(1);
      expect(bare(current[0]!.getAttribute("href"))).toBe(
        name === "welcome" ? "/welcome" : `/${name}`,
      );
      // "Sign in" for existing schools goes to the staff sign-in.
      for (const link of screen.getAllByRole("link", { name: m.cta.signIn })) {
        expect(link).toHaveAttribute("href", "/bff/auth/login");
      }
      // Heading levels never skip (h1 → h2 → h3).
      const levels = [...container.querySelectorAll("h1, h2, h3, h4")].map((h) =>
        Number(h.tagName[1]),
      );
      levels.forEach((level, index) => {
        if (index > 0)
          expect(level - levels[index - 1]!, `${name} heading ${index}`).toBeLessThanOrEqual(1);
      });
    });

    it(`${name}: without contact settings there is no "Talk to us", no email and no company`, () => {
      const { container } = render(name);
      expect(screen.queryByRole("link", { name: new RegExp(m.cta.talk) })).toBeNull();
      expect(container.querySelector('a[href^="mailto:"]')).toBeNull();
      expect(container.textContent).not.toMatch(/@|©/);
    });

    it(`${name}: with contact settings "Talk to us" is a mailto link`, () => {
      withContact();
      const { container } = render(name);
      const talk = screen.getAllByRole("link", { name: new RegExp(`^${m.cta.talk}`) });
      expect(talk.length).toBeGreaterThan(0);
      for (const link of talk) {
        expect(link).toHaveAttribute("href", `mailto:${CONTACT.SOS_PUBLIC_CONTACT_EMAIL}`);
      }
      expect(container.textContent).toContain(CONTACT.SOS_PUBLIC_COMPANY_NAME);
    });

    it(`${name}: CSP-safe and self-contained (SEC-010)`, () => {
      withContact();
      const { container } = render(name);
      expect(container.querySelectorAll("[style]")).toHaveLength(0);
      expect(container.querySelectorAll("img, script, iframe, link, video, object")).toHaveLength(
        0,
      );
      for (const link of container.querySelectorAll("a")) {
        expect(link.getAttribute("href") ?? "").toMatch(/^(#|\/(?!\/)|mailto:)/);
      }
      // Illustrations are decorative with a text caption, never interactive.
      for (const figure of container.querySelectorAll("figure")) {
        expect(figure.querySelector("figcaption")?.textContent?.trim()).not.toBe("");
        expect(
          figure.querySelector('[aria-hidden="true"] a, [aria-hidden="true"] button'),
        ).toBeNull();
      }
    });

    it(`${name}: the server HTML has no style attribute, so the nonce CSP blocks nothing`, () => {
      withContact();
      const { Page } = PAGES[name];
      const html = renderToString(
        <NextIntlClientProvider locale="en" messages={messages.englishOnly} timeZone="Asia/Kolkata">
          {Page() as ReactElement}
        </NextIntlClientProvider>,
      );
      expect(html).not.toMatch(/\sstyle="/);
      expect(html).toContain('id="main"');
    });
  }

  it("the home page shows the four sources, how it works, the plans and a closed FAQ", () => {
    const { container } = render("welcome");
    const sources = screen.getByRole("region", { name: m.home.sourcesLabel });
    for (const source of Object.values(m.home.sources)) {
      expect(within(sources).getByText(source)).toBeInTheDocument();
    }
    const h2 = screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent);
    expect(h2).toEqual(
      expect.arrayContaining([
        m.home.how.title,
        m.home.highlights.title,
        m.home.security.title,
        m.home.plans.title,
        m.home.faq.title,
        m.home.closing.title,
      ]),
    );
    const details = [...container.querySelectorAll("details")];
    expect(details).toHaveLength(5);
    for (const item of details) {
      expect(item.firstElementChild?.tagName).toBe("SUMMARY");
      expect(item).not.toHaveAttribute("open");
    }
    // Every feature tile links to its section on /features.
    const tiles = within(
      screen.getByRole("region", { name: m.home.highlights.title }),
    ).getAllByRole("link");
    expect(tiles.map((a) => bare(a.getAttribute("href")))).toEqual([
      "/features#records",
      "/features#checks",
      "/features#corrections",
      "/features#ask",
      "/features#audit",
      "/features#certificates",
    ]);
    // Without a contact address, "See how it works" leads to the steps.
    expect(screen.getByRole("link", { name: m.home.secondary })).toHaveAttribute("href", "#how");
    expect(container.querySelector("section#how")).not.toBeNull();
  });

  it("the hero illustration is sample data, described for screen readers", () => {
    const { container } = render("welcome");
    const figure = container.querySelector("figure");
    expect(figure?.querySelector("figcaption")).toHaveTextContent(m.mock.hero);
    expect(figure?.querySelector('[aria-hidden="true"]')).toHaveTextContent(m.sample);
  });

  it("certificates are marked Planned wherever they appear (docs/14)", () => {
    render("welcome");
    const tile = screen
      .getByRole("heading", { level: 3, name: m.home.highlights.certificates.title })
      .closest("a");
    expect(tile).toHaveTextContent(m.planned);
    render("features");
    const section = screen.getByRole("region", { name: m.features.certificates.title });
    expect(section).toHaveTextContent(m.planned);
  });

  it("features: one anchored section per feature, with a jump list", () => {
    const { container } = render("features");
    const jump = screen.getByRole("navigation", { name: m.features.jumpLabel });
    const ids = within(jump)
      .getAllByRole("link")
      .map((a) => a.getAttribute("href"));
    expect(ids).toEqual(["#records", "#checks", "#corrections", "#ask", "#audit", "#certificates"]);
    for (const id of ids) {
      expect(container.querySelector(`section${id ?? ""}`), id ?? "").not.toBeNull();
    }
  });

  it("pricing: two plans and a comparison table, never a price", () => {
    render("pricing");
    expect(
      screen.getByRole("heading", { level: 2, name: m.pricing.shared.name }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { level: 2, name: m.pricing.dedicated.name }),
    ).toBeInTheDocument();
    const table = screen.getByRole("table", { name: m.pricing.compare.caption });
    expect(within(table).getAllByRole("row")).toHaveLength(7);
    expect(within(table).getByText(m.pricing.compare.domainDedicated)).toBeInTheDocument();
  });

  it("about: the contact block appears only with settings, and shows each set line", () => {
    render("about");
    expect(screen.queryByRole("heading", { name: m.about.contact.title })).toBeNull();
    withContact();
    render("about");
    const contact = screen.getByRole("region", { name: m.about.contact.title });
    expect(contact).toHaveTextContent(CONTACT.SOS_PUBLIC_COMPANY_NAME);
    expect(within(contact).getByText("1 Sample Road")).toBeInTheDocument();
    expect(within(contact).getByText("Vijayawada 520001")).toBeInTheDocument();
    expect(
      within(contact).getByRole("link", { name: CONTACT.SOS_PUBLIC_CONTACT_EMAIL }),
    ).toHaveAttribute("href", `mailto:${CONTACT.SOS_PUBLIC_CONTACT_EMAIL}`);
  });

  it("with Telugu off: English only, no language switch (ADR-0036)", () => {
    for (const name of NAMES) {
      const { container, unmount } = render(name);
      expect(container.textContent ?? "", name).not.toMatch(/[ఀ-౿]|Telugu/);
      expect(screen.queryByRole("navigation", { name: messages.en.language.label })).toBeNull();
      unmount();
    }
  });
});

describe("dedicated host: no marketing, a plain sign-in (docs/17 §5.5)", () => {
  it.each(["features", "security", "pricing", "about"] as const)("/%s answers 404", (name) => {
    vi.stubEnv("SOS_DEPLOYMENT_MODE", "dedicated");
    withContact();
    expect(() => PAGES[name].Page()).toThrow(/NEXT_HTTP_ERROR_FALLBACK;404/);
  });

  it("/welcome is a branded sign-in page with no marketing links or contact", () => {
    vi.stubEnv("SOS_DEPLOYMENT_MODE", "dedicated");
    withContact();
    const { container } = render("welcome");
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(m.dedicated.headline);
    const links = screen.getAllByRole("link");
    expect(links.map((a) => a.getAttribute("href"))).toEqual(["/bff/auth/login"]);
    expect(container.textContent).not.toContain(CONTACT.SOS_PUBLIC_CONTACT_EMAIL);
    expect(container.querySelectorAll("[style]")).toHaveLength(0);
  });
});

describe("marketing settings (SOS_PUBLIC_*)", () => {
  it("unset or blank values show nothing", () => {
    expect(readMarketingSettings({})).toEqual({
      contactEmail: null,
      companyName: null,
      companyAddress: null,
    });
    expect(
      readMarketingSettings({
        SOS_PUBLIC_CONTACT_EMAIL: "  ",
        SOS_PUBLIC_COMPANY_NAME: " ",
        SOS_PUBLIC_COMPANY_ADDRESS: "|",
      }),
    ).toEqual({ contactEmail: null, companyName: null, companyAddress: null });
  });

  it("an address that could break out of the mailto link is refused", () => {
    for (const bad of [
      "not-an-email",
      "a@b",
      "x@y.com?subject=hi",
      "a b@c.com",
      "a@b.com,c@d.com",
      "javascript:alert(1)@x.com",
    ]) {
      expect(readMarketingSettings({ SOS_PUBLIC_CONTACT_EMAIL: bad }).contactEmail, bad).toBeNull();
    }
    expect(
      readMarketingSettings({ SOS_PUBLIC_CONTACT_EMAIL: " hello@schoolos.example " }).contactEmail,
    ).toBe("hello@schoolos.example");
  });

  it("the address splits into lines on | or \\n", () => {
    expect(
      readMarketingSettings({ SOS_PUBLIC_COMPANY_ADDRESS: "Line 1\\nLine 2| Line 3 " })
        .companyAddress,
    ).toEqual(["Line 1", "Line 2", "Line 3"]);
  });
});

describe("honest claims (docs/01, 07, 08, 14, 16)", () => {
  it("the marketing copy has no prices, ratings, testimonials, customer counts or certifications", () => {
    const text = JSON.stringify(messages.en.marketing);
    expect(text).not.toMatch(/₹|\bRs\.?\s|\bINR\b|\$\d|\bper (month|year)\b|\/(mo|month|yr)\b|%/i);
    expect(text).not.toMatch(/★|\brating|testimonial|trusted by|customers? (love|say)|\bloved\b/i);
    expect(text).not.toMatch(/\b\d[\d,]*\+? (schools|students|users|teachers|customers)\b/i);
    expect(text).not.toMatch(/\bISO ?27001|SOC ?2|certified|HIPAA|GDPR compliant/i);
    expect(text).not.toMatch(/Telugu/);
  });
});

describe("the signed-out page links to the public home page", () => {
  it("keeps its SchoolOS home link", async () => {
    pathname = "/en/signed-out";
    renderWithIntl(await SignedOutPage({ searchParams: Promise.resolve({}) }));
    expect(screen.getByRole("link", { name: messages.en.auth.signedOut.signIn })).toHaveAttribute(
      "href",
      "/bff/auth/login",
    );
    expect(
      bare(
        screen
          .getByRole("link", { name: messages.en.auth.signedOut.homeLink })
          .getAttribute("href"),
      ),
    ).toBe("/welcome");
  });
});
