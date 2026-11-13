import * as z from "@/lib/zod";
import { DEVICE_DRAFT_PREFIX, deviceDraftKeys, draftStorage } from "@/lib/device-drafts";
import { ATTENDANCE_STATUSES, type AttendanceStatus } from "./data";

/**
 * A teacher's attendance day not saved yet, kept on the device (FT-11; FR-ATT-032..FR-ATT-034).
 *
 * One draft per person, section and date. It holds student ids, the statuses chosen, the
 * version each mark had when it was read (so a later retry cannot overwrite a newer save:
 * FR-ATT-029), the section's label and the date: no names or other personal data. It is
 * dropped after a successful save, after `draft_keep_hours` (FR-ATT-026), when it belongs to
 * another person signed in on this device, and on sign-out (`clearDeviceDrafts`).
 */

const KIND = "attendance";
const KEY_PREFIX = `${DEVICE_DRAFT_PREFIX}${KIND}.`;
const HOUR_MS = 3_600_000;

const entrySchema = z.object({
  student_id: z.string().min(1).max(64),
  status: z.enum(ATTENDANCE_STATUSES as [AttendanceStatus, ...AttendanceStatus[]]),
  expected_version: z.number().int().min(0),
});

const draftSchema = z.object({
  v: z.literal(1),
  /** The membership that marked it (an id, never a name). */
  owner: z.string().min(1).max(64),
  section: z.string().min(1).max(64),
  /** Not personal data: "9A" (shown in the "not saved yet" list). */
  section_label: z.string().max(80),
  date: z.string().regex(/^\d{4}-\d{2}-\d{2}$/),
  /** One id per draft; with `rev` it makes the save's Idempotency-Key (FR-ATT-027). */
  id: z.string().min(8).max(64),
  rev: z.number().int().min(0),
  /** The teacher pressed Save and it did not reach the school yet: retry automatically. */
  queued: z.boolean(),
  updated_at: z.number(),
  keep_until: z.number(),
  entries: z.array(entrySchema).min(1).max(2500),
});

export type AttendanceDraft = z.infer<typeof draftSchema>;
export type AttendanceDraftEntry = z.infer<typeof entrySchema>;

export function draftKey(section: string, date: string): string {
  return `${KEY_PREFIX}${section}.${date}`;
}

function parse(raw: string | null): AttendanceDraft | null {
  if (raw === null) return null;
  try {
    const result = draftSchema.safeParse(JSON.parse(raw));
    return result.success ? result.data : null;
  } catch {
    return null;
  }
}

function remove(store: Storage | null, key: string): void {
  try {
    store?.removeItem(key);
  } catch {
    // Nothing to remove from blocked storage.
  }
}

/**
 * This person's drafts on the device, oldest first. Expired, unreadable and other people's
 * drafts are removed on the way (a shared office PC keeps nothing of the previous person).
 */
export function listDrafts(owner: string, now: number = Date.now()): AttendanceDraft[] {
  const store = draftStorage();
  const out: AttendanceDraft[] = [];
  for (const key of deviceDraftKeys(store)) {
    if (!key.startsWith(KEY_PREFIX)) continue;
    let raw: string | null = null;
    try {
      raw = store?.getItem(key) ?? null;
    } catch {
      raw = null;
    }
    const draft = parse(raw);
    if (!draft || draft.owner !== owner || draft.keep_until <= now) {
      remove(store, key);
      continue;
    }
    if (key !== draftKey(draft.section, draft.date)) {
      remove(store, key);
      continue;
    }
    out.push(draft);
  }
  return out.sort((a, b) => a.updated_at - b.updated_at);
}

export function readDraft(
  owner: string,
  section: string,
  date: string,
  now: number = Date.now(),
): AttendanceDraft | null {
  return listDrafts(owner, now).find((d) => d.section === section && d.date === date) ?? null;
}

/** Keep (or replace) the draft; false when the device refuses (full or blocked storage). */
export function writeDraft(draft: AttendanceDraft): boolean {
  const store = draftStorage();
  if (!store) return false;
  try {
    store.setItem(draftKey(draft.section, draft.date), JSON.stringify(draftSchema.parse(draft)));
    return true;
  } catch {
    return false;
  }
}

export function clearDraft(section: string, date: string): void {
  remove(draftStorage(), draftKey(section, date));
}

function sameEntries(a: readonly AttendanceDraftEntry[], b: readonly AttendanceDraftEntry[]) {
  if (a.length !== b.length) return false;
  return a.every(
    (entry, index) =>
      entry.student_id === b[index]?.student_id &&
      entry.status === b[index]?.status &&
      entry.expected_version === b[index]?.expected_version,
  );
}

/**
 * The next state of a draft after the form changed: the same id, `rev` raised when the
 * entries changed (a changed draft is a new save, so it gets a new Idempotency-Key).
 */
export function nextDraft(
  previous: AttendanceDraft | null,
  fields: {
    owner: string;
    section: string;
    section_label: string;
    date: string;
    entries: AttendanceDraftEntry[];
    queued: boolean;
    keepHours: number;
  },
  now: number = Date.now(),
  newId: () => string = () => crypto.randomUUID(),
): AttendanceDraft {
  const changed = !previous || !sameEntries(previous.entries, fields.entries);
  return {
    v: 1,
    owner: fields.owner,
    section: fields.section,
    section_label: fields.section_label,
    date: fields.date,
    id: previous?.id ?? newId(),
    rev: previous ? previous.rev + (changed ? 1 : 0) : 0,
    queued: fields.queued,
    updated_at: changed || !previous ? now : previous.updated_at,
    keep_until: now + fields.keepHours * HOUR_MS,
    entries: fields.entries,
  };
}

/** FR-ATT-027: one key per draft revision ("att-<id>-<rev>", 8..128 letters, digits, '-'). */
export function draftIdempotencyKey(draft: AttendanceDraft): string {
  return `att-${draft.id}-${draft.rev}`.replace(/[^A-Za-z0-9_-]/g, "").slice(0, 128);
}

/** Wait before automatic retry number `attempt` (0-based): 5 s, 15 s, 30 s, 1 min, then 2 min. */
export const RETRY_DELAYS_MS: readonly number[] = [5_000, 15_000, 30_000, 60_000, 120_000];

export function retryDelay(attempt: number): number {
  return RETRY_DELAYS_MS[Math.min(attempt, RETRY_DELAYS_MS.length - 1)] ?? 120_000;
}
