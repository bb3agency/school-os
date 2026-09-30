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
 * No URL carries a locale (`localePrefix: "never"`, ./routing.ts): the language comes from
 * the NEXT_LOCALE cookie, then Accept-Language, among the switched-on locales only.
 *
 * With the switch off: no language switcher, English whatever the cookie or header says, the
 * Telugu catalog is never loaded, English strings that mention Telugu use the overrides in
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

/** An old URL with a locale prefix (`/en/...`, `/te/...`) and the same path without it. */
export interface LegacyLocalePath {
  /** The locale the old prefix named (`en` or `te`), lower case. */
  locale: Locale;
  /** The path without the prefix: always one leading slash, never `//` (no open redirect). */
  pathname: string;
}

const LEGACY_PREFIX = /^\/(en|te)(?=\/|$)/i;

/**
 * No URL carries a locale any more (ADR-0036 note, 2026-09-30): `/en/x` and `/te/x` are old
 * links that the proxy answers with 308 to `/x`. Returns the prefix-less path, or `null` when
 * `pathname` has no locale prefix (`/teachers`, `/entries` and `/students/en` are not
 * prefixed). Leading slashes are collapsed to one, so `/en//evil.example` becomes
 * `/evil.example` on this site, never the protocol-relative `//evil.example`.
 */
export function legacyLocalePath(pathname: string): LegacyLocalePath | null {
  const match = LEGACY_PREFIX.exec(pathname);
  if (!match) return null;
  const rest = pathname.slice(match[0].length).replace(/^[/\\]+/, "");
  return { locale: (match[1] ?? ENGLISH).toLowerCase() as Locale, pathname: `/${rest}` };
}

/**
 * `path` (pathname plus optional query and hash) without an old locale prefix; unchanged when
 * it has none. For return addresses (`next`) that were saved before prefixes were dropped.
 */
export function withoutLocalePrefix(path: string): string {
  const cut = path.search(/[?#]/);
  const pathname = cut === -1 ? path : path.slice(0, cut);
  const legacy = legacyLocalePath(pathname);
  if (!legacy) return path;
  return `${legacy.pathname}${cut === -1 ? "" : path.slice(cut)}`;
}
