import { useSyncExternalStore } from "react";

/**
 * Display dates in the school's `date_format` (GET /me `settings`, FR-TEN-012). Display only:
 * API values stay ISO (YYYY-MM-DD, RFC 3339) and typed dates are parsed by the form helpers.
 * Times are shown in IST, 24-hour; the calendar day is the one in India (PRD §8).
 */

export const DATE_FORMATS = ["DD/MM/YYYY", "DD-MM-YYYY", "YYYY-MM-DD"] as const;
export type DateFormat = (typeof DATE_FORMATS)[number];
/** PRD §8 and the API's default. */
export const DEFAULT_DATE_FORMAT: DateFormat = "DD/MM/YYYY";

const TIME_ZONE = "Asia/Kolkata";
const DATE_ONLY = /^(\d{4})-(\d{2})-(\d{2})$/;

export function toDateFormat(value: unknown): DateFormat {
  return (DATE_FORMATS as readonly unknown[]).includes(value)
    ? (value as DateFormat)
    : DEFAULT_DATE_FORMAT;
}

function arrange(day: string, month: string, year: string, format: DateFormat): string {
  switch (format) {
    case "DD-MM-YYYY":
      return `${day}-${month}-${year}`;
    case "YYYY-MM-DD":
      return `${year}-${month}-${day}`;
    default:
      return `${day}/${month}/${year}`;
  }
}

function indiaParts(date: Date, options: Intl.DateTimeFormatOptions) {
  const parts = new Intl.DateTimeFormat("en-GB", { timeZone: TIME_ZONE, ...options }).formatToParts(
    date,
  );
  return (type: Intl.DateTimeFormatPartTypes) =>
    parts.find((part) => part.type === type)?.value ?? "";
}

function parse(value: string | null | undefined): Date | null {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

/** `2026-06-01` or an RFC 3339 timestamp → the date in `format` (IST calendar day). */
export function formatDisplayDate(
  value: string | null | undefined,
  format: DateFormat = schoolDateFormat(),
): string | null {
  if (!value) return null;
  const dateOnly = DATE_ONLY.exec(value);
  if (dateOnly) {
    const [, year = "", month = "", day = ""] = dateOnly;
    return arrange(day, month, year, format);
  }
  const date = parse(value);
  if (!date) return null;
  const get = indiaParts(date, { day: "2-digit", month: "2-digit", year: "numeric" });
  return arrange(get("day"), get("month"), get("year"), format);
}

/** RFC 3339 timestamp → the date in `format` and the IST time, 24-hour (`01/06/2026 14:05`). */
export function formatDisplayDateTime(
  value: string | null | undefined,
  format: DateFormat = schoolDateFormat(),
): string | null {
  const date = parse(value);
  if (!date) return null;
  const get = indiaParts(date, { hour: "2-digit", minute: "2-digit", hour12: false });
  return `${formatDisplayDate(value, format) ?? ""} ${get("hour")}:${get("minute")}`;
}

/*
 * The active school's format, kept in the browser only. The server always renders the
 * default, so one school's setting can never leak into another request's HTML; the first
 * browser render uses the default too (no hydration mismatch) until GET /me has loaded.
 */
let current: DateFormat = DEFAULT_DATE_FORMAT;
const listeners = new Set<() => void>();

/** Called with GET /me `settings.date_format` (null or unknown values reset to the default). */
export function setSchoolDateFormat(value: unknown): void {
  if (typeof window === "undefined") return;
  const next = toDateFormat(value);
  if (next === current) return;
  current = next;
  for (const listener of listeners) listener();
}

export function schoolDateFormat(): DateFormat {
  return typeof window === "undefined" ? DEFAULT_DATE_FORMAT : current;
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** The school's date format as React state (re-renders when /me brings it). */
export function useSchoolDateFormat(): DateFormat {
  return useSyncExternalStore(subscribe, schoolDateFormat, () => DEFAULT_DATE_FORMAT);
}
