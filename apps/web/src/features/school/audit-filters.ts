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

/** DD/MM/YYYY (what users type) → YYYY-MM-DD (API); anything else is ignored. */
export function toIsoDate(value: string | undefined): string | undefined {
  const match = /^(\d{2})\/(\d{2})\/(\d{4})$/.exec(value?.trim() ?? "");
  if (!match) return undefined;
  const [, day, month, year] = match;
  const iso = `${year}-${month}-${day}`;
  const date = new Date(`${iso}T00:00:00Z`);
  return !Number.isNaN(date.getTime()) && date.toISOString().startsWith(iso) ? iso : undefined;
}

/** Midnight of a day in India time: the API needs an explicit offset on its time filters. */
function istMidnight(iso: string | undefined): string | undefined {
  return iso ? `${iso}T00:00:00+05:30` : undefined;
}

/** The API query for the filters: blank values and dates that are not real are left out. */
export function auditQuery(filters: AuditFilters): AuditQuery {
  const from = istMidnight(toIsoDate(filters.from));
  const to = istMidnight(toIsoDate(filters.to));
  return {
    ...(filters.actor?.trim() ? { actor: filters.actor.trim() } : {}),
    ...(filters.action?.trim() ? { action: filters.action.trim() } : {}),
    ...(from ? { from } : {}),
    ...(to ? { to } : {}),
  };
}
