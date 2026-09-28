/**
 * Typed dates ↔ ISO dates for the API (YYYY-MM-DD), in the school's `date_format` (FR-TEN-012;
 * PRD §8 default DD/MM/YYYY). The rules live in `@/lib/date-format`: `14/03/2012`,
 * `14-03-2012`, `14.03.2012`, `4/3/2012` and `2012-03-14` are all read; impossible dates
 * (31/02) and two-digit years (which century is a guess the office must make) are refused.
 */
export { isoToTypedDate, typedDateToIso as toIsoDate } from "@/lib/date-format";
