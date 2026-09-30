import { defineRouting } from "next-intl/routing";

/**
 * Every UI language the app has. Which of them are switched on is decided at run time by
 * `SOS_TELUGU_ENABLED` in ./languages.ts (ADR-0036: English first, Telugu hidden while off).
 */
export const locales = ["en", "te"] as const;
export type Locale = (typeof locales)[number];

const common = {
  defaultLocale: "en",
  localePrefix: "always",
  // The language toggle persists across visits on shared office PCs (PRD §8).
  // A per-user preference stored server-side replaces this once sessions exist.
  localeCookie: {
    name: "NEXT_LOCALE",
    maxAge: 60 * 60 * 24 * 365,
    sameSite: "lax",
  },
} as const;

/** NFR-I18N-001: English and Telugu, locale prefix on every URL (/en/..., /te/...). */
export const routing = defineRouting({ locales, ...common });

/**
 * ADR-0036: the routing the proxy uses while Telugu is switched off. Only English is
 * negotiated (Accept-Language and a stored `te` cookie are ignored) and alternate links
 * name English only.
 */
export const englishRouting = defineRouting({ locales: ["en"] as const, ...common });
