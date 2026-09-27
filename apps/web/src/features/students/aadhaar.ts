/**
 * US-303 / FR-STU-012 / BR-02: nobody should type or paste a full Aadhaar number. The API
 * refuses one in any field (422 `aadhaar_full_number_rejected`); this client check only
 * explains it before the request is sent. Same rule as the API: 12 digits (spaces or hyphens
 * allowed between groups) that pass the Verhoeff check, first digit 2–9.
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

/** Verhoeff checksum over a digit string (the last digit is the check digit). */
export function verhoeffValid(digits: string): boolean {
  if (!/^\d+$/.test(digits)) return false;
  let check = 0;
  const reversed = [...digits].reverse();
  for (let i = 0; i < reversed.length; i += 1) {
    const digit = Number(reversed[i]);
    const permuted = P[i % 8]?.[digit] ?? 0;
    check = D[check]?.[permuted] ?? 0;
  }
  return check === 0;
}

const CANDIDATE = /(?<!\d)([2-9]\d{3})[\s-]?(\d{4})[\s-]?(\d{4})(?!\d)/g;

/** True when the text holds something that looks like a full Aadhaar number. */
export function containsFullAadhaar(text: string): boolean {
  for (const match of text.matchAll(CANDIDATE)) {
    const digits = `${match[1] ?? ""}${match[2] ?? ""}${match[3] ?? ""}`;
    if (verhoeffValid(digits)) return true;
  }
  return false;
}
