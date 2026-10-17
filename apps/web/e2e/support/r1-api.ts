/**
 * Canned API for the R1 screens (e2e/r1.spec.ts, the a11y and responsive specs): board and
 * portal readiness (US-504..US-506, ADR-0040) with the parent-slip PDF (FR-DQ-047), the APAAR
 * consent register (US-1901..US-1903, ADR-0039) with the consent-form PDF (FR-APC-007), the
 * transfer-in PEN check in the new-student form (FR-STU-018), the school's boards and operating
 * mode (FR-TEN-020..022) and the import template library (FR-IMP-030..032, ADR-0041).
 *
 * Answered only for the stand-in IdP subject `office` (an office admin of a BSEAP school), so
 * the other journeys and their /me stay as they were. Stateful only where a journey needs it
 * (boards saved, a PDF queued then ready, the school's form language); `resetR1()` (POST
 * /__e2e/reset on the stand-in API) starts again.
 *
 * Synthetic data only: "Synthetica …" names, SYN-… admission numbers, PEN 91345678901. No
 * Aadhaar number of any kind appears here: Aadhaar-as-printed values are masked unless the
 * API would reveal them (C3 with student.read_sensitive), and slips never show one.
 * Shapes follow apps/api/openapi.json through the generated types.
 */
import type { components } from "@schoolos/api-client";
import { FILES_PREFIX } from "./journey-api";

type Schemas = components["schemas"];
type Body = Record<string, unknown>;

/** Stand-in IdP subject of the R1 screens. */
export const OFFICE = "office";

const T1 = "0192f3a4-0000-7000-8000-000000000001";
const CLASS_ID = "0192f3a4-0000-7000-8000-0000000000c6";
const SECTION_A = "0192f3a4-0000-7000-8000-000000000500";
const SECTION_B = "0192f3a4-0000-7000-8000-000000000510";
const ME_USER = "0192f3a4-0000-7000-8000-0000000000d1";
const ME_MEMBERSHIP = "0192f3a4-0000-7000-8000-0000000000e1";

export const R1_IDS = {
  /** Needs the parent: Aadhaar lacks the space in "Sai Kumar". */
  student: "0192f3a4-0000-7000-8000-00000000f601",
  /** Ready: every record matches. */
  ready: "0192f3a4-0000-7000-8000-00000000f602",
  /** Needs the school: UDISE+ has another date of birth. */
  udise: "0192f3a4-0000-7000-8000-00000000f603",
  slipsExport: "0192f3a4-0000-7000-8000-00000000f6e1",
  formsExport: "0192f3a4-0000-7000-8000-00000000f6e2",
  sectionA: SECTION_A,
} as const;

export const SSC = "bseap-ssc-2027";
export const PEN_ON_ROLL = "91345678901";

const iso = (offsetMs = 0) => new Date(Date.now() + offsetMs).toISOString();
const page = <T>(data: T[]) => ({ data, next_cursor: null });
const notFound: [number, unknown] = [
  404,
  { type: "about:blank", title: "Not found", status: 404, code: "not_found" },
];

const PERMISSIONS = [
  "support.ticket.create",
  "student.read_basic",
  "student.read_sensitive",
  "student.create",
  "student.update_nonidentity",
  "import.run",
  "import.commit",
  "dq.findings.read",
  "dq.readiness.read",
  "dq.readiness.manage",
  "apaar.consent.read",
  "apaar.consent.record",
  "document.upload",
  "document.read",
  "export.board",
  "export.portal",
  "tenant.settings.manage",
];

function me(): Schemas["app__identity__schemas__MeOut"] {
  return {
    user_id: ME_USER,
    membership_id: ME_MEMBERSHIP,
    tenant_id: T1,
    tenant_ids: [T1],
    display_name: "Synthetica Office Admin",
    preferred_language: "en",
    roles: ["office_admin"],
    permissions: PERMISSIONS,
    scopes: [{ type: "school", ref: null }],
    mfa: true,
    tenant_status: "active",
    settings: { idle_timeout_minutes: 15, date_format: "DD/MM/YYYY", languages: ["en", "te"] },
  };
}

/* ------------------------------------------------------------------ state */

