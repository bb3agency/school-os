import type { Locale } from "@/i18n/routing";
import {
  formatDisplayDate,
  formatDisplayDateTime,
  schoolDateFormat,
  type DateFormat,
} from "./date-format";

/**
 * Indian conventions (PRD §8): dates in the school's date format (DD/MM/YYYY by default,
 * FR-TEN-012), times in IST, rupees with lakh/crore grouping. Digits stay Latin in both
 * languages, as on school registers.
 */
const NUMBER_LOCALE: Record<Locale, string> = { en: "en-IN", te: "te-IN" };

/** `2026-06-01` or an RFC 3339 timestamp → `01/06/2026` (IST; the school's date format). */
export function formatDate(
  value: string | null | undefined,
  format: DateFormat = schoolDateFormat(),
): string | null {
  return formatDisplayDate(value, format);
}

/** RFC 3339 timestamp → `01/06/2026 14:05` (IST, 24-hour; the school's date format). */
export function formatDateTime(
  value: string | null | undefined,
  format: DateFormat = schoolDateFormat(),
): string | null {
  return formatDisplayDateTime(value, format);
}

/** Decimal string from the API (numeric(14,2)) → `₹1,23,456.00`. */
export function formatInr(
  value: string | number | null | undefined,
  locale: Locale,
): string | null {
  if (value === null || value === undefined || value === "") return null;
  const amount = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(amount)) return null;
  return new Intl.NumberFormat(NUMBER_LOCALE[locale], {
    style: "currency",
    currency: "INR",
    numberingSystem: "latn",
  }).format(amount);
}

export function formatCount(value: number | null | undefined, locale: Locale): string | null {
  if (value === null || value === undefined || !Number.isFinite(value)) return null;
  return new Intl.NumberFormat(NUMBER_LOCALE[locale], { numberingSystem: "latn" }).format(value);
}

/** ["Owner", "Principal"] → "Owner and Principal" in the reader's language. */
export function formatList(items: readonly string[], locale: Locale): string {
  return new Intl.ListFormat(NUMBER_LOCALE[locale], { style: "long", type: "conjunction" }).format(
    items,
  );
}

const BYTE_UNITS = ["byte", "kilobyte", "megabyte", "gigabyte", "terabyte"] as const;

export function formatBytes(value: number | null | undefined, locale: Locale): string | null {
  if (value === null || value === undefined || !Number.isFinite(value) || value < 0) return null;
  let unitIndex = 0;
  let amount = value;
  while (amount >= 1024 && unitIndex < BYTE_UNITS.length - 1) {
    amount /= 1024;
    unitIndex += 1;
  }
  return new Intl.NumberFormat(NUMBER_LOCALE[locale], {
    style: "unit",
    unit: BYTE_UNITS[unitIndex] ?? "byte",
    unitDisplay: "short",
    maximumFractionDigits: 1,
    numberingSystem: "latn",
  }).format(amount);
}
