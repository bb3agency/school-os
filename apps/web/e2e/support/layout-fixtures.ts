/**
 * Responsive-layout fixtures (e2e/responsive.spec.ts, e2e/audit/responsive.audit.ts). Answers browser GET requests to
 * /bff/api/v1/<path> with SYNTHETIC data in the generated OpenAPI shapes, so every list and
 * detail screen has rows to lay out. Content is deliberately stressful: 60-character school
 * names, long e-mails, unbroken tokens and URLs, Telugu text, long free text and nulls.
 *
 * Synthetic data only ("Synthetica …" names, SYN-… admission numbers). No Aadhaar number of any
 * kind appears here; only masked aadhaar_last4 values, as the API sends them.
 * Other requests (writes, and paths not listed here) fall through to the stand-in API
 * (route.fallback()); the only POST answered here is the read-only student search.
 */
import type { Page, Route } from "@playwright/test";
import type { components } from "@schoolos/api-client";

type Schemas = components["schemas"];

/* ------------------------------------------------------------------ stress content */

const LONG_SCHOOL = "Sri Venkateswara Zilla Parishad High School and Junior College";
const LONG_EMAIL = "office.administration.synthetic@very-long-school-domain.example";
const TE_STUDENT = "సింథటిక్ విద్యార్థి అనూష కుమారి";
const TE_GUARDIAN = "సింథటిక్ తండ్రి వెంకట సుబ్రహ్మణ్యేశ్వర రావు";
const TE_TEXT =
  "దసరా సెలవుల తర్వాత పాఠశాల తిరిగి తెరుచుకుంటుంది; దయచేసి విద్యార్థుల వివరాలను సరిచూసుకోండి మరియు తప్పులుంటే కార్యాలయానికి తెలియజేయండి.";
const LONG_TOKEN = "SYNREF7A9C2E1B8D6F3A0C5E7B9D1F2A4C6E8B0D"; // 40 characters, no spaces
const LONG_URL =
  "https://synthetic-files.very-long-school-domain.example/tenants/0192f3a4-0000-7000-8000-000000000001/exports/precheck-cisce-registration-2026-final-reviewed-version.xlsx?X-Amz-Signature=synthetic";
const LONG_TEXT =
  "The office uploaded the admission register for classes 6 to 10, but the import stopped halfway with an error that did not say which row was wrong. We tried again after clearing the browser cache and the same thing happened on two different office computers. Please check what is wrong before the UDISE+ deadline next week.";

const T1 = "0192f3a4-0000-7000-8000-000000000001";
const T2 = "0192f3a4-0000-7000-8000-000000000002";
/** Provisioning-stopped school: its detail is left to the stand-in (PROVISIONING_DETAIL). */
const T3 = "0192f3a4-0000-7000-8000-000000000003";
const STUDENT_MAIN = "0192f3a4-0000-7000-8000-00000000e501";
const YEAR_ID = "0192f3a4-0000-7000-8000-0000000000a1";
const OLD_YEAR_ID = "0192f3a4-0000-7000-8000-0000000000a0";
const CLASS_ID = "0192f3a4-0000-7000-8000-0000000000c6";
const SECTION_A = "0192f3a4-0000-7000-8000-000000000500";
const SECTION_B = "0192f3a4-0000-7000-8000-000000000510";
const ME_MEMBERSHIP = "0192f3a4-0000-7000-8000-0000000000e1";
const OTHER_MEMBERSHIP = "0192f3a4-0000-7000-8000-0000000000e2";
const OPERATOR_ID = "0192f3a4-0000-7000-8000-0000000000f1";
const PLAN_ID = "0192f3a4-0000-7000-8000-00000000a001";
const SUB_ID = "0192f3a4-0000-7000-8000-00000000b001";
const BILLING_ACCOUNT = "0192f3a4-0000-7000-8000-00000000ba01";

/** A UUID in the synthetic range: `block` + n in hex fill the last 12-character group. */
const uid = (block: string, n: number) =>
  `0192f3a4-0000-7000-8000-${block}${n.toString(16).padStart(12 - block.length, "0")}`;
const at = (day: number, hour = 5, minute = 30) =>
  `2026-09-${String(day).padStart(2, "0")}T${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}:00Z`;
