"use client";

import { useLayoutEffect, type RefObject } from "react";

/** Room kept between an overlay panel and the edge of the screen (px). */
export const EDGE_MARGIN = 8;

/**
 * How far (px) to move a panel sideways so it stays on screen: positive moves it right,
 * negative left, 0 leaves it. A panel wider than the screen keeps its start edge in view.
 */
export function inlineShift(
  left: number,
  right: number,
  viewportWidth: number,
  margin = EDGE_MARGIN,
): number {
  if (viewportWidth <= 0 || right <= left) return 0;
  if (left < margin) return margin - left;
  const overflow = right - (viewportWidth - margin);
  if (overflow > 0) return Math.max(margin - left, -overflow);
  return 0;
}

/**
 * Keeps an open menu or popover panel inside the screen (docs/17 §5.7): a panel anchored to a
 * trigger near the edge of a 360px phone would otherwise stick out and make the page scroll
 * sideways. Measures after layout and nudges the panel with the CSSOM `translate` property
 * (allowed by the CSP; no style attribute is ever rendered). Does nothing when it fits.
 */
export function useKeepInViewport(ref: RefObject<HTMLElement | null>, open: boolean): void {
  useLayoutEffect(() => {
    const panel = ref.current;
    if (!open || !panel) return;
    const place = () => {
      panel.style.translate = "";
      const rect = panel.getBoundingClientRect();
      const width = document.documentElement.clientWidth || window.innerWidth;
      const shift = inlineShift(rect.left, rect.right, width);
      if (shift !== 0) panel.style.translate = `${Math.round(shift)}px 0`;
    };
    place();
    window.addEventListener("resize", place);
    return () => window.removeEventListener("resize", place);
  }, [ref, open]);
}
