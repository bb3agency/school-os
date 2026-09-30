import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { uiLocale } from "@/i18n/languages";

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
    // ADR-0036: Telugu titles only while Telugu is switched on.
    const t = await getTranslations({ locale: uiLocale(locale) });
    return { title: select(t) };
  };
}
