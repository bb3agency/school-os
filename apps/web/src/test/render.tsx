import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, type RenderResult } from "@testing-library/react";
import { NextIntlClientProvider, type IntlError } from "next-intl";
import type { ReactElement } from "react";
import en from "../../messages/en.json";
import te from "../../messages/te.json";
import type { Locale } from "@/i18n/routing";

export const messages = { en, te } as const;

/** Missing keys / bad ICU arguments seen while rendering (cleared per render). */
export const intlErrors: string[] = [];

/** A fresh TanStack Query client per render: no retries, nothing shared between tests. */
export function testQueryClient(): QueryClient {
  return new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
}

export function renderWithIntl(
  ui: ReactElement,
  locale: Locale = "en",
  /** A client of the test's own (e.g. one that keeps unobserved cache entries). */
  queryClient: QueryClient = testQueryClient(),
): RenderResult {
  intlErrors.length = 0;
  return render(
    <NextIntlClientProvider
      locale={locale}
      messages={messages[locale]}
      timeZone="Asia/Kolkata"
      onError={(error: IntlError) => {
        intlErrors.push(`${error.code}: ${error.message}`);
      }}
    >
      <QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>
    </NextIntlClientProvider>,
  );
}
