/**
 * Canned API for the M1 journeys (e2e/journeys.spec.ts): spreadsheet import → data-quality
 * findings → change request (maker, then checker) → board pre-check export. Stateful only as
 * far as the journey needs (an import moving parsed → validated → committed, findings resolved
 * or waived, a change request approved, an export queued → ready). `resetJourney()` (POST
 * /__e2e/reset on the stand-in API) starts again.
 *
 * Synthetic data only: "Synthetica …" names, SYN-… admission numbers. No Aadhaar number of any
 * kind appears here (Aadhaar-as-printed values are masked, as the API sends them).
 * Shapes follow apps/api/openapi.json through the generated types.
 */
import type { components } from "@schoolos/api-client";

type Schemas = components["schemas"];
type ImportOut = Schemas["ImportOut"];
type ImportRowOut = Schemas["ImportRowOut"];
type FindingOut = Schemas["FindingOut"];
type ChangeRequestOut = Schemas["ChangeRequestOut"];
type ExportOut = Schemas["ExportOut"];
type Bilingual = Schemas["Bilingual"];

const T1 = "0192f3a4-0000-7000-8000-000000000001";
/** Subjects of the stand-in IdP used by the journeys (the a11y "clerk" keeps its own /me). */
export const MAKER = "maker";
export const CHECKER = "checker";
const MEMBERSHIP: Record<string, string> = {
  [MAKER]: "0192f3a4-0000-7000-8000-0000000000e1",
  [CHECKER]: "0192f3a4-0000-7000-8000-0000000000e2",
};

export const IDS = {
  importOld: "0192f3a4-0000-7000-8000-0000000f0001",
  importNew: "0192f3a4-0000-7000-8000-0000000f0002",
  template: "0192f3a4-0000-7000-8000-0000000f0101",
  importDoc: "0192f3a4-0000-7000-8000-00000000d002",
  evidenceDoc: "0192f3a4-0000-7000-8000-00000000d003",
  student: "0192f3a4-0000-7000-8000-00000000e501",
  findingDob: "0192f3a4-0000-7000-8000-0000000f1001",
  findingFather: "0192f3a4-0000-7000-8000-0000000f1002",
  findingGender: "0192f3a4-0000-7000-8000-0000000f1003",
  changeRequest: "0192f3a4-0000-7000-8000-0000000c7001",
  export: "0192f3a4-0000-7000-8000-0000000e7001",
  run: "0192f3a4-0000-7000-8000-0000000f2001",
} as const;

const YEAR_ID = "0192f3a4-0000-7000-8000-0000000000a1";
const CLASS_ID = "0192f3a4-0000-7000-8000-0000000000c6";
const SECTION_A = "0192f3a4-0000-7000-8000-000000000500";
const SECTION_B = "0192f3a4-0000-7000-8000-000000000510";
/** Where the canned presigned POST points: same origin, answered by the spec's page.route. */
export const STORAGE_PATH = "/e2e-storage/upload";
/** Files behind the canned export download links (served by the stand-in API). */
export const FILES_PREFIX = "/e2e-files/";

const iso = (offsetMs = 0) => new Date(Date.now() + offsetMs).toISOString();
const HOUR = 3_600_000;
const DAY = 24 * HOUR;

const MAKER_PERMISSIONS = [
  "support.ticket.create",
  "student.read_basic",
  "student.create",
  "student.update_nonidentity",
  "student.identity_change.request",
  "import.run",
  "import.commit",
  "dq.findings.read",
  "dq.findings.resolve",
  "dq.findings.waive",
  "document.upload",
  "document.read",
  "export.board",
  "export.portal",
];
const CHECKER_PERMISSIONS = [
  ...MAKER_PERMISSIONS.filter((key) => key !== "student.identity_change.request"),
  "student.identity_change.approve",
];

