/**
 * Per-viewer UI preferences on this computer (density, hidden table columns): never tokens,
 * never personal data, never anything a record depends on (docs/13 §5). Storage may be
 * missing or throw (private windows, blocked site data): every access is wrapped and the
 * default is used then.
 */

function storage(): Storage | null {
  try {
    // UI preferences only (see above).
    // eslint-disable-next-line no-restricted-properties
    return window.localStorage;
  } catch {
    return null;
  }
}

/** Keys are namespaced `sos.` and must name a preference, never a record or a person. */
export function readPref(key: string): string | null {
  try {
    return storage()?.getItem(`sos.${key}`) ?? null;
  } catch {
    return null;
  }
}

export function writePref(key: string, value: string | null): void {
  try {
    const store = storage();
    if (!store) return;
    if (value === null) store.removeItem(`sos.${key}`);
    else store.setItem(`sos.${key}`, value);
  } catch {
    // Full or blocked: the choice lasts for this page only.
  }
}