const date = (month: number, day: number) =>
  `2026-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
const page = <T>(data: T[]) => ({ data, next_cursor: null });
const pick = <T>(items: readonly T[], i: number): T => items[i % items.length] as T;

/* ------------------------------------------------------------------ students */

const STUDENT_NAMES = [
  "Synthetica Ravi Kumar",
  TE_STUDENT,
  "Synthetica Venkata Naga Sai Lakshmi Prasanna Kumari Bommireddy",
  "Synthetica Anjali Devi",
  "Synthetica Kiran",
  "సింథటిక్ రాము",
  "Synthetica Mohammed Abdul Rahman Siddiqui",
  "Synthetica Meena Kumari",
  "Synthetica Harsha Vardhan Reddy",
  "Synthetica Divya",
  "Synthetica Lakshmi Narasimha Swamy Chowdary",
  "Synthetica Priya",
];
const STUDENT_STATUSES = ["active", "active", "active", "left", "active", "transferred"];

const studentId = (i: number) => (i === 0 ? STUDENT_MAIN : uid("00000000e5", i + 1));

const STUDENTS: Schemas["StudentSummary"][] = STUDENT_NAMES.map((name, i) => ({
  id: studentId(i),
  display_name: i === 9 ? null : name,
  admission_no:
    i === 4 ? null : i === 6 ? `SYN-${LONG_TOKEN}` : `SYN-2026-${String(i + 14).padStart(3, "0")}`,
  status: pick(STUDENT_STATUSES, i),
  class_section: i === 5 ? null : i % 2 === 0 ? "Class 6 · A" : "Class 10 · B (Telugu medium)",
  section_id: i === 5 ? null : i % 2 === 0 ? SECTION_A : SECTION_B,
  match: i === 2 ? { field: "full_name", score: 0.87 } : { field: null, score: null },
}));

function valueOut(
  n: number,
  attribute_key: string,
  source: string,
  value: string | null,
  extra: Partial<Schemas["ValueOut"]> = {},
): Schemas["ValueOut"] {
  return {
    id: uid("0000000f3a", n),
    attribute_key,
    source,
    value,
    masked: false,
    verification_status: "verified",
    verified_by: ME_MEMBERSHIP,
    verified_at: at(20),
    recorded_by: ME_MEMBERSHIP,
    recorded_at: at(18),
    evidence_document_id: null,
    import_batch_id: null,
    change_request_id: null,
    superseded_by: null,
    current: true,
    ...extra,
  };
}

function studentValues(name: string): Schemas["ValueOut"][] {
  return [
    valueOut(1, "full_name", "admission_register", name),
    valueOut(
      2,
      "full_name",
      "udise_plus",
      "SYNTHETICA VENKATA NAGA SAI LAKSHMI PRASANNA KUMARI B",
      {
        verification_status: "unverified",
        verified_by: null,
        verified_at: null,
      },
    ),
    valueOut(3, "full_name", "board_registration", TE_STUDENT, {
      verification_status: "rejected",
    }),
    valueOut(4, "dob", "admission_register", "2014-06-12"),
    valueOut(5, "dob", "birth_certificate", "2014-06-21", {
      verification_status: "unverified",
      verified_by: null,
      verified_at: null,
      evidence_document_id: uid("00000000d0", 9),
    }),
    valueOut(6, "gender", "admission_register", "female"),
    valueOut(7, "father_name", "admission_register", "Synthetica Venkata Subrahmanyeswara Rao"),
    valueOut(8, "father_name", "parent_form", TE_GUARDIAN, {
      verification_status: "unverified",
      verified_by: null,
      verified_at: null,
    }),
    valueOut(9, "admission_no", "admission_register", `SYN-${LONG_TOKEN}`),
    valueOut(10, "aadhaar_last4", "aadhaar_as_printed", "••••", {
      masked: true,
      verification_status: "unverified",
      verified_by: null,
      verified_at: null,
    }),
    valueOut(11, "aadhaar_dob_as_printed", "aadhaar_as_printed", null, {
      masked: true,
      verification_status: "unverified",
      verified_by: null,
      verified_at: null,
    }),
    valueOut(12, "full_name", "manual_entry", "Synthetica V. N. S. L. P. Kumari", {
      current: false,
      superseded_by: uid("0000000f3a", 1),
    }),
  ];
}

function canonical(
  value: string | null,
  source: string | null,
  extra: Partial<Schemas["CanonicalOut"]> = {},
): Schemas["CanonicalOut"] {
  return {
    value,
    source,
    verified: true,
    provisional: false,
    masked: false,
    conflicts: [],
    ...extra,
  };
}

function studentDetail(id: string): Schemas["StudentOut"] {
  const index = STUDENTS.findIndex((row) => row.id === id);
  const summary = STUDENTS[index >= 0 ? index : 2] as Schemas["StudentSummary"];
  const name = summary.display_name ?? STUDENT_NAMES[2] ?? "Synthetica";
  const values: Record<string, Schemas["ValueOut"][]> = {};
  for (const value of studentValues(name)) (values[value.attribute_key] ??= []).push(value);
  return {
    id,
    status: summary.status,
    admission_no: summary.admission_no,
    version: 7,
    created_at: "2026-06-10T04:30:00Z",
    updated_at: at(26),
    enrollment: {
      section_id: SECTION_A,
      class_id: CLASS_ID,
      academic_year_id: YEAR_ID,
      label: "Class 6 · A",
      roll_no: "14",
    },
    canonical: {
      full_name: canonical(name, "admission_register", {
        conflicts: ["udise_plus", "board_registration"],
      }),
      dob: canonical("2014-06-12", "admission_register", { conflicts: ["birth_certificate"] }),
      gender: canonical("female", "admission_register"),
      father_name: canonical("Synthetica Venkata Subrahmanyeswara Rao", "admission_register", {
        conflicts: ["parent_form"],
      }),
      mother_name: canonical(null, null, { verified: false }),
      admission_no: canonical(summary.admission_no, "admission_register"),
      aadhaar_last4: canonical("••••", "aadhaar_as_printed", { masked: true, verified: false }),
      category: canonical("BC-B", "parent_form", { provisional: true, verified: false }),
    },
    values,
    sensitive_revealable: true,
  };
}

function guardians(): Schemas["GuardianOut"][] {
  return [
    {
      id: uid("00000000aa", 1),
      full_name: "Synthetica Venkata Subrahmanyeswara Rao Bommireddy",
      relationship: "father",
      is_primary: true,
      phone: "+91 90000 00001",
      has_phone: true,
      address:
        "Door No. 12-34/5A, Synthetic Colony Main Road, Near Zilla Parishad High School, Synthetic Mandal, Synthetic District, Andhra Pradesh 520000",
      has_address: true,
      masked: false,
      version: 3,
    },
    {
      id: uid("00000000aa", 2),
      full_name: "సింథటిక్ తల్లి లక్ష్మీ వెంకట సుబ్బలక్ష్మి",
      relationship: "mother",
      is_primary: false,
      phone: null,
      has_phone: true,
      address: null,
      has_address: true,
      masked: true,
      version: 1,
    },
    {
      id: uid("00000000aa", 3),
      full_name: "Synthetica Guardian",
      relationship: "guardian",
      is_primary: false,
      phone: null,
      has_phone: false,
      address: null,
      has_address: false,
      masked: false,
      version: 1,
    },
  ];
}

function enrollments(id: string): Schemas["EnrollmentOut"][] {
  return [
    {
      id: uid("00000000cb", 1),
      student_id: id,
      section_id: SECTION_A,
      academic_year_id: YEAR_ID,
      roll_no: "14",
      status: "active",
      started_on: "2026-06-01",
      ended_on: null,
      version: 6,
    },
    {
      id: uid("00000000cb", 2),
      student_id: id,
      section_id: SECTION_B,
      academic_year_id: OLD_YEAR_ID,
      roll_no: LONG_TOKEN,
      status: "completed",
      started_on: "2025-06-02",
      ended_on: "2026-04-30",
      version: 2,
    },
    {
      id: uid("00000000cb", 3),
      student_id: id,
      section_id: SECTION_B,
      academic_year_id: OLD_YEAR_ID,
      roll_no: null,
      status: "transferred",
      started_on: null,
      ended_on: "2025-05-15",
      version: 1,
    },
  ];
}

/* ------------------------------------------------------------------ data quality */

const ROUTE_CR: Schemas["Bilingual"] = {
  code: "ROUTE-SCHOOL-CR",
  en: "Correct the school record (change request + evidence).",
  te: "పాఠశాల రికార్డును సరిచేయండి (రుజువు పత్రంతో మార్పు అభ్యర్థన ద్వారా).",
};
const ROUTE_UIDAI: Schemas["Bilingual"] = {
  code: "ROUTE-UIDAI",
  en: "Parent should correct Aadhaar with UIDAI.",
  te: "తల్లిదండ్రులు UIDAI ద్వారా ఆధార్‌లో సవరణ చేయించుకోవాలి.",
};
const RULE_TEXT: Array<[string, string, Schemas["Bilingual"]]> = [
  [
    "DQ-002",
    "dob",
    {
      code: "DQ-002",
      en: "Date of birth differs. Boards and APAAR need these to match.",
      te: "పుట్టిన తేదీ వేరుగా ఉంది. బోర్డులు, APAAR కోసం ఇవి ఒకేలా ఉండాలి.",
    },
  ],
  [
    "DQ-003",
    "gender",
    {
      code: "DQ-003",
      en: "Gender differs between records.",
      te: "రికార్డులలో లింగం (జెండర్) వేర్వేరుగా ఉంది.",
    },
  ],
  [
    "DQ-004",
    "father_name",
    {
      code: "DQ-004",
      en: "Parent name spelled differently across records. Long names with initials and surnames in a different order are common in Andhra Pradesh records, so check the admission register page before changing anything.",
      te: "తల్లి లేదా తండ్రి పేరు రికార్డులలో వేర్వేరుగా రాసి ఉంది.",
    },
  ],
];
const SEVERITIES: Schemas["FindingOut"]["severity"][] = [
  "blocker",
  "high",
  "medium",
  "low",
  "blocker",
  "info",
];
const FINDING_STATUSES: Schemas["FindingOut"]["status"][] = [
  "open",
  "open",
  "reopened",
  "open",
  "resolved",
  "waived",
];

const FINDINGS: Schemas["FindingOut"][] = Array.from({ length: 10 }, (_, i) => {
  const [rule_id, attribute_key, explanation] = pick(RULE_TEXT, i);
  const severity = pick(SEVERITIES, i);
  const status = i < 8 ? (i === 3 ? "reopened" : "open") : pick(FINDING_STATUSES, i);
  const summary = pick(STUDENTS, i);
  const values: Schemas["FindingOut"]["values"] =
    attribute_key === "dob"
      ? [
          {
            attribute_key: "dob",
            source: "admission_register",
            value_id: uid("0000000f3b", i * 2),
            masked: "2014-06-12",
            value: "2014-06-12",
            sensitive: false,
          },
          {
            attribute_key: "aadhaar_dob_as_printed",
            source: "aadhaar_as_printed",
            value_id: uid("0000000f3b", i * 2 + 1),
            masked: "••/••/2014",
            value: null,
            sensitive: true,
          },
        ]
      : attribute_key === "gender"
        ? [
            {
              attribute_key: "gender",
              source: "admission_register",
              value_id: uid("0000000f3b", i * 2),
              masked: "male",
              value: "male",
              sensitive: false,
            },
            {
              attribute_key: "gender",
              source: "udise_plus",
              value_id: uid("0000000f3b", i * 2 + 1),
              masked: "female",
              value: "female",
              sensitive: false,
            },
          ]
        : [
            {
              attribute_key: "father_name",
              source: "admission_register",
              value_id: uid("0000000f3b", i * 2),
              masked: "Synthetica Venkata Subrahmanyeswara Rao Bommireddy",
              value: "Synthetica Venkata Subrahmanyeswara Rao Bommireddy",
              sensitive: false,
            },
            {
              attribute_key: "father_name",
              source: "parent_form",
              value_id: uid("0000000f3b", i * 2 + 1),
              masked: TE_GUARDIAN,
              value: TE_GUARDIAN,
              sensitive: false,
            },
          ];
  return {
    id: uid("0000000f10", i + 1),
    student: {
      id: summary.id,
      display_name: summary.display_name ?? "Synthetica",
      admission_no: summary.admission_no ?? "SYN-2026-000",
    },
    related_student_id: null,
    rule_id,
    rule_version: 1,
    profile_key: i % 3 === 0 ? "cisce-registration-2026" : null,
    attribute_key,
    sources: values.map((value) => value.source),
    match_class: null,
    severity,
    blocker: severity === "blocker",
    status,
    explanation,
    match_explanation: null,
    routes: rule_id === "DQ-004" ? [ROUTE_CR] : [ROUTE_UIDAI, ROUTE_CR],
    values,
    details: {},
    resolution: status === "resolved" ? "note" : null,
    resolution_note: status === "resolved" ? LONG_TEXT : null,
    change_request_id: null,
    resolved_by: status === "resolved" ? ME_MEMBERSHIP : null,
    resolved_at: status === "resolved" ? at(27) : null,
    waived_by: status === "waived" ? ME_MEMBERSHIP : null,
    waived_at: status === "waived" ? at(27) : null,
    waived_reason: status === "waived" ? TE_TEXT : null,
    reopened_count: status === "reopened" ? 2 : 0,
    first_seen_at: at(10 + i),
    last_seen_at: at(26),
    first_seen_run_id: uid("0000000f20", 1),
    last_seen_run_id: uid("0000000f20", 2),
    version: 1 + i,
  };
});

const DQ_SUMMARY: Schemas["SummaryOut"] = {
  profile_key: null,
  blockers: 3,
  warnings: 5,
  students_with_blockers: 3,
  by_severity: { blocker: 3, high: 2, medium: 2, low: 1 },
  by_rule: [
    { rule_id: "DQ-002", count: 4, severity: "blocker" },
    { rule_id: "DQ-003", count: 2, severity: "high" },
    { rule_id: "DQ-004", count: 2, severity: "medium" },
  ],
  last_run: {
    id: uid("0000000f20", 2),
    trigger: "manual",
    event_type: null,
    status: "completed",
    profile_key: null,
    scope: {},
    stats: {},
    created_at: at(26, 5, 0),
    started_at: at(26, 5, 0),
    finished_at: at(26, 5, 1),
    error_code: null,
  },
};

/* ------------------------------------------------------------------ change requests, exports */

const CR_STATUSES: Schemas["ChangeRequestOut"]["status"][] = [
  "pending",
  "pending",
  "approved",
  "rejected",
  "pending",
  "expired",
  "cancelled",
  "pending",
];
const CHANGE_REQUESTS: Schemas["ChangeRequestOut"][] = CR_STATUSES.map((status, i) => {
  const pending = status === "pending";
  const mine = i % 2 === 1;
  const dob = i % 3 === 0;
  return {
    id: uid("0000000c70", i + 1),
    student_id: pick(STUDENTS, i).id,
    attribute_key: dob ? "dob" : "full_name",
    attribute_label_en: dob ? "Date of birth" : "Full name",
    attribute_label_te: dob ? "పుట్టిన తేదీ" : "పూర్తి పేరు",
    target_source: "admission_register",
    old_value_id: i === 4 ? null : uid("0000000f3a", i + 1),
    old_value:
      i === 4 ? null : dob ? "2014-06-12" : "Synthetica Venkata Naga Sai Lakshmi Prasana Kumari",
    new_value: dob
      ? "2014-06-21"
      : i === 2
        ? TE_STUDENT
        : "Synthetica Venkata Naga Sai Lakshmi Prasanna Kumari Bommireddy",
    masked: i === 7,
    reason: i % 2 === 0 ? LONG_TEXT : "Spelling differs from the birth certificate.",
    evidence_document_id: uid("00000000d0", 20 + i),
    status,
    requested_by: mine ? ME_MEMBERSHIP : OTHER_MEMBERSHIP,
    requested_at: at(20 + (i % 8)),
    decided_by: pending ? null : OTHER_MEMBERSHIP,
    decided_at: pending ? null : at(27),
    decision_note: status === "rejected" ? TE_TEXT : null,
    applied_value_id: status === "approved" ? uid("0000000f3a", 40) : null,
    expires_at: "2026-10-25T05:00:00Z",
    version: 1 + i,
    can_decide: pending && !mine,
    can_cancel: pending && mine,
  };
});

const EXPORT_STATUSES: Schemas["ExportOut"]["status"][] = [
  "ready",
  "running",
  "queued",
  "failed",
  "expired",
  "ready",
  "ready",
  "failed",
];
const EXPORTS: Schemas["ExportOut"][] = EXPORT_STATUSES.map((status, i) => {
  const kind: Schemas["ExportOut"]["kind"] = pick(
    ["board_precheck", "portal_precheck", "student_list"] as const,
    i,
  );
  const formats: Schemas["ExportOut"]["formats"] =
    i % 2 === 0 ? ["xlsx", "pdf"] : kind === "student_list" ? ["csv"] : ["xlsx"];
  return {
    id: uid("0000000e70", i + 1),
    kind,
    profile_key:
      kind === "board_precheck"
        ? "cisce-registration-2026"
        : kind === "portal_precheck"
          ? "udise-plus"
          : null,
    profile_version: kind === "student_list" ? null : 1,
    layout_version: 1,
    formats,
    language: i % 3 === 1 ? "te" : "en",
    scope: i % 2 === 0 ? { section_ids: [SECTION_A, SECTION_B] } : {},
    columns:
      kind === "student_list"
        ? ["admission_no", "full_name", "dob", "father_name", "mother_name", "category"]
        : null,
    include_sensitive: i === 5,
    student_count: [214, 1487, 36, 0, 120, 9, 1210, 57][i] ?? 1,
    status,
    error_code:
      status === "failed" ? (i === 3 ? "profile_missing_required_fields" : LONG_TOKEN) : null,
    created_at: at(20 + (i % 8)),
    started_at: status === "queued" ? null : at(20 + (i % 8), 5, 31),
    finished_at:
      status === "ready" || status === "failed" || status === "expired"
        ? at(20 + (i % 8), 5, 33)
        : null,
    expires_at: status === "ready" ? "2026-10-05T05:33:00Z" : null,
    files:
      status === "ready"
        ? formats.map((format) => ({
            format,
            content_type:
              format === "pdf"
                ? "application/pdf"
                : format === "csv"
                  ? "text/csv"
                  : "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            size_bytes: format === "pdf" ? 8_806_400 : 245_760,
          }))
        : [],
    requested_by: {
      membership_id: i % 2 === 0 ? ME_MEMBERSHIP : OTHER_MEMBERSHIP,
      display_name:
        i === 6
          ? null
          : i % 2 === 0
            ? "Synthetica Office Clerk"
            : "సింథటిక్ ప్రధానోపాధ్యాయులు శ్రీనివాస రావు",
    },
    own: i % 2 === 0,
    can_download: status === "ready",
  };
});

/* ------------------------------------------------------------------ extraction */

const BATCH_STATUSES: Schemas["BatchOut"]["status"][] = [
  "review",
  "processing",
  "queued",
  "completed",
  "failed",
  "review",
  "completed",
];
const BATCHES: Schemas["BatchOut"][] = BATCH_STATUSES.map((status, i) => ({
  id: uid("0000000ba7", i + 1),
  source: i === 3 ? "tc_incoming" : "admission_register",
  status,
  provider: i === 5 ? "synthetic-provider-with-a-very-long-identifier-v2" : "synthetic",
  error_code: status === "failed" ? "provider_timeout" : null,
  page_count: [12, 4, 50, 2, 3, 27, 1][i] ?? 1,
  pages_done:
    status === "queued" ? 0 : status === "processing" ? 2 : ([12, 4, 50, 2, 1, 25, 1][i] ?? 1),
  pages_failed: status === "failed" ? 2 : i === 5 ? 1 : 0,
  pages_withheld: i === 5 ? 1 : 0,
  items_total: [340, 0, 0, 48, 0, 812, 30][i] ?? 0,
  items_pending: [290, 0, 0, 0, 0, 640, 0][i] ?? 0,
  items_confirmed: [44, 0, 0, 45, 0, 150, 28][i] ?? 0,
  items_rejected: [6, 0, 0, 3, 0, 22, 2][i] ?? 0,
  items_low_confidence: [38, 0, 0, 5, 0, 97, 0][i] ?? 0,
  created_by: i % 2 === 0 ? ME_MEMBERSHIP : OTHER_MEMBERSHIP,
  created_at: at(15 + i),
  processed_at: status === "queued" || status === "processing" ? null : at(15 + i, 6),
  completed_at: status === "completed" ? at(16 + i) : null,
  version: 2 + i,
}));

function field(value: string, extra: Partial<Schemas["FieldOut"]> = {}): Schemas["FieldOut"] {
  return {
    value,
    confidence: 0.96,
    bbox: [0.1, 0.2, 0.3, 0.05],
    masked: false,
    low_confidence: false,
    ...extra,
  };
}
const ITEM_STATUSES: Schemas["ItemOut"]["status"][] = [
  "pending_review",
  "pending_review",
  "pending_review",
  "confirmed",
  "pending_review",
  "rejected",
  "pending_review",
  "pending_review",
  "confirmed",
  "pending_review",
];
const ITEMS: Schemas["ItemOut"][] = ITEM_STATUSES.map((status, i) => {
  const low = i % 3 === 0;
  return {
    id: uid("0000000170", i + 1),
    batch_id: BATCHES[0]?.id ?? uid("0000000ba7", 1),
    page_id: uid("00000001a0", 1 + Math.floor(i / 4)),
    document_id: uid("00000000d0", 60 + Math.floor(i / 4)),
    page_no: 1 + Math.floor(i / 4),
    row_index: i % 4,
    status,
    fields: {
      admission_no: field(
        i === 2 ? `SYN-${LONG_TOKEN}` : `SYN/1987/${String(12 + i).padStart(4, "0")}`,
      ),
      full_name: field(
        pick(STUDENT_NAMES, i),
        low ? { confidence: 0.38, low_confidence: true } : {},
      ),
      dob: field("2011-07-09", i % 2 === 0 ? { confidence: 0.41, low_confidence: true } : {}),
      gender: field(i % 2 === 0 ? "F" : "M"),
      father_name: field("XXXX XXXX", { masked: true, confidence: null, bbox: null }),
    },
    low_confidence: low || i % 2 === 0,
    low_confidence_fields: [...(i % 2 === 0 ? ["dob"] : []), ...(low ? ["full_name"] : [])],
    masked: true,
    reviewed_by: status === "pending_review" ? null : ME_MEMBERSHIP,
    reviewed_at: status === "pending_review" ? null : at(27),
    reject_reason: status === "rejected" ? "unreadable" : null,
    student_id: status === "confirmed" ? pick(STUDENTS, i).id : null,
    value_ids: status === "confirmed" ? [uid("0000000f3c", i)] : [],
    corrected_fields: status === "confirmed" ? ["dob"] : [],
    created_student: status === "confirmed" && i === 3,
    version: 1,
  };
});

/* ------------------------------------------------------------------ notifications, audit */

const NOTIFICATIONS: Schemas["NotificationOut"][] = [
  [
    "change_request.submitted",
    "change_request",
    "A correction request is waiting for you",
    "Open it to approve or reject it.",
    CHANGE_REQUESTS[0]?.id,
  ],
  [
    "export.ready",
    "export",
    "Export ready",
    `Your CISCE registration 2026 pre-check for 1,487 students is ready to download. The link works for 7 days: ${LONG_URL}`,
    EXPORTS[0]?.id,
  ],
  [
    "document.quarantined",
    "document",
    "File blocked",
    "The file “admission-register-classes-6-to-10-final-scanned-copy-2026.pdf” was blocked by the virus check.",
    uid("00000000d0", 70),
  ],
  [
    "announcement.new",
    "announcement",
    "కొత్త సందేశం: ఆదివారం నిర్వహణ",
    TE_TEXT,
    uid("00000000a5", 1),
  ],
  [
    "extraction.batch.ready",
    "extraction_batch",
    "Register photos are ready to review",
    "340 rows were read from 12 photos. 38 rows need a closer look.",
    BATCHES[0]?.id,
  ],
  ["dq.run.completed", "dq_run", "Data checks finished", LONG_TEXT, uid("0000000f20", 2)],
  [
    "breakglass.requested",
    "breakglass_grant",
    "Support asked to see your school's records",
    "Synthetica Support Person asked for 2 hours of access to help with a failed import.",
    uid("00000000b6", 1),
  ],
  [
    "import.committed",
    "import_batch",
    "Import finished",
    "42 students were added. You can undo this for 24 hours.",
    uid("0000000f00", 1),
  ],
  [
    "export.failed",
    "export",
    "Export failed",
    `Error code ${LONG_TOKEN}. Try again, or contact support with this code.`,
    EXPORTS[3]?.id,
  ],
].map(([template_key, resource_type, title, body, resource_id], i) => ({
  id: uid("00000000c0", 0xe1 + i),
  template_key: template_key ?? "",
  language: i === 3 ? "te" : "en",
  title: title ?? "",
  body: body ?? "",
  params: {},
  resource_type: resource_type ?? null,
  resource_id: resource_id ?? null,
  created_at: at(28 - Math.floor(i / 3), 9 - (i % 3)),
  read_at: i < 3 ? null : at(28, 10),
}));

const AUDIT_ACTIONS: Array<[string, string, Record<string, unknown>]> = [
  [
    "student.update",
    "student",
    { attribute_key: "father_name", source: "admission_register", fields: 3 },
  ],
  ["change_request.approve", "change_request", { attribute_key: "dob", note: TE_TEXT }],
  [
    "export.created",
    "export",
    { profile_key: "cisce-registration-2026", student_count: 1487, formats: ["xlsx", "pdf"] },
  ],
  ["kb.query", "knowledge_query", { mode: "full", cited_sources: 3, latency_ms: 4210 }],
  ["breakglass.access", "breakglass_grant", { reason_code: "support_request", token: LONG_TOKEN }],
  ["auth.login", "user", { method: "oidc", mfa: true }],
  ["role.assign", "membership", { role: "class_teacher", scope: `section:${SECTION_A}` }],
  ["import.commit", "import_batch", { rows: 42, created: 40, updated: 2, skipped: 0 }],
  [
    "document.upload",
    "document",
    {
      title: "admission-register-classes-6-to-10-final-scanned-copy-2026.pdf",
      size_bytes: 8806400,
    },
  ],
  ["student.sensitive_reveal", "student", {}],
];
const AUDIT_EVENTS: Schemas["AuditEventOut"][] = AUDIT_ACTIONS.map(
  ([action, resource_type, summary], i) => ({
    id: uid("00000000ad", i + 1),
    seq: 12_345 - i,
    action,
    actor_type: i === 4 ? "operator" : i === 3 ? "system" : "user",
    actor_id: i === 3 ? null : i % 2 === 0 ? ME_MEMBERSHIP : OTHER_MEMBERSHIP,
    resource_type,
    resource_id: i === 5 ? null : uid("00000000ae", i + 1),
    request_id: i % 3 === 0 ? null : `req_${LONG_TOKEN}`,
    occurred_at: at(28 - Math.floor(i / 2), 11 - (i % 5)),
    summary,
  }),
);

/* ------------------------------------------------------------------ support, break-glass, billing */

function messages(n: number): Schemas["TicketMessageOut"][] {
  const all: Schemas["TicketMessageOut"][] = [
    {
      id: uid("00000000a8", 1),
      author_type: "school_user",
      author_id: ME_MEMBERSHIP,
      body: LONG_TEXT,
      internal_note: false,
      created_at: at(24, 5),
    },
    {
      id: uid("00000000a8", 2),
      author_type: "operator",
      author_id: OPERATOR_ID,
      body: `Thanks. We found the row: the date column has a value we could not read. Reference ${LONG_TOKEN}. See ${LONG_URL}`,
      internal_note: false,
      created_at: at(24, 7),
    },
    {
      id: uid("00000000a8", 3),
      author_type: "operator",
      author_id: OPERATOR_ID,
      body: "Internal: check the parser for DD.MM.YY dates before replying again.",
      internal_note: true,
      created_at: at(24, 8),
    },
    {
      id: uid("00000000a8", 4),
      author_type: "school_user",
      author_id: OTHER_MEMBERSHIP,
      body: TE_TEXT,
      internal_note: false,
      created_at: at(25, 4),
    },
  ];
  return all.slice(0, n);
}

const TICKET_SUBJECTS = [
  "Import stopped halfway through the admission register for classes 6 to 10 without saying which row",
  "దసరా సెలవుల ముందు ఎగుమతి పని చేయడం లేదు",
  "Invoice",
  `Cannot sign in on the shared office computer (error ${LONG_TOKEN})`,
  "Ask the school answered in English when the question was in Telugu",
  "Please add a second principal account for the junior college section",
  "Board registration pre-check shows students who left last year",
  "Billing address has the wrong district on the September invoice",
];
const TICKET_STATUSES: Schemas["TicketOut"]["status"][] = [
  "open",
  "waiting_on_school",
  "in_progress",
  "resolved",
  "closed",
  "open",
  "in_progress",
  "waiting_on_school",
];
const CATEGORIES = [
  "import",
  "exports",
  "billing",
  "access",
  "ask",
  "other",
  "data_quality",
  "billing",
];

function ticket(i: number, tenant_id: string, withMessages: boolean): Schemas["TicketOut"] {
  const status = pick(TICKET_STATUSES, i);
  const done = status === "resolved" || status === "closed";
  return {
    id: uid("00000000cc", i + 1),
    tenant_id,
    ticket_no: 120 + i,
    number: i === 3 ? `SOS-T-${LONG_TOKEN}` : `SOS-T-${String(120 + i).padStart(6, "0")}`,
    subject: pick(TICKET_SUBJECTS, i),
    category: pick(CATEGORIES, i),
    channel: pick(["portal", "email", "phone", "whatsapp"], i),
    priority: pick(["p2", "p3", "p1", "p4", "p3"], i),
    status,
    assigned_to: i % 3 === 0 ? null : OPERATOR_ID,
    personal_data_flagged: i === 1,
    first_response_due_at: at(20 + (i % 8), 9),
    resolution_due_at: "2026-10-02T09:00:00Z",
    first_responded_at: status === "open" ? null : at(20 + (i % 8), 7),
    resolved_at: done ? at(27) : null,
    closed_at: status === "closed" ? at(28) : null,
    created_at: at(20 + (i % 8), 5),
    updated_at: at(28),
    version: 2 + i,
    ...(withMessages ? { messages: messages(i === 1 ? 1 : 4) } : {}),
  };
}

const GRANT_STATUSES: Schemas["GrantOut"]["status"][] = [
  "requested",
  "active",
  "approved",
  "expired",
  "revoked",
  "denied",
  "requested",
];
const GRANTS: Schemas["GrantOut"][] = GRANT_STATUSES.map((status, i) => ({
  id: uid("00000000b6", 0xa1 + i),
  platform_request_id: uid("00000000b6", 0xb1 + i),
  status,
  emergency: i === 1,
  reason_code: pick(["support_request", "security_incident", "legal_obligation", "other"], i),
  reason:
    i % 2 === 0
      ? LONG_TEXT
      : i === 3
        ? TE_TEXT
        : "The school asked for help with an import that failed.",
  scope: i % 2 === 0 ? { section_id: SECTION_A } : { resource: "students", ref: LONG_TOKEN },
  duration_minutes: i === 6 ? null : ([120, 30, 240, 60, 120, 480][i % 6] ?? 120),
  operator_display_name:
    i === 5
      ? null
      : i === 2
        ? "Synthetica Platform Support Engineer With A Long Name"
        : "Synthetica Support Person",
  requested_at: at(20 + i),
  starts_at:
    status === "active" || status === "expired" || status === "revoked" ? at(20 + i, 6) : null,
  expires_at:
    status === "active" ? "2026-09-29T09:00:00Z" : status === "expired" ? at(20 + i, 8) : null,
  decided_at: status === "requested" ? null : at(20 + i, 5, 45),
  revoked_at: status === "revoked" ? at(20 + i, 7) : null,
  approved_by_membership: ["active", "approved", "expired", "revoked"].includes(status)
    ? ME_MEMBERSHIP
    : null,
  denied_by_membership: status === "denied" ? ME_MEMBERSHIP : null,
  revoked_by_membership: status === "revoked" ? OTHER_MEMBERSHIP : null,
  membership_id: status === "active" ? uid("00000000e9", 1) : null,
  created_at: at(20 + i),
}));

const TENANT_INVOICES: Schemas["TenantInvoice"][] = Array.from({ length: 8 }, (_, i) => {
  const month = 9 - i;
  const status = pick(["draft", "issued", "paid", "paid", "void", "paid", "issued", "paid"], i);
  return {
    invoice_id: uid("0000000c1", i + 1),
    invoice_number:
      status === "draft"
        ? null
        : i === 6
          ? `SOS/2026-27/${LONG_TOKEN}`
          : `SOS/2026-27/${String(123 - i).padStart(6, "0")}`,
    status,
    period_start: date(month, 1),
    period_end: date(month, 28),
    issue_date: status === "draft" ? null : date(month, 1),
    due_date: status === "draft" ? null : date(month, 15),
    total_inr: i === 3 ? "1234567.89" : "5898.82",
    amount_due_inr: status === "issued" ? (i === 6 ? "1234567.89" : "5898.82") : "0.00",
  };
});

// FR-ADM-002: retention categories (editable and fixed), as GET /admin/retention answers.
const RETENTION: Schemas["RetentionOut"] = {
  categories: [
    ["import_raw_files", 45, 90, 7, 90, true, true],
    ["exports", 7, 7, 1, 7, true, true],
    ["notifications_read", 90, 90, 30, 90, true, true],
    ["tenant_exports", 1, 1, 1, 1, false, true],
    ["kb_queries", 180, 180, 180, 180, false, false],
    ["audit_events", 395, 395, 395, 395, false, false],
  ].map(([key, days, def, min, max, configurable, enforced]) => ({
    key: String(key),
    days: Number(days),
    default_days: Number(def),
    min_days: Number(min),
    max_days: Number(max),
    configurable: Boolean(configurable),
    enforced: Boolean(enforced),
    is_default: days === def,
  })),
  version: 3,
  updated_at: at(2),
  updated_by: { membership_id: uid("00000000e9", 1), display_name: `Synthetica ${LONG_TOKEN}` },
};

const VERIFIED_ANSWERS: Schemas["VerifiedAnswerOut"][] = [
  [
    "When are the Dasara holidays?",
    "en",
    "From 02/10/2026 to 12/10/2026. School reopens on 13/10/2026.",
    "active",
  ],
  [
    "దసరా సెలవులు ఎప్పుడు?",
    "te",
    "02/10/2026 నుండి 12/10/2026 వరకు. పాఠశాల 13/10/2026న తిరిగి తెరుచుకుంటుంది.",
    "active",
  ],
  [
    "What documents does a parent need to bring for a transfer certificate request, and how many days does the office take?",
    "en",
    LONG_TEXT,
    "needs_review",
  ],
  ["Fee due date?", "mixed", "15th of every month. ఆలస్య రుసుము లేదు.", "retired"],
  [
    "What is the uniform policy for the junior college section on Saturdays and during exam weeks?",
    "en",
    "Coloured dress is allowed on Saturdays. During exam weeks the full uniform with ID card is required.",
    "active",
  ],
  [
    "Who signs bonafide certificates?",
    "en",
    "The principal signs bonafide certificates. The office prints them the same day.",
    "needs_review",
  ],
].map(([question, language, answer_text, status], i) => ({
  id: uid("00000000e1", i + 1),
  question: question ?? "",
  language: (language ?? "en") as Schemas["VerifiedAnswerOut"]["language"],
  answer_text: answer_text ?? "",
  citations: [
    {
      source: `sos://doc/${uid("00000000d0", 1)}/v1#p1`,
      cited_text: "Holidays from 02/10/2026 to 12/10/2026",
    },
    ...(i === 2
      ? [{ source: `sos://doc/${uid("00000000d0", 2)}/v3#p14`, cited_text: TE_TEXT }]
      : []),
  ],
  status: (status ?? "active") as Schemas["VerifiedAnswerOut"]["status"],
  verified_by: ME_MEMBERSHIP,
  verified_by_name:
    i === 4 ? null : i === 1 ? "సింథటిక్ ప్రధానోపాధ్యాయులు" : "Synthetica Principal",
  verified_at: at(20 + i),
  review_due: status === "needs_review" ? "2026-09-30" : null,
  version: 1 + i,
  created_at: at(20 + i),
}));

