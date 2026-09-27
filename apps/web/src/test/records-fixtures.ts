import type { components } from "@schoolos/api-client";
import { verhoeffValid } from "@/features/students/aadhaar";

/**
 * Synthetic fixtures for the student, import and extraction screens (invariant 11: never real
 * data). Shapes are the generated OpenAPI types, so a changed API breaks these at compile time.
 */
type Schemas = components["schemas"];

export const ID = {
  student: "0192f3a4-0000-7000-8000-00000000c101",
  student2: "0192f3a4-0000-7000-8000-00000000c102",
  value: "0192f3a4-0000-7000-8000-00000000c201",
  value2: "0192f3a4-0000-7000-8000-00000000c202",
  guardian: "0192f3a4-0000-7000-8000-00000000c301",
  year: "0192f3a4-0000-7000-8000-00000000c401",
  klass: "0192f3a4-0000-7000-8000-00000000c402",
  section: "0192f3a4-0000-7000-8000-00000000c403",
  user: "0192f3a4-0000-7000-8000-00000000c501",
  import: "0192f3a4-0000-7000-8000-00000000c601",
  doc: "0192f3a4-0000-7000-8000-00000000c701",
  upload: "0192f3a4-0000-7000-8000-00000000c702",
  batch: "0192f3a4-0000-7000-8000-00000000c801",
  page: "0192f3a4-0000-7000-8000-00000000c802",
  page2: "0192f3a4-0000-7000-8000-00000000c803",
  item: "0192f3a4-0000-7000-8000-00000000c901",
  item2: "0192f3a4-0000-7000-8000-00000000c902",
  template: "0192f3a4-0000-7000-8000-00000000ca01",
} as const;

export const NOW = "2026-09-27T05:30:00Z";

/** A made-up 12-digit number that passes the Verhoeff check (what the UI must refuse). */
export function fakeAadhaar(prefix = "23456789012"): string {
  for (let digit = 0; digit <= 9; digit += 1) {
    if (verhoeffValid(`${prefix}${digit}`)) return `${prefix}${digit}`;
  }
  throw new Error("no check digit");
}

export function me(permissions: readonly string[]): Schemas["app__identity__schemas__MeOut"] {
  return {
    user_id: ID.user,
    membership_id: ID.user,
    tenant_id: "0192f3a4-0000-7000-8000-000000000001",
    tenant_ids: ["0192f3a4-0000-7000-8000-000000000001"],
    tenant_status: "active",
    display_name: "Office Clerk",
    preferred_language: "en",
    roles: ["office_admin"],
    scopes: [],
    mfa: true,
    permissions: [...permissions],
    settings: { idle_timeout_minutes: 15, date_format: "DD/MM/YYYY", languages: ["en", "te"] },
  };
}

export const ALL_RECORD_PERMISSIONS = [
  "student.read_basic",
  "student.read_sensitive",
  "student.create",
  "student.update_nonidentity",
  "student.identity_change.request",
  "import.run",
  "import.commit",
  "document.upload",
  "document.read",
] as const;

function attribute(
  key: string,
  label_en: string,
  label_te: string,
  extra: Partial<Schemas["AttributeOut"]> = {},
): Schemas["AttributeOut"] {
  return {
    key,
    data_type: "text",
    classification: "C2",
    is_identity: false,
    label_en,
    label_te,
    sort_order: 0,
    allowed_sources: null,
    allowed_values: null,
    precedence: [],
    is_global: true,
    ...extra,
  };
}

