/** Staff sign-in: a plain link, the BFF route starts the OIDC redirect (as SignedOutView). */
export const SIGN_IN_HREF = "/bff/auth/login";

/** The public pages, in header order. Paths are locale-free (`Link` from @/i18n/navigation). */
export const MARKETING_PAGES = ["features", "security", "pricing", "about"] as const;
export type MarketingPage = (typeof MARKETING_PAGES)[number] | "home";

export const PAGE_HREF: Record<MarketingPage, string> = {
  home: "/welcome",
  features: "/features",
  security: "/security",
  pricing: "/pricing",
  about: "/about",
};

/** Feature sections on /features, in page order (their ids are the anchors). */
export const FEATURE_SECTIONS = [
  "records",
  "checks",
  "corrections",
  "ask",
  "audit",
  "certificates",
] as const;
export type FeatureSection = (typeof FEATURE_SECTIONS)[number];

/** Not built for schools yet (docs/14 M3): always shown with a "Planned" badge. */
export const PLANNED_FEATURES: ReadonlySet<FeatureSection> = new Set(["certificates"]);

/** "Talk to us": a plain mailto link (owner decision: no form, no lead data stored). */
export function mailtoHref(email: string): string {
  return `mailto:${email}`;
}

/**
 * "Ask on WhatsApp": a plain wa.me link with a prefilled greeting (owner decision). `digits`
 * comes from `readMarketingSettings` (validated); the message is fixed UI text and never
 * carries student or other personal data.
 */
export function whatsappHref(digits: string, message: string): string {
  return `https://wa.me/${digits}?text=${encodeURIComponent(message)}`;
}
