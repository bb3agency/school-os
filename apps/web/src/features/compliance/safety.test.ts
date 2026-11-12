import { describe, expect, it } from "vitest";
import {
  checkBody,
  checkFormSchema,
  checksOverviewSchema,
  ifMatch,
  myPoliciesSchema,
  policyFormSchema,
} from "./safety-data";

describe("safety check form (FC-09, FR-COMP-048)", () => {
  it("sends drill counts only for drills and empty fields as null", () => {
    const input = checkFormSchema.parse({
      kind: "fire_drill",
      label: "Ignored",
      done_on: "2026-11-10",
      led_by_membership_id: "",
      participants_count: "412",
      duration_minutes: "6",
      result: "action_needed",
      observations: "  Stair B was slow ",
      document_id: "",
    });
    expect(checkBody(input, true)).toEqual({
      kind: "fire_drill",
      label: null,
      done_on: "2026-11-10",
      led_by_membership_id: null,
      participants_count: 412,
      duration_minutes: 6,
      result: "action_needed",
      observations: "Stair B was slow",
      document_id: null,
    });
    const check = checkFormSchema.parse({
      kind: "electrical_check",
      done_on: "2026-11-10",
      participants_count: "3",
    });
    expect(checkBody(check, false).participants_count).toBeNull();
    expect(checkBody(check, false).result).toBe("ok");
  });

  it("refuses a missing check, a missing date and non-numbers", () => {
    expect(checkFormSchema.safeParse({ kind: "", done_on: "2026-11-10" }).success).toBe(false);
    expect(checkFormSchema.safeParse({ kind: "fire_drill", done_on: "" }).success).toBe(false);
    expect(
      checkFormSchema.safeParse({
        kind: "fire_drill",
        done_on: "2026-11-10",
        participants_count: "4o",
      }).success,
    ).toBe(false);
    expect(
      checkFormSchema.safeParse({
        kind: "fire_drill",
        done_on: "2026-11-10",
        duration_minutes: "2000",
      }).success,
    ).toBe(false);
  });
});

describe("policy form (FC-10, FR-COMP-056)", () => {
  it("needs a title and a document; roles may be empty", () => {
    expect(
      policyFormSchema.safeParse({ title: "Ok", document_id: "d", ack_roles: [] }).success,
    ).toBe(false);
    expect(
      policyFormSchema.safeParse({ title: "Child protection", document_id: "", ack_roles: [] })
        .success,
    ).toBe(false);
    const ok = policyFormSchema.parse({
      title: "Child protection",
      document_id: "d",
      ack_roles: ["teacher"],
    });
    expect(ok.ack_roles).toEqual(["teacher"]);
  });

  it("sends If-Match as a weak ETag", () => {
    expect(ifMatch(2)).toBe('W/"2"');
  });
});

describe("answers are checked before use", () => {
  it("accepts the API's overview and refuses an unknown state", () => {
    const overview = {
      statuses: [
        {
          kind: "fire_drill",
          kind_label: "Fire evacuation drill",
          every_months: 4,
          drill: true,
          state: "overdue",
          last_done_on: "2026-06-01",
          next_due: "2026-10-01",
          days_left: -42,
          last_entry_id: "e1",
        },
      ],
      counts: { overdue: 1, due_soon: 0, never: 0, ok: 0 },
      recent: [],
      kinds: [],
      due_soon_days: 14,
      verified: false,
      today: "2026-11-12",
      leaders: [],
    };
    expect(checksOverviewSchema.parse(overview).statuses[0]?.state).toBe("overdue");
    const bad = { ...overview, statuses: [{ ...overview.statuses[0], state: "late" }] };
    expect(checksOverviewSchema.safeParse(bad).success).toBe(false);
  });

  it("reads the member's own list", () => {
    const mine = myPoliciesSchema.parse({
      items: [
        {
          id: "p1",
          title: "Anti-bullying policy",
          current_version_id: "v1",
          acknowledged_at: null,
          needs_ack: true,
        },
      ],
      pending: 1,
    });
    expect(mine.pending).toBe(1);
  });
});
