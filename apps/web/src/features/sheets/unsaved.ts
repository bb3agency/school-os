"use client";

import { useEffect } from "react";

/**
 * Warn before unsaved sheet changes are lost (US-701 AC5; docs/17 §4.1 sheet grid).
 *
 * While `active`:
 * - closing, reloading or leaving the site asks the browser's own "Leave site?" question
 *   (`beforeunload`; the text is the browser's);
 * - a click on a link to another page of SchoolOS asks `message` first. The listener runs in
 *   the capture phase, before the router sees the click, and a "Cancel" stops it there.
 *
 * Links that open elsewhere (`target`, modifier keys), downloads and links to this same page
 * pass through. The browser's back button is covered only when it leaves the app.
 */
export function useUnsavedChangesWarning(active: boolean, message: string): void {
  useEffect(() => {
    if (!active) return;
    const beforeUnload = (event: BeforeUnloadEvent) => event.preventDefault();
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
      if (!window.confirm(message)) {
        event.preventDefault();
        event.stopPropagation();
      }
    };
    window.addEventListener("beforeunload", beforeUnload);
    document.addEventListener("click", onClick, true);
    return () => {
      window.removeEventListener("beforeunload", beforeUnload);
      document.removeEventListener("click", onClick, true);
    };
  }, [active, message]);
}
