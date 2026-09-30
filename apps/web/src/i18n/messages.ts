import type enMessages from "../../messages/en.json";
import { ENGLISH, TELUGU, teluguEnabled } from "./languages";
import type { Locale } from "./routing";

export type IntlMessages = typeof enMessages;

type Tree = { [key: string]: string | Tree };

/** `base` with every string of `overrides` put in at the same key path (a new object). */
export function mergeMessages<T extends object>(base: T, overrides: object): T {
  const out: Tree = { ...(base as Tree) };
  for (const [key, value] of Object.entries(overrides as Tree)) {
    const current = out[key];
    out[key] =
      typeof value === "object" && typeof current === "object"
        ? mergeMessages(current, value)
        : value;
  }
  return out as T;
}

/**
 * The message catalog for a request (ADR-0036). The Telugu catalog is loaded only when Telugu
 * is switched on and asked for; any other request gets English. With Telugu off, English
 * strings that talk about Telugu ("in English and Telugu") are replaced by the English-only
 * wording in `messages/en.telugu-off.json`.
 */
export async function loadMessages(
  requested: unknown,
  telugu: boolean = teluguEnabled(),
): Promise<{ locale: Locale; messages: IntlMessages }> {
  const locale: Locale = telugu && requested === TELUGU ? TELUGU : ENGLISH;
  if (locale === TELUGU) {
    const te = (await import("../../messages/te.json")) as { default: IntlMessages };
    return { locale, messages: te.default };
  }
  const en = (await import("../../messages/en.json")) as { default: IntlMessages };
  if (telugu) return { locale, messages: en.default };
  const overrides = (await import("../../messages/en.telugu-off.json")) as { default: object };
  return { locale, messages: mergeMessages(en.default, overrides.default) };
}
