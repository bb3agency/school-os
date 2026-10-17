/**
 * Compact-sidebar preference, shared by the root layout (server) and the sidebar state hook
 * (sidebar-state.ts). No React here, so a Server Component may import it.
 */

export const SIDEBAR_STORAGE_KEY = "sos.sidebar";
export const SIDEBAR_ATTRIBUTE = "data-sidebar";
export const SIDEBAR_COLLAPSED = "collapsed";

/**
 * Runs inline in <head> before the body is parsed, so a compact sidebar has its compact width
 * on the first paint. Plain ES5, constant text (no user input); any failure (storage blocked
 * or missing) leaves the sidebar expanded.
 */
export const SIDEBAR_STATE_SCRIPT = `try{if(window.localStorage.getItem("${SIDEBAR_STORAGE_KEY}")==="${SIDEBAR_COLLAPSED}")document.documentElement.setAttribute("${SIDEBAR_ATTRIBUTE}","${SIDEBAR_COLLAPSED}")}catch(e){}`;

/** Density preference (docs/17 §3.4): "compact", or absent for comfortable. */
export const DENSITY_STORAGE_KEY = "sos.density";
export const DENSITY_ATTRIBUTE = "data-density";
export const DENSITY_COMPACT = "compact";

/**
 * Same idea for the density: `<html data-density="compact">` before the first paint, so
 * compact rows never jump from comfortable after hydration. Constant text, ES5.
 */
export const DENSITY_STATE_SCRIPT = `try{if(window.localStorage.getItem("${DENSITY_STORAGE_KEY}")==="${DENSITY_COMPACT}")document.documentElement.setAttribute("${DENSITY_ATTRIBUTE}","${DENSITY_COMPACT}")}catch(e){}`;

/** Both preferences, in the one inline <head> script the root layout runs with its nonce. */
export const SHELL_STATE_SCRIPT = `${SIDEBAR_STATE_SCRIPT}${DENSITY_STATE_SCRIPT}`;