export const ATTRIBUTES: Schemas["AttributeOut"][] = [
  attribute("admission_no", "Admission number", "ప్రవేశ సంఖ్య", {
    is_identity: true,
    sort_order: 1,
  }),
  attribute("full_name", "Full name", "పూర్తి పేరు", { is_identity: true, sort_order: 2 }),
  attribute("dob", "Date of birth", "పుట్టిన తేదీ", {
    is_identity: true,
    data_type: "date",
    sort_order: 3,
  }),
  attribute("gender", "Gender", "లింగం", {
    data_type: "enum",
    allowed_values: ["female", "male", "transgender"],
    sort_order: 4,
  }),
  attribute("father_name", "Father's name", "తండ్రి పేరు", { sort_order: 5 }),
  attribute("mother_name", "Mother's name", "తల్లి పేరు", { sort_order: 6 }),
  attribute("aadhaar_last4", "Aadhaar (last 4 digits)", "ఆధార్ (చివరి 4 అంకెలు)", {
    data_type: "digits4",
    classification: "C3",
    allowed_sources: ["aadhaar_as_printed"],
    sort_order: 100,
  }),
];

export const YEAR: Schemas["AcademicYearOut"] = {
  id: ID.year,
  label: "2026-27",
  starts_on: "2026-06-01",
  ends_on: "2027-04-30",
  is_current: true,
  version: 1,
  created_at: NOW,
  updated_at: NOW,
};

export const CLASS: Schemas["ClassOut"] = {
  id: ID.klass,
  code: "IX",
  display_en: "Class 9",
  display_te: "9వ తరగతి",
  sort_order: 9,
  version: 1,
  created_at: NOW,
  updated_at: NOW,
};

export const SECTION: Schemas["SectionOut"] = {
  id: ID.section,
  academic_year_id: ID.year,
  class_id: ID.klass,
  name: "A",
  class_teacher_membership_id: null,
  version: 1,
  created_at: NOW,
  updated_at: NOW,
};

export function summary(extra: Partial<Schemas["StudentSummary"]> = {}): Schemas["StudentSummary"] {
  return {
    id: ID.student,
    display_name: "Venkata Sai K.",
    admission_no: "2019/0457",
    status: "active",
    class_section: "9A",
    section_id: ID.section,
    match: { field: "full_name", score: 0.9 },
    ...extra,
  };
}

function value(extra: Partial<Schemas["ValueOut"]>): Schemas["ValueOut"] {
  return {
    id: ID.value,
    attribute_key: "full_name",
    source: "admission_register",
    value: "Venkata Sai K.",
    masked: false,
    verification_status: "verified",
    verified_by: ID.user,
    verified_at: NOW,
    recorded_by: ID.user,
    recorded_at: NOW,
    evidence_document_id: null,
    import_batch_id: null,
    change_request_id: null,
    superseded_by: null,
    current: true,
    ...extra,
  };
}

export function student(extra: Partial<Schemas["StudentOut"]> = {}): Schemas["StudentOut"] {
  return {
    id: ID.student,
    status: "active",
    admission_no: "2019/0457",
    version: 4,
    created_at: NOW,
    updated_at: NOW,
    enrollment: {
      section_id: ID.section,
      class_id: ID.klass,
      academic_year_id: ID.year,
      label: "9A",
      roll_no: "12",
    },
    canonical: {
      full_name: {
        value: "Venkata Sai K.",
        source: "admission_register",
        verified: true,
        provisional: false,
        masked: false,
        conflicts: ["udise_plus"],
      },
      aadhaar_last4: {
        value: "••••",
        source: "aadhaar_as_printed",
        verified: false,
        provisional: false,
        masked: true,
        conflicts: [],
      },
    },
    values: {
      full_name: [
        value({}),
        value({
          id: ID.value2,
          source: "udise_plus",
          value: "Venkatasai Kumar",
          verification_status: "unverified",
          verified_by: null,
          verified_at: null,
        }),
      ],
      aadhaar_last4: [
        value({
          id: "0192f3a4-0000-7000-8000-00000000c203",
          attribute_key: "aadhaar_last4",
          source: "aadhaar_as_printed",
          value: "••••",
          masked: true,
          verification_status: "unverified",
        }),
      ],
    },
    sensitive_revealable: true,
    ...extra,
  };
}

