import { defineRouting } from "next-intl/routing";

/**
 * Every UI language the app has. Which of them are switched on is decided at run time by
 * `SOS_TELUGU_ENABLED` in ./languages.ts (ADR-0036: English first, Telugu hidden while off).
 */
export const locales = ["en", "te"] as const;
export type Locale = (typeof locales)[number];

/**
 * The cookie that carries the chosen UI language (set by the language switcher, and by the
 * proxy when an old `/te/...` link is followed while Telugu is on). The choice persists across
 * visits on shared office PCs (PRD §8).
 */
export const LOCALE_COOKIE = {
  name: "NEXT_LOCALE",
  maxAge: 60 * 60 * 24 * 365,
  sameSite: "lax",
} as const;

/**
 * No URL carries a locale, in any language (product owner, 2026-09-30; ADR-0036 note): every
 * page is `/`, `/students`, `/platform/schools`, … The proxy rewrites internally to
 * `app/[locale]/...` with the language taken from the NEXT_LOCALE cookie, then
 * Accept-Language, among the switched-on locales only. Old `/en/...` and `/te/...` URLs
 * answer 308 to the same path without the prefix (src/proxy.ts).
 */
const common = {
  defaultLocale: "en",
  localePrefix: "never",
} as const;

/** NFR-I18N-001: English and Telugu, no locale prefix (language from the cookie). */
export const routing = defineRouting({ locales, ...common, localeCookie: LOCALE_COOKIE });

/**
 * ADR-0036: the routing the proxy uses while Telugu is switched off. Only English is
 * negotiated (Accept-Language and a stored `te` cookie are ignored), and the proxy never
 * writes the language cookie, so a stored choice is left as it is for when Telugu returns.
 */
export const englishRouting = defineRouting({
  locales: ["en"] as const,
  ...common,
  localeCookie: false,
});
