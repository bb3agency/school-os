/**
 * Dates as the office types them, in the school's `date_format` (FR-TEN-012; PRD §8 default
 * DD/MM/YYYY) ↔ the API's YYYY-MM-DD. Plain module; the rules live in `@/lib/date-format`.
 */
export { typedDateToIso as displayDateToIso } from "@/lib/date-format";

/** Today in India (IST), as YYYY-MM-DD, whatever the PC's time zone is. */
export function todayInIndia(now: Date = new Date()): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Kolkata",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(now);
}
