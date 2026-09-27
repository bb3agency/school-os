import { describe, expect, it } from "vitest";
import { containsAadhaarNumber, verhoeffValid } from "./aadhaar";

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
});
