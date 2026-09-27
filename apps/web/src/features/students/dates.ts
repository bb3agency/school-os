/**
 * Dates as offices write them (PRD §8: DD/MM/YYYY) ↔ ISO dates for the API (YYYY-MM-DD).
 * Accepts `14/03/2012`, `14-03-2012`, `14.03.2012`, `4/3/2012` and ISO; rejects impossible
 * dates (31/02) and two-digit years (which century is a guess the office must make).
 */
const DMY = /^(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})$/;
const ISO = /^(\d{4})-(\d{2})-(\d{2})$/;

function valid(year: number, month: number, day: number): string | null {
  const iso = `${String(year).padStart(4, "0")}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
  const date = new Date(`${iso}T00:00:00Z`);
  return !Number.isNaN(date.getTime()) && date.toISOString().startsWith(iso) ? iso : null;
}

export function toIsoDate(value: string): string | null {
  const text = value.trim();
  const dmy = DMY.exec(text);
  if (dmy) return valid(Number(dmy[3]), Number(dmy[2]), Number(dmy[1]));
  const iso = ISO.exec(text);
  if (iso) return valid(Number(iso[1]), Number(iso[2]), Number(iso[3]));
  return null;
}

/** ISO → DD/MM/YYYY for an input's starting value; anything else is returned unchanged. */
export function isoToDmy(value: string): string {
  const iso = ISO.exec(value.trim());
  return iso ? `${iso[3]}/${iso[2]}/${iso[1]}` : value;
}
