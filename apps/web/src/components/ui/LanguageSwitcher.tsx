"use client";

import { useLocale, useTranslations } from "next-intl";
import { Link, usePathname } from "@/i18n/navigation";
import { routing } from "@/i18n/routing";
import { cn } from "@/lib/cn";

/**
 * Switch between English and Telugu on the same page. Plain links (work without JS);
 * each language name is shown in its own script and marked with its own `lang`.
 */
export function LanguageSwitcher({ tone = "light" }: { tone?: "light" | "dark" }) {
  const t = useTranslations("language");
  const locale = useLocale();
  const pathname = usePathname() ?? "/";
  return (
    <nav aria-label={t("label")} data-print="hide">
      <ul className="flex items-center gap-1">
        {routing.locales.map((target) => {
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
