import { defineRouting } from "next-intl/routing";

export const locales = ["en", "te"] as const;
export type Locale = (typeof locales)[number];

/** NFR-I18N-001: English and Telugu, locale prefix on every URL (/en/..., /te/...). */
export const routing = defineRouting({
  locales,
  defaultLocale: "en",
  localePrefix: "always",
  // The language toggle persists across visits on shared office PCs (PRD §8).
  // A per-user preference stored server-side replaces this once sessions exist.
  localeCookie: {
    name: "NEXT_LOCALE",
    maxAge: 60 * 60 * 24 * 365,
    sameSite: "lax",
  },
});
