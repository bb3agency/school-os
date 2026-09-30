import { locales, type Locale } from "./routing";

/**
 * Which UI languages the web app shows (ADR-0036: English first, Telugu hidden).
 *
 * One switch, the same as the API's: `SOS_TELUGU_ENABLED` (default false). It is read here
 * and nowhere else, at run time on the server (the proxy, layouts, pages, route handlers and
 * the BFF), so one image serves both settings and the value never reaches browser bundles.
 * Client components get the answer from `LanguagesProvider` (`useTeluguEnabled()`), which
 * the locale layout fills from `enabledLocales()`.
 *
 * With the switch off: no language switcher, `/te/...` redirects to `/en/...`, the Telugu
 * catalog is never loaded, English strings that mention Telugu use the overrides in
 * `messages/en.telugu-off.json`, no Telugu field, column, preview or option is shown, and no
 * Telugu font is loaded. With it on, everything works as before (NFR-I18N-001).
 */

export const ENGLISH = "en" satisfies Locale;
export const TELUGU = "te" satisfies Locale;

/** The environment variable, shared with the API (`app.core.languages`). */
export const TELUGU_SWITCH = "SOS_TELUGU_ENABLED";

type Env = Readonly<Record<string, string | undefined>>;

// The same truthy spellings as the API's settings (pydantic booleans).
const TRUE_VALUES = new Set(["1", "true", "t", "yes", "y", "on"]);

/** True only when Telugu has been switched back on (server-side run-time setting). */
export function teluguEnabled(env: Env = process.env): boolean {
  return TRUE_VALUES.has((env[TELUGU_SWITCH] ?? "").trim().toLowerCase());
}

/** The UI languages people may use, English first. */
export function enabledLocales(env: Env = process.env): readonly Locale[] {
  return teluguEnabled(env) ? locales : [ENGLISH];
}

/** True when `value` is a locale that is switched on. */
export function isEnabledLocale(value: unknown, env: Env = process.env): value is Locale {
  return typeof value === "string" && (enabledLocales(env) as readonly string[]).includes(value);
}

/** The locale to render in: the requested one when it is switched on, otherwise English. */
export function uiLocale(requested: unknown, env: Env = process.env): Locale {
  return isEnabledLocale(requested, env) ? requested : ENGLISH;
}

/**
 * The `/te` equivalent of a path when Telugu is off: `/te` → `/en`, `/te/x?y` → `/en/x?y`.
 * `null` when the path is not a Telugu one (or Telugu is on).
 */
export function englishPathFor(pathname: string, env: Env = process.env): string | null {
  if (teluguEnabled(env)) return null;
  const match = /^\/te(\/.*)?$/i.exec(pathname);
  if (!match) return null;
  return `/${ENGLISH}${match[1] ?? ""}`;
}