interface R1State {
  tenant: Schemas["TenantOut"];
  formLanguage: "en" | "te";
  settingsVersion: number;
  /** Polls of each queued PDF; the second poll finds it ready. */
  polls: Map<string, number>;
  exports: Map<string, Schemas["ExportOut"]>;
}

function freshTenant(): Schemas["TenantOut"] {
  return {
    id: T1,
    code: "sshs",
    name: "Sri Saraswati High School",
    boards: ["BSEAP"],
    state_code: "37",
    status: "active",
    plan_tier: "shared",
    deployment_mode: "shared",
    modules_hidden: [],
    settings: {
      languages: ["en", "te"],
      date_format: "DD/MM/YYYY",
      idle_timeout_minutes: 15,
      ai_features_enabled: true,
      ai_memory_enabled: false,
      ai_monthly_budget_inr: 5000,
      class_boards: {},
      operating_mode: "full",
      current_erp_name: "",
      fees_enabled: false,
      parent_language: "en",
    },
    version: 3,
  };
}

const state: R1State = {
  tenant: freshTenant(),
  formLanguage: "en",
  settingsVersion: 1,
  polls: new Map(),
  exports: new Map(),
};

export function resetR1(): void {
  state.tenant = freshTenant();
  state.formLanguage = "en";
  state.settingsVersion = 1;
  state.polls.clear();
  state.exports.clear();
}

/* ------------------------------------------------------------------ readiness */

const bi = (code: string, en: string, te = ""): Schemas["Bilingual"] => ({ code, en, te });

const SOURCES = [
  "https://www.tlm4all.com/2026/02/ap-ssc-pe--2026-nominal-rolls-correction.html",
  "https://teachernews.in/submission-nominal-rolls-for-ssc-public/",
];

const PROFILES: Schemas["ProfileOut"][] = [
  {
    key: SSC,
    version: 1,
    label_en: "AP SSC 2027 (BSEAP)",
    label_te: "",
    required_fields: ["full_name", "dob", "gender", "father_name", "mother_name", "admission_no"],
    needs_apaar: false,
    board: "BSEAP",
    classes: ["IX", "X"],
    applies_to_classes: ["IX", "X"],
    source: SOURCES,
    verified: false,
    readiness: true,
    superseded: false,
    parent_verification_slip: true,
    board_fields: [],
  },
  {
    key: "apaar",
    version: 1,
    label_en: "APAAR ID readiness",
    label_te: "",
    required_fields: [],
    needs_apaar: true,
    board: null,
    classes: [],
    applies_to_classes: [],
    source: ["https://apaar.education.gov.in/"],
    verified: false,
    readiness: true,
    superseded: false,
    parent_verification_slip: false,
    board_fields: [],
  },
  {
    key: "udise-plus",
    version: 1,
    label_en: "UDISE+ 2026-27",
    label_te: "",
    required_fields: ["full_name", "dob", "gender"],
    needs_apaar: false,
    board: null,
    classes: [],
    applies_to_classes: [],
    source: ["https://udiseplus.gov.in/"],
    verified: false,
    readiness: false,
    superseded: false,
    parent_verification_slip: false,
    board_fields: [],
  },
];

function readinessProfile(key: string): Schemas["ReadinessProfileOut"] {
  const found = PROFILES.find((profile) => profile.key === key) ?? PROFILES[0];
  return {
    key: found?.key ?? SSC,
    version: 1,
    label_en: found?.label_en ?? "",
    label_te: "",
    source: found?.source ?? [],
    verified: false,
    classes: found?.classes ?? [],
    fields: ["full_name", "dob", "gender", "father_name", "mother_name"],
  };
}

const STUDENTS: Schemas["ReadinessStudentOut"][] = [
  {
    student: {
      id: R1_IDS.student,
      display_name: "Synthetica Ravi Sai Kumar",
      admission_no: "SYN-2026-301",
    },
    section_id: SECTION_A,
    status: "needs_parent",
    owners: ["parent_aadhaar"],
    attribute_keys: ["full_name"],
    open_items: 1,
  },
  {
    student: {
      id: R1_IDS.udise,
      display_name: "Synthetica Anjali Devi",
      admission_no: "SYN-2026-302",
    },
    section_id: SECTION_A,
    status: "needs_school",
    owners: ["school_udise"],
    attribute_keys: ["dob"],
    open_items: 1,
  },
  {
    student: {
      id: R1_IDS.ready,
      display_name: "Synthetica Kiran Kumar",
      admission_no: "SYN-2026-303",
    },
    section_id: SECTION_A,
    status: "ready",
    owners: [],
    attribute_keys: [],
    open_items: 0,
  },
];

