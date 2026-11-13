import { describe, expect, it, vi } from "vitest";
import type { ApiClient } from "@schoolos/api-client";
import en from "../../../messages/en.json";
import {
  boardLetterHref,
  bySection,
  fixLettersApi,
  parentLettersHref,
  type FixLetterStudent,
} from "./api";

function fakeClient() {
  const ok = (data: unknown) =>
    Promise.resolve({ data, response: new Response(null, { status: 200 }) });
  const client = { GET: vi.fn(() => ok({})), POST: vi.fn(() => ok({})) };
  return { client, api: client as unknown as ApiClient };
}

function row(id: string, section: string | null, label: string | null): FixLetterStudent {
  return {
    student: { id, display_name: `Synthetic ${id}`, admission_no: null },
    section_id: section,
    class_section: label,
    fields: ["Full name"],
    last_sent_on: null,
  };
}

describe("fix-owner letters (FA-08, FR-DQ-049..060)", () => {
  it("opens the print views through the BFF", () => {
    expect(parentLettersHref("bseap-ssc-2027", { studentId: "s-1" })).toBe(
      "/bff/api/v1/dq/readiness/bseap-ssc-2027/parent-letters?student_id=s-1",
    );
    expect(parentLettersHref("bseap-ssc-2027", { sectionId: "sec 1" })).toBe(
      "/bff/api/v1/dq/readiness/bseap-ssc-2027/parent-letters?section_id=sec%201",
    );
    expect(boardLetterHref("bseap-ssc-2027", "deo")).toBe(
      "/bff/api/v1/dq/readiness/bseap-ssc-2027/board-letter?recipient=deo",
    );
  });

  it("records letters given with an idempotency key and the date (FR-DQ-057)", async () => {
    const { client, api } = fakeClient();
    await fixLettersApi.recordSent(
      api,
      "bseap-ssc-2027",
      "parent_aadhaar",
      ["s-1", "s-2"],
      "2026-11-12",
      "key-1",
    );
    expect(client.POST).toHaveBeenCalledWith(
      "/api/v1/dq/readiness/bseap-ssc-2027/fix-letters/parent_aadhaar/sent",
      {
        body: { student_ids: ["s-1", "s-2"], occurred_on: "2026-11-12" },
        headers: { "Idempotency-Key": "key-1" },
      },
    );
  });

  it("checks the board letter before opening it (step-up, nothing audited)", async () => {
    const { client, api } = fakeClient();
    await fixLettersApi.checkBoardLetter(api, "bseap-ssc-2027", "deo");
    expect(client.GET).toHaveBeenCalledWith("/api/v1/dq/readiness/bseap-ssc-2027/board-letter", {
      params: { query: { recipient: "deo", check: true } },
      parseAs: "text",
    });
  });

  it("groups the students by section in the order given", () => {
    const groups = bySection([row("1", "a", "IX A"), row("2", "b", "IX B"), row("3", "a", "IX A")]);
    expect(groups.map((g) => [g.label, g.students.map((s) => s.student.id)])).toEqual([
      ["IX A", ["1", "3"]],
      ["IX B", ["2"]],
    ]);
  });

  it("has English words for both letters and their errors", () => {
    const sent = en.fixLetters.sent;
    expect(sent.parent_aadhaar.trigger).toBeTruthy();
    expect(sent.board_registration.trigger).toBeTruthy();
    expect(en.fixLetters.errors.nothing_to_print.title).toBeTruthy();
    expect(en.fixLetters.profiles["bseap-ssc-2027"]).toBeTruthy();
  });
});