function me(subject: string): Schemas["app__identity__schemas__MeOut"] {
  const checker = subject === CHECKER;
  return {
    user_id: checker
      ? "0192f3a4-0000-7000-8000-0000000000d2"
      : "0192f3a4-0000-7000-8000-0000000000d1",
    membership_id: MEMBERSHIP[subject] ?? "",
    tenant_id: T1,
    tenant_ids: [T1],
    display_name: checker ? "Synthetica Principal" : "Synthetica Office Clerk",
    preferred_language: "en",
    roles: checker ? ["principal"] : ["office_admin"],
    permissions: checker ? CHECKER_PERMISSIONS : MAKER_PERMISSIONS,
    scopes: [{ type: "school", ref: null }],
    mfa: true,
    tenant_status: "active",
    settings: { idle_timeout_minutes: 15, date_format: "DD/MM/YYYY", languages: ["en", "te"] },
  };
}

/* ------------------------------------------------------------------ catalog */

type Attribute = Schemas["AttributeOut"];
const SOURCES_ALL = [
  "admission_register",
  "udise_plus",
  "board_registration",
  "birth_certificate",
  "parent_form",
  "tc_incoming",
  "manual_entry",
];
function attribute(
  key: string,
  label_en: string,
  label_te: string,
  sort_order: number,
  extra: Partial<Attribute> = {},
): Attribute {
  return {
    key,
    data_type: "text",
    classification: "C2",
    is_identity: true,
    label_en,
    label_te,
    sort_order,
    allowed_sources: SOURCES_ALL,
    allowed_values: null,
    precedence: ["admission_register", "birth_certificate", "tc_incoming", "parent_form"],
    is_global: true,
    ...extra,
  };
}
/** Labels as in apps/api/app/students/attributes.yaml. */
const ATTRIBUTES: Attribute[] = [
  attribute("full_name", "Full name", "పూర్తి పేరు", 10),
  attribute("dob", "Date of birth", "పుట్టిన తేదీ", 20, { data_type: "date" }),
  attribute("gender", "Gender", "లింగం", 30, {
    data_type: "enum",
    allowed_values: ["female", "male", "transgender"],
  }),
  attribute("father_name", "Father's name", "తండ్రి పేరు", 40),
  attribute("admission_no", "Admission number", "ప్రవేశ సంఖ్య", 60, {
    allowed_sources: ["admission_register", "tc_incoming", "manual_entry"],
  }),
  attribute("aadhaar_last4", "Aadhaar (last 4 digits)", "ఆధార్ (చివరి 4 అంకెలు)", 90, {
    data_type: "digits4",
    classification: "C3",
    is_identity: false,
    allowed_sources: ["aadhaar_as_printed"],
  }),
  attribute(
    "aadhaar_dob_as_printed",
    "Date of birth as printed on Aadhaar",
    "ఆధార్‌లో ముద్రించిన పుట్టిన తేదీ",
    95,
    {
      data_type: "date",
      classification: "C3",
      is_identity: false,
      allowed_sources: ["aadhaar_as_printed"],
    },
  ),
];

/** Texts as in apps/api/app/dq/config/explanations.yaml. */
const ROUTE_CR: Bilingual = {
  code: "ROUTE-SCHOOL-CR",
  en: "Correct the school record (change request + evidence).",
  te: "పాఠశాల రికార్డును సరిచేయండి (రుజువు పత్రంతో మార్పు అభ్యర్థన ద్వారా).",
};
const ROUTE_UIDAI: Bilingual = {
  code: "ROUTE-UIDAI",
  en: "Parent should correct Aadhaar with UIDAI.",
  te: "తల్లిదండ్రులు UIDAI ద్వారా ఆధార్‌లో సవరణ చేయించుకోవాలి.",
};
const EXPLAIN: Record<string, Bilingual> = {
  "DQ-002": {
    code: "DQ-002",
    en: "Date of birth differs. Boards and APAAR need these to match.",
    te: "పుట్టిన తేదీ వేరుగా ఉంది. బోర్డులు, APAAR కోసం ఇవి ఒకేలా ఉండాలి.",
  },
  "DQ-003": {
    code: "DQ-003",
    en: "Gender differs between records.",
    te: "రికార్డులలో లింగం (జెండర్) వేర్వేరుగా ఉంది.",
  },
  "DQ-004": {
    code: "DQ-004",
    en: "Parent name spelled differently across records.",
    te: "తల్లి లేదా తండ్రి పేరు రికార్డులలో వేర్వేరుగా రాసి ఉంది.",
  },
};

