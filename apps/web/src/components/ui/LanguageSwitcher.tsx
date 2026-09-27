"use client";

import { useLocale, useTranslations } from "next-intl";
import { Link, usePathname } from "@/i18n/navigation";
import { routing, type Locale } from "@/i18n/routing";
import { cn } from "@/lib/cn";

/**
 * The languages to offer. Inside a school: the school's `languages` from GET /me (FR-TEN-012)
 * in its order (the first is the school's default), known codes only, English if nothing
 * usable is left. Outside a school (`undefined`/`null`): every language the app has.
 */
export function offeredLanguages(languages: readonly string[] | null | undefined): Locale[] {
  if (!languages) return [...routing.locales];
  const known = languages.filter((code): code is Locale =>
    (routing.locales as readonly string[]).includes(code),
  );
  const unique = [...new Set(known)];
  return unique.length > 0 ? unique : ["en"];
}

/**
 * Switch between English and Telugu on the same page. Plain links (work without JS);
 * each language name is shown in its own script and marked with its own `lang`.
 * `languages` limits the choice to the school's languages; nothing is shown when the only
 * one offered is already in use.
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
  const offered = offeredLanguages(languages);
  if (offered.length === 1 && offered[0] === locale) return null;
  return (
    <nav aria-label={t("label")} data-print="hide">
      <ul className="flex items-center gap-1">
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
                  "inline-block rounded-md px-2.5 py-1 text-sm",
                  tone === "dark"
                    ? active
                      ? "bg-platform-ink font-semibold text-platform"
                      : "text-platform-ink hover:bg-platform-hover"
                    : active
                      ? "bg-primary font-semibold text-on-primary"
                      : "text-primary hover:bg-primary-soft",
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