/* ------------------------------------------------------------------ platform */

const SCHOOLS: Array<[string, string, string]> = [
  [T1, "Sri Saraswati High School", "sshs"],
  [T2, "Vidya Nilayam", "vn"],
  [T3, "Sample Model School", "sms"],
  [uid("00000000ab", 4), LONG_SCHOOL, "svzphs-and-junior-college-guntur-district"],
  [uid("00000000ab", 5), "శ్రీ సరస్వతి విద్యా నిలయం ఉన్నత పాఠశాల", "ssvn"],
  [uid("00000000ab", 6), "Synthetica Public School", "sps"],
  [uid("00000000ab", 7), "Synthetica Residential School for Girls, Visakhapatnam", "srsg-vsp"],
  [uid("00000000ab", 8), "Synthetica EM School", "sems"],
  [uid("00000000ab", 9), "Synthetica Montessori and High School (English Medium)", "smhs"],
  [uid("00000000ab", 10), "Synthetica Academy", "sa"],
];
const TENANT_STATUS: Schemas["TenantSummaryOut"]["tenant_status"][] = [
  "active",
  "suspended",
  "provisioning",
  "active",
  "active",
  "offboarding",
  "active",
  "deleted",
  "active",
  "active",
];
const SUB_STATUS: Schemas["TenantSummaryOut"]["subscription_status"][] = [
  "active",
  "suspended",
  "trial",
  "past_due",
  "trial",
  "cancelled",
  "active",
  null,
  "active",
  "past_due",
];
const DEPLOY_STATUS = [
  "healthy",
  "unreachable",
  "provisioning",
  "degraded",
  "healthy",
  "healthy",
  "healthy",
  "decommissioned",
  "healthy",
  "degraded",
];