const RULES: Schemas["RuleOut"][] = [
  ["DQ-002", "value_equal", ["dob"], "blocker"],
  ["DQ-003", "value_equal", ["gender"], "high"],
  ["DQ-004", "name_match", ["father_name"], "medium"],
].map(([id, check, keys, level]) => ({
  id: id as string,
  version: 1,
  check: check as string,
  scope: "student",
  attribute_keys: keys as string[],
  sources: ["admission_register", "aadhaar_as_printed"],
  requires_profile: false,
  severity: {
    mode: "fixed",
    level: level as Schemas["SeverityPolicyOut"]["level"],
    floor: null,
    cap: null,
  },
  explanation: EXPLAIN[id as string] as Bilingual,
  routes: id === "DQ-004" ? [ROUTE_CR] : [ROUTE_UIDAI, ROUTE_CR],
}));

const PROFILE_FIELDS = ["full_name", "dob", "gender", "father_name", "admission_no"];
const DQ_PROFILES: Schemas["ProfileOut"][] = [
  {
    key: "cisce-registration-2026",
    version: 1,
    label_en: "CISCE registration 2026",
    label_te: "CISCE నమోదు 2026",
    required_fields: PROFILE_FIELDS,
    needs_apaar: false,
    verified: false,
    superseded: false,
    parent_verification_slip: false,
  },
];
const EXPORT_PROFILES: Schemas["ExportProfileOut"][] = [
  {
    key: "cisce-registration-2026",
    kind: "board",
    permission: "export.board",
    version: 1,
    layout_version: 1,
    label_en: "CISCE registration 2026",
    label_te: "CISCE నమోదు 2026",
    fields: PROFILE_FIELDS,
    required_fields: PROFILE_FIELDS,
    allowed: true,
    verified: false,
    superseded: false,
  },
];

/* ------------------------------------------------------------------ students, documents */

function canonical(value: string, conflicts: string[] = []): Schemas["CanonicalOut"] {
  return {
    value,
    source: "admission_register",
    verified: true,
    provisional: false,
    masked: false,
    conflicts,
  };
}

function student(dob: string, dobConflicts: string[]): Schemas["StudentOut"] {
  return {
    id: IDS.student,
    status: "active",
    admission_no: "SYN-2026-014",
    version: 4,
    created_at: "2026-06-10T04:30:00Z",
    updated_at: "2026-09-20T04:30:00Z",
    enrollment: {
      section_id: SECTION_A,
      class_id: CLASS_ID,
      academic_year_id: YEAR_ID,
      label: "Class 6 · A",
      roll_no: "14",
    },
    canonical: {
      full_name: canonical("Synthetica Ravi Kumar"),
      dob: canonical(dob, dobConflicts),
      gender: canonical("male"),
      father_name: canonical("Synthetica Venkat Rao"),
      admission_no: canonical("SYN-2026-014"),
    },
    values: {},
    sensitive_revealable: false,
  };
}

function documentDetail(id: string, purpose: "import_file" | "evidence", title: string) {
  const version = {
    id: id.replace(/d00(\d)$/, "d10$1"),
    version_no: 1,
    mime_type: purpose === "evidence" ? "application/pdf" : "text/csv",
    size_bytes: 2048,
    status: "ready" as const,
    error: null,
    created_at: iso(),
    uploaded_by_me: true,
  };
  return {
    id,
    purpose,
    doc_type: purpose,
    title,
    issuer: null,
    issued_on: null,
    academic_year_id: null,
    language: null,
    sensitivity: "C2" as const,
    status: "active" as const,
    current_version: version,
    acl: [],
    created_by: "0192f3a4-0000-7000-8000-0000000000d1",
    created_at: iso(),
    updated_at: iso(),
    uploaded_by_me: true,
    version: 1,
    versions: [version],
  } satisfies Schemas["DocumentDetail"];
}

