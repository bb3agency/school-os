import { createTranslator } from "next-intl";
import en from "../../../messages/en.json";
import te from "../../../messages/te.json";
import { TELUGU_FONT_BASE, TELUGU_STYLESHEET } from "@/i18n/telugu-font";

/**
 * Parent page texts (ADR-0044; FR-PUB-008). The language is the parent language the API sends
 * for the link (the guardian's choice, else the school's, else English), never the staff
 * switch `SOS_TELUGU_ENABLED`: a school may write to parents in Telugu while its staff screens
 * stay English. Texts live in the `public` namespace of messages/en.json and te.json (Telugu
 * entries unreviewed until a fluent reviewer signs them off, D23: app/core/parent_texts.yaml
 * `public.shell`).
 */

export type ParentLanguage = "en" | "te";

export const PUBLIC_TELUGU_FONT_BASE = `/p${TELUGU_FONT_BASE}`;
export const PUBLIC_TELUGU_STYLESHEET_HREF = `${PUBLIC_TELUGU_FONT_BASE}/${TELUGU_STYLESHEET}`;

export function parentLanguage(value: unknown): ParentLanguage {
  return value === "te" ? "te" : "en";
}

export function publicTranslator(language: ParentLanguage) {
  return createTranslator({
    locale: language,
    messages: language === "te" ? te : en,
    namespace: "public",
    timeZone: "Asia/Kolkata",
  });
}

/** A date as parents in India write it (DD/MM/YYYY), in the page's language. */
export function parentDate(iso: string, language: ParentLanguage): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat(language === "te" ? "te-IN" : "en-IN", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    timeZone: "Asia/Kolkata",
  }).format(date);
}
