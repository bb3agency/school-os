import { describe, expect, it } from "vitest";
import { ALL_SCHOOLS_MAX_DAYS, usageRangeTooLong } from "./OperationsViews";

/**
 * Operator usage (FR-PLT-020; audit 2026-11-08 AZ-03): every school's usage is one unpaged
 * list, so the API takes at most 31 days without a school (422 `range_too_long`). The screen
 * does not send such a query; it explains the limit instead. One school keeps 400 days.
 */
const TODAY = "2026-11-08";
const SCHOOL = "0192f3a4-0000-7000-8000-00000000000a";

describe("usageRangeTooLong", () => {
  it("allows 31 days for all schools and refuses 32", () => {
    expect(ALL_SCHOOLS_MAX_DAYS).toBe(31);
    expect(usageRangeTooLong({ from: "2026-10-01", to: "2026-10-31" }, TODAY)).toBe(false);
    expect(usageRangeTooLong({ from: "2026-09-30", to: "2026-10-31" }, TODAY)).toBe(true);
  });

  it("an open end counts to today; no from is the API's last 31 days", () => {
    expect(usageRangeTooLong({ from: "2026-10-09" }, TODAY)).toBe(false);
    expect(usageRangeTooLong({ from: "2026-10-08" }, TODAY)).toBe(true);
    expect(usageRangeTooLong({}, TODAY)).toBe(false);
    expect(usageRangeTooLong({ to: "2026-01-01" }, TODAY)).toBe(false);
  });

  it("one school is never limited here (the API allows 400 days)", () => {
    expect(
      usageRangeTooLong({ from: "2026-01-01", to: "2026-10-31", tenant_id: SCHOOL }, TODAY),
    ).toBe(false);
  });
});
