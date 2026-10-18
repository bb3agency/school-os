import { createTranslator } from "next-intl";
import en from "../../../messages/en.json";
import te from "../../../messages/te.json";
import parentEn from "../../../messages/parent.en.json";
import parentTe from "../../../messages/parent.te.json";
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

/**
 * The parent page catalogue (wave R2-3H; messages/parent.en.json and parent.te.json): notice,
 * receipt, dues, payment and opt-in texts. The Telugu file is unreviewed until a fluent reviewer
 * signs it off (D23: app/core/parent_texts.yaml `public.pages`).
 */
export function parentTranslator(language: ParentLanguage) {
  return createTranslator({
    locale: language,
    messages: language === "te" ? parentTe : parentEn,
    timeZone: "Asia/Kolkata",
  });
}

/** Rupees as parents read them: en-IN grouping, two decimals (₹1,23,456.00). */
export function parentRupees(amount: string): string {
  const [whole = "0", fraction = "00"] = amount.split(".");
  const negative = whole.startsWith("-");
  const digits = negative ? whole.slice(1) : whole;
  const last3 = digits.slice(-3);
  const rest = digits.slice(0, -3).replace(/\B(?=(\d{2})+(?!\d))/g, ",");
  const grouped = rest ? `${rest},${last3}` : last3;
  return `${negative ? "-" : ""}₹${grouped}.${fraction.padEnd(2, "0").slice(0, 2)}`;
}

/** A calendar date (YYYY-MM-DD from the API) as DD/MM/YYYY. */
export function parentDay(iso: string | null | undefined): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso ?? "");
  return match ? `${match[3]}/${match[2]}/${match[1]}` : "";
}