/* ------------------------------------------------------------------ imports */

const HEADERS: Array<[string, string | null, number]> = [
  ["Admission No", "admission_no", 100],
  ["Student Name", "full_name", 96],
  ["పుట్టిన తేదీ", "dob", 92],
  ["Gender", "gender", 100],
  ["Class", "class_section", 88],
  ["Remarks", null, 0],
];

function newBatch(): ImportOut {
  return {
    id: IDS.importNew,
    kind: "spreadsheet",
    source: "admission_register",
    status: "parsed",
    document_id: IDS.importDoc,
    file_kind: "csv",
    header_row: 1,
    columns: HEADERS.map(([header, suggested, score], index) => ({
      index,
      header,
      suggested,
      score,
      target: null,
    })),
    mapping_template_id: null,
    stats: { rows: 3 },
    row_count: 3,
    error_count: 0,
    error_code: null,
    job_id: "0192f3a4-0000-7000-8000-0000000f0201",
    created_by: MEMBERSHIP[MAKER] as string,
    created_at: iso(-5 * 60_000),
    updated_at: iso(),
    committed_at: null,
    revert_deadline: null,
    reverted_at: null,
    raw_file_deleted_at: null,
    version: 1,
    can_commit: false,
    can_revert: false,
  };
}

const OLD_BATCH_SUMMARY: Schemas["ImportSummary"] = {
  id: IDS.importOld,
  source: "admission_register",
  status: "committed",
  row_count: 42,
  error_count: 0,
  created_by: MEMBERSHIP[MAKER] as string,
  created_at: "2026-09-02T05:30:00Z",
  committed_at: "2026-09-02T05:40:00Z",
  reverted_at: null,
};

const TEMPLATE: Schemas["TemplateOut"] = {
  id: IDS.template,
  name: "Class list (office format)",
  source: "admission_register",
  headers: ["Admission No", "Student Name", "Class"],
  mapping: { "0": "admission_no", "1": "full_name", "2": "class_section" },
  created_by: MEMBERSHIP[MAKER] as string,
  created_at: "2026-09-02T05:45:00Z",
  last_used_at: "2026-09-02T05:45:00Z",
  version: 1,
};

function row(
  row_no: number,
  admission_no: string,
  full_name: string,
  class_section: string,
  extra: Partial<ImportRowOut> = {},
): ImportRowOut {
  return {
    row_no,
    status: "valid",
    action: "create",
    student_id: null,
    admission_no,
    values: { full_name },
    sensitive: [],
    section_id: class_section.endsWith("A") ? SECTION_A : SECTION_B,
    class_section,
    roll_no: null,
    errors: [],
    warnings: [],
    ...extra,
  };
}

function importRows(committed: boolean): ImportRowOut[] {
  const done = (id: string): Partial<ImportRowOut> =>
    committed ? { status: "committed", student_id: id } : {};
  return [
    row(2, "SYN-2026-101", "Synthetica Anjali Devi", "6A", done(IDS.student)),
    row(3, "SYN-2026-102", "Synthetica Kiran Kumar", "6A", {
      warnings: [
        {
          field: "dob",
          code: "ambiguous_date",
          message_key: "imports.issues.ambiguous_date",
          ref: null,
        },
      ],
      ...done("0192f3a4-0000-7000-8000-00000000e502"),
    }),
    row(4, "SYN-2026-103", "Synthetica Meena Kumari", "6B", {
      status: committed ? "skipped" : "error",
      action: null,
      errors: [
        {
          field: "dob",
          code: "invalid_date",
          message_key: "imports.issues.invalid_date",
          ref: null,
        },
      ],
    }),
  ];
}

/* ------------------------------------------------------------------ findings */

