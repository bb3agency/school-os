import { describe, expect, it } from "vitest";
import {
  formatBytes,
  formatCount,
  formatDate,
  formatDateTime,
  formatInr,
  formatList,
} from "./format";

describe("Indian formatting conventions (PRD §8)", () => {
  it("formats dates as DD/MM/YYYY", () => {
    expect(formatDate("2026-06-01")).toBe("01/06/2026");
    // 20:00 UTC on 31 May is 01:30 IST on 1 June.
    expect(formatDate("2026-05-31T20:00:00Z")).toBe("01/06/2026");
    expect(formatDate(null)).toBeNull();
    expect(formatDate("not a date")).toBeNull();
  });

  it("formats timestamps in IST, 24-hour", () => {
    expect(formatDateTime("2026-05-31T20:00:00Z")).toBe("01/06/2026 01:30");
  });

  it("formats rupees with lakh grouping and Latin digits in both languages", () => {
    expect(formatInr("123456.5", "en")).toBe("₹1,23,456.50");
    expect(formatInr("123456.5", "te")).toMatch(/1,23,456\.50/);
    expect(formatInr(null, "en")).toBeNull();
    expect(formatInr("abc", "en")).toBeNull();
  });

  it("formats counts, sizes and lists", () => {
    expect(formatCount(1500000, "en")).toBe("15,00,000");
    expect(formatBytes(1536, "en")).toBe("1.5 kB");
    expect(formatList(["Owner", "Principal"], "en")).toBe("Owner and Principal");
  });
});
