import { LOCALE_COOKIE, type Locale } from "./routing";

/**
 * The language choice lives in the NEXT_LOCALE cookie, never in the URL (product owner
 * 2026-09-30; ADR-0036 note). Client-safe: the language switcher writes it, then refreshes
 * the same prefix-less page, and the proxy renders that page in the stored language (only
 * among switched-on locales: with Telugu off it is English whatever the cookie says).
 */

/** The `document.cookie` string that stores `locale` for a year on the whole site. */
export function localeCookieString(locale: Locale, secure: boolean): string {
  const parts = [
    `${LOCALE_COOKIE.name}=${locale}`,
    "path=/",
    `max-age=${LOCALE_COOKIE.maxAge}`,
    `samesite=${LOCALE_COOKIE.sameSite}`,
  ];
  if (secure) parts.push("secure");
  return parts.join("; ");
}

/** Store the chosen UI language in this browser (not personal data: a language code). */
export function storeLocale(locale: Locale): void {
  document.cookie = localeCookieString(locale, window.location.protocol === "https:");
}
