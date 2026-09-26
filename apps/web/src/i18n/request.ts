import { hasLocale } from "next-intl";
import { getRequestConfig } from "next-intl/server";
import type enMessages from "../../messages/en.json";
import { routing } from "./routing";

export const TIME_ZONE = "Asia/Kolkata";

export default getRequestConfig(async ({ requestLocale }) => {
  const requested = await requestLocale;
  const locale = hasLocale(routing.locales, requested) ? requested : routing.defaultLocale;
  const messages = (await import(`../../messages/${locale}.json`)) as {
    default: IntlMessages;
  };
  return {
    locale,
    messages: messages.default,
    timeZone: TIME_ZONE,
  };
});

type IntlMessages = typeof enMessages;
