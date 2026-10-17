/**
 * Certificate verification codes as people type them (ADR-0045 decision 6; FR-CERT-022).
 * Mirrors apps/api/app/certificates/codes.py: 16 characters of Crockford base32 (0-9 and A-Z
 * without I L O U). Case, spaces and dashes do not matter; O reads as 0 and I/L as 1, so a
 * misread paper still finds its certificate. Pure module (the proxy and pages import it).
 */

const CODE = /^[0-9A-HJKMNP-TV-Z]{16}$/;
const IGNORED = /[\s\-\u2010-\u2015]+/g;

/** The canonical code for what someone typed or scanned, or null when it cannot be one. */
export function normaliseCode(typed: unknown): string | null {
  if (typeof typed !== "string" || typed.length > 64) return null;
  const text = typed.replace(IGNORED, "").toUpperCase().replace(/O/g, "0").replace(/[IL]/g, "1");
  return CODE.test(text) ? text : null;
}

/** `7K4MQ2XP9HD3RT6W` -> `7K4M-Q2XP-9HD3-RT6W` (as printed). */
export function groupCode(code: string): string {
  return code.replace(/(.{4})(?=.)/g, "$1-");
}