const TENANTS: Schemas["TenantSummaryOut"][] = SCHOOLS.map(([tenant_id, school_name, code], i) => ({
  tenant_id,
  school_name,
  code,
  tier: i === 3 || i === 6 ? "dedicated" : "shared",
  tenant_status: pick(TENANT_STATUS, i),
  subscription_status: pick(SUB_STATUS, i),
  plan_code: i === 7 ? null : i === 3 ? "dedicated-premium-with-ai-and-custom-domain" : "standard",
  deployment_status: pick(DEPLOY_STATUS, i),
  app_version:
    i === 2 || i === 7
      ? null
      : i === 3
        ? "2026.09.1-hotfix.3+build.20260928.abcdef0"
        : pick(["2026.09.1", "2026.08.4"], i),
  last_heartbeat_at: i === 2 || i === 7 ? null : at(28, 4, 30 - i),
  created_at: `2026-0${6 + (i % 3)}-0${1 + (i % 9)}T04:30:00Z`,
}));

function subscription(i: number, tenant_id: string): Schemas["SubscriptionOut"] {
  const status = pick(
    ["active", "trial", "past_due", "suspended", "cancelled", "active", "trial", "active"] as const,
    i,
  );
  return {
    id: i === 0 ? SUB_ID : uid("00000000b0", 0x10 + i),
    tenant_id,
    plan_id: PLAN_ID,
    billing_account_id: i === 0 ? BILLING_ACCOUNT : uid("00000000ba", 0x10 + i),
    status,
    current_period_start: "2026-09-01",
    current_period_end: "2026-09-30",
    trial_ends_at: status === "trial" ? "2026-10-12T00:00:00Z" : null,
    past_due_since: status === "past_due" ? "2026-09-16" : null,
    grace_ends_on: status === "past_due" ? "2026-10-01" : null,
    cancel_at_period_end: i === 5,
    cancelled_at: status === "cancelled" ? at(20) : null,
    pending_plan_id: i === 6 ? uid("00000000a0", 2) : null,
    price_override_inr: i === 3 ? "123456.78" : null,
    override_reason: i === 3 ? "Multi-campus society, price agreed in writing" : null,
    version: 1 + i,
  };
}

