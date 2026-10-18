"use client";

import { useEffect, useState } from "react";

/**
 * On-screen keyboard detection for phones (docs/17 §5.7): the bottom bar hides while the
 * keyboard is up, so it never sits on top of the field being typed in, a SaveBar or the Ask
 * composer.
 *
 * Browsers do not report the keyboard directly. Two signals, either one is enough:
 * - the visual viewport is much shorter than the tallest it has been at this width (works
 *   with `interactive-widget=resizes-content` on Android, where the layout viewport shrinks
 *   too, and with Safari, where only the visual viewport does);
 * - on a touch screen, a text field has focus (a keyboard opens for it).
 * Rotating the phone (a new width) starts a new baseline.
 */

/** Below this share of the tallest height seen at this width, a keyboard covers the page. */
export const KEYBOARD_RATIO = 0.75;

export interface ViewportSize {
  width: number;
  height: number;
}

/** Tallest height seen at the current width (a new width resets it). */
export function nextBaseline(previous: ViewportSize | null, current: ViewportSize): ViewportSize {
  if (!previous || Math.abs(previous.width - current.width) > 1) return current;
  return current.height > previous.height ? current : previous;
}

/** The visual viewport lost more than a quarter of its height at the same width. */
export function viewportSuggestsKeyboard(baseline: ViewportSize, current: ViewportSize): boolean {
  if (Math.abs(baseline.width - current.width) > 1 || baseline.height <= 0) return false;
  return current.height < baseline.height * KEYBOARD_RATIO;
}

const TEXT_INPUT_TYPES = new Set([
  "",
  "text",
  "search",
  "email",
  "tel",
  "url",
  "password",
  "number",
  "date",
  "datetime-local",
  "month",
  "time",
  "week",
]);

/** An element the on-screen keyboard opens for (text inputs, textareas, editable content). */
export function opensKeyboard(element: Element | null): boolean {
  if (!element) return false;
  if (element instanceof HTMLTextAreaElement) return !element.readOnly && !element.disabled;
  if (element instanceof HTMLInputElement) {
    return (
      TEXT_INPUT_TYPES.has(element.type.toLowerCase()) && !element.readOnly && !element.disabled
    );
  }
  return element instanceof HTMLElement && element.isContentEditable === true;
}

function coarsePointer(): boolean {
  return typeof window.matchMedia === "function" && window.matchMedia("(pointer: coarse)").matches;
}

/** True while the on-screen keyboard is (very likely) open. Always false on the server. */
export function useOnScreenKeyboard(): boolean {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const viewport = window.visualViewport ?? null;
    let baseline: ViewportSize | null = null;
    let byViewport = false;
    let byFocus = false;
    const publish = () => setOpen(byViewport || byFocus);

    const onResize = () => {
      if (!viewport) return;
      const current = { width: viewport.width, height: viewport.height };
      baseline = nextBaseline(baseline, current);
      byViewport = viewportSuggestsKeyboard(baseline, current);
      publish();
    };
    const onFocus = () => {
      byFocus = coarsePointer() && opensKeyboard(document.activeElement);
      publish();
    };
    // focusout fires before the next element has focus: look once focus has settled.
    const onFocusOut = () => {
      window.setTimeout(onFocus, 0);
    };

    onResize();
    onFocus();
    viewport?.addEventListener("resize", onResize);
    document.addEventListener("focusin", onFocus);
    document.addEventListener("focusout", onFocusOut);
    return () => {
      viewport?.removeEventListener("resize", onResize);
      document.removeEventListener("focusin", onFocus);
      document.removeEventListener("focusout", onFocusOut);
    };
  }, []);

  return open;
}