export function importBatch(extra: Partial<Schemas["ImportOut"]> = {}): Schemas["ImportOut"] {
  return {
    id: ID.import,
    kind: "spreadsheet",
    source: "admission_register",
    status: "parsed",
    document_id: ID.doc,
    file_kind: "xlsx",
    header_row: 1,
    columns: [
      { index: 0, header: "Adm No", suggested: "admission_no", score: 100, target: null },
      { index: 1, header: "విద్యార్థి పేరు", suggested: "full_name", score: 100, target: null },
      { index: 2, header: "DOB", suggested: "dob", score: 95, target: null },
      { index: 3, header: "Remarks", suggested: null, score: 0, target: null },
    ],
    mapping_template_id: null,
    stats: {},
    row_count: 3,
    error_count: 0,
    error_code: null,
    job_id: null,
    created_by: ID.user,
    created_at: NOW,
    updated_at: NOW,
    committed_at: null,
    revert_deadline: null,
    reverted_at: null,
    raw_file_deleted_at: null,
    version: 3,
    can_commit: false,
    can_revert: false,
    ...extra,
  };
}

export function importRow(extra: Partial<Schemas["ImportRowOut"]> = {}): Schemas["ImportRowOut"] {
  return {
    row_no: 2,
    status: "valid",
    action: "create",
    student_id: null,
    admission_no: "2019/0457",
    values: { full_name: "Venkata Sai K.", dob: "2012-03-14" },
    sensitive: [],
    section_id: ID.section,
    class_section: "9A",
    roll_no: "12",
    errors: [],
    warnings: [],
    ...extra,
  };
}

export function extractionPage(extra: Partial<Schemas["PageOut"]> = {}): Schemas["PageOut"] {
  return {
    id: ID.page,
    document_id: ID.doc,
    document_version_no: 1,
    page_no: 1,
    seq: 1,
    status: "done",
    error_code: null,
    aadhaar_detected: false,
    image_withheld: false,
    image_redacted: false,
    row_count: 2,
    low_confidence_count: 1,
    processed_at: NOW,
    ...extra,
  };
}

export function extractionBatch(
  extra: Partial<Schemas["BatchDetail"]> = {},
): Schemas["BatchDetail"] {
  return {
    id: ID.batch,
    source: "admission_register",
    status: "review",
    provider: "synthetic",
    error_code: null,
    page_count: 2,
    pages_done: 2,
    pages_failed: 0,
    pages_withheld: 0,
    items_total: 2,
    items_pending: 2,
    items_confirmed: 0,
    items_rejected: 0,
    items_low_confidence: 1,
    created_by: ID.user,
    created_at: NOW,
    processed_at: NOW,
    completed_at: null,
    version: 2,
    pages: [extractionPage()],
    ...extra,
  };
}

function field(value: string, extra: Partial<Schemas["FieldOut"]> = {}): Schemas["FieldOut"] {
  return {
    value,
    confidence: 0.97,
    bbox: [0.1, 0.2, 0.3, 0.05],
    masked: false,
    low_confidence: false,
    ...extra,
  };
}

export function extractionItem(extra: Partial<Schemas["ItemDetail"]> = {}): Schemas["ItemDetail"] {
  return {
    id: ID.item,
    batch_id: ID.batch,
    page_id: ID.page,
    document_id: ID.doc,
    page_no: 1,
    row_index: 0,
    status: "pending_review",
    fields: {
      admission_no: field("1987/0012"),
      full_name: field("Lakshmi Prasanna B."),
      dob: field("2011-07-09", { confidence: 0.41, low_confidence: true }),
      gender: field("F"),
      father_name: field("XXXX XXXX 4821", { masked: true, confidence: null }),
    },
    low_confidence: true,
    low_confidence_fields: ["dob"],
    masked: true,
    reviewed_by: null,
    reviewed_at: null,
    reject_reason: null,
    student_id: null,
    value_ids: [],
    corrected_fields: [],
    created_student: false,
    version: 1,
    image: {
      url: "https://files.schoolos.example/t/x/page-1.jpg?X-Amz-Signature=test",
      expires_at: "2026-09-27T05:35:00Z",
      mime_type: "image/jpeg",
    },
    image_unavailable: null,
    possible_matches: [],
    ...extra,
  };
}