function invoice(i: number, tenant_id: string): Schemas["InvoiceOut"] {
  const status = pick(
    ["issued", "paid", "draft", "void", "paid", "issued", "paid", "issued"] as const,
    i,
  );
  const big = i === 5;
  const month = 9 - (i % 4);
  return {
    id: uid("0000000c2", i + 1),
    tenant_id,
    subscription_id: SUB_ID,
    invoice_number:
      status === "draft"
        ? null
        : i === 7
          ? `SOS/2026-27/${LONG_TOKEN}`
          : `SOS/2026-27/${String(123 + i).padStart(6, "0")}`,
    financial_year: status === "draft" ? null : "2026-27",
    status,
    period_start: date(month, 1),
    period_end: date(month, 30),
    issue_date: status === "draft" ? null : date(month, 1),
    due_date: status === "draft" ? null : date(month, 15),
    supplier_legal_name: "SchoolOS Synthetic Supplier Private Limited",
    supplier_gstin: "37AAAAA0000A1Z5",
    supplier_state_code: "37",
    recipient_legal_name:
      i === 1
        ? "Sri Venkateswara Zilla Parishad Educational Society and Charitable Trust"
        : "Sample Education Society",
    recipient_gstin: i % 2 === 0 ? null : "37BBBBB1111B1Z6",
    place_of_supply_state_code: i === 4 ? "36" : "37",
    tax_type: i === 4 ? "igst" : "cgst_sgst",
    taxable_value_inr: big ? "1046243.96" : "4999.00",
    cgst_inr: i === 4 ? "0.00" : big ? "94161.96" : "449.91",
    sgst_inr: i === 4 ? "0.00" : big ? "94161.96" : "449.91",
    igst_inr: i === 4 ? "899.82" : "0.00",
    total_inr: big ? "1234567.88" : "5898.82",
    amount_paid_inr: status === "paid" ? (big ? "1234567.88" : "5898.82") : "0.00",
    tds_inr: status === "paid" && i === 1 ? "99.98" : "0.00",
    balance_due_inr: status === "issued" ? (big ? "1234567.88" : "5898.82") : "0.00",
    notes: i === 1 ? LONG_TEXT : null,
    void_reason:
      status === "void"
        ? "Raised against the wrong billing account; replaced by a corrected invoice."
        : null,
    version: 1 + i,
  };
}

const PLATFORM_INVOICES: Schemas["InvoiceOut"][] = Array.from({ length: 8 }, (_, i) =>
  invoice(i, pick(SCHOOLS, i)[0]),
);

function tenantDetail(tenant_id: string): Schemas["TenantDetailOut"] {
  const index = Math.max(
    0,
    TENANTS.findIndex((row) => row.tenant_id === tenant_id),
  );
  const summary = TENANTS[index] as Schemas["TenantSummaryOut"];
  return {
    ...summary,
    tenant_id,
    ...(tenant_id === T1
      ? {
          tenant_status: "active" as const,
          subscription_status: "active" as const,
          deployment_status: "healthy",
        }
      : {}),
    boards: ["STATE_AP", "CBSE", "CISCE"],
    tenant_status_reason: summary.tenant_status === "suspended" ? LONG_TEXT : null,
    offboard_requested_at: summary.tenant_status === "offboarding" ? at(21) : null,
    offboard_approved_at: null,
    subscription: subscription(0, tenant_id),
    counts: { users: 48, active_memberships: 45, academic_years: 3, sections: 36 },
    open_tickets: 3,
    invoices: Array.from({ length: 6 }, (_, i) => invoice(i, tenant_id)),
    flag_overrides: {
      "ask.citations_v2": true,
      "extraction.register_photos": false,
      "exports.udise_plus_2026_layout": true,
    },
    provisioning: null,
  };
}

function billingAccount(tenant_id: string): Schemas["BillingAccountOut"] {
  return {
    id: BILLING_ACCOUNT,
    tenant_id,
    legal_name: "Sri Venkateswara Zilla Parishad Educational Society and Charitable Trust",
    gstin: "37BBBBB1111B1Z6",
    pan: null,
    billing_contact_name: "Synthetica Accounts Officer",
    billing_email: LONG_EMAIL,
    billing_phone: null,
    address_line1: "Door No. 12-34/5A, Synthetic Colony Main Road, Near Zilla Parishad High School",
    address_line2: null,
    city: "Synthetic Nagar",
    district: "Sri Potti Sriramulu Nellore",
    state_code: "37",
    postal_code: "520000",
    po_reference: LONG_TOKEN,
    version: 3,
  };
}

function usageRow(tenant_id: string, day: number, i: number): Schemas["UsageDailyOut"] {
  return {
    tenant_id,
    usage_date: date(9, day),
    source: i % 4 === 3 ? "heartbeat" : "shared",
    students_active: [1210, 1487, 36, 9876, 0, 412][i % 6] ?? 0,
    staff_users: [48, 61, 3, 212, 0, 19][i % 6] ?? 0,
    active_users: [31, 44, 1, 187, 0, 12][i % 6] ?? 0,
    documents: [320, 1045, 2, 18230, 0, 77][i % 6] ?? 0,
    storage_bytes:
      [3_489_660_928, 52_613_349_376, 1_048_576, 98_765_432_100, 0, 734_003_200][i % 6] ?? 0,
    ai_queries: [140, 902, 0, 12_455, 0, 33][i % 6] ?? 0,
    ai_input_tokens: [420_000, 2_706_000, 0, 37_365_000, 0, 99_000][i % 6] ?? 0,
    ai_output_tokens: [70_000, 451_000, 0, 6_227_500, 0, 16_500][i % 6] ?? 0,
    ai_cost_inr: ["112.40", "721.60", "0.00", "9964.25", "0.00", "26.40"][i % 6] ?? "0.00",
    ai_answers: [120, 860, 0, 11_980, 0, 30][i % 6] ?? 0,
  };
}

const FLEET_VERSIONS: Schemas["FleetVersionOut"][] = [
  { version: "2026.09.1", deployments: 142 },
  { version: "2026.08.4", deployments: 17 },
  { version: "2026.09.1-hotfix.3+build.20260928.abcdef0", deployments: 2 },
  { version: "2026.07.2", deployments: 1 },
];

const DEPLOYMENTS: Schemas["DeploymentOut"][] = SCHOOLS.slice(0, 8).map(
  ([tenant_id, school_name, tenant_code], i) => {
    const dedicated = i === 3 || i === 6 || i === 1;
    return {
      id: uid("00000000de", i + 1),
      tenant_id,
      school_name,
      tenant_code,
      tenant_status: pick(TENANT_STATUS, i),
      mode: dedicated ? "dedicated" : "shared",
      region: "ap-south-1",
      backup_region: "ap-south-2",
      status: pick(DEPLOY_STATUS, i),
      hostname: dedicated ? `${tenant_code}.dedicated.very-long-school-domain.example` : null,
      custom_domain: i === 3 ? "records.sri-venkateswara-zilla-parishad-high-school.example" : null,
      host_ref: dedicated ? `i-${LONG_TOKEN.toLowerCase()}` : null,
      app_version: i === 2 || i === 7 ? null : pick(FLEET_VERSIONS, i).version,
      target_version: i === 3 ? "2026.09.1" : null,
      heartbeat_key_id: dedicated ? `hbk_${LONG_TOKEN}` : null,
      heartbeat_next_key_id: i === 3 ? `hbk_next_${LONG_TOKEN}` : null,
      last_heartbeat_at: i === 2 || i === 7 ? null : at(28, 4, 30 - i),
      version: 1 + i,
    };
  },
);

const FLAGS: Schemas["FlagOut"][] = [
  {
    key: "ask.citations_v2",
    enabled: true,
    rollout_percent: 100,
    description: "Show source chips under every AI answer with page numbers.",
    tenant_id: null,
  },
  {
    key: "extraction.register_photos",
    enabled: true,
    rollout_percent: 25,
    description: LONG_TEXT,
    tenant_id: null,
  },
  {
    key: "exports.udise_plus_2026_layout_with_new_columns_for_apaar",
    enabled: false,
    rollout_percent: null,
    description: null,
    tenant_id: null,
  },
  {
    key: "notifications.whatsapp",
    enabled: false,
    rollout_percent: 0,
    description: "తల్లిదండ్రులకు వాట్సాప్ ద్వారా సందేశాలు పంపడం (ప్రయోగాత్మకం).",
    tenant_id: null,
  },
  {
    key: "promotions.bulk",
    enabled: true,
    rollout_percent: null,
    description: "Year-end promotions in bulk.",
    tenant_id: null,
  },
  {
    key: "dq.fuzzy_names_te",
    enabled: true,
    rollout_percent: 50,
    description: "Telugu-aware name matching.",
    tenant_id: null,
  },
  {
    key: "ask.citations_v2",
    enabled: false,
    rollout_percent: null,
    description: null,
    tenant_id: T2,
  },
  {
    key: "extraction.register_photos",
    enabled: true,
    rollout_percent: null,
    description: null,
    tenant_id: T1,
  },
  {
    key: "extraction.register_photos",
    enabled: true,
    rollout_percent: null,
    description: null,
    tenant_id: uid("00000000ab", 4),
  },
].map((flag, i) => ({ ...flag, updated_at: i === 4 ? null : at(20 + (i % 8)), version: 1 + i }));

