/**
 * Dates as the office types them (PRD §8: DD/MM/YYYY) ↔ the API's YYYY-MM-DD. Plain module.
 */

/** `01/06/2012` (or `1-6-2012`) → `2012-06-01`; null when it is not a real calendar date. */
export function displayDateToIso(value: string): string | null {
  const match = /^(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})$/.exec(value.trim());
  if (!match) return null;
  const [, day = "", month = "", year = ""] = match;
  const iso = `${year}-${month.padStart(2, "0")}-${day.padStart(2, "0")}`;
  const date = new Date(`${iso}T00:00:00Z`);
  return !Number.isNaN(date.getTime()) && date.toISOString().startsWith(iso) ? iso : null;
}

/** Today in India (IST), as YYYY-MM-DD, whatever the PC's time zone is. */
export function todayInIndia(now: Date = new Date()): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Kolkata",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(now);
}
