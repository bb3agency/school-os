import { useCallback, useEffect, useSyncExternalStore } from "react";
import { readPref, writePref } from "@/lib/ui-prefs";
import { DENSITY_ATTRIBUTE, DENSITY_COMPACT, DENSITY_STORAGE_KEY } from "./sidebar-script";

/**
 * Density (docs/17 §3.4): comfortable by default, compact on request, remembered per viewer
 * on this computer. The state is `<html data-density="compact">` (absent = comfortable), set
 * before the first paint by the root layout's head script (sidebar-script.ts), then by the
 * toggle; CSS (`compact:` variant, `--row-height`) does the rest. Same pattern as the
 * compact sidebar (sidebar-state.ts): the server snapshot is comfortable, so hydration never
 * mismatches.
 */
export type Density = "comfortable" | "compact";

const EVENT = "sos:density";
/** ui-prefs adds the `sos.` prefix. */
const PREF_KEY = DENSITY_STORAGE_KEY.replace(/^sos\./, "");

function apply(density: Density): void {
  const root = document.documentElement;
  if (density === "compact") root.setAttribute(DENSITY_ATTRIBUTE, DENSITY_COMPACT);
  else root.removeAttribute(DENSITY_ATTRIBUTE);
  window.dispatchEvent(new Event(EVENT));
}

function snapshot(): Density {
  return document.documentElement.getAttribute(DENSITY_ATTRIBUTE) === DENSITY_COMPACT
    ? "compact"
    : "comfortable";
}

function serverSnapshot(): Density {
  return "comfortable";
}

function subscribe(onChange: () => void): () => void {
  const onStorage = (event: StorageEvent) => {
    if (event.key === DENSITY_STORAGE_KEY)
      apply(event.newValue === DENSITY_COMPACT ? "compact" : "comfortable");
  };
  window.addEventListener(EVENT, onChange);
  window.addEventListener("storage", onStorage);
  return () => {
    window.removeEventListener(EVENT, onChange);
    window.removeEventListener("storage", onStorage);
  };
}

/** Switch the density and remember it (best effort). */
export function setDensity(density: Density): void {
  apply(density);
  writePref(PREF_KEY, density);
}

/** `[density, setDensity]` for the whole console. */
export function useDensity(): readonly [Density, (density: Density) => void] {
  const density = useSyncExternalStore(subscribe, snapshot, serverSnapshot);
  // Fallback when the head script did not run (a client-only render).
  useEffect(() => {
    if (readPref(PREF_KEY) === DENSITY_COMPACT && snapshot() !== "compact") apply("compact");
  }, []);
  const set = useCallback((next: Density) => setDensity(next), []);
  return [density, set] as const;
}
