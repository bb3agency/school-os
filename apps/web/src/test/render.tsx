import { render, type RenderResult } from "@testing-library/react";
import { NextIntlClientProvider, type IntlError } from "next-intl";
import type { ReactElement } from "react";
import en from "../../messages/en.json";
import te from "../../messages/te.json";
import type { Locale } from "@/i18n/routing";

export const messages = { en, te } as const;

/** Missing keys / bad ICU arguments seen while rendering (cleared per render). */
export const intlErrors: string[] = [];

export function renderWithIntl(ui: ReactElement, locale: Locale = "en"): RenderResult {
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
      {ui}
    </NextIntlClientProvider>,
  );
}
