/**
 * Unsent work kept on this device (FT-11, FR-ATT-032): today only a teacher's attendance day
 * not saved yet. Never tokens; never names, dates of birth or phone numbers: record ids,
 * statuses, a section label and a date (docs/07 §5.2, docs/13 §5). Everything under the prefix
 * is removed on sign-out, "Lock now", the idle timeout and on the signed-out page, so the next
 * person on a shared office PC never sees it.
 *
 * Storage may be missing or throw (private windows, blocked site data): every access is
 * wrapped, and the screen then keeps the draft for this page only and says so.
 */

export const DEVICE_DRAFT_PREFIX = "sos.draft.";

export function draftStorage(): Storage | null {
  try {
    // FT-11: unsent attendance (ids and statuses only), see above.
    // eslint-disable-next-line no-restricted-properties
    const store = window.localStorage;
    return store ?? null;
  } catch {
    return null;
  }
}

/** Every key of this device's drafts (all people, all kinds). */
export function deviceDraftKeys(store: Storage | null = draftStorage()): string[] {
  if (!store) return [];
  try {
    const keys: string[] = [];
    for (let index = 0; index < store.length; index += 1) {
      const key = store.key(index);
      if (key?.startsWith(DEVICE_DRAFT_PREFIX)) keys.push(key);
    }
    return keys;
  } catch {
    return [];
  }
}

/** Sign-out and the signed-out page: forget every draft on this device. */
export function clearDeviceDrafts(): void {
  const store = draftStorage();
  for (const key of deviceDraftKeys(store)) {
    try {
      store?.removeItem(key);
    } catch {
      // Blocked storage holds nothing to clear.
    }
  }
}
