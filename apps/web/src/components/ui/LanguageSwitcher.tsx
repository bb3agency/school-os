"use client";

import { useLocale, useTranslations } from "next-intl";
import { useEnabledLocales } from "@/i18n/LanguagesProvider";
import { Link, usePathname } from "@/i18n/navigation";
import { routing, type Locale } from "@/i18n/routing";
import { cn } from "@/lib/cn";

/**
 * The languages to offer. Inside a school: the school's `languages` from GET /me (FR-TEN-012)
 * in its order (the first is the school's default), known codes only, English if nothing
 * usable is left. Outside a school (`undefined`/`null`): every language the app has.
 * Only languages that are switched on count (`enabled`; ADR-0036: English only while
 * SOS_TELUGU_ENABLED is off).
 */
export function offeredLanguages(
  languages: readonly string[] | null | undefined,
  enabled: readonly Locale[] = routing.locales,
): Locale[] {
  if (!languages) return [...enabled];
  const known = languages.filter((code): code is Locale =>
    (enabled as readonly string[]).includes(code),
  );
  const unique = [...new Set(known)];
  return unique.length > 0 ? unique : ["en"];
}

/**
 * Switch between English and Telugu on the same page. Plain links (work without JS);
 * each language name is shown in its own script and marked with its own `lang`.
 * `languages` limits the choice to the school's languages; nothing is shown when the only
 * one offered is already in use, and never while Telugu is switched off (ADR-0036).
 */
export function LanguageSwitcher({
  tone = "light",
  languages,
}: {
  tone?: "light" | "dark";
  languages?: readonly string[] | null;
}) {
  const t = useTranslations("language");
  const locale = useLocale();
  const pathname = usePathname() ?? "/";
  const enabled = useEnabledLocales();
  if (enabled.length < 2) return null;
  const offered = offeredLanguages(languages, enabled);
  if (offered.length === 1 && offered[0] === locale) return null;
  return (
    <nav aria-label={t("label")} data-print="hide">
      <ul
        className={cn(
          "flex items-center gap-0.5 rounded-full p-0.5",
          tone === "dark" ? "bg-platform-hover" : "bg-surface-sunken",
        )}
      >
        {offered.map((target) => {
          const active = target === locale;
          return (
            <li key={target}>
              <Link
                href={pathname}
                locale={target}
                lang={target}
                hrefLang={target}
                aria-current={active ? "true" : undefined}
                className={cn(
                  "inline-flex min-h-8 items-center rounded-full px-3 text-sm font-medium",
                  tone === "dark"
                    ? active
                      ? "bg-platform-ink text-platform"
                      : "text-platform-ink hover:bg-platform"
                    : active
                      ? "bg-surface text-ink shadow-raised"
                      : "text-ink-muted hover:text-ink",
                )}
              >
                {t(target)}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
