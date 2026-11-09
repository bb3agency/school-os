import { describe, expect, it, vi } from "vitest";
import type { ApiClient } from "@schoolos/api-client";
import en from "../../../messages/en.json";
import { ifMatch, letterHref, needsReview, udiseApi } from "./api";
import { MATCH_BASES } from "./MatchCell";

function fakeClient() {
  const ok = (data: unknown) =>
    Promise.resolve({ data, response: new Response(null, { status: 200 }) });
  const client = {
    GET: vi.fn(() => ok({})),
    POST: vi.fn(() => ok({})),
    PUT: vi.fn(() => ok({})),
  };
  return { client, api: client as unknown as ApiClient };
}

describe("UDISE+ corrections (FO-02, FO-04)", () => {
  it("opens the letter through the BFF", () => {
    expect(letterHref("0192f3a4-0000-7000-8000-00000000c001")).toBe(
      "/bff/api/v1/dq/udise-corrections/0192f3a4-0000-7000-8000-00000000c001/letter",
    );
  });

  it("only rows the import could not match wait for a person", () => {
    expect(needsReview([{ code: "udise_unmatched" }])).toBe(true);
    expect(needsReview([{ code: "udise_match_ambiguous" }])).toBe(true);
    expect(needsReview([{ code: "missing" }])).toBe(false);
    expect(needsReview([])).toBe(false);
  });

  it("sends If-Match and the decision for a row match (FR-UDI-014)", async () => {
    const { client, api } = fakeClient();
    await udiseApi.matchRow(api, "imp-1", 3, 7, "student", "stu-1");
    const path = "/api/v1/imports/{import_id}/rows/{row_no}/match";
    const params = { path: { import_id: "imp-1", row_no: 3 } };
    expect(client.PUT).toHaveBeenCalledWith(path, {
      params,
      body: { decision: "student", student_id: "stu-1" },
      headers: { "If-Match": ifMatch(7) },
    });
    await udiseApi.matchRow(api, "imp-1", 3, 8, "skip");
    expect(client.PUT).toHaveBeenLastCalledWith(path, {
      params,
      body: { decision: "skip" },
      headers: { "If-Match": 'W/"8"' },
    });
  });

  it("prepares a request with an idempotency key and records it sent with If-Match", async () => {
    const { client, api } = fakeClient();
    await udiseApi.prepare(api, "bseap-ssc-2027", "key-1");
    expect(client.POST).toHaveBeenCalledWith("/api/v1/dq/udise-corrections", {
      body: { profile_key: "bseap-ssc-2027" },
      headers: { "Idempotency-Key": "key-1" },
    });
    const request = { id: "req-1", version: 2 } as Parameters<typeof udiseApi.markSent>[1];
    await udiseApi.markSent(api, request, "2026-11-05", "meo");
    expect(client.POST).toHaveBeenLastCalledWith(
      "/api/v1/dq/udise-corrections/{correction_id}/sent",
      {
        params: { path: { correction_id: "req-1" } },
        body: { sent_on: "2026-11-05", sent_to: "meo" },
        headers: { "If-Match": 'W/"2"' },
      },
    );
  });

  it("has an English label for every match basis", () => {
    const labels = en.udise.match.basis as Record<string, string>;
    for (const basis of MATCH_BASES) expect(labels[basis]).toBeTruthy();
  });
});
