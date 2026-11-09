import { describe, expect, it } from "vitest";
import { deliverySchema, readinessSchema } from "./readiness-api";
import { buildAdmissionSchema, buildDateSchema } from "./ReadinessScreen";

describe("DPDP readiness (FC-03, FR-DPDP-053..060)", () => {
  it("accepts the checklist and refuses an answer without the not-legal-advice flag", () => {
    const body = {
      not_legal_advice: true,
      done: 1,
      applicable: 7,
      items: [
        {
          key: "dpa_signed",
          status: "done",
          detail: { done_on: "2026-11-01" },
          mark: {
            item: "dpa_signed",
            done_on: "2026-11-01",
            document_id: null,
            recorded_by_name: null,
            recorded_at: "2026-11-01T10:00:00Z",
          },
          source: "docs/08 §12",
        },
      ],
      admission: { since: null, added_since: 0, without_notice: 0 },
    };
    expect(readinessSchema.parse(body).done).toBe(1);
    expect(() => readinessSchema.parse({ ...body, not_legal_advice: false })).toThrow();
  });

  it("refuses future dates and a missing channel for the admission notice", () => {
    const schema = buildAdmissionSchema("2026-11-09");
    expect(schema.safeParse({ given_on: "01/11/2026", channel: "paper" }).success).toBe(true);
    expect(schema.safeParse({ given_on: "01/12/2026", channel: "paper" }).success).toBe(false);
    expect(schema.safeParse({ given_on: "01/11/2026", channel: "" }).success).toBe(false);
    expect(buildDateSchema("done_on", "2026-11-09").safeParse({ done_on: "" }).success).toBe(false);
  });

  it("reads the WhatsApp delivery counts and the not-reached list (FP-13)", () => {
    const out = deliverySchema.parse({
      notice: { id: "n", issued_on: "2026-11-09", template_version: "0.2" },
      broadcast_id: "b",
      guardians: { delivered: 3, read: 1, left_out: 2 },
      students: 6,
      students_not_reached: 2,
      hidden: 1,
      not_reached: [
        { student_id: "s", display_name: null, admission_no: null, class_section: "IX-A" },
      ],
    });
    expect(out.not_reached).toHaveLength(1);
  });
});