function summary(key: string): Schemas["ReadinessSummaryOut"] {
  return {
    profile: readinessProfile(key),
    totals: { students: 5, ready: 2, needs_parent: 1, needs_school: 1, blocked: 1 },
    sections: [
      {
        section_id: SECTION_A,
        class_id: CLASS_ID,
        students: 3,
        ready: 1,
        needs_parent: 1,
        needs_school: 1,
        blocked: 0,
      },
      {
        section_id: SECTION_B,
        class_id: CLASS_ID,
        students: 2,
        ready: 1,
        needs_parent: 0,
        needs_school: 0,
        blocked: 1,
      },
    ],
    last_run: null,
  };
}

function value(source: string, text: string, sensitive = false): Schemas["ReadinessValueOut"] {
  return { source, value: text, masked: sensitive ? "S••••••• R•• S••••••" : null, sensitive };
}

function detail(key: string, id: string): Schemas["ReadinessStudentDetailOut"] | undefined {
  const row = STUDENTS.find((item) => item.student.id === id);
  if (!row) return undefined;
  const name = row.student.display_name ?? "Synthetica";
  const aadhaarName = id === R1_IDS.student ? name.replace("Sai Kumar", "Saikumar") : name;
  const nameItems: Schemas["ReadinessItemOut"][] =
    id === R1_IDS.student
      ? [
          {
            reason: "mismatch",
            owner: "parent_aadhaar",
            status: "needs_parent",
            source: "aadhaar_as_printed",
            against: "admission_register",
            sources: ["admission_register", "aadhaar_as_printed"],
            kinds: ["spacing"],
            advisory: false,
            waived: false,
            severity: "blocker",
            explanation: bi(
              "readiness_mismatch",
              "Aadhaar (as printed) differs from the admission register.",
            ),
            owner_label: bi("parent_aadhaar", "The parent corrects Aadhaar at an Aadhaar centre."),
            kinds_text: bi("spacing", "A space is missing or extra."),
            segments: [
              { op: "equal", reference: "Synthetica Ravi Sai", other: "Synthetica Ravi Sai" },
              { op: "delete", reference: " ", other: "" },
              { op: "equal", reference: "Kumar", other: "Kumar" },
            ],
            changes: [bi("space_missing", "Space missing after “Sai”.")],
            finding_id: null,
            finding_status: null,
          },
        ]
      : [];
  return {
    profile: readinessProfile(key),
    student: row.student,
    section_id: row.section_id,
    applies: true,
    status: row.status,
    values_shown: true,
    fields: [
      {
        attribute_key: "full_name",
        reference: "admission_register",
        values: [
          value("admission_register", name),
          value("aadhaar_as_printed", aadhaarName, true),
          value("udise_plus", name),
        ],
        items: nameItems,
      },
      {
        attribute_key: "dob",
        reference: "admission_register",
        values: [
          value("admission_register", "14/03/2012"),
          value("aadhaar_as_printed", "14/03/2012", true),
          value("udise_plus", id === R1_IDS.udise ? "15/03/2012" : "14/03/2012"),
        ],
        items: [],
      },
    ],
  };
}

/* ------------------------------------------------------------------ APAAR consent */

const CONSENT_ROWS: Schemas["ConsentRowOut"][] = [
  {
    student_id: R1_IDS.student,
    display_name: "Synthetica Ravi Sai Kumar",
    admission_no: "SYN-2026-301",
    class_section: "Class 6 · A",
    section_id: SECTION_A,
    status: "pending",
    decided_on: null,
    has_form: false,
    recorded_at: null,
    version: 0,
    has_apaar_id: false,
  },
  {
    student_id: R1_IDS.udise,
    display_name: "Synthetica Anjali Devi",
    admission_no: "SYN-2026-302",
    class_section: "Class 6 · A",
    section_id: SECTION_A,
    status: "refused",
    decided_on: "2026-09-20",
    has_form: true,
    recorded_at: "2026-09-21T05:30:00Z",
    version: 1,
    has_apaar_id: false,
  },
  {
    student_id: R1_IDS.ready,
    display_name: "Synthetica Kiran Kumar",
    admission_no: "SYN-2026-303",
    class_section: "Class 6 · A",
    section_id: SECTION_A,
    status: "given",
    decided_on: "2026-09-18",
    has_form: true,
    recorded_at: "2026-09-19T05:30:00Z",
    version: 1,
    has_apaar_id: true,
  },
];

