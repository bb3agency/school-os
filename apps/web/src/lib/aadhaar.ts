/**
 * Client-side guard for invariant 4 (no Aadhaar numbers, ever): finds 12-digit sequences,
 * ungrouped or grouped 4-4-4 (see `AADHAAR_GROUP_SEPARATOR`: whitespace gaps of any width,
 * dashes and the table separators `|` `:` `*` with gaps around them, 1-3 of whitespace, dashes,
 * dot, slash, underscore and invisible format characters in any mix, or a single comma; lead
 * decision D52 as extended on 2026-11-08), that pass the Verhoeff check, as `core.redaction` does
 * on the server. Forms refuse such text before it is sent; the API
 * refuses it again (`aadhaar_full_number_rejected`). Never logs or returns the number.
 */

const D = [
  [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
  [1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
  [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
  [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
  [4, 0, 1, 2, 3, 9, 5, 6, 7, 8],
  [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
  [6, 5, 9, 8, 7, 1, 0, 4, 3, 2],
  [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
  [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
  [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
] as const;

const P = [
  [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
  [1, 5, 7, 6, 2, 8, 3, 0, 9, 4],
  [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
  [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
  [9, 4, 5, 3, 1, 2, 6, 8, 7, 0],
  [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
  [2, 7, 9, 3, 8, 0, 6, 4, 1, 5],
  [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
] as const;

/** Verhoeff checksum over a string of digits (the last digit is the check digit). */
export function verhoeffValid(digits: string): boolean {
  if (!/^\d+$/.test(digits)) return false;
  let check = 0;
  const reversed = [...digits].reverse();
  for (let i = 0; i < reversed.length; i += 1) {
    const digit = Number(reversed[i]);
    const p = P[i % 8]?.[digit] ?? 0;
    check = D[check]?.[p] ?? 0;
  }
  return check === 0;
}

/**
 * What may join two 4-digit groups (the same rule as core.redaction):
 * - a whitespace or format-character (Cf) gap of any width (audit 2026-11-08 DPA-01: the
 *   server collapses whitespace, so every gap is a join);
 * - one dash (Pd, minus) or table separator `|` `:` `*` with any gap around it (DPA-03, lead
 *   decision 2026-11-08 extending D52);
 * - 1-3 of whitespace, dashes, dot, slash, underscore and Cf in any mix, or one comma (D52).
 * Gaps, separators and digits are disjoint and each try is anchored on 4 digits, so matching
 * stays linear. `x` and parentheses are not separators.
 */
export const AADHAAR_GROUP_SEPARATOR = String.raw`(?:[\s\p{Cf}]*[\p{Pd}\u2212|:*][\s\p{Cf}]*|[\s\p{Cf}]+|[\s\p{Cf}\p{Pd}\u2212._/]{1,3}|,)`;

// 12 digits not touching other digits, ungrouped or grouped 4-4-4. Lookahead so that
// overlapping candidates (a fourth group) are all checked.
const CANDIDATE = new RegExp(
  String.raw`(?<!\d)(?=(\d{4})(?:${AADHAAR_GROUP_SEPARATOR})?(\d{4})(?:${AADHAAR_GROUP_SEPARATOR})?(\d{4})(?!\d))`,
  "gu",
);

/** True when the text holds something that looks like a full Aadhaar number. */
export function containsAadhaarNumber(text: string): boolean {
  for (const match of text.matchAll(CANDIDATE)) {
    if (verhoeffValid(`${match[1]}${match[2]}${match[3]}`)) return true;
  }
  return false;
}