const OPERATORS: Schemas["OperatorOut"][] = [
  ["Synthetica Platform Owner", "owner@schoolos.example", ["platform_owner"], "active", true],
  [
    "Synthetica Platform Engineer With A Long Name",
    LONG_EMAIL,
    ["platform_engineer", "support_agent", "billing_admin"],
    "active",
    true,
  ],
  [
    "సింథటిక్ సపోర్ట్ ఏజెంట్ శ్రీలక్ష్మి",
    "support.te@schoolos.example",
    ["support_agent"],
    "active",
    false,
  ],
  ["Synthetica Billing", "billing@schoolos.example", ["billing_admin"], "invited", false],
  ["Synthetica Viewer", "viewer@schoolos.example", ["platform_viewer"], "deactivated", true],
  [
    "Synthetica On-call",
    "oncall.rotation.primary.synthetic@schoolos.example",
    ["platform_engineer", "platform_viewer"],
    "active",
    true,
  ],
  ["Synthetica Trainee", "trainee@schoolos.example", [], "invited", false],
].map(([display_name, email, roles, status, mfa], i) => ({
  id: i === 0 ? OPERATOR_ID : uid("00000000f1", i + 1),
  display_name: display_name as string,
  email: email as string,
  roles: roles as Schemas["OperatorOut"]["roles"],
  status: status as Schemas["OperatorOut"]["status"],
  mfa_enrolled: mfa as boolean,
  last_login_at: status === "invited" ? null : at(28 - i, 4),
  created_at: `2026-06-0${1 + i}T04:30:00Z`,
}));

const ANNOUNCEMENTS: Schemas["AnnouncementOut"][] = [
  [
    "Maintenance on Sunday",
    "ఆదివారం నిర్వహణ",
    "SchoolOS is unavailable from 06:00 to 07:00.",
    "06:00 నుండి 07:00 వరకు SchoolOS అందుబాటులో ఉండదు.",
    "maintenance",
    "scheduled",
    "all",
  ],
  [
    "New: register photos can now be read in Telugu and English, with a review queue for rows the reader was unsure about",
    "కొత్తది: రిజిస్టర్ ఫోటోలు",
    LONG_TEXT,
    TE_TEXT,
    "info",
    "scheduled",
    "tier",
  ],
  [
    "UDISE+ deadline",
    "UDISE+ గడువు",
    "Submit before 15 October.",
    "అక్టోబర్ 15 లోపు సమర్పించండి.",
    "warning",
    "draft",
    "tenants",
  ],
  [
    "Security update",
    "భద్రతా నవీకరణ",
    `Rotate heartbeat keys. Ref ${LONG_TOKEN}.`,
    "హార్ట్‌బీట్ కీలను మార్చండి.",
    "critical",
    "cancelled",
    "tier",
  ],
  [
    "Diwali greetings",
    "దీపావళి శుభాకాంక్షలు",
    "Support hours are shorter on 20 October.",
    "అక్టోబర్ 20న సపోర్ట్ సమయం తక్కువ.",
    "info",
    "scheduled",
    "all",
  ],
  [
    "Billing change",
    "బిల్లింగ్ మార్పు",
    "Invoices now show the SAC code.",
    "ఇన్‌వాయిస్‌లలో SAC కోడ్ ఉంటుంది.",
    "celebration",
    "scheduled",
    "all",
  ],
].map(([title_en, title_te, body_en, body_te, severity, status, audience], i) => ({
  id: uid("00000000a5", i + 1),
  title_en: title_en ?? "",
  title_te: title_te ?? "",
  body_en: body_en ?? "",
  body_te: body_te ?? "",
  severity: severity ?? "info",
  status: status ?? "scheduled",
  audience: audience ?? "all",
  audience_tier: audience === "tier" ? (i === 1 ? "dedicated" : "shared") : null,
  audience_tenant_ids: audience === "tenants" ? SCHOOLS.slice(0, 7).map(([id]) => id) : [],
  starts_at: at(26 + (i % 3), 0),
  ends_at: `2026-10-${String(1 + i).padStart(2, "0")}T01:30:00Z`,
  version: 1 + i,
}));

const PLATFORM_AUDIT: Schemas["PlatformAuditEventOut"][] = [
  [
    "tenant.provisioned",
    "tenant",
    { code: "svzphs-and-junior-college-guntur-district", tier: "dedicated" },
  ],
  ["tenant.suspended", "tenant", { reason: LONG_TEXT }],
  [
    "invoice.issued",
    "invoice",
    { invoice_number: `SOS/2026-27/${LONG_TOKEN}`, total_inr: "1234567.88" },
  ],
  [
    "flag.updated",
    "feature_flag",
    { key: "exports.udise_plus_2026_layout_with_new_columns_for_apaar", enabled: false },
  ],
  [
    "operator.roles_changed",
    "operator",
    { roles: ["platform_engineer", "support_agent", "billing_admin"] },
  ],
  ["deployment.heartbeat_key_rotated", "deployment", { key_id: `hbk_next_${LONG_TOKEN}` }],
  ["announcement.created", "announcement", { title_te: "ఆదివారం నిర్వహణ" }],
  [
    "breakglass.requested",
    "break_glass_request",
    { reason_code: "support_request", duration_minutes: 120 },
  ],
  ["auth.login", "operator", {}],
].map(([action, resource_type, summary], i) => ({
  id: uid("00000000fa", i + 1),
  seq: 9_876 - i,
  action: action as string,
  actor_type: i === 5 ? "system" : "operator",
  actor_id: i === 5 ? null : OPERATOR_ID,
  resource_type: resource_type as string,
  resource_id: i === 8 ? null : uid("00000000fb", i + 1),
  subject_tenant_id: i === 3 || i === 4 || i === 8 ? null : pick(SCHOOLS, i)[0],
  request_id: i % 2 === 0 ? `req_${LONG_TOKEN}` : null,
  occurred_at: at(28 - Math.floor(i / 2), 12 - (i % 6)),
  summary: summary as Record<string, unknown>,
}));

const PLATFORM_BREAK_GLASS: Schemas["BreakGlassOut"][] = Array.from({ length: 7 }, (_, i) => ({
  id: uid("00000000b6", 0xb1 + i),
  tenant_id: pick(SCHOOLS, i)[0],
  requested_by: OPERATOR_ID,
  reason_code: pick(["support_request", "security_incident", "legal_obligation"], i),
  reason: i % 2 === 0 ? LONG_TEXT : "The school asked for help with an import that failed.",
  scope: i % 2 === 0 ? { section_id: SECTION_A } : { resource: "students", ref: LONG_TOKEN },
  duration_minutes: [120, 30, 240, 60, 120, 480, 15][i] ?? 60,
  emergency: i === 1 || i === 4,
  emergency_confirmed_by_1: i === 1 || i === 4 ? uid("00000000f1", 2) : null,
  emergency_confirmed_by_2: i === 1 ? uid("00000000f1", 6) : null,
  status: pick(["requested", "active", "approved", "expired", "revoked", "denied", "requested"], i),
  created_at: at(20 + i),
}));

/* ------------------------------------------------------------------ certificates (US-1101..US-1108) */

const CERT_MAIN = "0192f3a4-0000-7000-8000-0000000ce001";
const CERT_TYPES = ["transfer", "bonafide", "study", "conduct"] as const;
const CERT_STATUSES = [
  "pending",
  "issued",
  "issued",
  "rejected",
  "withdrawn",
  "cancelled",
] as const;
const CERT_PREFIX = { transfer: "TC", bonafide: "BC", study: "SC", conduct: "CC" } as const;
const CERT_TITLES = {
  transfer: ["Transfer certificate", "బదిలీ ధృవీకరణ పత్రం"],
  bonafide: ["Bonafide certificate", "బోనఫైడ్ ధృవీకరణ పత్రం"],
  study: ["Study certificate", "చదువు ధృవీకరణ పత్రం"],
  conduct: ["Conduct certificate", "ప్రవర్తన ధృవీకరణ పత్రం"],
} as const;

const certLine = (key: string, en: string, te: string, value: string): Schemas["ContentLine"] => ({
  key,
  label_en: en,
  label_te: te,
  value,
});

function certificateContent(
  type: (typeof CERT_TYPES)[number],
  serial: string,
  name: string,
): Schemas["CertificateContent"] {
  const [title_en, title_te] = CERT_TITLES[type];
  return {
    certificate_type: type,
    title_en,
    title_te,
    serial,
    academic_year_label: "2026-27",
    issued_on: date(9, 29),
    school_name_en: LONG_SCHOOL,
    school_name_te: "శ్రీ వేంకటేశ్వర జిల్లా పరిషత్ ఉన్నత పాఠశాల మరియు జూనియర్ కళాశాల",
    school_address_en: "Door No. 12-3-45, Synthetic Main Road, Near Bus Stand, Synthetic Town",
    school_address_te: "డోర్ నం. 12-3-45, సింథటిక్ మెయిన్ రోడ్, సింథటిక్ పట్టణం",
    school_affiliation: `Recognition No. ${LONG_TOKEN}`,
    school_place: "Synthetic Town",
    student_name: name,
    admission_no: "SYN-2026-014",
    class_label_en: "Class 10 · B (Telugu medium)",
    class_label_te: "10వ తరగతి · B (తెలుగు మాధ్యమం)",
    fields: [
      certLine("full_name", "Full name", "పూర్తి పేరు", name),
      certLine("father_name", "Father's name", "తండ్రి పేరు", TE_GUARDIAN),
      certLine("dob", "Date of birth", "పుట్టిన తేదీ", "14/03/2012"),
      certLine("admission_no", "Admission number", "ప్రవేశ సంఖ్య", "SYN-2026-014"),
    ],
    details:
      type === "transfer"
        ? [
            certLine("leaving_date", "Date of leaving", "విడిచిన తేదీ", "29/09/2026"),
            certLine("leaving_reason", "Reason for leaving", "విడిచిన కారణం", "Parent transferred"),
            certLine("remarks", "Remarks", "వ్యాఖ్యలు", LONG_TEXT),
          ]
        : [certLine("purpose", "Purpose", "ప్రయోజనం", "Bus pass")],
    blanks:
      type === "transfer"
        ? [certLine("tc_caste", "Caste (as in the admission register)", "కులం", "")]
        : [],
  };
}

