import { describe, expect, it } from "vitest";
import type { NoticeDelivery, Readiness } from "./readiness-api";
import { buildAdmissionSchema, buildDateSchema } from "./ReadinessScreen";

describe("DPDP readiness (FC-03, FR-DPDP-053..060)", () => {
  // The answers are typed by the generated client; these pin the shapes the screens rely on.
  it("accepts the checklist and refuses an answer without the not-legal-advice flag", () => {
    const body: Readiness = {
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
    expect(body.done).toBe(1);
    // @ts-expect-error the generated type only takes `not_legal_advice: true`.
    const refused: Readiness = { ...body, not_legal_advice: false };
    expect(refused.not_legal_advice).toBe(false);
  });

  it("refuses future dates and a missing channel for the admission notice", () => {
    const schema = buildAdmissionSchema("2026-11-09");
    expect(schema.safeParse({ given_on: "01/11/2026", channel: "paper" }).success).toBe(true);
    expect(schema.safeParse({ given_on: "01/12/2026", channel: "paper" }).success).toBe(false);
    expect(schema.safeParse({ given_on: "01/11/2026", channel: "" }).success).toBe(false);
    expect(buildDateSchema("done_on", "2026-11-09").safeParse({ done_on: "" }).success).toBe(false);
  });

  it("reads the WhatsApp delivery counts and the not-reached list (FP-13)", () => {
    const out: NoticeDelivery = {
      not_legal_advice: true,
      notice: {
        id: "n",
        audience: "parents",
        channel: "sos_whatsapp",
        document_id: null,
        issued_on: "2026-11-09",
        language: "en",
        recorded_at: "2026-11-09T10:00:00Z",
        recorded_by_name: null,
        template_key: "notice_parents",
        template_version: "0.2",
      },
      broadcast_id: "b",
      guardians: { delivered: 3, read: 1, left_out: 2 },
      students: 6,
      students_not_reached: 2,
      hidden: 1,
      not_reached: [
        { student_id: "s", display_name: null, admission_no: null, class_section: "IX-A" },
      ],
    };
    expect(out.not_reached).toHaveLength(1);
  });
});
