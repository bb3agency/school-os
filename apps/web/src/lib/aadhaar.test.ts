import { describe, expect, it } from "vitest";
import { containsAadhaarNumber, verhoeffValid } from "./aadhaar";

const SERVER_VECTORS = [
  "608756022628",
  "770086703123",
  "473477610964",
  "396582268825",
  "615269690303",
  "587722121294",
  "543896182326",
  "597923868319",
] as const;

/** Synthetic numbers only (built with the Verhoeff check digit, not real Aadhaar numbers). */
describe("invariant 4: full Aadhaar numbers are refused before sending", () => {
  it("implements the Verhoeff check", () => {
    expect(verhoeffValid("2363")).toBe(true);
    expect(verhoeffValid("2364")).toBe(false);
    expect(verhoeffValid("12a")).toBe(false);
  });

  it("finds a Verhoeff-valid 12-digit number, grouped or not", () => {
    // 23412341234 + check digit
    const digits = ["2", "3", "4", "1", "2", "3", "4", "1", "2", "3", "4"];
    const check = [..."0123456789"].find((d) => verhoeffValid(`${digits.join("")}${d}`)) ?? "";
    const valid = `${digits.join("")}${check}`;
    expect(containsAadhaarNumber(`Aadhaar ${valid} on file`)).toBe(true);
    expect(
      containsAadhaarNumber(`${valid.slice(0, 4)} ${valid.slice(4, 8)} ${valid.slice(8)}`),
    ).toBe(true);
    expect(
      containsAadhaarNumber(`${valid.slice(0, 4)}-${valid.slice(4, 8)}-${valid.slice(8)}`),
    ).toBe(true);
  });

  it("leaves last-4 digits, phone numbers and longer digit runs alone", () => {
    expect(containsAadhaarNumber("Aadhaar ends with 1234")).toBe(false);
    expect(containsAadhaarNumber("Phone 9876543210")).toBe(false);
    expect(containsAadhaarNumber("Receipt 1234567890123456")).toBe(false);
    expect(containsAadhaarNumber("Born 01/06/2012")).toBe(false);
  });

  it("uses the standard Verhoeff tables (same vectors as core.redaction)", () => {
    // Synthetic vectors computed with core.redaction.verhoeff_check_digit.
    expect(verhoeffValid("2363")).toBe(true);
    for (const valid of SERVER_VECTORS) {
      expect(verhoeffValid(valid)).toBe(true);
      const flipped = `${valid.slice(0, -1)}${(Number(valid.at(-1)) + 1) % 10}`;
      expect(verhoeffValid(flipped)).toBe(false);
    }
  });

  it("finds grouped numbers whatever separators join the groups (D52)", () => {
    const [valid] = SERVER_VECTORS;
    const [a, b, c] = [valid.slice(0, 4), valid.slice(4, 8), valid.slice(8)];
    for (const text of [
      `${a}.${b}/${c}`,
      `${a} - ${b} - ${c}`,
      `${a}\u00a0\u00a0${b}_${c}`,
      `${a}/ ${b} .${c}`,
      `${a}\u200b${b}\u202f${c}`,
      `${a}\u2014${b}\u2212${c}`,
      `${a},${b},${c}`,
    ]) {
      expect(containsAadhaarNumber(`Ref ${text} end`)).toBe(true);
    }
    expect(containsAadhaarNumber(`${a}._/.${b}._/.${c}`)).toBe(false);
    const invalid = `${valid.slice(0, -1)}${(Number(valid.at(-1)) + 1) % 10}`;
    expect(
      containsAadhaarNumber(`${invalid.slice(0, 4)}.${invalid.slice(4, 8)}/${invalid.slice(8)}`),
    ).toBe(false);
  });

  it("scans long adversarial input quickly", () => {
    const started = performance.now();
    containsAadhaarNumber("1234._/".repeat(20000));
    containsAadhaarNumber("1".repeat(100000));
    expect(performance.now() - started).toBeLessThan(2000);
  });
});
