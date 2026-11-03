import { describe, expect, it } from "vitest";
import { describeApiError } from "@/lib/api-errors";
import { ApiError } from "@/lib/bff/query";
import { messages } from "@/test/render";

/**
 * Backend field and problem codes that reach a screen have their own plain-language message
 * (NFR-I18N-001; docs/09 errors): without one the user only sees "Check this value." or the
 * generic 409. Codes from academics, insights, tally, extraction, documents, disclosure and
 * billing that had no message anywhere.
 */

const FIELD_CODES = [
  // academics
  "absent_has_no_marks",
  "bad_max_marks",
  "date_out_of_range",
  "duplicate_entry",
  "exam_name_taken",
  "exam_other_year",
  "invalid_month",
  "marks_over_max",
  "not_an_import_file",
  "not_in_section",
  "too_many_entries",
  "too_many_subjects",
  // insights
  "before_flag",
  "future_date",
  "not_enrolled",
  "owner_not_eligible",
  "rule_required",
  "threshold_out_of_bounds",
  "unknown_rule",
  // tally
  "unknown_group",
  // extraction
  "duplicate",
  "masked_value",
  "no_values",
  "only_for_new_student",
  "required",
  "unknown_field",
  // documents
  "size_mismatch",
  // disclosure
  "confirmation_required",
  // platform billing (422 ai_bundle_not_available, message_key errors.ai_bundle)
  "ai_bundle",
] as const;

const DISCLOSURE_CODES = [
  "confirmation_required",
  "incomplete",
  "files_failed",
  "files_not_ready",
  "not_published",
  "snapshot_not_live",
] as const;

describe("field messages for backend codes (NFR-I18N-001)", () => {
  it.each(FIELD_CODES)("errors.field.%s exists in en and te", (code) => {
    const en = messages.en.errors.field as unknown as Record<string, string>;
    const te = messages.te.errors.field as unknown as Record<string, string>;
    expect(en[code]).toBeTruthy();
    expect(te[code]).toBeTruthy();
  });
});

describe("problem messages (FR-PLT-012, FR-DISC-005)", () => {
  it("a 409 invoice_number_length has its own message, not the generic conflict", () => {
    const error = new ApiError(409, "invoice_number_length", { status: 409 });
    expect(describeApiError(error)).toMatchObject({ key: "invoice_number_length" });
    const api = messages.en.errors.api as unknown as Record<string, { title?: string }>;
    expect(api.invoice_number_length?.title).toBeTruthy();
  });

  it.each(DISCLOSURE_CODES)("disclosure.errors.%s has a title and body", (code) => {
    for (const locale of ["en", "te"] as const) {
      const own = (messages[locale].disclosure as Record<string, unknown>).errors as
        Record<string, { title: string; body: string }> | undefined;
      expect(own?.[code]?.title, locale).toBeTruthy();
      expect(own?.[code]?.body, locale).toBeTruthy();
    }
  });
});
