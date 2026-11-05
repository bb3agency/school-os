/**
 * The school audit log's filters (FR-AUD-005, US-1001): what users type in the filter bar
 * (kept in the URL) and the API query built from it. The viewer (`GET /audit/events`) and the
 * CSV download (`GET /audit/export`) use the same query, so the file holds exactly the events
 * the filters select.
 */

export interface AuditFilters {
  actor?: string | undefined;
  action?: string | undefined;
  from?: string | undefined;
  to?: string | undefined;
}

export interface AuditQuery {
  actor?: string;
  action?: string;
  from?: string;
  to?: string;
}

/**
 * DD/MM/YYYY (what users type) or ISO YYYY-MM-DD (a pasted or linked date; D56) → YYYY-MM-DD
 * (API); anything else, and dates that do not exist, are ignored.
 */
export function toIsoDate(value: string | undefined): string | undefined {
  const text = value?.trim() ?? "";
  const dmy = /^(\d{2})\/(\d{2})\/(\d{4})$/.exec(text);
  const ymd = /^(\d{4})-(\d{2})-(\d{2})$/.exec(text);
  let iso: string;
  if (dmy) iso = `${dmy[3]}-${dmy[2]}-${dmy[1]}`;
  else if (ymd) iso = text;
  else return undefined;
  const date = new Date(`${iso}T00:00:00Z`);
  return !Number.isNaN(date.getTime()) && date.toISOString().startsWith(iso) ? iso : undefined;
}

/** Midnight of a day in India time: the API needs an explicit offset on its time filters. */
function istMidnight(iso: string | undefined): string | undefined {
  return iso ? `${iso}T00:00:00+05:30` : undefined;
}

/** The day after a real YYYY-MM-DD date. */
function nextDay(iso: string | undefined): string | undefined {
  if (!iso) return undefined;
  const date = new Date(`${iso}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + 1);
  return date.toISOString().slice(0, 10);
}

/**
 * The API query for the filters: blank values and dates that are not real are left out. The
 * API's time range is `[from, to)`, so "To date" is sent as the next day's midnight (India
 * time) and the chosen day itself is included.
 */
export function auditQuery(filters: AuditFilters): AuditQuery {
  const from = istMidnight(toIsoDate(filters.from));
  const to = istMidnight(nextDay(toIsoDate(filters.to)));
  return {
    ...(filters.actor?.trim() ? { actor: filters.actor.trim() } : {}),
    ...(filters.action?.trim() ? { action: filters.action.trim() } : {}),
    ...(from ? { from } : {}),
    ...(to ? { to } : {}),
  };
}
