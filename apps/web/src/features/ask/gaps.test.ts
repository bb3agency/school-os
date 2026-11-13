import { describe, expect, it } from "vitest";
import { documentIdFrom, gapSummarySchema, knowledgeGapSchema } from "./gaps";

const ID = "0199a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b";

describe("knowledge gaps (FA-06)", () => {
  it("reads the document id from a pasted page link or a bare id", () => {
    expect(documentIdFrom(`https://school.example.test/en/documents/${ID}?tab=versions`)).toBe(ID);
    expect(documentIdFrom(`  ${ID.toUpperCase()}  `)).toBe(ID);
    expect(documentIdFrom("fee circular")).toBeNull();
  });

  it("accepts the API's gap and summary bodies and rejects other shapes", () => {
    const gap = {
      id: ID,
      question: "When is the PTM for class 9A? Call [redacted-phone]",
      language: "en",
      occurrences: 3,
      not_found_count: 2,
      unchecked_count: 0,
      not_helpful_count: 1,
      first_seen_at: "2026-11-10T04:00:00Z",
      last_seen_at: "2026-11-12T04:00:00Z",
      status: "open",
      resolution: null,
      verified_answer_id: null,
      document_id: null,
      closed_by: null,
      closed_by_name: null,
      closed_at: null,
      version: 1,
    };
    expect(knowledgeGapSchema.parse(gap).occurrences).toBe(3);
    expect(() => knowledgeGapSchema.parse({ ...gap, status: "deleted" })).toThrow();
    const summary = gapSummarySchema.parse({
      open_gaps: 1,
      not_found: 2,
      unchecked: 0,
      not_helpful: 1,
      months: [{ month: "2026-11", closed_with_answer: 1, closed_with_document: 0, dismissed: 0 }],
      retention_days: 90,
    });
    expect(summary.months[0]?.month).toBe("2026-11");
  });
});
