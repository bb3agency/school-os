import { describe, expect, it } from "vitest";
import { notificationHref } from "@/features/notifications/data";
import { actionsFor, ifMatch, placeBody, placeSchema, type Hold } from "./data";

/** FC-12 legal holds (ADR-0059, FR-DPDP-039..052): row actions and the place body. */

function hold(over: Partial<Hold> = {}): Hold {
  return {
    id: "00000000-0000-7000-8000-000000000001",
    scope: "student",
    student_id: "00000000-0000-7000-8000-000000000002",
    subject_label: "Synthetica Kumar (SYN-1)",
    record_class: null,
    reason_code: "court_case",
    reason_label: "Court case or tribunal",
    reference: null,
    review_on: "2027-02-01",
    review_overdue: false,
    status: "pending",
    effective: false,
    placed_by_name: "Principal",
    placed_at: "2026-11-09T05:00:00Z",
    approved_by_name: null,
    approved_at: null,
    lift_requested_by_name: null,
    lift_requested_at: null,
    lifted_by_name: null,
    lifted_at: null,
    rejected_at: null,
    can_approve: true,
    version: 1,
    ...over,
  };
}

describe("FR-DPDP-042 maker-checker actions", () => {
  it("the asker can only withdraw; another holder approves or rejects", () => {
    expect(actionsFor(hold())).toEqual(["approve", "reject"]);
    expect(actionsFor(hold({ can_approve: false }))).toEqual(["reject"]);
  });

  it("an active hold can be asked to lift; a lift waits for another person", () => {
    expect(actionsFor(hold({ status: "active" }))).toEqual(["request-lift"]);
    expect(actionsFor(hold({ status: "lift_pending" }))).toEqual(["approve-lift", "cancel-lift"]);
    expect(actionsFor(hold({ status: "lift_pending", can_approve: false }))).toEqual([
      "cancel-lift",
    ]);
    expect(actionsFor(hold({ status: "lifted" }))).toEqual([]);
    expect(actionsFor(hold({ status: "rejected" }))).toEqual([]);
  });

  it("writes send the row version as If-Match", () => {
    expect(ifMatch(3)).toBe('W/"3"');
  });
});

describe("FR-DPDP-039 place body", () => {
  it("sends only the chosen scope's field and no empty reference", () => {
    const input = placeSchema.parse({
      scope: "record_class",
      student_id: "x",
      record_class: "behaviour_notes",
      reason_code: "police_inquiry",
      reference: "  ",
      review_on: "2027-01-01",
    });
    expect(placeBody(input)).toEqual({
      scope: "record_class",
      record_class: "behaviour_notes",
      reason_code: "police_inquiry",
      review_on: "2027-01-01",
    });
  });

  it("needs a reason and a review date", () => {
    expect(
      placeSchema.safeParse({ scope: "student", reason_code: "", review_on: "" }).success,
    ).toBe(false);
  });
});

describe("FR-DPDP-049 review reminder link", () => {
  it("a hold notification opens the holds list", () => {
    expect(notificationHref({ resource_type: "legal_hold", resource_id: hold().id })).toBe(
      "/privacy/holds",
    );
  });
});
