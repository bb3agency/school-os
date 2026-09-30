/**
 * Motion tokens shared by CSS and Motion (docs/17 §2.6 and §5.5; NFR-A11Y-001).
 *
 * The same values are written as custom properties in `app/globals.css` (`--ease-*`,
 * `--duration-*`); `lib/motion.test.ts` fails when the two drift apart. A crisp work tool
 * earns few, short motions: everything that enters uses `EASE_OUT`, on-screen movement
 * `EASE_IN_OUT`, the menu drawer `EASE_DRAWER`, and UI motion stays under 300ms.
 *
 * Curves are cubic-bezier control points; durations are milliseconds (Motion takes seconds:
 * use `seconds()`).
 */

export type Bezier = readonly [number, number, number, number];

/** Strong ease-out for anything that enters or exits (dialogs, popovers, alerts). */
export const EASE_OUT: Bezier = [0.23, 1, 0.32, 1];
/** Strong ease-in-out for elements moving on screen. */
export const EASE_IN_OUT: Bezier = [0.77, 0, 0.175, 1];
/** iOS-like sheet curve for the menu drawer. */
export const EASE_DRAWER: Bezier = [0.32, 0.72, 0, 1];

export const DURATION = {
  /** Button press feedback (scale 0.97 on :active). */
  press: 140,
  /** Small popovers and menus (notification panel), exits of dialogs and panels. */
  quick: 150,
  /** Dialogs, alerts, a changed number, content replacing a skeleton. */
  enter: 200,
  /** The menu drawer sliding in. */
  drawer: 260,
} as const;

export type DurationName = keyof typeof DURATION;

/** A duration token in seconds, for Motion's `transition.duration`. */
export function seconds(name: DurationName): number {
  return DURATION[name] / 1000;
}

/** The curve as CSS text (`cubic-bezier(…)`), e.g. for the Web Animations API. */
export function cssEasing(curve: Bezier): string {
  return `cubic-bezier(${curve.join(", ")})`;
}

/** True when the viewer asked the system for less motion (false where matchMedia is missing). */
export function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}
