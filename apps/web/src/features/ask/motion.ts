"use client";

import { useEffect, useRef, useState, useSyncExternalStore } from "react";

/**
 * Motion helpers for the Ask chat (docs/17 §5.3). Everything that moves is CSS (transform and
 * opacity only); the one script-driven effect is the smooth reveal of streamed text, which is
 * skipped entirely when the viewer asked for reduced motion.
 */

const REDUCE = "(prefers-reduced-motion: reduce)";

function reduceQuery(): MediaQueryList | null {
  return typeof window !== "undefined" && typeof window.matchMedia === "function"
    ? window.matchMedia(REDUCE)
    : null;
}

/**
 * True when motion should be avoided. Without `matchMedia` (very old engines, test DOMs) the
 * page behaves as reduced motion: text appears at once, nothing animates from script.
 */
export function prefersReducedMotion(): boolean {
  const query = reduceQuery();
  return query ? query.matches : true;
}

function subscribe(onChange: () => void): () => void {
  const query = reduceQuery();
  if (!query) return () => undefined;
  query.addEventListener("change", onChange);
  return () => query.removeEventListener("change", onChange);
}

/** `prefers-reduced-motion: reduce`, live (server snapshot: reduced, so SSR never animates). */
export function useReducedMotion(): boolean {
  return useSyncExternalStore(subscribe, prefersReducedMotion, () => true);
}

/** Behaviour for `scrollTo`/`scrollIntoView`: smooth only when motion is welcome. */
export function scrollBehavior(): ScrollBehavior {
  return prefersReducedMotion() ? "auto" : "smooth";
}

// --- smooth reveal of streamed text -----------------------------------------------------------

/** Slowest pace, in characters per second (a calm reading pace, well above speech). */
export const REVEAL_MIN_CPS = 60;
/** The backlog is revealed within about this many milliseconds, so the reveal never lags far. */
export const REVEAL_CATCH_UP_MS = 700;

/**
 * How much of `target` to show after `elapsedMs`, given `shown` characters are shown now.
 * The pace grows with the backlog (fast networks never fall behind), and the cut is moved to
 * the end of the word so a Telugu word (base letter plus vowel signs) is never split while it
 * appears; the last, still-arriving word is shown as it is.
 */
export function nextRevealLength(target: string, shown: number, elapsedMs: number): number {
  if (shown >= target.length) return target.length;
  const backlog = target.length - shown;
  const cps = Math.max(REVEAL_MIN_CPS, (backlog * 1000) / REVEAL_CATCH_UP_MS);
  const step = Math.max(1, Math.round((cps * Math.max(0, elapsedMs)) / 1000));
  const cut = shown + step;
  if (cut >= target.length) return target.length;
  const rest = target.slice(cut);
  const space = rest.search(/\s/u);
  return space === -1 ? target.length : cut + space;
}

/**
 * The part of a growing `target` to show now: revealed at a natural pace with
 * requestAnimationFrame while `active`, instantly under reduced motion or once inactive.
 * A target that is not a continuation of the previous one starts over.
 */
export function useSmoothText(target: string, active: boolean): string {
  const reduced = useReducedMotion();
  // What is shown, and of which text: a text that does not continue it starts over.
  const [view, setView] = useState({ base: target, shown: 0 });
  const progress = useRef({ base: target, shown: 0 });
  const animate = active && !reduced && typeof requestAnimationFrame === "function";

  useEffect(() => {
    if (!animate) return;
    const state = progress.current;
    if (!target.startsWith(state.base)) state.shown = 0;
    state.base = target;
    let frame = 0;
    let last = performance.now();
    const tick = (now: number) => {
      const next = nextRevealLength(target, Math.min(state.shown, target.length), now - last);
      last = now;
      if (next !== state.shown) {
        state.shown = next;
        setView({ base: target, shown: next });
      }
      if (next < target.length) frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [target, animate]);

  if (!animate) return target;
  const shown = target.startsWith(view.base) || view.base.startsWith(target) ? view.shown : 0;
  return target.slice(0, Math.min(shown, target.length));
}