const counts = (rows: readonly Schemas["ConsentRowOut"][]): Schemas["StatusCounts"] => ({
  total: rows.length,
  given: rows.filter((row) => row.status === "given").length,
  refused: rows.filter((row) => row.status === "refused").length,
  pending: rows.filter((row) => row.status === "pending").length,
  withdrawn: rows.filter((row) => row.status === "withdrawn").length,
});

function consentSummary(): Schemas["ConsentSummaryOut"] {
  return {
    totals: counts(CONSENT_ROWS),
    sections: [
      { section_id: SECTION_A, class_section: "Class 6 · A", counts: counts(CONSENT_ROWS) },
    ],
  };
}

function studentConsent(id: string): Schemas["StudentConsentOut"] | undefined {
  const row = CONSENT_ROWS.find((item) => item.student_id === id);
  if (!row) return undefined;
  const history: Schemas["ConsentEntryOut"][] =
    row.status === "pending"
      ? []
      : [
          {
            id: "0192f3a4-0000-7000-8000-00000000f6c1",
            seq: 1,
            status: row.status,
            relationship: "father",
            guardian_id: null,
            decided_on: row.decided_on,
            form_language: "en",
            evidence_document_id: null,
            note: null,
            recorded_by: ME_USER,
            recorded_by_name: "Synthetica Office Admin",
            recorded_at: row.recorded_at ?? iso(),
          },
        ];
  return {
    student_id: id,
    status: row.status,
    current: history[0] ?? null,
    history,
    version: row.version,
  };
}

function canonical(value: string | null): Schemas["CanonicalOut"] {
  return {
    value,
    source: value === null ? null : "admission_register",
    verified: true,
    provisional: false,
    masked: false,
    conflicts: [],
  };
}

/** The student record behind the consent page (C2 values only). */
function student(id: string): Schemas["StudentOut"] | undefined {
  const row = CONSENT_ROWS.find((item) => item.student_id === id);
  if (!row) return undefined;
  return {
    id,
    status: "active",
    admission_no: row.admission_no,
    version: 3,
    created_at: "2026-06-10T04:30:00Z",
    updated_at: "2026-09-21T05:30:00Z",
    enrollment: {
      section_id: SECTION_A,
      class_id: CLASS_ID,
      academic_year_id: "0192f3a4-0000-7000-8000-0000000000a1",
      label: "Class 6 · A",
      roll_no: "7",
    },
    canonical: {
      full_name: canonical(row.display_name),
      dob: canonical("2012-03-14"),
      gender: canonical("male"),
      admission_no: canonical(row.admission_no),
      udise_pen: canonical(id === R1_IDS.student ? PEN_ON_ROLL : null),
    },
    values: {},
    sensitive_revealable: false,
  };
}

const GUARDIANS: Schemas["GuardianOut"][] = [
  {
    id: "0192f3a4-0000-7000-8000-00000000f6a1",
    full_name: "Synthetica Father",
    relationship: "father",
    is_primary: true,
    phone: null,
    address: null,
    has_phone: false,
    has_address: false,
    masked: true,
    version: 1,
  },
];

/* ------------------------------------------------------------------ print jobs (PDF) */