const CERTIFICATES: Schemas["CertificateOut"][] = Array.from({ length: 12 }, (_, i) => {
  const type = pick(CERT_TYPES, i);
  const status = pick(CERT_STATUSES, i);
  const hasSerial = status === "issued" || status === "cancelled";
  const serial = hasSerial
    ? `${CERT_PREFIX[type]}/2026-27/${String(i + 1).padStart(4, "0")}`
    : null;
  const name = pick(STUDENT_NAMES, i);
  const duplicate = i === 7;
  return {
    id: i === 0 ? CERT_MAIN : uid("0000000ce", i + 1),
    student_id: studentId(i % STUDENT_NAMES.length),
    certificate_type: type,
    status,
    requires_approval: type === "transfer",
    inputs:
      type === "transfer"
        ? {
            leaving_date: date(9, 29),
            leaving_reason: "parent_transferred",
            promotion: "promoted",
            conduct: "good",
            remarks: LONG_TEXT,
          }
        : { purpose: "bus_pass" },
    original_certificate_id: duplicate ? uid("0000000ce", 2) : null,
    duplicate_no: duplicate ? 1 : null,
    duplicate_reason: duplicate ? LONG_TEXT : null,
    academic_year_id: hasSerial ? YEAR_ID : null,
    serial,
    student_name: i === 9 ? null : name,
    admission_no: i === 4 ? null : `SYN-2026-${String(i + 14).padStart(3, "0")}`,
    content: hasSerial || i === 0 ? certificateContent(type, serial ?? "", name) : null,
    requested_by: i % 2 === 0 ? OTHER_MEMBERSHIP : ME_MEMBERSHIP,
    requested_at: at(10 + i),
    decided_by: status === "pending" ? null : ME_MEMBERSHIP,
    decided_at: status === "pending" ? null : at(11 + i),
    decision_note: status === "rejected" ? LONG_TEXT : null,
    issued_by: hasSerial ? ME_MEMBERSHIP : null,
    issued_at: hasSerial ? at(11 + i) : null,
    cancelled_by: status === "cancelled" ? ME_MEMBERSHIP : null,
    cancelled_at: status === "cancelled" ? at(12 + i) : null,
    cancel_reason: status === "cancelled" ? LONG_TEXT : null,
    document_id: hasSerial ? uid("0000000d0", i + 1) : null,
    pdf_status: hasSerial ? pick(["ready", "queued", "failed"] as const, i) : "none",
    version: 1 + (i % 3),
    can_approve: status === "pending" && i % 2 === 0,
    can_withdraw: status === "pending" && i % 2 === 1,
    can_cancel: status === "issued",
    can_duplicate: status === "issued",
  };
});

const choice = (value: string, en: string, te: string): Schemas["ChoiceOut"] => ({
  value,
  label_en: en,
  label_te: te,
});

const PURPOSE_INPUTS = (required: boolean): Schemas["InputOut"][] => [
  {
    key: "purpose",
    kind: "choice",
    required,
    max_length: null,
    choices: [
      choice("bus_pass", "Bus pass", "బస్ పాస్"),
      choice("scholarship", "Scholarship application", "ఉపకార వేతన దరఖాస్తు"),
    ],
  },
  { key: "purpose_note", kind: "text", required: false, max_length: 120, choices: [] },
];

const CERTIFICATE_TYPE_INFO: Schemas["CertificateTypeOut"][] = [
  {
    key: "transfer",
    label_en: CERT_TITLES.transfer[0],
    label_te: CERT_TITLES.transfer[1],
    requires_approval: true,
    ends_enrolment: true,
    printed: ["full_name", "father_name", "dob", "admission_no"],
    inputs: [
      { key: "leaving_date", kind: "date", required: true, max_length: null, choices: [] },
      {
        key: "leaving_reason",
        kind: "choice",
        required: true,
        max_length: null,
        choices: [
          choice("parent_request", "At the request of the parent", "తల్లిదండ్రుల అభ్యర్థన మేరకు"),
          choice("parent_transferred", "Parent transferred", "తల్లిదండ్రుల బదిలీ"),
        ],
      },
      {
        key: "promotion",
        kind: "choice",
        required: true,
        max_length: null,
        choices: [choice("promoted", "Yes, promoted", "అవును, ఉత్తీర్ణత")],
      },
      {
        key: "conduct",
        kind: "choice",
        required: true,
        max_length: null,
        choices: [choice("good", "Good", "మంచిది")],
      },
      { key: "remarks", kind: "text", required: false, max_length: 200, choices: [] },
    ],
  },
  ...(["bonafide", "study", "conduct"] as const).map((key): Schemas["CertificateTypeOut"] => ({
    key,
    label_en: CERT_TITLES[key][0],
    label_te: CERT_TITLES[key][1],
    requires_approval: false,
    ends_enrolment: false,
    printed: ["full_name", "father_name", "dob"],
    inputs: PURPOSE_INPUTS(key === "bonafide"),
  })),
];

/** The transfer preview shows the blocker list; the others show the form. */
function certificatePreview(type: string, studentName: string): Schemas["CertificatePreview"] {
  const transfer = type === "transfer";
  return {
    student_id: STUDENT_MAIN,
    certificate_type: transfer ? "transfer" : "bonafide",
    requires_approval: transfer,
    fields: [
      {
        key: "full_name",
        label_en: "Full name",
        label_te: "పూర్తి పేరు",
        value: studentName,
        source: "admission_register",
        verified: false,
        provisional: true,
      },
      {
        key: "father_name",
        label_en: "Father's name",
        label_te: "తండ్రి పేరు",
        value: TE_GUARDIAN,
        source: "aadhaar_as_printed",
        verified: true,
        provisional: false,
      },
      {
        key: "dob",
        label_en: "Date of birth",
        label_te: "పుట్టిన తేదీ",
        value: "14/03/2012",
        source: "admission_register",
        verified: true,
        provisional: false,
      },
      {
        key: "admission_no",
        label_en: "Admission number",
        label_te: "ప్రవేశ సంఖ్య",
        value: `SYN-${LONG_TOKEN}`,
        source: null,
        verified: false,
        provisional: false,
      },
    ],
    class_label: "Class 10 · B (Telugu medium)",
    academic_year_label: "2026-27",
    blockers: transfer
      ? [
          {
            code: "dq_blocker",
            attribute_key: "dob",
            finding_id: uid("0000000f1", 1),
            rule_id: "DQ-002",
          },
          { code: "missing_value", attribute_key: "mother_name", finding_id: null, rule_id: null },
        ]
      : [],
    warnings: [{ code: "provisional_value", attribute_key: "full_name" }],
    can_issue: !transfer,
  };
}

/* ------------------------------------------------------------------ M5 flags */

const FLAG_RULES = [
  [
    "attendance",
    "attendance_streak",
    { days: 4, from: date(9, 21), to: date(9, 24), threshold: 3 },
  ],
  ["attendance", "attendance_rate", { rate: 62, days: 29, present: 18, threshold: 75 }],
  ["course", "course_low", { percent: 28, threshold: 35 }],
  ["course", "course_decline", { drop: 18.5, percent: 41, previous_percent: 59.5, threshold: 15 }],
  ["behaviour", "behaviour_concerns", { concerns: 3, window_days: 30, threshold: 3 }],
  ["behaviour", "manual", {}],
] as const;
const FLAG_STATUSES = ["open", "in_progress", "open", "closed"] as const;

const INSIGHT_FLAGS: Schemas["InsightFlagOut"][] = STUDENT_NAMES.map((name, i) => {
  const [indicator, rule, evidence] = pick(FLAG_RULES, i);
  const status = pick(FLAG_STATUSES, i);
  return {
    id: uid("0000000f50", i + 1),
    student: {
      id: studentId(i),
      full_name: i === 9 ? null : name,
      admission_no: `SYN-2026-${String(i + 14).padStart(3, "0")}`,
      section_label: i % 2 === 0 ? "Class 6 · A" : "Class 10 · B (Telugu medium)",
    },
    indicator,
    rule,
    evidence: { ...evidence },
    status,
    owner:
      i % 5 === 4
        ? null
        : {
            membership_id: ME_MEMBERSHIP,
            display_name: "Synthetica Venkata Ramana Murthy (Class teacher)",
          },
    raised_on: date(9, 10 + (i % 15)),
    due_on: date(9, 17 + (i % 12)),
    overdue: status === "open" && i % 3 === 0,
    actioned: status !== "open",
    first_action_at: status === "open" ? null : at(20),
    closed_at: status === "closed" ? at(26) : null,
    close_reason: status === "closed" ? "support_in_place" : null,
    raised_by:
      rule === "manual"
        ? { membership_id: OTHER_MEMBERSHIP, display_name: "Synthetica Principal" }
        : null,
    version: 1 + (i % 3),
  };
});

/* ------------------------------------------------------------------ routing */

// Ask chat (FR-KB-012): long titles in both scripts, a long answer with a table, many
// sources (one withheld), versions, follow-ups and memory items.
const ASK_CHAT = "0192f3a4-0000-7000-8000-00000000e9a1";
const ASK_DOC_SOURCE = "sos://doc/0192f3a4-0000-7000-8000-00000000d001/v1#p12";
const ASK_TITLES = [
  "Admission register mismatches for Class 6 to Class 10 before the UDISE+ deadline next week",
  "పదవ తరగతి బోర్డు రిజిస్ట్రేషన్ కోసం కావలసిన పత్రాలు మరియు చివరి తేదీలు",
  "Dasara holidays",
  "Transfer certificate steps for students leaving mid-year with pending fee dues",
];
const ASK_CHATS = Array.from({ length: 12 }, (_, i) => ({
  id: i === 0 ? ASK_CHAT : uid("00000000e9", 0xb0 + i),
  title: ASK_TITLES[i % ASK_TITLES.length] ?? "",
  pinned: i < 2,
  created_at: at(20 - i),
  updated_at: at(28 - i),
  message_count: 3 + i,
  version: 1,
}));
const ASK_ANSWER = [
  `The admission register and the Aadhaar-as-printed details differ for ${pick(STUDENT_NAMES, 0)} and two more students. [1] [2]`,
  "",
  "| Student | Register | Aadhaar as printed | Source |",
  "|---|---|:-:|--:|",
  `| ${pick(STUDENT_NAMES, 0)} | 12/06/2014 | 21/06/2014 | [1] |`,
  `| ${TE_GUARDIAN} | సుబ్రహ్మణ్యేశ్వర | Subrahmanyeswara | [2] |`,
  "",
  "- Check the **admission register** first: it is the legal record. [1]",
  "- Then raise a correction request with the evidence document. [3]",
].join("\n");
// MessageCitationOut: a source the member can no longer see has no title or snippet.
const askCitation = (index: number, title: string, withheld = false) => ({
  index,
  source: ASK_DOC_SOURCE,
  title: withheld ? null : title,
  snippet: withheld
    ? null
    : "Synthetic passage: the date of birth in the admission register is the legal anchor (BR-01) and must match the documents before the portal upload.",
  withheld,
});
// MessageOut. The Telugu question's answer cited a source the member can no longer see, so the
// API withholds that answer and its follow-ups too (answer_withheld).
const askMessage = (i: number, superseded = false) => {
  const withheld = i === 1;
  return {
    query_id: uid("00000000ea", 0xa0 + i),
    question:
      i === 1
        ? "పదవ తరగతి విద్యార్థుల బోర్డు రిజిస్ట్రేషన్ కోసం ఏ పత్రాలు కావాలి, చివరి తేదీ ఎప్పుడు?"
        : "Which students in Class 6 to Class 10 have a date of birth in the admission register that differs from the Aadhaar as printed, and what should the office do before the UDISE+ upload?",
    answer: withheld ? null : ASK_ANSWER,
    answer_withheld: withheld,
    status: "answered",
    mode: "full",
    language: i === 1 ? "te" : "en",
    citations: withheld
      ? [
          askCitation(
            1,
            "Admission register 2014-15, page 12 (scanned copy uploaded by the office)",
          ),
          askCitation(2, "", true),
        ]
      : [
          askCitation(
            1,
            "Admission register 2014-15, page 12 (scanned copy uploaded by the office)",
          ),
          askCitation(2, "Aadhaar as printed · synthetic record"),
          askCitation(3, "Correction requests: evidence documents · synthetic guide"),
        ],
    feedback: i === 0 ? "helpful" : null,
    followups:
      i === 2
        ? [
            "Show me only the Class 10 students whose names differ between the register and Aadhaar",
            "ఈ విద్యార్థుల కోసం సవరణ అభ్యర్థన ఎలా పెట్టాలి?",
          ]
        : [],
    created_at: at(28, 5 + i),
    superseded,
    cached: false,
    summarized: i === 2,
  };
};
const ASK_MEMORIES = [
  "I am the office clerk and prepare the UDISE+ upload for Classes 6 to 10 every September.",
  "సమాధానాలు తెలుగులో కావాలి, కానీ తేదీలు DD/MM/YYYY రూపంలో ఉండాలి.",
  "Prefers short answers with the source first.",
].map((text, i) => ({
  // MemoryOut: a suggestion is pending until saved and expires after 24 hours.
  id: uid("00000000e8", 0xa0 + i),
  text,
  source: i === 2 ? "suggested" : "explicit",
  status: i === 2 ? "pending" : "active",
  created_at: at(10 + i),
  updated_at: at(10 + i),
  expires_at: i === 2 ? at(11 + i) : null,
  version: 1,
}));

