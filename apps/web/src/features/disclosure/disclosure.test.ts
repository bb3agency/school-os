import { describe, expect, it } from "vitest";
import {
  draftBody,
  draftState,
  itemOfField,
  liveSnapshot,
  type DisclosureDraft,
  type DisclosureFormat,
  type Snapshot,
} from "./data";

/** CBSE disclosure editor data (R3-1C; ADR-0051; FR-DISC-004). Synthetic data only. */

function item(key: string, extra: Partial<DisclosureFormat["sections"][0]["items"][0]>) {
  return {
    key,
    label: key,
    fill: "input" as const,
    type: null,
    required: false,
    contact: false,
    takes_documents: false,
    max_length: null,
    max_value: null,
    max_rows: null,
    ...extra,
  };
}

const FORMAT: DisclosureFormat = {
  key: "cbse-appendix-ix",
  version: 1,
  title: "Mandatory public disclosure",
  verified: false,
  sources: [],
  sections: [
    {
      key: "general",
      title: "A",
      items: [
        item("school_name", { fill: "school" }),
        item("affiliation_no", { type: "text", max_length: 40, required: true }),
        item("internet", { type: "yes_no" }),
        item("girls_toilets", { type: "integer", max_value: 1000 }),
        item("board_results", { type: "results", max_rows: 6 }),
        item("video", { fill: "link" }),
        item("fire_safety", { fill: "document", takes_documents: true }),
        item("fee_structure", { fill: "derived", takes_documents: true }),
      ],
    },
  ],
};

const DRAFT: DisclosureDraft = {
  format_key: "cbse-appendix-ix",
  format_version: 1,
  inputs: {
    affiliation_no: "1130001",
    internet: true,
    girls_toilets: 4,
    board_results: [{ year: "2025-26", class_level: "XII", appeared: 45, passed: 44 }],
  },
  documents: { fire_safety: [{ id: "d1", title: "Fire NOC", publishable: true }] },
  from_schoolos: {},
  missing: [],
  version: 3,
  updated_at: null,
  updated_by_name: null,
};

describe("draft state and body (FR-DISC-004)", () => {
  it("round-trips the saved draft", () => {
    const state = draftState(FORMAT, DRAFT);
    expect(state.values).toEqual({
      affiliation_no: "1130001",
      internet: "yes",
      girls_toilets: "4",
      video: "",
    });
    expect(state.results.board_results).toEqual([
      { year: "2025-26", class_level: "XII", appeared: "45", passed: "44" },
    ]);
    expect(state.documents).toEqual({ fire_safety: ["d1"], fee_structure: [] });
    expect(draftBody(FORMAT, state)).toEqual({
      inputs: {
        affiliation_no: "1130001",
        internet: true,
        girls_toilets: 4,
        board_results: [{ year: "2025-26", class_level: "XII", appeared: 45, passed: 44 }],
      },
      documents: { fire_safety: ["d1"] },
    });
  });

  it("drops empty values and blank result rows, and sends odd numbers as typed", () => {
    const state = draftState(FORMAT, DRAFT);
    state.values.affiliation_no = "  ";
    state.values.girls_toilets = "four";
    state.results.board_results = [{ year: "", class_level: "X", appeared: "", passed: "" }];
    expect(draftBody(FORMAT, state).inputs).toEqual({ internet: true, girls_toilets: "four" });
  });

  it("maps API error fields to item keys", () => {
    expect(itemOfField("inputs.affiliation_no")).toBe("affiliation_no");
    expect(itemOfField("documents.fire_safety")).toBe("fire_safety");
    expect(itemOfField("confirm_no_student_data")).toBeNull();
  });

  it("finds the live snapshot", () => {
    const base = { id: "s1", live: false } as Snapshot;
    expect(liveSnapshot([base])).toBeNull();
    expect(liveSnapshot([base, { ...base, id: "s2", live: true }])?.id).toBe("s2");
  });
});
