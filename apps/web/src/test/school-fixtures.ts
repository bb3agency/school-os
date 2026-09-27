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
