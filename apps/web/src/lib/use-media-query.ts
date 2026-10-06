"use client";

import { useCallback, useSyncExternalStore } from "react";

/**
 * Below this width a `DataTable` with `stacked` shows its rows as a list of cards
 * (docs/17 §5.7). Tailwind `sm` (40rem, 640px).
 */
export const NARROW_QUERY = "(width < 40rem)";

/** Coarse primary pointer and no fine pointer anywhere: a touch-first screen (phone, tablet). */
export const TOUCH_QUERY = "(pointer: coarse) and (not (any-pointer: fine))";

function media(query: string): MediaQueryList | null {
  return typeof window !== "undefined" && typeof window.matchMedia === "function"
    ? window.matchMedia(query)
    : null;
}

/**
 * Whether `query` matches, kept in step with the window (`useSyncExternalStore`). The server
 * and the hydrating render use `serverValue` (default false), so the HTML never mismatches;
 * the browser's value follows in the next render. Where matchMedia is missing (old browsers,
 * jsdom) it stays `serverValue`.
 */
export function useMediaQuery(query: string, serverValue = false): boolean {
  const subscribe = useCallback(
    (onChange: () => void) => {
      const list = media(query);
      list?.addEventListener("change", onChange);
      return () => list?.removeEventListener("change", onChange);
    },
    [query],
  );
  return useSyncExternalStore(
    subscribe,
    () => media(query)?.matches ?? serverValue,
    () => serverValue,
  );
}