function finding(
  id: string,
  rule_id: string,
  attribute_key: string,
  severity: FindingOut["severity"],
  values: FindingOut["values"],
): FindingOut {
  return {
    id,
    student: {
      id: IDS.student,
      display_name: "Synthetica Ravi Kumar",
      admission_no: "SYN-2026-014",
    },
    related_student_id: null,
    rule_id,
    rule_version: 1,
    profile_key: null,
    attribute_key,
    sources: values.map((value) => value.source),
    match_class: null,
    severity,
    blocker: severity === "blocker",
    status: "open",
    explanation: EXPLAIN[rule_id] as Bilingual,
    match_explanation: null,
    routes: rule_id === "DQ-004" ? [ROUTE_CR] : [ROUTE_UIDAI, ROUTE_CR],
    values,
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
    first_seen_at: "2026-09-20T05:00:00Z",
    last_seen_at: "2026-09-26T05:00:00Z",
    first_seen_run_id: IDS.run,
    last_seen_run_id: IDS.run,
    version: 1,
  };
}

function value(
  n: number,
  attribute_key: string,
  source: string,
  shown: string | null,
  masked: string | null = null,
): FindingOut["values"][number] {
  return {
    attribute_key,
    source,
    value_id: `0192f3a4-0000-7000-8000-0000000f3${String(n).padStart(3, "0")}`,
    masked: masked ?? shown,
    value: shown,
    sensitive: shown === null,
  };
}

function initialFindings(): FindingOut[] {
  return [
    finding(IDS.findingDob, "DQ-002", "dob", "blocker", [
      value(1, "dob", "admission_register", "2014-06-12"),
      // Aadhaar-as-printed values are restricted (C3): the API sends them masked only.
      value(2, "aadhaar_dob_as_printed", "aadhaar_as_printed", null, "••/••/2014"),
    ]),
    finding(IDS.findingFather, "DQ-004", "father_name", "medium", [
      value(3, "father_name", "admission_register", "Synthetica Venkat Rao"),
      value(4, "father_name", "parent_form", "Synthetica Venkata Rao"),
    ]),
    finding(IDS.findingGender, "DQ-003", "gender", "high", [
      value(5, "gender", "admission_register", "male"),
      value(6, "gender", "udise_plus", "female"),
    ]),
  ];
}

/* ------------------------------------------------------------------ state */

interface JourneyState {
  batch: ImportOut | null;
  findings: FindingOut[];
  uploads: Map<string, "import_file" | "evidence">;
  changeRequest: ChangeRequestOut | null;
  exportJob: ExportOut | null;
  exportReads: number;
}

let state: JourneyState = fresh();

function fresh(): JourneyState {
  return {
    batch: null,
    findings: initialFindings(),
    uploads: new Map(),
    changeRequest: null,
    exportJob: null,
    exportReads: 0,
  };
}

export function resetJourney(): void {
  state = fresh();
}

const page = <T>(data: T[]) => ({ data, next_cursor: null });

function withRequestFlags(request: ChangeRequestOut, subject: string): ChangeRequestOut {
  const pending = request.status === "pending";
  const mine = MEMBERSHIP[subject] === request.requested_by;
  return { ...request, can_decide: pending && !mine, can_cancel: pending && mine };
}

function summary(): Schemas["SummaryOut"] {
  const open = state.findings.filter(
    (item) => item.status === "open" || item.status === "reopened",
  );
  const blockers = open.filter((item) => item.blocker);
  return {
    profile_key: null,
    blockers: blockers.length,
    warnings: open.length - blockers.length,
    students_with_blockers: new Set(blockers.map((item) => item.student.id)).size,
    by_severity: {},
    by_rule: [],
    last_run: {
      id: IDS.run,
      trigger: "manual",
      event_type: null,
      status: "completed",
      profile_key: null,
      scope: {},
      stats: {},
      created_at: "2026-09-26T05:00:00Z",
      started_at: "2026-09-26T05:00:00Z",
      finished_at: "2026-09-26T05:01:00Z",
      error_code: null,
    },
  };
}

type Body = Record<string, unknown>;

function updateFinding(id: string, change: Partial<FindingOut>): FindingOut | undefined {
  const index = state.findings.findIndex((item) => item.id === id);
  const current = state.findings[index];
  if (!current) return undefined;
  const next = { ...current, ...change, version: current.version + 1 };
  state.findings[index] = next;
  return next;
}

