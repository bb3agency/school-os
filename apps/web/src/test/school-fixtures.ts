import type { components } from "@schoolos/api-client";

/**
 * Synthetic school-side API payloads for screen tests (findings, change requests,
 * notifications, break-glass). Invented names and IDs only; never real student data.
 */
type Schemas = components["schemas"];

export const TENANT = "0192f3a4-0000-7000-8000-000000000001";
export const ME_MEMBERSHIP = "0192f3a4-0000-7000-8000-0000000000b1";
export const OTHER_MEMBERSHIP = "0192f3a4-0000-7000-8000-0000000000b2";
export const STUDENT = "0192f3a4-0000-7000-8000-00000000d001";
export const SECTION = "0192f3a4-0000-7000-8000-00000000e001";

export function me(
  permissions: string[],
  overrides: Partial<Schemas["app__identity__schemas__MeOut"]> = {},
): Schemas["app__identity__schemas__MeOut"] {
  return {
    user_id: "0192f3a4-0000-7000-8000-0000000000a1",
    tenant_id: TENANT,
    membership_id: ME_MEMBERSHIP,
    display_name: "Test Clerk",
    preferred_language: "en",
    roles: ["office_admin"],
    permissions,
    scopes: [],
    mfa: true,
    tenant_ids: [TENANT],
    tenant_status: "active",
    settings: { idle_timeout_minutes: 15, date_format: "DD/MM/YYYY", languages: ["en", "te"] },
    ...overrides,
  };
}

export function finding(overrides: Partial<Schemas["FindingOut"]> = {}): Schemas["FindingOut"] {
  return {
    id: "0192f3a4-0000-7000-8000-00000000f001",
    student: { id: STUDENT, display_name: "Asha Test", admission_no: "A-101" },
    related_student_id: null,
    rule_id: "DQ-003",
    rule_version: 1,
    profile_key: null,
    attribute_key: "date_of_birth",
    sources: ["admission_register", "birth_certificate"],
    match_class: null,
    severity: "blocker",
    blocker: true,
    status: "open",
    explanation: {
      code: "DOB-MISMATCH",
      en: "Date of birth differs between the register and the birth certificate.",
      te: "రిజిస్టర్‌లో మరియు జనన ధృవీకరణ పత్రంలో పుట్టిన తేదీ వేరుగా ఉంది.",
    },
    match_explanation: null,
    routes: [
      {
        code: "ROUTE-SCHOOL-CR",
        en: "Correct the school record with a change request.",
        te: "మార్పు అభ్యర్థనతో పాఠశాల రికార్డును సరిచేయండి.",
      },
    ],
    values: [
      {
        attribute_key: "date_of_birth",
        source: "admission_register",
        value_id: "0192f3a4-0000-7000-8000-00000000a001",
        masked: "••/••/2012",
        value: null,
        sensitive: true,
      },
      {
        attribute_key: "date_of_birth",
        source: "birth_certificate",
        value_id: "0192f3a4-0000-7000-8000-00000000a002",
        masked: "••/••/2013",
        value: null,
        sensitive: true,
      },
    ],
    details: {},
    resolution: null,
    resolution_note: null,
    change_request_id: null,
    resolved_by: null,
    resolved_at: null,
    waived_by: null,
    waived_at: null,
    waived_reason: null,
    reopened_count: 0,
    first_seen_at: "2026-09-20T04:30:00Z",
    last_seen_at: "2026-09-26T04:30:00Z",
    first_seen_run_id: null,
    last_seen_run_id: null,
    version: 3,
    ...overrides,
  };
}

export function changeRequest(
  overrides: Partial<Schemas["ChangeRequestOut"]> = {},
): Schemas["ChangeRequestOut"] {
  return {
    id: "0192f3a4-0000-7000-8000-00000000c001",
    student_id: STUDENT,
    attribute_key: "full_name",
    attribute_label_en: "Full name",
    attribute_label_te: "పూర్తి పేరు",
    target_source: "admission_register",
    old_value_id: "0192f3a4-0000-7000-8000-00000000a003",
    old_value: "Asha Tset",
    new_value: "Asha Test",
    masked: false,
    reason: "Spelling differs from the birth certificate.",
    evidence_document_id: "0192f3a4-0000-7000-8000-00000000d0c1",
    status: "pending",
    requested_by: OTHER_MEMBERSHIP,
    requested_at: "2026-09-25T05:00:00Z",
    decided_by: null,
    decided_at: null,
    decision_note: null,
    applied_value_id: null,
    expires_at: "2026-10-25T05:00:00Z",
    version: 1,
    can_decide: true,
    can_cancel: false,
    ...overrides,
  };
}

