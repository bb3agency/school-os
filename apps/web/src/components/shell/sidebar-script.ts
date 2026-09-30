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
