import { getRequestConfig } from "next-intl/server";
import { loadMessages } from "./messages";

export const TIME_ZONE = "Asia/Kolkata";

/**
 * Per-request locale and messages. Telugu only when it is switched on (ADR-0036,
 * `SOS_TELUGU_ENABLED`); a `te` request with it off renders in English.
 */
export default getRequestConfig(async ({ requestLocale }) => {
  const { locale, messages } = await loadMessages(await requestLocale);
  return {
    locale,
    messages,
    timeZone: TIME_ZONE,
  };
});