function printJob(
  id: string,
  kind: "readiness_slips" | "apaar_consent_forms",
  body: Body,
): Schemas["ExportOut"] {
  const studentId = typeof body.student_id === "string" ? body.student_id : null;
  const sectionId = typeof body.section_id === "string" ? body.section_id : null;
  return {
    id,
    kind,
    profile_key: kind === "readiness_slips" ? String(body.profile_key ?? SSC) : null,
    profile_version: kind === "readiness_slips" ? 1 : null,
    layout_version: 1,
    formats: ["pdf"],
    language: kind === "apaar_consent_forms" ? state.formLanguage : "en",
    scope: studentId ? { student_ids: [studentId] } : { section_ids: [sectionId ?? SECTION_A] },
    columns: null,
    include_sensitive: body.include_sensitive === true,
    student_count: studentId ? 1 : 3,
    status: "queued",
    error_code: null,
    created_at: iso(),
    started_at: null,
    finished_at: null,
    expires_at: null,
    files: [],
    requested_by: { membership_id: ME_MEMBERSHIP, display_name: "Synthetica Office Admin" },
    own: true,
    can_download: true,
  };
}

function polled(id: string): Schemas["ExportOut"] | undefined {
  const job = state.exports.get(id);
  if (!job) return undefined;
  const polls = (state.polls.get(id) ?? 0) + 1;
  state.polls.set(id, polls);
  if (polls < 2 || job.status === "ready") return job;
  const ready: Schemas["ExportOut"] = {
    ...job,
    status: "ready",
    started_at: iso(-2_000),
    finished_at: iso(),
    expires_at: iso(7 * 24 * 3_600_000),
    files: [{ format: "pdf", content_type: "application/pdf", size_bytes: 48_213 }],
  };
  state.exports.set(id, ready);
  return ready;
}

function pdfLink(url: URL, job: Schemas["ExportOut"]): Schemas["ExportDownloadOut"] {
  const name =
    job.kind === "readiness_slips" ? `verification-slips-${SSC}.pdf` : "apaar-consent-forms.pdf";
  return {
    url: `${url.origin}${FILES_PREFIX}${name}`,
    expires_at: iso(5 * 60_000),
    format: "pdf",
    content_type: "application/pdf",
    filename: `schoolos-${name}`,
  };
}

/* ------------------------------------------------------------------ import library, PEN */

const PRESETS: Schemas["PresetOut"][] = [
  {
    key: "schoolos-blank",
    version: 1,
    label_en: "SchoolOS student template",
    label_te: "",
    description_en: "One row per student with the SchoolOS column names.",
    import_source: "admission_register",
    template: true,
    verified: true,
    source: [],
    columns: [
      { header: "Admission number", target: "admission_no", aliases: [], note: "" },
      { header: "Full name", target: "full_name", aliases: [], note: "" },
    ],
  },
  {
    key: "udise-student-list",
    version: 1,
    label_en: "UDISE+ student list",
    label_te: "",
    description_en: "The student list downloaded from the UDISE+ Student Module.",
    import_source: "udise_plus",
    template: true,
    verified: false,
    source: ["https://udiseplus.gov.in/"],
    columns: [
      { header: "Student Name", target: "full_name", aliases: [], note: "" },
      { header: "PEN", target: "udise_pen", aliases: [], note: "11 digits" },
    ],
  },
];

function penCheck(body: Body): Schemas["NationalIdCheckOut"] {
  const pen = String(body.udise_pen ?? "");
  if (pen === PEN_ON_ROLL)
    return {
      udise_action: "open_existing_record",
      matches: [
        {
          identifier: "udise_pen",
          student_id: R1_IDS.student,
          display_name: "Synthetica Ravi Sai Kumar",
          admission_no: "SYN-2026-301",
          class_section: "Class 6 · A",
          status: "active",
        },
      ],
    };
  return { udise_action: pen ? "import_by_pen" : "new_udise_record", matches: [] };
}

/* ------------------------------------------------------------------ dispatch */

/**
 * The R1 answer to a request, or undefined when this module does not own it. Only the
 * `office` subject is answered here, except the import template library, which every imports
 * page (the M1 journey's too) loads.
 */
