import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, type RenderResult } from "@testing-library/react";
import { NextIntlClientProvider, type IntlError } from "next-intl";
import type { ReactElement } from "react";
import en from "../../messages/en.json";
import enTeluguOff from "../../messages/en.telugu-off.json";
import te from "../../messages/te.json";
import { LanguagesProvider } from "@/i18n/LanguagesProvider";
import { mergeMessages } from "@/i18n/messages";
import { locales, type Locale } from "@/i18n/routing";

/**
 * The catalogs: `en` and `te` as shipped, and `englishOnly`, what English speakers see while
 * Telugu is switched off (ADR-0036: the default; en.json with en.telugu-off.json on top).
 */
export const messages = { en, te, englishOnly: mergeMessages(en, enTeluguOff) } as const;

export interface IntlRenderOptions {
  locale?: Locale;
  /**
   * Telugu switched on (SOS_TELUGU_ENABLED; ADR-0036). Default: on for a Telugu render, off
   * (the product default) otherwise. Tests of Telugu behaviour in English set it explicitly.
   */
  telugu?: boolean;
  queryClient?: QueryClient;
}

/** Missing keys / bad ICU arguments seen while rendering (cleared per render). */
export const intlErrors: string[] = [];

/** A fresh TanStack Query client per render: no retries, nothing shared between tests. */
export function testQueryClient(): QueryClient {
  return new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
}

export function renderWithIntl(
  ui: ReactElement,
  localeOrOptions: Locale | IntlRenderOptions = "en",
  /** A client of the test's own (e.g. one that keeps unobserved cache entries). */
  queryClient: QueryClient = testQueryClient(),
): RenderResult {
  const options: IntlRenderOptions =
    typeof localeOrOptions === "string" ? { locale: localeOrOptions } : localeOrOptions;
  const locale = options.locale ?? "en";
  const telugu = options.telugu ?? locale === "te";
  if (locale === "te" && !telugu) throw new Error("A Telugu render needs Telugu switched on.");
  intlErrors.length = 0;
  return render(
    <NextIntlClientProvider
      locale={locale}
      messages={telugu ? messages[locale] : messages.englishOnly}
      timeZone="Asia/Kolkata"
      onError={(error: IntlError) => {
        intlErrors.push(`${error.code}: ${error.message}`);
      }}
    >
      <LanguagesProvider locales={telugu ? locales : ["en"]}>
        <QueryClientProvider client={options.queryClient ?? queryClient}>{ui}</QueryClientProvider>
      </LanguagesProvider>
    </NextIntlClientProvider>,
  );
}
