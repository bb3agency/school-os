import { describe, expect, it, vi } from "vitest";
import type { ApiClient } from "@schoolos/api-client";
import en from "../../../messages/en.json";
import {
  attendancePrintHref,
  attendanceXlsxPath,
  packApi,
  percentText,
  statsPrintHref,
  statsQuery,
  statsXlsxPath,
} from "./api";

function fakeClient(data: unknown) {
  const ok = () => Promise.resolve({ data, response: new Response(null, { status: 200 }) });
  const client = { GET: vi.fn(ok), POST: vi.fn(ok) };
  return { client, api: client as unknown as ApiClient };
}

const STATS = {
  year: { id: "y1", label: "2026-27", starts_on: "2026-06-01", ends_on: "2027-03-31" },
  as_of: "2026-09-30",
  reference_date: "2026-09-30",
  students: 3,
  verified: false,
  source: ["https://udiseplus.gov.in/"],
  restricted_hidden: true,
  tables: [
    {
      key: "gender",
      title: "Enrolment by class and gender",
      restricted: false,
      columns: [{ key: "female", label: "Girls" }],
      rows: [{ class_id: "c1", label: "Class IX", counts: [2], total: 2 }],
      totals: { class_id: null, label: "Total", counts: [2], total: 3 },
    },
  ],
  gaps: [],
};

const QUERY = {
  schemeKey: "thalliki_vandanam",
  sectionId: "0192f3a4-0000-7000-8000-00000000a001",
  fromDate: "2026-07-01",
  toDate: "2026-07-31",
};

describe("enrolment statistics and scheme packs (FO-06, FO-11)", () => {
  it("builds the queries and the BFF print paths (FR-EXP-013, FR-EXP-022)", () => {
    expect(statsQuery({})).toEqual({});
    expect(statsQuery({ yearId: "y1", asOf: "2026-09-30" })).toEqual({
      academic_year_id: "y1",
      as_of: "2026-09-30",
    });
    expect(statsPrintHref({ yearId: "y1", asOf: "2026-09-30" })).toBe(
      "/bff/api/v1/reports/enrolment-statistics/print?academic_year_id=y1&as_of=2026-09-30",
    );
    expect(statsXlsxPath({})).toBe("/api/v1/reports/enrolment-statistics/xlsx");
    expect(attendancePrintHref(QUERY)).toBe(
      "/bff/api/v1/scheme-packs/attendance/print?scheme_key=thalliki_vandanam" +
        "&section_id=0192f3a4-0000-7000-8000-00000000a001&from_date=2026-07-01&to_date=2026-07-31",
    );
    expect(attendanceXlsxPath(QUERY)).toContain("/api/v1/scheme-packs/attendance/xlsx?");
  });

  it("validates the counts the API sends (FR-EXP-009)", async () => {
    const { client, api } = fakeClient(STATS);
    const out = await packApi.stats(api, { yearId: "y1" });
    expect(out.tables[0]?.totals.total).toBe(3);
    expect(client.GET).toHaveBeenCalledWith("/api/v1/reports/enrolment-statistics", {
      params: { query: { academic_year_id: "y1" } },
    });
    const bad = fakeClient({ ...STATS, students: "three" });
    await expect(packApi.stats(bad.api, {})).rejects.toThrow();
  });

  it("checks the print first without printing (FR-EXP-014)", async () => {
    const { client, api } = fakeClient(undefined);
    await packApi.checkAttendancePrint(api, QUERY);
    expect(client.GET).toHaveBeenCalledWith("/api/v1/scheme-packs/attendance/print", {
      params: {
        query: {
          scheme_key: "thalliki_vandanam",
          section_id: QUERY.sectionId,
          from_date: "2026-07-01",
          to_date: "2026-07-31",
          check: true,
        },
      },
      parseAs: "text",
    });
  });

  it("sends the chosen students with an idempotency key (FR-EXP-024)", async () => {
    const result = {
      scheme_key: "thalliki_vandanam",
      certificate_type: "study",
      issued: 1,
      refused: 0,
      results: [
        {
          student_id: "s1",
          status: "issued",
          certificate_id: "c1",
          serial: "SC/2026-27/0001",
          error_code: null,
        },
      ],
    };
    const { client, api } = fakeClient(result);
    const out = await packApi.issueCertificates(api, "thalliki_vandanam", ["s1"], "key-1");
    expect(out.issued).toBe(1);
    expect(client.POST).toHaveBeenCalledWith("/api/v1/scheme-packs/certificates", {
      body: { scheme_key: "thalliki_vandanam", student_ids: ["s1"] },
      headers: { "Idempotency-Key": "key-1" },
    });
  });

  it("never shows a student without counted days as 0% (FR-EXP-023)", () => {
    expect(percentText(null, "No marks")).toBe("No marks");
    expect(percentText(80, "No marks")).toBe("80.0%");
    expect(percentText(66.66, "No marks")).toBe("66.7%");
  });

  it("has English labels for every table total and the no-eligibility note", () => {
    for (const key of ["gender", "age", "category", "minority", "cwsn", "repeaters", "movement"]) {
      expect(en.enrolmentStats.total[key as keyof typeof en.enrolmentStats.total]).toBeTruthy();
    }
    expect(en.schemePacks.noEligibilityTitle).toMatch(/department/);
  });
});