export function grant(overrides: Partial<Schemas["GrantOut"]> = {}): Schemas["GrantOut"] {
  return {
    id: "0192f3a4-0000-7000-8000-00000000b6a1",
    platform_request_id: "0192f3a4-0000-7000-8000-00000000b6b1",
    status: "requested",
    emergency: false,
    reason_code: "support_request",
    reason: "The school asked for help with an import that failed.",
    scope: { section_id: SECTION },
    duration_minutes: 120,
    operator_display_name: "Support Person",
    requested_at: "2026-09-26T05:00:00Z",
    starts_at: null,
    expires_at: null,
    decided_at: null,
    revoked_at: null,
    approved_by_membership: null,
    denied_by_membership: null,
    revoked_by_membership: null,
    membership_id: null,
    created_at: "2026-09-26T05:00:00Z",
    ...overrides,
  };
}

export function notification(
  overrides: Partial<Schemas["NotificationOut"]> = {},
): Schemas["NotificationOut"] {
  return {
    id: "0192f3a4-0000-7000-8000-00000000e0a1",
    template_key: "change_request.submitted",
    language: "en",
    title: "A correction request is waiting for you",
    body: "Open it to approve or reject it.",
    params: {},
    resource_type: "change_request",
    resource_id: "0192f3a4-0000-7000-8000-00000000c001",
    created_at: "2026-09-26T05:00:00Z",
    read_at: null,
    ...overrides,
  };
}

/** Academic structure routes used by section pickers. */
export function structureRoutes(): Record<string, () => Response> {
  const year = "0192f3a4-0000-7000-8000-0000000000y1";
  const klass = "0192f3a4-0000-7000-8000-0000000000c9";
  return {
    "GET /bff/api/v1/academic-years": () =>
      Response.json({ data: [{ id: year, is_current: true }], next_cursor: null }),
    "GET /bff/api/v1/classes": () =>
      Response.json({
        data: [{ id: klass, display_en: "Class 9", display_te: "9వ తరగతి", sort_order: 9 }],
        next_cursor: null,
      }),
    "GET /bff/api/v1/sections": () =>
      Response.json({
        data: [{ id: SECTION, class_id: klass, academic_year_id: year, name: "A" }],
        next_cursor: null,
      }),
  };
}

export const EXPORT_ID = "0192f3a4-0000-7000-8000-00000000e0a9";

/** One export (ADR-0021 shape: requester, own, can_download). Synthetic only. */
export function exportRow(overrides: Partial<Schemas["ExportOut"]> = {}): Schemas["ExportOut"] {
  return {
    id: EXPORT_ID,
    kind: "board_precheck",
    profile_key: "cisce-registration-2026",
    profile_version: 1,
    layout_version: 1,
    formats: ["xlsx", "pdf"],
    language: "en",
    scope: {},
    columns: null,
    include_sensitive: false,
    student_count: 120,
    status: "ready",
    error_code: null,
    created_at: "2026-09-26T05:00:00Z",
    started_at: "2026-09-26T05:00:05Z",
    finished_at: "2026-09-26T05:01:00Z",
    expires_at: "2026-10-03T05:01:00Z",
    files: [
      {
        format: "xlsx",
        content_type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        size_bytes: 20480,
      },
      { format: "pdf", content_type: "application/pdf", size_bytes: 40960 },
    ],
    requested_by: { membership_id: ME_MEMBERSHIP, display_name: "Test Clerk" },
    own: true,
    can_download: true,
    ...overrides,
  };
}

/** Export profiles as GET /export-profiles returns them (apps/api/app/exports/config.yaml). */
export function exportProfiles(): Schemas["ExportProfileOut"][] {
  return [
    {
      key: "cisce-registration-2026",
      kind: "board",
      permission: "export.board",
      version: 1,
      layout_version: 1,
      label_en: "CISCE registration 2026",
      label_te: "CISCE నమోదు 2026",
      fields: ["admission_no", "full_name", "gender", "dob", "father_name", "mother_name"],
      required_fields: ["full_name", "dob"],
      allowed: true,
      verified: false,
    },
    {
      key: "udise-plus",
      kind: "portal",
      permission: "export.portal",
      version: 1,
      layout_version: 1,
      label_en: "UDISE+",
      label_te: "UDISE+",
      fields: ["admission_no", "full_name", "gender", "dob", "mother_name", "category"],
      required_fields: ["full_name"],
      allowed: true,
      verified: false,
    },
  ];
}

/** Student attribute catalogue (GET /attributes), a synthetic subset with classifications. */
export function exportAttributes(): Schemas["AttributeOut"][] {
  const base = {
    data_type: "text",
    is_identity: false,
    allowed_sources: [],
    allowed_values: null,
    precedence: [],
    is_global: true,
  };
  const make = (key: string, label: string, classification: string, sort: number) => ({
    ...base,
    key,
    label_en: label,
    label_te: label,
    classification,
    sort_order: sort,
  });
  return [
    make("admission_no", "Admission number", "C1", 1),
    make("full_name", "Full name", "C2", 2),
    make("dob", "Date of birth", "C2", 3),
    make("aadhaar_last4", "Aadhaar last 4 digits", "C3", 4),
    make("aadhaar_name_as_printed", "Name on Aadhaar", "C3", 5),
    make("category", "Category", "C3", 6),
  ];
}