const UUID = "([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})";
const re = (pattern: string) => new RegExp(`^/api/v1${pattern.replace(/\{id\}/g, UUID)}$`);

type Answer = unknown | undefined;
type Handler = (match: RegExpExecArray, query: URLSearchParams) => Answer;

/** Filter a list by a query parameter when the caller sent it (values repeat: ?status=a&status=b). */
function by<T>(rows: T[], query: URLSearchParams, param: string, read: (row: T) => unknown): T[] {
  const wanted = query.getAll(param).flatMap((value) => value.split(","));
  return wanted.length === 0 ? rows : rows.filter((row) => wanted.includes(String(read(row))));
}

const ROUTES: Array<[RegExp, Handler]> = [
  // School console: students.
  [re("/students"), (_, q) => page(by(STUDENTS, q, "status", (r) => r.status))],
  [re("/students/{id}"), ([, id = STUDENT_MAIN]) => studentDetail(id)],
  [
    re("/students/{id}/values"),
    (_, q) => {
      const values = studentValues(STUDENT_NAMES[2] ?? "Synthetica");
      return by(values, q, "attribute", (r) => r.attribute_key);
    },
  ],
  [re("/students/{id}/guardians"), () => guardians()],
  [re("/students/{id}/enrollments"), ([, id = STUDENT_MAIN]) => enrollments(id)],
  // Data quality, change requests, exports.
  [
    re("/dq/findings"),
    (_, q) => {
      let rows = by(FINDINGS, q, "status", (r) => r.status);
      rows = by(rows, q, "severity", (r) => r.severity);
      rows = by(rows, q, "student_id", (r) => r.student.id);
      return page(rows);
    },
  ],
  [re("/dq/summary"), () => DQ_SUMMARY],
  [
    re("/change-requests"),
    (_, q) =>
      page(
        by(
          by(CHANGE_REQUESTS, q, "status", (r) => r.status),
          q,
          "student_id",
          (r) => r.student_id,
        ),
      ),
  ],
  [re("/exports"), () => page(EXPORTS)],
  // Certificates (US-1101..US-1105). Register print views are HTML and fall through.
  [re("/certificates/types"), () => CERTIFICATE_TYPE_INFO],
  [
    re("/certificates"),
    (_, q) => {
      let rows = by(CERTIFICATES, q, "status", (r) => r.status);
      rows = by(rows, q, "certificate_type", (r) => r.certificate_type);
      rows = by(rows, q, "student_id", (r) => r.student_id);
      return page(rows);
    },
  ],
  [
    re("/certificates/{id}"),
    ([, id = CERT_MAIN]) => ({ ...(CERTIFICATES.find((c) => c.id === id) ?? CERTIFICATES[0]), id }),
  ],
  [
    re("/students/{id}/certificates/preview"),
    (_, q) => certificatePreview(q.get("certificate_type") ?? "bonafide", pick(STUDENT_NAMES, 2)),
  ],
  // Register photos.
  [re("/extraction-batches"), () => page(BATCHES)],
  [re("/extraction-items"), (_, q) => page(by(ITEMS, q, "status", (r) => r.status))],
  // Notifications, audit.
  [
    re("/notifications"),
    (_, q) =>
      page(
        q.get("unread") === "true"
          ? NOTIFICATIONS.filter((n) => n.read_at === null)
          : NOTIFICATIONS,
      ),
  ],
  [
    re("/notifications/unread-count"),
    () => ({ count: NOTIFICATIONS.filter((n) => n.read_at === null).length }),
  ],
  [re("/audit/events"), () => page(AUDIT_EVENTS)],
  // M5: early-warning flags (long names, every rule and status).
  [re("/insights/flags"), (_, q) => page(by(INSIGHT_FLAGS, q, "status", (r) => r.status))],
  // Support, break-glass, billing, verified answers.
  [re("/support/tickets"), () => page(Array.from({ length: 8 }, (_, i) => ticket(i, T1, false)))],
  [
    re("/support/tickets/{id}"),
    ([, id = ""]) => ({ ...ticket(Math.max(0, parseInt(id.slice(-2), 16) - 1), T1, true), id }),
  ],
  [re("/breakglass/requests"), (_, q) => page(by(GRANTS, q, "status", (r) => r.status))],
  [
    re("/breakglass/requests/{id}"),
    ([, id = ""]) => ({ ...(GRANTS.find((g) => g.id === id) ?? GRANTS[1]), id }),
  ],
  [re("/tenant/billing/invoices"), () => page(TENANT_INVOICES)],
  // Admin console (US-1201): retention settings.
  [re("/admin/retention"), () => RETENTION],
  [re("/knowledge/conversations"), () => page(ASK_CHATS)],
  [
    re("/knowledge/conversations/{id}"),
    ([, id = ASK_CHAT]) => ({
      ...(ASK_CHATS.find((c) => c.id === id) ?? ASK_CHATS[0]),
      messages: [askMessage(0, true), askMessage(0), askMessage(1), askMessage(2)],
    }),
  ],
  [re("/knowledge/memories"), () => page(ASK_MEMORIES)],
  [re("/knowledge/memory-settings"), () => ({ enabled: true, school_enabled: true })],
  [
    re("/knowledge/verified-answers"),
    (_, q) => page(by(VERIFIED_ANSWERS, q, "status", (r) => r.status)),
  ],
  // Platform.
  [
    re("/platform/usage"),
    () => SCHOOLS.slice(0, 10).map(([tenant_id], i) => usageRow(tenant_id, 28 - (i % 3), i)),
  ],
  [re("/platform/fleet/versions"), () => FLEET_VERSIONS],
  [re("/platform/deployments"), (_, q) => page(by(DEPLOYMENTS, q, "status", (r) => r.status))],
  [re("/platform/tenants/{id}/billing-account"), ([, id = T1]) => billingAccount(id)],
  [
    re("/platform/tenants/{id}/usage"),
    ([, id = T1]) => Array.from({ length: 10 }, (_, i) => usageRow(id, 28 - i, i)),
  ],
  // The provisioning-stopped school keeps the stand-in's detail (undefined → fallback).
  [re("/platform/tenants/{id}"), ([, id = T1]) => (id === T3 ? undefined : tenantDetail(id))],
  [
    re("/platform/tenants"),
    (_, q) => {
      let rows = by(TENANTS, q, "status", (r) => r.tenant_status);
      rows = by(rows, q, "tier", (r) => r.tier);
      const text = (q.get("q") ?? "").toLowerCase();
      if (text)
        rows = rows.filter((r) => `${r.school_name} ${r.code}`.toLowerCase().includes(text));
      return page(rows);
    },
  ],
  [
    re("/platform/subscriptions"),
    (_, q) =>
      page(
        by(
          SCHOOLS.slice(0, 8).map(([id], i) => subscription(i, id)),
          q,
          "status",
          (r) => r.status,
        ),
      ),
  ],
  [re("/platform/flags"), () => page(FLAGS)],
  [re("/platform/operators"), () => page(OPERATORS)],
  [re("/platform/announcements"), () => page(ANNOUNCEMENTS)],
  [
    re("/platform/support/tickets"),
    (_, q) => {
      const rows = Array.from({ length: 8 }, (_, i) => ticket(i, pick(SCHOOLS, i)[0], false));
      return page(
        by(
          by(rows, q, "status", (r) => r.status),
          q,
          "tenant_id",
          (r) => r.tenant_id,
        ),
      );
    },
  ],
  [
    re("/platform/support/tickets/{id}"),
    ([, id = ""]) => {
      const i = Math.max(0, parseInt(id.slice(-2), 16) - 1);
      return { ...ticket(i, pick(SCHOOLS, i)[0], true), id };
    },
  ],
  [re("/platform/audit/events"), () => page(PLATFORM_AUDIT)],
  [
    re("/platform/break-glass-requests"),
    (_, q) => page(by(PLATFORM_BREAK_GLASS, q, "tenant_id", (r) => r.tenant_id)),
  ],
  [
    re("/platform/invoices"),
    (_, q) =>
      page(
        by(
          by(PLATFORM_INVOICES, q, "status", (r) => r.status),
          q,
          "tenant_id",
          (r) => r.tenant_id,
        ),
      ),
  ],
];

/** Searches sent as POST that only read (answered like the GET lists above). */
const SEARCHES: Array<[RegExp, () => unknown]> = [[re("/students/search"), () => page(STUDENTS)]];

async function answer(route: Route): Promise<void> {
  const request = route.request();
  const url = new URL(request.url());
  const path = url.pathname.replace(/^\/bff/, "");
  if (request.method() === "POST") {
    const search = SEARCHES.find(([pattern]) => pattern.test(path));
    if (!search) return route.fallback();
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(search[1]()),
    });
  }
  if (request.method() !== "GET") return route.fallback();
  for (const [pattern, handler] of ROUTES) {
    const match = pattern.exec(path);
    if (!match) continue;
    const body = handler(match, url.searchParams);
    if (body === undefined) return route.fallback();
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(body),
    });
  }
  return route.fallback();
}

/** Answer the listed GET /bff/api/v1/* routes with synthetic stress data; everything else falls through. */
export async function installFixtures(page: Page): Promise<void> {
  await page.route((url) => url.pathname.startsWith("/bff/api/v1/"), answer);
}

/** For the audit spec: the ids that have detail screens. */
export const FIXTURE_IDS = {
  tenant: T1,
  student: STUDENT_MAIN,
  supportTicket: uid("00000000cc", 1),
  breakGlass: GRANTS[0]?.id ?? "",
  platformTicket: uid("00000000cc", 1),
  changeRequest: CHANGE_REQUESTS[0]?.id ?? "",
  export: EXPORTS[0]?.id ?? "",
  batch: BATCHES[0]?.id ?? "",
  item: ITEMS[0]?.id ?? "",
  certificate: CERT_MAIN,
} as const;
