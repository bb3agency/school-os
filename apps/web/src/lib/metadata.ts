import type { Metadata } from "next";
import { hasLocale } from "next-intl";
import { getTranslations } from "next-intl/server";
import { routing } from "@/i18n/routing";

type Translator = Awaited<ReturnType<typeof getTranslations<never>>>;

/**
 * Build `generateMetadata` for a page whose <title> is one of its message keys
 * (unique, translated page titles: WCAG 2.4.2).
 */
export function pageMetadata(select: (t: Translator) => string) {
  return async function generateMetadata({
    params,
  }: {
    params: Promise<{ locale: string }>;
  }): Promise<Metadata> {
    const { locale } = await params;
    const t = await getTranslations({
      locale: hasLocale(routing.locales, locale) ? locale : routing.defaultLocale,
    });
    return { title: select(t) };
  };
}
