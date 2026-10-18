"use client";

import { useEffect } from "react";

const REDUCE = "(prefers-reduced-motion: reduce)";
/** A touch screen as the main pointer (phones, tablets): their own scrolling is left alone. */
const COARSE = "(pointer: coarse)";

type LenisInstance = {
  scrollTo: (
    target: HTMLElement,
    options?: { onComplete?: () => void; immediate?: boolean },
  ) => void;
  destroy: () => void;
  /** Stop and start again: ends a glide in progress (Lenis 1.3 public API). */
  stop: () => void;
  start: () => void;
  isScrolling: boolean | "native" | "smooth";
};

/** Keys whose default action scrolls the page (native; Lenis only follows them). */
const SCROLL_KEYS = new Set(["PageDown", "PageUp", " ", "ArrowDown", "ArrowUp", "Home", "End"]);

/**
 * Smooth wheel scrolling on the marketing home page only (owner request 2026-10-18; docs/17
 * §5.6), with Lenis (MIT, pinned in apps/web/package.json; dependency check in docs/17 §5.6).
 *
 * - Mounted only by `HomeView`; never in the staff app, the operator console or the parent
 *   and verification pages. The library is imported after hydration, so it is its own small
 *   chunk off the critical path, and nothing is server-rendered (no style attribute; CSP).
 * - Not created at all under `prefers-reduced-motion: reduce` or on a touch screen
 *   (`pointer: coarse`: phones and tablets keep their native, momentum scrolling and nothing
 *   is downloaded; docs/17 §5.7), and destroyed if either becomes true while the page is open
 *   (and on unmount).
 * - Only the wheel is smoothed (`smoothWheel`; touch stays native, `syncTouch` off). Keyboard
 *   scrolling (arrows, Page Up/Down, Space, Home/End) and focus scrolling stay the browser's
 *   own: Lenis follows native scroll positions.
 * - In-page links (`href="#…"`) clicked with a pointer glide to their target with
 *   `scrollTo`, which honours the target's `scroll-margin-top` and the root's
 *   `scroll-padding-top` (the sticky header), then move focus to the target as the browser
 *   would and update the address bar. Links activated from the keyboard (`event.detail` is 0)
 *   and the skip link keep the native, instant jump.
 * - A scroll key pressed while a wheel glide is still running ends the glide first, so the
 *   key's own (native) scroll is never overwritten by the glide's next frame.
 * - Lenis writes styles only through the CSSOM (`element.style.setProperty`) and classes on
 *   <html>; it injects no <style> element, so `style-src 'self'` holds. The few rules of its
 *   recommended CSS live in marketing.css.
 */
export function SmoothScroll() {
  useEffect(() => {
    // Without matchMedia (very old browsers, test DOMs) the reduced-motion setting cannot be
    // read, so the page keeps native scrolling.
    if (typeof window.matchMedia !== "function") return;
    const query = window.matchMedia(REDUCE);
    const coarse = window.matchMedia(COARSE);
    const off = () => query.matches || coarse.matches;
    let lenis: LenisInstance | null = null;
    let cancelled = false;

    const onClick = (event: MouseEvent) => {
      if (!lenis || event.defaultPrevented || event.button !== 0 || event.detail === 0) return;
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      const link = (event.target as Element | null)?.closest?.("a[href^='#']");
      if (!(link instanceof HTMLAnchorElement)) return;
      const hash = link.getAttribute("href") ?? "";
      if (hash.length < 2 || hash === "#main") return;
      const target = document.getElementById(decodeURIComponent(hash.slice(1)));
      if (!target) return;
      event.preventDefault();
      lenis.scrollTo(target, {
        onComplete: () => {
          if (!target.hasAttribute("tabindex") && !isFocusable(target)) {
            target.setAttribute("tabindex", "-1");
          }
          target.focus({ preventScroll: true });
          if (window.location.hash !== hash) window.history.pushState(null, "", hash);
        },
      });
    };

    const onKeyDown = (event: KeyboardEvent) => {
      if (!lenis || lenis.isScrolling !== "smooth" || !SCROLL_KEYS.has(event.key)) return;
      lenis.stop();
      lenis.start();
    };

    const start = async () => {
      if (lenis || off()) return;
      const { default: Lenis } = await import("lenis");
      if (cancelled || off() || lenis) return;
      lenis = new Lenis({ autoRaf: true, smoothWheel: true, syncTouch: false, anchors: false });
    };
    const stop = () => {
      lenis?.destroy();
      lenis = null;
    };
    const onChange = () => {
      if (off()) stop();
      else void start();
    };

    void start();
    query.addEventListener("change", onChange);
    coarse.addEventListener("change", onChange);
    document.addEventListener("click", onClick);
    document.addEventListener("keydown", onKeyDown, true);
    return () => {
      cancelled = true;
      query.removeEventListener("change", onChange);
      coarse.removeEventListener("change", onChange);
      document.removeEventListener("click", onClick);
      document.removeEventListener("keydown", onKeyDown, true);
      stop();
    };
  }, []);
  return null;
}

function isFocusable(element: HTMLElement): boolean {
  return element.matches("a[href], button, input, select, textarea, summary, [tabindex]");
}
