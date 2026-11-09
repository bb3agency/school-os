import { describe, expect, it } from "vitest";
import { certificateBody, certificateFormSchema, ifMatch } from "./data";
import { hasExpired, registerCertificates } from "./RegisterNote";

describe("compliance certificate form (FC-01, FR-COMP-004, FR-COMP-008)", () => {
  it("sends empty fields as null and a name only for 'other'", () => {
    const input = certificateFormSchema.parse({
      kind: "fire_noc",
      label: "Ignored",
      reference_no: "  FS/77 ",
      valid_until: "2031-03-31",
      valid_from: "",
      document_id: "",
      owner_membership_id: "m-1",
      notes: "",
    });
    expect(certificateBody(input)).toEqual({
      label: null,
      reference_no: "FS/77",
      issued_by: null,
      valid_from: null,
      valid_until: "2031-03-31",
      document_id: null,
      owner_membership_id: "m-1",
      notes: null,
    });
    const other = certificateFormSchema.parse({ kind: "other", label: " Lift licence " });
    expect(certificateBody(other).label).toBe("Lift licence");
  });

  it("asks for a certificate kind", () => {
    expect(certificateFormSchema.safeParse({ kind: "" }).success).toBe(false);
  });

  it("sends If-Match as a weak ETag", () => {
    expect(ifMatch(3)).toBe('W/"3"');
  });
});

describe("register certificates on the CBSE disclosure draft (FR-COMP-020, FR-COMP-021)", () => {
  it("reads them when present and is empty before the client knows them", () => {
    expect(registerCertificates({})).toEqual({});
    const draft: Parameters<typeof registerCertificates>[0] = {
      certificates: {
        fire_safety: [
          {
            certificate_id: "c-1",
            kind: "fire_noc",
            kind_label: "Fire safety certificate (fire NOC)",
            valid_until: "2026-01-01",
            state: "expired",
            document_id: "d-1",
            publishable: true,
          },
        ],
      },
    };
    expect(registerCertificates(draft).fire_safety).toHaveLength(1);
    expect(hasExpired(registerCertificates(draft))).toBe(true);
    expect(hasExpired({})).toBe(false);
  });
});
