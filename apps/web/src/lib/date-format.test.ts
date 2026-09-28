import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import {
  DATE_FORMATS,
  DEFAULT_DATE_FORMAT,
  formatDisplayDate,
  formatDisplayDateTime,
  isoToTypedDate,
  schoolDateFormat,
  setSchoolDateFormat,
  toDateFormat,
  typedDateToIso,
  useDateInput,
  useSchoolDateFormat,
} from "./date-format";
import { formatDate, formatDateTime } from "./format";

afterEach(() => {
  setSchoolDateFormat(null);
});

describe("display dates follow the school's date_format (FR-TEN-012)", () => {
  it("knows the API's three formats and defaults to DD/MM/YYYY (PRD §8)", () => {
    expect(DATE_FORMATS).toEqual(["DD/MM/YYYY", "DD-MM-YYYY", "YYYY-MM-DD"]);
    expect(DEFAULT_DATE_FORMAT).toBe("DD/MM/YYYY");
    expect(toDateFormat("YYYY-MM-DD")).toBe("YYYY-MM-DD");
    for (const value of [undefined, null, "", "MM/DD/YYYY", 3, {}]) {
      expect(toDateFormat(value)).toBe("DD/MM/YYYY");
    }
  });

  it("formats a calendar date in each format without shifting the day", () => {
    expect(formatDisplayDate("2026-06-01", "DD/MM/YYYY")).toBe("01/06/2026");
    expect(formatDisplayDate("2026-06-01", "DD-MM-YYYY")).toBe("01-06-2026");
    expect(formatDisplayDate("2026-06-01", "YYYY-MM-DD")).toBe("2026-06-01");
  });

  it("formats timestamps on the India calendar day, with a 24-hour IST time", () => {
    // 20:00 UTC on 31 May is 01:30 IST on 1 June.
    expect(formatDisplayDate("2026-05-31T20:00:00Z", "YYYY-MM-DD")).toBe("2026-06-01");
    expect(formatDisplayDateTime("2026-05-31T20:00:00Z", "DD-MM-YYYY")).toBe("01-06-2026 01:30");
    expect(formatDisplayDateTime("2026-05-31T20:00:00Z", "YYYY-MM-DD")).toBe("2026-06-01 01:30");
  });

  it("returns null for empty or invalid values", () => {
    expect(formatDisplayDate(null, "YYYY-MM-DD")).toBeNull();
    expect(formatDisplayDate(undefined, "YYYY-MM-DD")).toBeNull();
    expect(formatDisplayDate("", "YYYY-MM-DD")).toBeNull();
    expect(formatDisplayDate("not a date", "YYYY-MM-DD")).toBeNull();
    expect(formatDisplayDateTime("2026-06-01T25:99:00Z", "YYYY-MM-DD")).toBeNull();
  });

  it("formatDate/formatDateTime use the school's format once /me has set it", () => {
    expect(formatDate("2026-06-01")).toBe("01/06/2026");
    setSchoolDateFormat("YYYY-MM-DD");
    expect(schoolDateFormat()).toBe("YYYY-MM-DD");
    expect(formatDate("2026-06-01")).toBe("2026-06-01");
    expect(formatDateTime("2026-05-31T20:00:00Z")).toBe("2026-06-01 01:30");
    // An explicit format still wins (e.g. platform screens that are not about one school).
    expect(formatDate("2026-06-01", "DD/MM/YYYY")).toBe("01/06/2026");
    // Unknown values fall back to the default.
    setSchoolDateFormat("MM/DD/YYYY");
    expect(formatDate("2026-06-01")).toBe("01/06/2026");
  });

  it("useSchoolDateFormat re-renders when the school's format arrives", () => {
    const { result } = renderHook(() => useSchoolDateFormat());
    expect(result.current).toBe("DD/MM/YYYY");
    act(() => setSchoolDateFormat("DD-MM-YYYY"));
    expect(result.current).toBe("DD-MM-YYYY");
  });
});

describe("typed dates follow the school's date_format (FR-TEN-012, PRD §8)", () => {
  it("reads what the office types in any of the school formats, and ISO", () => {
    for (const typed of ["14/03/2012", "14-03-2012", "14.03.2012", "2012-03-14", " 2012/3/14 "]) {
      expect(typedDateToIso(typed)).toBe("2012-03-14");
    }
    expect(typedDateToIso("4/3/2012")).toBe("2012-03-04");
    expect(typedDateToIso("29/02/2028")).toBe("2028-02-29");
  });

  it("refuses impossible dates, two-digit years and other shapes", () => {
    for (const typed of ["31/02/2012", "29/02/2027", "14/03/12", "2012-13-01", "", "soon"]) {
      expect(typedDateToIso(typed)).toBeNull();
    }
  });

  it("shows starting values in the school's format", () => {
    expect(isoToTypedDate("2012-03-14", "DD/MM/YYYY")).toBe("14/03/2012");
    expect(isoToTypedDate("2012-03-14", "DD-MM-YYYY")).toBe("14-03-2012");
    expect(isoToTypedDate("2012-03-14", "YYYY-MM-DD")).toBe("2012-03-14");
    // Anything that is not an ISO date is left as typed.
    expect(isoToTypedDate("14/03/2012", "YYYY-MM-DD")).toBe("14/03/2012");
    expect(isoToTypedDate("", "DD-MM-YYYY")).toBe("");
    setSchoolDateFormat("DD-MM-YYYY");
    expect(isoToTypedDate("2012-03-14")).toBe("14-03-2012");
    for (const format of DATE_FORMATS) {
      expect(typedDateToIso(isoToTypedDate("2026-06-01", format))).toBe("2026-06-01");
    }
  });

  it("useDateInput gives the placeholder and hint values in the school's format", () => {
    const { result } = renderHook(() => useDateInput());
    expect(result.current.placeholder).toBe("DD/MM/YYYY");
    expect(result.current.hint("2012-03-14")).toEqual({
      format: "DD/MM/YYYY",
      example: "14/03/2012",
    });
    act(() => setSchoolDateFormat("YYYY-MM-DD"));
    expect(result.current.placeholder).toBe("YYYY-MM-DD");
    expect(result.current.hint("2012-03-14")).toEqual({
      format: "YYYY-MM-DD",
      example: "2012-03-14",
    });
    expect(result.current.fromIso("2026-06-01")).toBe("2026-06-01");
    expect(result.current.toIso("01/06/2026")).toBe("2026-06-01");
  });
});
