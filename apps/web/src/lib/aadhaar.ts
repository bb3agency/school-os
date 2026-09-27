/**
 * Client-side guard for invariant 4 (no Aadhaar numbers, ever): finds 12-digit sequences
 * (optionally grouped 4-4-4 by spaces or hyphens) that pass the Verhoeff check, the same
 * rule `core.redaction` uses on the server. Forms refuse such text before it is sent; the API
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
  [9, 4, 5, 3, 1, 2, 7, 6, 8, 0],
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

// 12 digits not touching other digits; groups of 4 may be separated by one space or hyphen.
const CANDIDATE = /(?<!\d)(\d{4})[ -]?(\d{4})[ -]?(\d{4})(?!\d)/g;

/** True when the text holds something that looks like a full Aadhaar number. */
export function containsAadhaarNumber(text: string): boolean {
  for (const match of text.matchAll(CANDIDATE)) {
    if (verhoeffValid(`${match[1]}${match[2]}${match[3]}`)) return true;
  }
  return false;
}