export function r1Answer(
  method: string,
  url: URL,
  subject: string,
  body: Body,
): [number, unknown] | undefined {
  const path = url.pathname;
  const get = method === "GET";
  const post = method === "POST";

  // Import template library (FR-IMP-030..032).
  if (path === "/api/v1/import-presets" && get) return [200, PRESETS];
  if (subject !== OFFICE) return undefined;

  if (path === "/api/v1/me" && get) return [200, me()];
  if (path === "/api/v1/tenant" && get) return [200, state.tenant];
  if (path === "/api/v1/tenant" && method === "PATCH") {
    // TenantSettingsPatch: boards at the top level, the other changed settings beside them.
    const settings = Object.fromEntries(
      Object.entries(body).filter(([key]) => key !== "boards"),
    ) as Partial<Schemas["TenantSettings"]>;
    state.tenant = {
      ...state.tenant,
      boards: Array.isArray(body.boards) ? (body.boards as string[]) : state.tenant.boards,
      settings: { ...state.tenant.settings, ...settings },
      version: state.tenant.version + 1,
    };
    return [200, state.tenant];
  }

  // Readiness (ADR-0040).
  if (path === "/api/v1/dq/profiles" && get) {
    const boards = state.tenant.boards;
    const all = url.searchParams.get("all") === "true";
    return [
      200,
      PROFILES.filter(
        (profile) => all || !profile.board || boards.length === 0 || boards.includes(profile.board),
      ),
    ];
  }
  const readiness =
    /^\/api\/v1\/dq\/readiness\/([a-z0-9-]+)(?:\/students(?:\/([0-9a-f-]{36}))?)?$/.exec(path);
  if (readiness && get) {
    const [, key = SSC, studentId] = readiness;
    if (!PROFILES.some((profile) => profile.key === key && profile.readiness)) return notFound;
    if (path.endsWith("/students")) {
      const section = url.searchParams.get("section_id");
      return [200, STUDENTS.filter((row) => row.section_id === section)];
    }
    if (studentId) {
      const found = detail(key, studentId);
      return found ? [200, found] : notFound;
    }
    return [200, summary(key)];
  }

  // APAAR consent register (ADR-0039).
  if (path === "/api/v1/apaar/consents/summary" && get) return [200, consentSummary()];
  if (path === "/api/v1/apaar/consents" && get) {
    const status = url.searchParams.get("status");
    return [200, page(CONSENT_ROWS.filter((row) => !status || row.status === status))];
  }
  if (path === "/api/v1/apaar/settings" && get)
    return [200, { form_language: state.formLanguage, version: state.settingsVersion }];
  if (path === "/api/v1/apaar/settings" && method === "PUT") {
    state.formLanguage = body.form_language === "te" ? "te" : "en";
    state.settingsVersion += 1;
    return [200, { form_language: state.formLanguage, version: state.settingsVersion }];
  }
  const consent = /^\/api\/v1\/students\/([0-9a-f-]{36})\/apaar-consent$/.exec(path);
  if (consent && get) {
    const found = studentConsent(consent[1] ?? "");
    return found ? [200, found] : notFound;
  }

  const record = /^\/api\/v1\/students\/([0-9a-f-]{36})(\/guardians)?$/.exec(path);
  if (record && get) {
    if (record[2]) return [200, GUARDIANS];
    const found = student(record[1] ?? "");
    if (found) return [200, found];
  }

  // Print jobs: slips and consent forms as PDF (FR-DQ-047, FR-APC-007).
  if (path === "/api/v1/exports/readiness-slips" && post) {
    const job = printJob(R1_IDS.slipsExport, "readiness_slips", body);
    state.exports.set(job.id, job);
    state.polls.set(job.id, 0);
    return [202, job];
  }
  if (path === "/api/v1/exports/apaar-consent-forms" && post) {
    const job = printJob(R1_IDS.formsExport, "apaar_consent_forms", body);
    state.exports.set(job.id, job);
    state.polls.set(job.id, 0);
    return [202, job];
  }
  const job = /^\/api\/v1\/exports\/([0-9a-f-]{36})(\/download-url)?$/.exec(path);
  if (job && get && state.exports.has(job[1] ?? "")) {
    const id = job[1] ?? "";
    if (job[2]) {
      const row = state.exports.get(id);
      return row && row.status === "ready" ? [200, pdfLink(url, row)] : notFound;
    }
    const row = polled(id);
    return row ? [200, row] : notFound;
  }
  if (path === "/api/v1/exports" && get) return [200, page([...state.exports.values()])];

  // New-student form: transfer-in by PEN (FR-STU-018). The PEN is in the body, never the URL.
  if (path === "/api/v1/students/national-id-check" && post) return [200, penCheck(body)];

  return undefined;
}
