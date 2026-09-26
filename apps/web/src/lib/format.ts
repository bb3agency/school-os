import type { Locale } from "@/i18n/routing";

/**
 * Indian conventions (PRD §8): dates as DD/MM/YYYY, times in IST, rupees with lakh/crore
 * grouping. Digits stay Latin in both languages, as on school registers.
 */
const TIME_ZONE = "Asia/Kolkata";
const NUMBER_LOCALE: Record<Locale, string> = { en: "en-IN", te: "te-IN" };

const DATE_ONLY = /^(\d{4})-(\d{2})-(\d{2})$/;

/** `2026-06-01` or an RFC 3339 timestamp → `01/06/2026` (IST). */
export function formatDate(value: string | null | undefined): string | null {
  if (!value) return null;
  const dateOnly = DATE_ONLY.exec(value);
  if (dateOnly) {
    const [, y, m, d] = dateOnly;
    return `${d}/${m}/${y}`;
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: TIME_ZONE,
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
  }).formatToParts(date);
  const get = (type: Intl.DateTimeFormatPartTypes) =>
    parts.find((part) => part.type === type)?.value ?? "";
  return `${get("day")}/${get("month")}/${get("year")}`;
}

/** RFC 3339 timestamp → `01/06/2026 14:05` (IST, 24-hour). */
export function formatDateTime(value: string | null | undefined): string | null {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  const time = new Intl.DateTimeFormat("en-GB", {
    timeZone: TIME_ZONE,
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
  return `${formatDate(value) ?? ""} ${time}`;
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
