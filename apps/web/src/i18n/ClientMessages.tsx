import { NextIntlClientProvider, useLocale, useMessages } from "next-intl";
import type { ReactNode } from "react";
import { CLIENT_NAMESPACES, pickMessages, type ClientScope } from "./client-messages";

/**
 * WP-04: the client components below get only the namespaces of their area
 * (`CLIENT_NAMESPACES[scope]`), not the whole catalogue. Locale, time zone and error handler
 * come from the outer provider; the messages replace its messages.
 */
export function ClientMessages({ scope, children }: { scope: ClientScope; children: ReactNode }) {
  const messages = useMessages();
  const locale = useLocale();
  return (
    <NextIntlClientProvider
      locale={locale}
      messages={pickMessages(messages, CLIENT_NAMESPACES[scope])}
    >
      {children}
    </NextIntlClientProvider>
  );
}
