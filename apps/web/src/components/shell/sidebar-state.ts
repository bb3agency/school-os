import { useCallback, useEffect, useSyncExternalStore } from "react";

/**
 * Compact (icon-only) sidebar, remembered per viewer on this computer (docs/17 §5.2).
 *
 * The choice is a UI preference, not personal data or a token, so it may live in browser
 * storage (docs/13 §5 forbids tokens and personal data there). Storage can be missing or
 * throw (private windows, blocked site data): every access is wrapped, and the sidebar is
 * simply expanded then.
 *
 * The state is the `data-sidebar="collapsed"` attribute on <html>. The root layout runs
 * SIDEBAR_STATE_SCRIPT (sidebar-script.ts) in <head>, with the CSP nonce, before the body
 * is parsed, so the compact width is right on the first paint (no layout shift); CSS (the
 * `collapsed:` variant in globals.css) does the rest. React reads the attribute only for labels, after hydration
 * (the server snapshot is "expanded", so hydration never mismatches).
 */

import {
  SIDEBAR_ATTRIBUTE,
  SIDEBAR_COLLAPSED as COLLAPSED,
  SIDEBAR_STORAGE_KEY,
} from "./sidebar-script";

const EVENT = "sos:sidebar";

function storage(): Storage | null {
  try {
    // A UI preference only (see above); never tokens or personal data.
    // eslint-disable-next-line no-restricted-properties
    return window.localStorage;
  } catch {
    return null;
  }
}

function readStored(): boolean {
  try {
    return storage()?.getItem(SIDEBAR_STORAGE_KEY) === COLLAPSED;
  } catch {
    return false;
  }
}

function apply(collapsed: boolean): void {
  const root = document.documentElement;
  if (collapsed) root.setAttribute(SIDEBAR_ATTRIBUTE, COLLAPSED);
  else root.removeAttribute(SIDEBAR_ATTRIBUTE);
  window.dispatchEvent(new Event(EVENT));
}

function snapshot(): boolean {
  return document.documentElement.getAttribute(SIDEBAR_ATTRIBUTE) === COLLAPSED;
}

function serverSnapshot(): boolean {
  return false;
}

function subscribe(onChange: () => void): () => void {
  // Another tab changed the choice: follow it.
  const onStorage = (event: StorageEvent) => {
    if (event.key === SIDEBAR_STORAGE_KEY) apply(event.newValue === COLLAPSED);
  };
  window.addEventListener(EVENT, onChange);
  window.addEventListener("storage", onStorage);
  return () => {
    window.removeEventListener(EVENT, onChange);
    window.removeEventListener("storage", onStorage);
  };
}

/** Collapse or expand the sidebar and remember it (best effort). */
export function setSidebarCollapsed(collapsed: boolean): void {
  apply(collapsed);
  try {
    storage()?.setItem(SIDEBAR_STORAGE_KEY, collapsed ? COLLAPSED : "expanded");
  } catch {
    // Storage full or blocked: the choice lasts for this page only.
  }
}

/** `[collapsed, toggle]` for the sidebar's compact mode. */
export function useSidebarCollapsed(): readonly [boolean, () => void] {
  const collapsed = useSyncExternalStore(subscribe, snapshot, serverSnapshot);
  // Fallback when the head script did not run (e.g. a client-only render): apply the stored
  // choice once after mounting.
  useEffect(() => {
    if (readStored() && !snapshot()) apply(true);
  }, []);
  const toggle = useCallback(() => setSidebarCollapsed(!snapshot()), []);
  return [collapsed, toggle] as const;
}
