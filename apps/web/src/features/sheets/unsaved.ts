"use client";

import { useEffect } from "react";

/**
 * Warn before unsaved sheet changes are lost (US-701 AC5; docs/17 §4.1 sheet grid).
 *
 * While `active`:
 * - closing, reloading or leaving the site asks the browser's own "Leave site?" question
 *   (`beforeunload`; the text is the browser's);
 * - a click on a link to another page of SchoolOS asks `message` first. The listener runs in
 *   the capture phase, before the router sees the click, and a "Cancel" stops it there;
 * - the browser's Back (and Forward) button inside SchoolOS asks `message` too, through a
 *   history guard entry (below).
 *
 * Links that open elsewhere (`target`, modifier keys), downloads and links to this same page
 * pass through.
 */
export function useUnsavedChangesWarning(active: boolean, message: string): void {
  useEffect(() => {
    if (!active) return;
    const beforeUnload = (event: BeforeUnloadEvent) => {
      if (!leaving) event.preventDefault();
    };
    const onClick = (event: MouseEvent) => {
      if (event.defaultPrevented || event.button !== 0) return;
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      const link = event.target instanceof Element ? event.target.closest("a[href]") : null;
      if (!(link instanceof HTMLAnchorElement)) return;
      if ((link.target && link.target !== "_self") || link.hasAttribute("download")) return;
      const url = new URL(link.href, window.location.href);
      if (url.origin !== window.location.origin) return; // beforeunload asks
      if (url.pathname === window.location.pathname && url.search === window.location.search) {
        return;
      }
      if (window.confirm(message)) {
        here = false; // the router is about to push a new entry above the guard entry
      } else {
        event.preventDefault();
        event.stopPropagation();
      }
    };
    const release = holdHistory(message);
    window.addEventListener("beforeunload", beforeUnload);
    document.addEventListener("click", onClick, true);
    return () => {
      release();
      window.removeEventListener("beforeunload", beforeUnload);
      document.removeEventListener("click", onClick, true);
    };
  }, [active, message]);
}

/* -------------------------------------------------------------- Back / Forward guard */

/*
 * Baseline Widely Available only (the Navigation API's cancelable `navigate` event is not): a
 * popstate cannot be cancelled, so the first active warning pushes a duplicate of the current
 * history entry (the guard entry: same URL, the router's own state plus a marker). Back then
 * lands on the original entry of the same page, and the `popstate` listener, registered in the
 * capture phase so it runs before the Next.js router's listener on `window`, holds the router
 * back and asks:
 * - Cancel: the guard entry is pushed again; the page never changed.
 * - OK: the guard is dropped and the navigation goes ahead (one more Back when the landing
 *   entry is this same page, otherwise the held popstate is handed to the router).
 *
 * When nothing is unsaved any more the guard entry stays in the history (an entry cannot be
 * removed without a traversal, which would race the next keystroke) but goes quiet: the next
 * Back that leaves it skips the duplicate, so Back still takes one press. One module-level
 * guard serves every hook on the page, so two active warnings never stack two entries.
 */

const KEY = "__sosUnsavedGuard";
const holders = new Map<symbol, string>();
let token: string | null = null; // marker of the guard entry we pushed (null: none)
let guardUrl = "";
let baseState: Record<string, unknown> = {};
let here = false; // the guard entry is the current entry
let leaving = false; // the person chose to leave: no second "Leave site?" question
let listening = false;
let serial = 0;

function isGuard(state: unknown): boolean {
  return (
    token !== null &&
    typeof state === "object" &&
    state !== null &&
    (state as Record<string, unknown>)[KEY] === token
  );
}

function pushGuard(): void {
  token = `${Date.now().toString(36)}-${++serial}`;
  // The router's state (`__NA` and its tree) is copied, so the Next.js router treats the
  // guard entry as its own and does not re-render on the push.
  window.history.pushState({ ...baseState, [KEY]: token }, "", guardUrl);
  here = true;
  if (!listening) {
    window.addEventListener("popstate", onPopState, true);
    listening = true;
  }
}

function dropGuard(): void {
  token = null;
  here = false;
  if (listening) {
    window.removeEventListener("popstate", onPopState, true);
    listening = false;
  }
}

function currentMessage(): string {
  return [...holders.values()].pop() ?? "";
}

function onPopState(event: PopStateEvent): void {
  if (isGuard(event.state)) {
    here = true; // came back to the guard entry: the router restores this page as usual
    return;
  }
  if (!here) return;
  here = false;
  const samePage = window.location.href === guardUrl;
  if (holders.size === 0) {
    // Quiet guard: skip the duplicate entry of this page.
    dropGuard();
    if (samePage) {
      event.stopImmediatePropagation();
      window.history.back();
    }
    return;
  }
  event.stopImmediatePropagation(); // the router stays on this page until the person answers
  if (!window.confirm(currentMessage())) {
    pushGuard();
    return;
  }
  dropGuard();
  leaving = true;
  if (samePage) {
    window.history.back();
    // Nowhere further back (a page opened in a new tab): stay guarded.
    window.setTimeout(() => {
      if (token === null && holders.size > 0 && window.location.href === guardUrl) {
        leaving = false;
        pushGuard();
      }
    }, 500);
  } else {
    window.dispatchEvent(new PopStateEvent("popstate", { state: event.state as unknown }));
  }
}

function holdHistory(message: string): () => void {
  const id = Symbol("unsaved");
  if (holders.size === 0) {
    leaving = false;
    if (!(token !== null && here)) {
      guardUrl = window.location.href;
      const state: unknown = window.history.state;
      baseState = typeof state === "object" && state !== null ? { ...state } : {};
      delete baseState[KEY];
      pushGuard();
    }
  }
  holders.set(id, message);
  return () => {
    holders.delete(id);
  };
}

/** Tests only: forget the guard between test cases. */
export function resetUnsavedGuardForTesting(): void {
  holders.clear();
  dropGuard();
  leaving = false;
}