function exportFile(url: URL): [number, unknown] {
  const format = url.searchParams.get("format") === "pdf" ? "pdf" : "xlsx";
  return [
    200,
    {
      url: `${url.origin}${FILES_PREFIX}precheck-cisce-registration-2026.${format}`,
      expires_at: iso(5 * 60_000),
      format,
      content_type:
        format === "pdf"
          ? "application/pdf"
          : "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      filename: `precheck-cisce-registration-2026.${format}`,
    },
  ];
}

/**
 * The journey's answer to a request, or undefined when this module does not own the path (the
 * stand-in then answers as before). `/api/v1/me` is answered only for the journey subjects.
 */
export function journeyAnswer(
  method: string,
  url: URL,
  subject: string,
  body: Body,
): [number, unknown] | undefined {
  const path = url.pathname;
  const get = method === "GET";
  const post = method === "POST";

  if (path === "/api/v1/me" && (subject === MAKER || subject === CHECKER))
    return [200, me(subject)];
  if (path === "/api/v1/attributes") return [200, ATTRIBUTES];

  // Documents: presigned upload → register → scanned.
  if (path === "/api/v1/documents/uploads" && post) {
    const purpose = body.purpose === "evidence" ? "evidence" : "import_file";
    const uploadId = `0192f3a4-0000-7000-8000-0000000ab00${purpose === "evidence" ? 2 : 1}`;
    state.uploads.set(uploadId, purpose);
    return [
      201,
      {
        upload_id: uploadId,
        url: STORAGE_PATH,
        fields: {
          key: `tenants/${T1}/uploads/${uploadId}`,
          "Content-Type": String(body.content_type ?? ""),
        },
        expires_at: iso(10 * 60_000),
        max_bytes: purpose === "evidence" ? 25 * 1024 * 1024 : 10 * 1024 * 1024,
        purpose,
        batch_id: null,
        document_id: null,
      },
    ];
  }
  if (path === "/api/v1/documents" && post) {
    const purpose = state.uploads.get(String(body.upload_id)) ?? "import_file";
    const id = purpose === "evidence" ? IDS.evidenceDoc : IDS.importDoc;
    // DocumentOut: the detail without its version list.
    const out: Partial<Schemas["DocumentDetail"]> = documentDetail(
      id,
      purpose,
      String(body.title ?? "file"),
    );
    delete out.versions;
    return [202, out];
  }
  if (path === `/api/v1/documents/${IDS.importDoc}` && get)
    return [200, documentDetail(IDS.importDoc, "import_file", "Class 6 admissions.csv")];
  if (path === `/api/v1/documents/${IDS.evidenceDoc}` && get)
    return [200, documentDetail(IDS.evidenceDoc, "evidence", "Evidence: Date of birth")];

  // Imports.
  if (path === "/api/v1/imports" && get) {
    const rows = state.batch
      ? [
          {
            id: state.batch.id,
            source: state.batch.source,
            status: state.batch.status,
            row_count: state.batch.row_count,
            error_count: state.batch.error_count,
            created_by: state.batch.created_by,
            created_at: state.batch.created_at,
            committed_at: state.batch.committed_at,
            reverted_at: state.batch.reverted_at,
          },
          OLD_BATCH_SUMMARY,
        ]
      : [OLD_BATCH_SUMMARY];
    return [200, page(rows)];
  }
  if (path === "/api/v1/import-templates" && get) return [200, [TEMPLATE]];
  if (path === "/api/v1/imports" && post) {
    state.batch = newBatch();
    return [202, state.batch];
  }
  const importMatch = /^\/api\/v1\/imports\/([0-9a-f-]{36})(\/[a-z]+)?$/.exec(path);
  if (importMatch) {
    const [, id, action = ""] = importMatch;
    const batch = id === IDS.importNew ? state.batch : null;
    if (!batch)
      return [404, { type: "about:blank", title: "Not found", status: 404, code: "not_found" }];
    if (action === "" && get) return [200, batch];
    if (action === "/mapping" && method === "PUT") {
      const columns = Array.isArray(body.columns)
        ? (body.columns as Array<{ index: number; target: string }>)
        : [];
      state.batch = {
        ...batch,
        columns: batch.columns.map((column) => ({
          ...column,
          target: columns.find((item) => item.index === column.index)?.target ?? null,
        })),
        version: batch.version + 1,
        updated_at: iso(),
      };
      return [200, state.batch];
    }
    if (action === "/validate" && post) {
      state.batch = {
        ...batch,
        status: "validated",
        stats: { rows: 3, valid: 2, errors: 1, warnings: 1, create: 2, update: 0 },
        error_count: 1,
        can_commit: true,
        version: batch.version + 1,
        updated_at: iso(),
      };
      return [202, state.batch];
    }
    if (action === "/commit" && post) {
      state.batch = {
        ...batch,
        status: "committed",
        stats: { ...batch.stats, committed: 2, created: 2, updated: 0, skipped: 1 },
        committed_at: iso(),
        revert_deadline: iso(DAY),
        can_commit: false,
        can_revert: true,
        version: batch.version + 1,
        updated_at: iso(),
      };
      return [202, state.batch];
    }
    if (action === "/rows" && get) {
      const all = importRows(batch.status === "committed");
      const status = url.searchParams.get("status");
      const rows = !status
        ? all
        : status === "warning"
          ? all.filter((item) => item.warnings.length > 0)
          : all.filter((item) => item.status === status);
      return [200, page(rows)];
    }
  }

  // Data quality.
  if (path === "/api/v1/dq/rules") return [200, RULES];
  if (path === "/api/v1/dq/profiles") return [200, DQ_PROFILES];
  if (path === "/api/v1/dq/summary") return [200, summary()];
  if (path === "/api/v1/dq/findings" && get) {
    const statuses = url.searchParams.getAll("status");
    const wanted = statuses.length > 0 ? statuses : ["open", "reopened"];
    const studentId = url.searchParams.get("student_id");
    return [
      200,
      page(
        state.findings.filter(
          (item) => wanted.includes(item.status) && (!studentId || item.student.id === studentId),
        ),
      ),
    ];
  }
  const findingMatch = /^\/api\/v1\/dq\/findings\/([0-9a-f-]{36})(\/[a-z]+)?$/.exec(path);
  if (findingMatch) {
    const [, id = "", action = ""] = findingMatch;
    const current = state.findings.find((item) => item.id === id);
    if (!current)
      return [404, { type: "about:blank", title: "Not found", status: 404, code: "not_found" }];
    if (action === "" && get) return [200, current];
    if (action === "/resolve" && post)
      return [
        200,
        updateFinding(id, {
          status: "resolved",
          resolution: body.change_request_id ? "change_request" : "note",
          resolution_note: typeof body.note === "string" ? body.note : null,
          change_request_id:
            typeof body.change_request_id === "string" ? body.change_request_id : null,
          resolved_by: MEMBERSHIP[subject] ?? null,
          resolved_at: iso(),
        }),
      ];
    if (action === "/waive" && post)
      return [
        200,
        updateFinding(id, {
          status: "waived",
          waived_reason: String(body.reason ?? ""),
          waived_by: MEMBERSHIP[subject] ?? null,
          waived_at: iso(),
        }),
      ];
  }

  // Students (the change-request screens read one student).
  if (path === `/api/v1/students/${IDS.student}` && get) {
    const approved = state.changeRequest?.status === "approved";
    return [
      200,
      approved ? student("2014-06-21", []) : student("2014-06-12", ["aadhaar_as_printed"]),
    ];
  }

  // Change requests (maker-checker).
  if (path === "/api/v1/change-requests" && get) {
    const request = state.changeRequest;
    const studentId = url.searchParams.get("student_id");
    const statusFilter = url.searchParams.get("status");
    const rows =
      request &&
      (!studentId || request.student_id === studentId) &&
      (!statusFilter || request.status === statusFilter)
        ? [withRequestFlags(request, subject)]
        : [];
    return [200, page(rows)];
  }
  if (path === "/api/v1/change-requests" && post) {
    state.changeRequest = {
      id: IDS.changeRequest,
      student_id: String(body.student_id),
      attribute_key: String(body.attribute_key),
      attribute_label_en: "Date of birth",
      attribute_label_te: "పుట్టిన తేదీ",
      target_source: String(body.target_source),
      old_value_id: "0192f3a4-0000-7000-8000-0000000f3001",
      old_value: "2014-06-12",
      new_value: String(body.new_value_date ?? body.new_value ?? ""),
      masked: false,
      reason: String(body.reason ?? ""),
      evidence_document_id: String(body.evidence_document_id),
      status: "pending",
      requested_by: MEMBERSHIP[subject] as string,
      requested_at: iso(),
      decided_by: null,
      decided_at: null,
      decision_note: null,
      applied_value_id: null,
      expires_at: iso(30 * DAY),
      version: 1,
      can_decide: false,
      can_cancel: true,
    };
    return [201, withRequestFlags(state.changeRequest, subject)];
  }
  const requestMatch = /^\/api\/v1\/change-requests\/([0-9a-f-]{36})(\/[a-z]+)?$/.exec(path);
  if (requestMatch) {
    const [, id, action = ""] = requestMatch;
    const request = state.changeRequest;
    if (!request || request.id !== id)
      return [404, { type: "about:blank", title: "Not found", status: 404, code: "not_found" }];
    if (action === "" && get) return [200, withRequestFlags(request, subject)];
    if (action === "/approve" && post) {
      state.changeRequest = {
        ...request,
        status: "approved",
        decided_by: MEMBERSHIP[subject] ?? null,
        decided_at: iso(),
        decision_note: typeof body.note === "string" ? body.note : null,
        applied_value_id: "0192f3a4-0000-7000-8000-0000000f3007",
        version: request.version + 1,
      };
      // Approval records the verified value and runs the checks again: the finding clears.
      updateFinding(IDS.findingDob, {
        status: "resolved",
        resolution: "change_request",
        change_request_id: request.id,
        resolved_by: MEMBERSHIP[subject] ?? null,
        resolved_at: iso(),
      });
      return [200, withRequestFlags(state.changeRequest, subject)];
    }
  }

  // Exports (board pre-check).
  if (path === "/api/v1/export-profiles") return [200, EXPORT_PROFILES];
  if (path === "/api/v1/exports" && post) {
    state.exportReads = 0;
    state.exportJob = {
      id: IDS.export,
      kind: "board_precheck",
      profile_key: String(body.profile_key),
      profile_version: 1,
      layout_version: 1,
      formats: (Array.isArray(body.format) ? body.format : ["xlsx"]) as ExportOut["formats"],
      language: body.language === "te" ? "te" : "en",
      scope: {},
      columns: null,
      include_sensitive: false,
      student_count: 214,
      status: "queued",
      error_code: null,
      created_at: iso(),
      started_at: null,
      finished_at: null,
      expires_at: null,
      files: [],
      requested_by: {
        membership_id: MEMBERSHIP[subject] ?? "",
        display_name: "Synthetica Office Clerk",
      },
      own: true,
      can_download: false,
    };
    return [202, state.exportJob];
  }
  if (path === "/api/v1/exports" && get)
    return [200, page(state.exportJob ? [state.exportJob] : [])];
  if (path === `/api/v1/exports/${IDS.export}` && get && state.exportJob) {
    // The first read after creating it still shows it waiting; the worker then finishes it.
    state.exportReads += 1;
    if (state.exportReads > 1 && state.exportJob.status === "queued") {
      state.exportJob = {
        ...state.exportJob,
        status: "ready",
        started_at: iso(-2000),
        finished_at: iso(),
        expires_at: iso(7 * DAY),
        files: state.exportJob.formats.map((format) => ({
          format,
          content_type:
            format === "pdf"
              ? "application/pdf"
              : "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
          size_bytes: format === "pdf" ? 88_064 : 24_576,
        })),
        can_download: true,
      };
    }
    return [200, state.exportJob];
  }
  if (path === `/api/v1/exports/${IDS.export}/download-url` && get) return exportFile(url);

  return undefined;
}
