/**
 * Canned API for the wave F3 screens (e2e/f3.spec.ts): TC requests (FO-09), enrolment
 * statistics (FO-06), scheme verification packs (FO-11), inspection packs and the compliance
 * home card (FC-07 / FO-12, FC-01), safety drills and checks (FC-09), the policy library and
 * "Policies to read" (FC-10), unanswered Ask questions (FA-06), fix-owner letters (FA-08), the
 * fee counter's Family panel (FM-07), the fee structure notice (FM-10), cheques (FM-08), entry
 * completeness (FT-14), follow-up counts (FT-13), the flag's "Next review" (FT-12) and the
 * closure screen's reopening message (FP-02 follow-up).
 *
 * Answered only for the stand-in IdP subject `F3_OFFICE`, so the other journeys and their
 * /me stay as they were. Everything else this subject asks for is answered by the R1 office
 * stand-in (students, readiness), the R2 cashier stand-in (the fee counter) and the shared
 * stand-in (structure, exams), see `isF3Subject` in stand-in.ts.
 *
 * Print pages (the API renders them) are answered by `f3Html` with a small synthetic page that
 * carries a strict CSP like the API's, so the spec checks that the BFF opens them.
 *
 * Stateful where a journey writes; `resetF3()` (POST /__e2e/reset) starts again.
 * Synthetic data only ("Synthetica …" names, SYN-… admission numbers).
 */
import type { components } from "@schoolos/api-client";
import { R2_IDS, R2_STUDENT } from "./r2-api";

type Schemas = components["schemas"];
type Body = Record<string, unknown>;

/** An office admin who sees every wave F3 screen (the office home has the certificates card). */
export const F3_OFFICE = "f3-office";

const T1 = "0192f3a4-0000-7000-8000-000000000001";
const YEAR_ID = "0192f3a4-0000-7000-8000-0000000000a1";
const CLASS_ID = "0192f3a4-0000-7000-8000-0000000000c6";
const SECTION_A = "0192f3a4-0000-7000-8000-000000000500";
const SECTION_B = "0192f3a4-0000-7000-8000-000000000510";
const EXAM_ID = "0192f3a4-0000-7000-8000-00000000ea01";
/** The shared stand-in's flag (stand-in.ts FLAG_ID). */
const FLAG_ID = "0192f3a4-0000-7000-8000-00000000f501";
const USER = "0192f3a4-0000-7000-8000-0000000f4001";
const MEMBERSHIP = "0192f3a4-0000-7000-8000-0000000f4002";
const TEACHER = "0192f3a4-0000-7000-8000-0000000f4003";
const DOC_ID = "0192f3a4-0000-7000-8000-00000000d001";

const id = (n: number) => `0192f3a4-0000-7000-8000-0000000f4${n.toString(16).padStart(3, "0")}`;

export const F3_IDS = {
  tcRequest: id(0x101),
  tcStudent: "0192f3a4-0000-7000-8000-00000000f601",
  gap: id(0x201),
  policy: id(0x301),
  myPolicy: id(0x302),
  pack: id(0x401),
  closure: id(0x501),
  flag: FLAG_ID,
  sibling: id(0x601),
  siblingAccount: id(0x602),
} as const;

export const F3_TEXT = {
  tcStudent: "Synthetica Ravi Sai Kumar",
  gapQuestion: "When does the school reopen after the Sankranti holidays?",
  policy: "Child protection policy",
  sibling: "Synthetica Lakshmi Sai Teja",
  closureReason: "weather",
} as const;

/** The synthetic print pages' own policy: no scripts, hashed styles only (like the API's). */
export const PRINT_CSP =
  "default-src 'none'; style-src 'none'; img-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'";

const PERMISSIONS = [
  "support.ticket.create",
  "student.read_basic",
  "document.read",
  "certificate.read",
  "certificate.issue",
  "register.read",
  "export.portal",
  "attendance.read",
  "kb.ask",
  "kb.verified_answer.manage",
  "dq.findings.read",
  "dq.readiness.read",
  "dq.readiness.manage",
  "compliance.read",
  "compliance.manage",
  "finance.read",
  "fee.collect",
  "fee.reconcile",
  "fee.manage",
  "completeness.read",
  "completeness.remind",
  "insights.read",
  "insights.act",
  "insights.counts_read",
  "message.read",
  "message.closure.send",
];

const page = <T>(data: T[]) => ({ data, next_cursor: null });
const problem = (status: number, code: string): [number, unknown] => [
  status,
  { type: "about:blank", title: code, status, code },
];
const DAY = 24 * 3_600_000;
const isoDate = (ms: number) =>
  new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" }).format(new Date(ms));
const today = () => isoDate(Date.now());
const daysAgo = (n: number) => isoDate(Date.now() - n * DAY);
const daysAhead = (n: number) => isoDate(Date.now() + n * DAY);
const at = (n: number) => new Date(Date.now() - n * DAY).toISOString();

function me(): Schemas["app__identity__schemas__MeOut"] {
  return {
    user_id: USER,
    membership_id: MEMBERSHIP,
    tenant_id: T1,
    tenant_ids: [T1],
    display_name: "Synthetica Principal",
    preferred_language: "en",
    roles: ["office_admin"],
    permissions: PERMISSIONS,
    scopes: [{ type: "school", ref: null }],
    mfa: true,
    tenant_status: "active",
    settings: { idle_timeout_minutes: 15, date_format: "DD/MM/YYYY", languages: ["en", "te"] },
  };
}

/* ------------------------------------------------------------------ TC requests (FO-09) */

function tcRequest(
  n: number,
  student: { id: string; name: string; adm: string },
  status: Schemas["TcRequestOut"]["status"],
  daysOpen: number,
): Schemas["TcRequestOut"] {
  return {
    id: n === 0 ? F3_IDS.tcRequest : id(0x101 + n),
    student_id: student.id,
    student_name: student.name,
    admission_no: student.adm,
    class_label: "X-A",
    status,
    received_on: daysAgo(daysOpen),
    channel: n % 2 === 0 ? "paper" : "in_person",
    leaving_date: null,
    leaving_reason: n === 0 ? "parent_transferred" : null,
    certificate_id: null,
    certificate_status: null,
    certificate_serial: null,
    received_by: MEMBERSHIP,
    received_by_name: "Synthetica Office Admin",
    received_at: at(daysOpen),
    preparing_by: status === "in_preparation" ? MEMBERSHIP : null,
    preparing_by_name: status === "in_preparation" ? "Synthetica Office Admin" : null,
    preparing_at: status === "in_preparation" ? at(daysOpen - 1) : null,
    ready_by: null,
    ready_by_name: null,
    ready_at: null,
    issued_by: null,
    issued_by_name: null,
    issued_at: null,
    cancelled_by: null,
    cancelled_by_name: null,
    cancelled_at: null,
    cancel_reason: null,
    days_open: daysOpen,
    overdue: daysOpen > 7,
    fee_note: {
      account_id: R2_IDS.account,
      balance: "12000.00",
      overdue: "7600.00",
      oldest_due_on: "2026-06-15",
      last_payment_on: null,
    },
    can_prepare: status === "received",
    can_hand_over: false,
    can_cancel: status === "received" || status === "in_preparation",
    version: 1,
  };
}

function seededTcRequests(): Schemas["TcRequestOut"][] {
  return [
    tcRequest(
      0,
      { id: F3_IDS.tcStudent, name: F3_TEXT.tcStudent, adm: "SYN-2026-301" },
      "received",
      9,
    ),
    tcRequest(
      1,
      {
        id: "0192f3a4-0000-7000-8000-00000000f602",
        name: "Synthetica Anusha Devi",
        adm: "SYN-2026-302",
      },
      "in_preparation",
      3,
    ),
  ];
}

/* ------------------------------------------------------------------ enrolment statistics (FO-06) */

function enrolmentStatistics(): Schemas["EnrolmentStatisticsOut"] {
  const row = (label: string, counts: number[]): Schemas["EnrolmentStatsRowOut"] => ({
    class_id: label === "Total" ? null : CLASS_ID,
    label,
    counts,
    total: counts.reduce((a, b) => a + b, 0),
  });
  return {
    year: { id: YEAR_ID, label: "2026-27", starts_on: "2026-06-01", ends_on: "2027-04-30" },
    as_of: today(),
    reference_date: "2026-09-30",
    students: 61,
    verified: false,
    source: ["UDISE+ DCF 2025-26 (unverified)"],
    restricted_hidden: true,
    tables: [
      {
        key: "gender",
        title: "Enrolment by gender",
        restricted: false,
        columns: [
          { key: "boys", label: "Boys" },
          { key: "girls", label: "Girls" },
          { key: "transgender", label: "Transgender" },
        ],
        rows: [row("Class 6", [31, 30, 0])],
        totals: row("Total", [31, 30, 0]),
      },
      {
        key: "age",
        title: "Enrolment by age on the reference date",
        restricted: false,
        columns: [
          { key: "10", label: "10 years" },
          { key: "11", label: "11 years" },
          { key: "12", label: "12 years" },
        ],
        rows: [row("Class 6", [12, 44, 5])],
        totals: row("Total", [12, 44, 5]),
      },
    ],
    gaps: [{ attribute_key: "social_category", label: "Social category", count: 4 }],
  };
}

/* ------------------------------------------------------------------ scheme packs (FO-11) */

const SCHEME: Schemas["SchemeOut"] = {
  key: "post_matric_scholarship",
  label: "Scholarship verification (synthetic)",
  certificate_type: "study",
  purpose_note: "For scholarship verification only.",
  note: "The scheme office decides who is eligible; this list only shows attendance.",
  source: ["Scheme guidelines (unverified)"],
  verified: false,
};

function schemeConfig(): Schemas["SchemePackConfigOut"] {
  return {
    schemes: [SCHEME],
    present_statuses: ["present", "half_day"],
    excluded_statuses: ["holiday"],
    max_certificates: 60,
    note: "SchoolOS does not decide eligibility for any scheme. Check the scheme's own rules.",
    verified: false,
  };
}

function schemeAttendance(url: URL): Schemas["SchemeAttendanceListOut"] {
  const sectionId = url.searchParams.get("section_id") ?? SECTION_A;
  return {
    scheme: SCHEME,
    section_id: sectionId,
    section_label: sectionId === SECTION_B ? "Class 6-B" : "Class 6-A",
    academic_year_label: "2026-27",
    from_date: url.searchParams.get("from_date") ?? daysAgo(30),
    to_date: url.searchParams.get("to_date") ?? today(),
    students: [
      {
        student_id: F3_IDS.tcStudent,
        full_name: F3_TEXT.tcStudent,
        admission_no: "SYN-2026-301",
        roll_no: "1",
        days_counted: 22,
        days_present: 20,
        leave_days: 1,
        percent: 90.9,
      },
      {
        student_id: "0192f3a4-0000-7000-8000-00000000f602",
        full_name: "Synthetica Anusha Devi",
        admission_no: "SYN-2026-302",
        roll_no: "2",
        days_counted: 0,
        days_present: 0,
        leave_days: 0,
        percent: null,
      },
    ],
  };
}

/* ------------------------------------------------------------------ inspection packs (FC-07) */

const PACK_ITEMS: Schemas["InspectionPackItemOut"][] = [
  {
    key: "fire_noc",
    label: "Fire safety certificate (NOC)",
    source: "certificate",
    state: "expiring",
    fix_by: "Renew it with the fire department before it expires.",
    valid_until: daysAhead(20),
    has_copy: true,
  },
  {
    key: "admission_register",
    label: "Admission and withdrawal register",
    source: "register",
    state: "ready",
    fix_by: "Print it from Registers.",
    register_view: "admission_withdrawal",
    included: false,
  },
  {
    key: "building_safety",
    label: "Building safety certificate",
    source: "certificate",
    state: "missing",
    fix_by: "Record the certificate on the school certificates page.",
  },
  {
    key: "staff_police_verification",
    label: "Staff police verification",
    source: "not_tracked",
    state: "not_tracked",
    fix_by: "Keep the paper file ready; SchoolOS does not track this.",
  },
];

const PACK_COUNTS: Schemas["InspectionPackCountsOut"] = {
  ready: 1,
  attention: 1,
  missing: 1,
  left_out: 0,
  not_tracked: 1,
};

function readiness(): Schemas["InspectionReadinessOut"] {
  return {
    packs: [
      {
        key: "deo_inspection",
        label: "District education officer inspection",
        authority: "District education officer",
        note: "What an inspection usually asks to see (synthetic list, not verified).",
        counts: PACK_COUNTS,
        items: PACK_ITEMS,
      },
    ],
    source: "Inspection checklist (unverified)",
    verified: false,
    version: 1,
  };
}

function madePack(body: Body): Schemas["InspectionPackDetailOut"] {
  return {
    id: F3_IDS.pack,
    pack_key: typeof body.pack_key === "string" ? body.pack_key : "deo_inspection",
    pack_label: "District education officer inspection",
    pack_version: 1,
    authority: "District education officer",
    academic_year_id: YEAR_ID,
    academic_year_label: "2026-27",
    visit_on: typeof body.visit_on === "string" ? body.visit_on : null,
    registers: Array.isArray(body.registers) ? (body.registers as string[]) : [],
    counts: PACK_COUNTS,
    generated_at: new Date().toISOString(),
    generated_by_name: "Synthetica Principal",
    verified: false,
    items: PACK_ITEMS,
  };
}

/* ------------------------------------------------------------------ drills and checks (FC-09) */

const LEADER: Schemas["ComplianceOwnerOut"] = {
  membership_id: TEACHER,
  display_name: "Synthetica Teacher",
  active: true,
};

function checkEntry(): Schemas["ComplianceCheckOut"] {
  return {
    id: id(0x701),
    kind: "fire_drill",
    kind_label: "Fire drill",
    label: null,
    done_on: daysAgo(40),
    duration_minutes: 12,
    participants_count: 412,
    led_by: LEADER,
    result: "ok",
    observations: "Synthetic: all classes out in under five minutes.",
    document: null,
    recorded_by_name: "Synthetica Office Admin",
    created_at: at(40),
    status: "recorded",
    version: 1,
  };
}

function checksOverview(state: F3State): Schemas["ChecksOverviewOut"] {
  return {
    today: today(),
    due_soon_days: 30,
    verified: false,
    counts: { ok: 1, due_soon: 1, overdue: 1, never: 1 },
    kinds: [
      {
        kind: "fire_drill",
        label: "Fire drill",
        drill: true,
        every_months: 3,
        applies: true,
        note: "Usually every term (unverified).",
      },
      {
        kind: "extinguisher_check",
        label: "Fire extinguisher check",
        drill: false,
        every_months: 12,
        applies: true,
        note: "Refill dates are on each extinguisher's tag.",
      },
      {
        kind: "cctv_check",
        label: "CCTV working check",
        drill: false,
        every_months: 1,
        applies: true,
        note: "",
      },
      {
        kind: "water_test",
        label: "Drinking water test",
        drill: false,
        every_months: 6,
        applies: true,
        note: "",
      },
    ],
    leaders: [LEADER],
    statuses: [
      {
        kind: "fire_drill",
        kind_label: "Fire drill",
        drill: true,
        every_months: 3,
        last_done_on: daysAgo(40),
        last_entry_id: id(0x701),
        next_due: daysAhead(50),
        days_left: 50,
        state: "ok",
      },
      {
        kind: "extinguisher_check",
        kind_label: "Fire extinguisher check",
        drill: false,
        every_months: 12,
        last_done_on: daysAgo(350),
        last_entry_id: null,
        next_due: daysAhead(15),
        days_left: 15,
        state: "due_soon",
      },
      {
        kind: "cctv_check",
        kind_label: "CCTV working check",
        drill: false,
        every_months: 1,
        last_done_on: daysAgo(45),
        last_entry_id: null,
        next_due: daysAgo(15),
        days_left: -15,
        state: "overdue",
      },
      {
        kind: "water_test",
        kind_label: "Drinking water test",
        drill: false,
        every_months: 6,
        last_done_on: null,
        last_entry_id: null,
        next_due: null,
        days_left: null,
        state: "never",
      },
    ],
    recent: [...state.checks, checkEntry()],
  };
}

/* ------------------------------------------------------------------ policies (FC-10) */

const ROLES: Schemas["PolicyRoleOut"][] = [
  { role: "class_teacher", name: "Class teacher" },
  { role: "office_admin", name: "Office admin" },
];

function policy(): Schemas["CompliancePolicyOut"] {
  return {
    id: F3_IDS.policy,
    title: F3_TEXT.policy,
    status: "active",
    document: { id: DOC_ID, title: "Child protection policy 2026" },
    current_version_id: id(0x311),
    last_reviewed_on: daysAgo(330),
    review_due: daysAhead(35),
    review_state: "due_soon",
    days_left: 35,
    ack_roles: ROLES,
    ack_requested_at: at(20),
    targeted: 3,
    acknowledged: 1,
    created_at: at(400),
    updated_at: at(20),
    version: 2,
  };
}

function policies(): Schemas["PoliciesOut"] {
  return {
    today: today(),
    review_months: 12,
    verified: false,
    roles: ROLES,
    counts: { due_soon: 1, overdue: 0, pending_acks: 2 },
    items: [policy()],
  };
}

function policyDetail(): Schemas["PolicyDetailOut"] {
  return {
    ...policy(),
    members: [
      {
        membership_id: TEACHER,
        display_name: "Synthetica Teacher",
        roles: ["class_teacher"],
        acknowledged_at: at(10),
        acknowledged_earlier: false,
      },
      {
        membership_id: id(0x321),
        display_name: "Synthetica Office Admin",
        roles: ["office_admin"],
        acknowledged_at: null,
        acknowledged_earlier: true,
      },
      {
        membership_id: id(0x322),
        display_name: "Synthetica Venkata Naga Sai Lakshmi Prasanna Kumari",
        roles: ["class_teacher"],
        acknowledged_at: null,
        acknowledged_earlier: false,
      },
    ],
  };
}

function myPolicies(state: F3State): Schemas["MyPoliciesOut"] {
  const mine: Schemas["MyPolicyOut"] = {
    id: F3_IDS.myPolicy,
    title: F3_TEXT.policy,
    current_version_id: id(0x311),
    acknowledged_at: state.acknowledgedAt,
    needs_ack: state.acknowledgedAt === null,
  };
  return { items: [mine], pending: state.acknowledgedAt === null ? 1 : 0 };
}

/* ------------------------------------------------------------------ unanswered questions (FA-06) */

function gap(state: F3State): Schemas["KnowledgeGapOut"] {
  const closed = state.gapStatus !== "open";
  return {
    id: F3_IDS.gap,
    question: F3_TEXT.gapQuestion,
    language: "en",
    status: state.gapStatus,
    occurrences: 6,
    not_found_count: 4,
    not_helpful_count: 1,
    unchecked_count: 1,
    first_seen_at: at(12),
    last_seen_at: at(1),
    resolution: null,
    document_id: null,
    verified_answer_id: null,
    closed_at: closed ? new Date().toISOString() : null,
    closed_by: closed ? MEMBERSHIP : null,
    closed_by_name: closed ? "Synthetica Principal" : null,
    version: closed ? 2 : 1,
  };
}

function gapSummary(state: F3State): Schemas["KnowledgeGapSummaryOut"] {
  return {
    open_gaps: state.gapStatus === "open" ? 1 : 0,
    not_found: 4,
    not_helpful: 1,
    unchecked: 1,
    retention_days: 180,
    months: [
      {
        month: daysAgo(0).slice(0, 7),
        closed_with_answer: 2,
        closed_with_document: 1,
        dismissed: state.gapStatus === "dismissed" ? 1 : 0,
      },
    ],
  };
}

/* ------------------------------------------------------------------ fix-owner letters (FA-08) */

function fixLetters(profileKey: string): Schemas["FixLettersOut"] {
  const student = (n: number, name: string, section: string): Schemas["FixLetterStudentOut"] => ({
    student: {
      id: n === 0 ? F3_IDS.tcStudent : id(0x801 + n),
      display_name: name,
      admission_no: `SYN-2026-3${String(n + 1).padStart(2, "0")}`,
    },
    class_section: section === SECTION_A ? "X-A" : "X-B",
    section_id: section,
    fields: ["Date of birth", "Father's name"],
    last_sent_on: n === 1 ? daysAgo(5) : null,
  });
  return {
    profile_key: profileKey,
    verified: false,
    letters: [
      {
        key: "parent_aadhaar",
        title: "Please update your child's Aadhaar details",
        label: "Letters to parents (Aadhaar as printed)",
        audience: "parent",
        fix_owner: "parent_aadhaar",
        followup_channel: "letter",
        followup_event: "slip_given",
        print_permission: "dq.readiness.manage",
        step_up: false,
        lines_count: 4,
        students_count: 2,
        recipients: [],
        verified: false,
        version: 1,
        students: [
          student(0, F3_TEXT.tcStudent, SECTION_A),
          student(1, "Synthetica Kiran Teja", SECTION_B),
        ],
      },
      {
        key: "board_registration",
        title: "Request to correct board registration details",
        label: "Letter to the board office",
        audience: "office",
        fix_owner: "school_udise",
        followup_channel: "letter",
        followup_event: "request_sent",
        print_permission: "export.portal",
        step_up: true,
        lines_count: 1,
        students_count: 1,
        recipients: [{ key: "deo", label: "District educational officer" }],
        verified: false,
        version: 1,
        students: [student(2, "Synthetica Anusha Devi", SECTION_A)],
      },
    ],
  };
}

/* ------------------------------------------------------------------ fees (FM-07, FM-08, FM-10) */

/** The counter student (R2) and a sibling linked through the same mother (FR-FEE-101). */
function family(): Schemas["FamilyCounterOut"] {
  const due = (account: string, balance: string, overdue: string): Schemas["YearDue"] => ({
    academic_year_id: YEAR_ID,
    academic_year_label: "2026-27",
    account_id: account,
    balance,
    collectable: balance,
    earlier_year: false,
    oldest_due_on: "2026-06-15",
    overdue,
  });
  return {
    student_id: R2_IDS.student,
    as_of: today(),
    members: [
      {
        student_id: R2_IDS.student,
        display_name: R2_STUDENT.name,
        admission_no: R2_STUDENT.admissionNo,
        class_section: R2_STUDENT.classSection,
        status: "active",
        selected: true,
        linked_by: ["Synthetica Lakshmi (mother)"],
        accounts: [due(R2_IDS.account, "19600.00", "7600.00")],
        collectable: "19600.00",
      },
      {
        student_id: F3_IDS.sibling,
        display_name: F3_TEXT.sibling,
        admission_no: "SYN-2026-202",
        class_section: "IV-B",
        status: "active",
        selected: false,
        linked_by: ["Synthetica Lakshmi (mother)"],
        accounts: [due(F3_IDS.siblingAccount, "9000.00", "0.00")],
        collectable: "9000.00",
      },
    ],
    total_collectable: "28600.00",
    max_accounts: 8,
    modes: ["cash", "cheque", "bank_transfer"],
  };
}

function structureNotice(): Schemas["FeeStructureNoticeOut"] {
  return {
    academic_year_id: YEAR_ID,
    academic_year_label: "2026-27",
    title: "Fee structure 2026-27",
    disclaimer: "Synthetic notice.",
    classes_without_plan: [],
    classes: [
      {
        class_id: CLASS_ID,
        class_label: "Class 6",
        plan_id: id(0x901),
        plan_status: "active",
        annual_total: "24000.00",
        late_fee_text: "₹100 after the due date",
        heads: [
          { head_id: id(0x911), code: "TUI", name: "Tuition fee", annual_amount: "24000.00" },
        ],
        instalments: [
          { instalment_no: 1, label: "Term 1", due_on: "2026-06-15", amount: "12000.00" },
          { instalment_no: 2, label: "Term 2", due_on: "2026-11-15", amount: "12000.00" },
        ],
      },
    ],
  };
}

function structureChanges(url: URL): Schemas["FeeStructureChangesOut"] {
  const warn = url.searchParams.get("warn_percent") ?? "10";
  return {
    academic_year_id: YEAR_ID,
    academic_year_label: "2026-27",
    previous_year_id: "0192f3a4-0000-7000-8000-0000000000a0",
    previous_year_label: "2025-26",
    warn_percent: warn,
    warn_percent_verified: false,
    classes_above_threshold: 1,
    classes: [
      {
        class_id: CLASS_ID,
        class_label: "Class 6",
        plan_status: "active",
        has_previous: true,
        previous_total: "21000.00",
        current_total: "24000.00",
        difference: "3000.00",
        percent: "14.3",
        above_threshold: true,
        heads: [
          {
            head_id: id(0x911),
            code: "TUI",
            name: "Tuition fee",
            change: "changed",
            previous_amount: "20000.00",
            current_amount: "24000.00",
            difference: "4000.00",
            percent: "20.0",
            above_threshold: true,
          },
          {
            head_id: id(0x912),
            code: "LAB",
            name: "Laboratory fee",
            change: "removed",
            previous_amount: "1000.00",
            current_amount: null,
            difference: "-1000.00",
            percent: null,
            above_threshold: false,
          },
        ],
      },
    ],
  };
}

const HEADS: Schemas["HeadOut"][] = [
  {
    id: id(0x911),
    code: "TUI",
    name: "Tuition fee",
    kind: "tuition",
    active: true,
    gst_exempt: true,
    tax_tuition: true,
    sort_order: 1,
    version: 1,
  },
];

function cheque(n: number, status: Schemas["ChequeOut"]["cheque_status"]): Schemas["ChequeOut"] {
  return {
    receipt_id: id(0xa01 + n),
    receipt_number: `SSHS/2026-27/0000${50 + n}`,
    receipt_date: daysAgo(n + 1),
    receipt_status: "issued",
    student_name: n === 0 ? R2_STUDENT.name : "Synthetica Kiran Teja",
    class_section: n === 0 ? R2_STUDENT.classSection : "IX-B",
    amount: n === 0 ? "7600.00" : "12000.00",
    cheque_number: `00${4512 + n}`,
    bank_name: "Synthetic Co-operative Bank",
    cheque_date: daysAgo(n + 1),
    cheque_status: status,
    deposited_on: status === "received" ? null : daysAgo(n),
    deposit_overdue: n === 2,
    cleared_on: status === "cleared" ? daysAgo(0) : null,
    cleared_from_statement: status === "cleared",
    returned_on: null,
    cancellation_id: null,
  };
}

function cheques(state: F3State, url: URL): Schemas["ChequeListOut"] {
  const all = [
    cheque(0, state.deposited ? "deposited" : "received"),
    cheque(1, "deposited"),
    cheque(2, "received"),
  ];
  const status = url.searchParams.get("status");
  const items = status ? all.filter((c) => c.cheque_status === status) : all;
  const counts: Record<string, number> = { received: 0, deposited: 0, cleared: 0, returned: 0 };
  for (const c of all) counts[c.cheque_status] = (counts[c.cheque_status] ?? 0) + 1;
  return {
    as_of: today(),
    date_from: daysAgo(30),
    date_to: today(),
    deposit_due_days: 2,
    truncated: false,
    counts,
    items,
  };
}

/* ------------------------------------------------------------------ completeness (FT-13, FT-14) */

const TEACHER_REF: Schemas["CompletenessTeacherRef"] = {
  membership_id: TEACHER,
  display_name: "Synthetica Teacher",
};

function attendanceCompleteness(url: URL): Schemas["AttendanceCompletenessOut"] {
  return {
    academic_year_id: YEAR_ID,
    on_date: url.searchParams.get("date") ?? today(),
    sections: 2,
    complete: 1,
    partial: 0,
    not_taken: 1,
    day_off: 0,
    no_students: 0,
    reminders_enabled: true,
    rows: [
      {
        class_id: CLASS_ID,
        section_id: SECTION_A,
        label: "Class 6-A",
        class_teacher: TEACHER_REF,
        students: 31,
        marked: 31,
        status: "complete",
        calendar: "working_day",
      },
      {
        class_id: CLASS_ID,
        section_id: SECTION_B,
        label: "Class 6-B",
        class_teacher: TEACHER_REF,
        students: 30,
        marked: 0,
        status: "not_taken",
        calendar: "working_day",
      },
    ],
  };
}

function marksCompleteness(): Schemas["MarksCompletenessOut"] {
  return {
    exam_id: EXAM_ID,
    exam_name: "Formative assessment 1",
    held_on: "2026-08-10",
    locked: false,
    sections: 2,
    complete: 0,
    partial: 1,
    not_started: 1,
    no_students: 0,
    reminders_enabled: true,
    subjects: ["English", "Mathematics"],
    rows: [
      {
        class_id: CLASS_ID,
        section_id: SECTION_A,
        label: "Class 6-A",
        class_teacher: TEACHER_REF,
        students: 31,
        status: "partial",
        subjects: [
          { subject: "English", entered: 31, status: "complete" },
          { subject: "Mathematics", entered: 12, status: "partial" },
        ],
      },
      {
        class_id: CLASS_ID,
        section_id: SECTION_B,
        label: "Class 6-B",
        class_teacher: null,
        students: 30,
        status: "not_started",
        subjects: [
          { subject: "English", entered: 0, status: "missing" },
          { subject: "Mathematics", entered: 0, status: "missing" },
        ],
      },
    ],
  };
}

function followUpCounts(): Schemas["InsightCountsOut"] {
  return {
    since: daysAgo(30),
    raised: 14,
    actioned_on_time: 9,
    actioned_late: 3,
    not_actioned: 2,
    overdue: 2,
    review_due: 1,
    open: 5,
    closed: 9,
    outcomes: { improved: 6, resolved_other: 1, transferred: 1, no_longer_relevant: 1 },
  };
}

/* ------------------------------------------------------------------ flag "Next review" (FT-12) */

function flagDetail(state: F3State): Schemas["InsightFlagDetail"] {
  const staff = { membership_id: TEACHER, display_name: "Synthetica Teacher" };
  return {
    id: FLAG_ID,
    student: {
      id: "0192f3a4-0000-7000-8000-00000000e501",
      full_name: "Synthetica Rao",
      admission_no: "SYN-0001",
      section_label: "VI-A",
    },
    indicator: "attendance",
    rule: "attendance_streak",
    evidence: { days: 3, from: daysAgo(5), to: daysAgo(3), threshold: 3 },
    status: "in_progress",
    owner: staff,
    raised_on: daysAgo(5),
    due_on: daysAhead(2),
    overdue: false,
    review_on: state.reviewOn,
    review_due: false,
    actioned: true,
    first_action_at: at(4),
    closed_at: null,
    close_reason: null,
    raised_by: null,
    version: 2 + state.actions.length,
    actions: [
      {
        id: id(0xb01),
        kind: "raised",
        acted_on: daysAgo(5),
        note: null,
        by: null,
        created_at: at(5),
      },
      ...state.actions,
    ],
  };
}

/* ------------------------------------------------------------------ closures (FP-02 follow-up) */

function closures(state: F3State): Schemas["ClosurePage"] {
  const closure: Schemas["ClosureOut"] = {
    id: F3_IDS.closure,
    closed_from: daysAgo(2),
    closed_to: daysAgo(1),
    reason: F3_TEXT.closureReason,
    notice_text: "Synthetic: the collector's order closes schools for heavy rain.",
    whole_school: true,
    section_ids: [],
    recipients: 58,
    queued: 58,
    counts: { queued: 58 },
    languages: { en: 58 },
    skipped: { no_optin: 3 },
    estimated_cost_inr: "52.20",
    sent_at: at(3),
    sent_by: USER,
    reopening: state.reopening,
  };
  return {
    items: [closure],
    reasons: ["weather", "government_order", "local_emergency", "other"],
    uses: 1,
    uses_window_days: 30,
    window_start: "07:00:00",
    window_end: "21:00:00",
  };
}

function reopeningPreview(body: Body): Schemas["ClosureReopeningPreviewOut"] {
  const reopensOn = typeof body.reopens_on === "string" ? body.reopens_on : today();
  return {
    closure_id: F3_IDS.closure,
    reopens_on: reopensOn,
    message_en: `Sri Saraswati High School reopens on ${reopensOn}. Classes run as usual.`,
    recipients: 58,
    guardians: 55,
    language_fallbacks: 0,
    languages: { en: 58 },
    skipped: { no_optin: 3 },
    estimated_cost_inr: "52.20",
    sends_at: null,
    window_start: "07:00:00",
    window_end: "21:00:00",
    blocked_reason: null,
    preview_token: "e2e-reopening-preview-token",
  };
}

/* ------------------------------------------------------------------ state */

interface F3State {
  tcRequests: Schemas["TcRequestOut"][];
  gapStatus: Schemas["KnowledgeGapOut"]["status"];
  acknowledgedAt: string | null;
  deposited: boolean;
  checks: Schemas["ComplianceCheckOut"][];
  packs: Schemas["InspectionPackOut"][];
  reviewOn: string | null;
  actions: Schemas["InsightFlagDetail"]["actions"];
  reopening: Schemas["ClosureReopeningOut"] | null;
  reminders: Set<string>;
}

function fresh(): F3State {
  return {
    tcRequests: seededTcRequests(),
    gapStatus: "open",
    acknowledgedAt: null,
    deposited: false,
    checks: [],
    packs: [],
    reviewOn: null,
    actions: [],
    reopening: null,
    reminders: new Set(),
  };
}

let state: F3State = fresh();

export function resetF3(): void {
  state = fresh();
}

export function isF3Subject(subject: string): boolean {
  return subject === F3_OFFICE;
}

/* ------------------------------------------------------------------ print pages */

function printPage(title: string): string {
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><title>${title}</title></head><body><main><h1>${title}</h1><p>Synthetic print page from the e2e stand-in.</p></main></body></html>`;
}

/**
 * The API's HTML print pages (opened through the BFF in a new tab), or undefined. A `check`
 * query is the step-up check the screens send first; it answers 204 in `f3Answer`.
 */
export function f3Html(method: string, url: URL, subject: string): string | undefined {
  if (!isF3Subject(subject) || method !== "GET" || url.searchParams.get("check")) return undefined;
  const path = url.pathname;
  if (path === "/api/v1/reports/enrolment-statistics/print")
    return printPage("Enrolment statistics (synthetic)");
  if (path === "/api/v1/scheme-packs/attendance/print")
    return printPage("Scheme attendance list (synthetic)");
  if (path === `/api/v1/compliance/inspection-packs/${F3_IDS.pack}/index`)
    return printPage("Inspection pack index (synthetic)");
  if (path === "/api/v1/fees/cheques/deposit-slip/print")
    return printPage("Cheque deposit list (synthetic)");
  if (path === "/api/v1/fees/structure-notice/print")
    return printPage("Fee structure notice (synthetic)");
  if (/^\/api\/v1\/dq\/readiness\/[a-z0-9-]+\/parent-letters$/.test(path))
    return printPage("Letters to parents (synthetic)");
  return undefined;
}

/* ------------------------------------------------------------------ answers */

/**
 * The F3 answer to a request, or undefined when this module does not own the path (the
 * stand-in then asks the R2 cashier, R1 office and shared answers, in that order).
 */
export function f3Answer(
  method: string,
  url: URL,
  subject: string,
  body: Body,
): [number, unknown] | undefined {
  if (!isF3Subject(subject)) return undefined;
  const path = url.pathname;
  const get = method === "GET";
  const post = method === "POST";

  if (path === "/api/v1/me" && get) return [200, me()];

  // FO-09: TC requests.
  if (path === "/api/v1/tc-requests" && get) {
    const status = url.searchParams.get("status");
    const overdue = url.searchParams.get("overdue") === "true";
    const rows = state.tcRequests.filter(
      (r) => (!status || r.status === status) && (!overdue || r.overdue),
    );
    return [
      200,
      {
        ...page(rows),
        overdue_count: state.tcRequests.filter((r) => r.overdue).length,
        overdue_after_days: 7,
        overdue_verified: false,
      } satisfies Schemas["TcRequestList"],
    ];
  }
  if (path === "/api/v1/tc-requests" && post) {
    const studentId = typeof body.student_id === "string" ? body.student_id : F3_IDS.tcStudent;
    const created: Schemas["TcRequestOut"] = {
      ...tcRequest(
        state.tcRequests.length + 1,
        { id: studentId, name: "Synthetica Anjali Devi", adm: "SYN-2026-305" },
        "received",
        0,
      ),
      received_on: typeof body.received_on === "string" ? body.received_on : today(),
      channel: body.channel === "phone" ? "phone" : "paper",
    };
    state.tcRequests = [created, ...state.tcRequests];
    return [201, created];
  }

  // FO-06 / FO-11: enrolment statistics and scheme packs; print checks answer 204.
  if (
    get &&
    url.searchParams.get("check") &&
    /^\/api\/v1\/(reports\/enrolment-statistics|scheme-packs\/attendance)\/print$/.test(path)
  )
    return [204, null];
  if (path === "/api/v1/reports/enrolment-statistics" && get) return [200, enrolmentStatistics()];
  if (path === "/api/v1/scheme-packs" && get) return [200, schemeConfig()];
  if (path === "/api/v1/scheme-packs/attendance" && get) return [200, schemeAttendance(url)];

  // FC-01 / FC-07: the home card and the inspection packs.
  if (path === "/api/v1/compliance/summary" && get)
    return [200, { expired: 1, expiring: 2, missing: 3 } satisfies Schemas["RegisterSummaryOut"]];
  if (path === "/api/v1/compliance/inspection-readiness" && get) return [200, readiness()];
  if (path === "/api/v1/compliance/inspection-packs" && get) return [200, state.packs];
  if (path === "/api/v1/compliance/inspection-packs" && post) {
    const made = madePack(body);
    const logged: Schemas["InspectionPackOut"] = { ...made };
    delete (logged as Partial<Schemas["InspectionPackDetailOut"]>).items;
    state.packs = [logged, ...state.packs.filter((p) => p.id !== made.id)];
    return [201, made];
  }

  // FC-09: drills and checks.
  if (path === "/api/v1/compliance/checks" && get) return [200, checksOverview(state)];
  if (path === "/api/v1/compliance/checks/entries" && post) {
    const entry: Schemas["ComplianceCheckOut"] = {
      ...checkEntry(),
      id: id(0x702 + state.checks.length),
      kind: typeof body.kind === "string" ? body.kind : "fire_drill",
      kind_label: body.kind === "cctv_check" ? "CCTV working check" : "Fire drill",
      done_on: typeof body.done_on === "string" ? body.done_on : today(),
      observations: typeof body.observations === "string" ? body.observations : null,
      created_at: new Date().toISOString(),
    };
    state.checks = [entry, ...state.checks];
    return [201, entry];
  }

  // FC-10: the policy library and "Policies to read".
  if (path === "/api/v1/compliance/policies" && get) return [200, policies()];
  if (path === `/api/v1/compliance/policies/${F3_IDS.policy}` && get) return [200, policyDetail()];
  if (path === `/api/v1/compliance/policies/${F3_IDS.policy}:request-acknowledgement` && post)
    return [200, { notified: 2 } satisfies Schemas["PolicyAckRequestOut"]];
  if (path === "/api/v1/compliance/my-policies" && get) return [200, myPolicies(state)];
  if (path === `/api/v1/compliance/my-policies/${F3_IDS.myPolicy}/file` && get)
    return [
      200,
      {
        url: `${url.origin}/e2e-files/child-protection-policy.pdf`,
        document_version_id: id(0x311),
        expires_at: new Date(Date.now() + 300_000).toISOString(),
      } satisfies Schemas["PolicyFileOut"],
    ];
  if (path === `/api/v1/compliance/my-policies/${F3_IDS.myPolicy}:acknowledge` && post) {
    state.acknowledgedAt = new Date().toISOString();
    return [200, myPolicies(state).items[0]];
  }

  // FA-06: unanswered questions.
  if (path === "/api/v1/knowledge/gaps" && get) {
    const status = url.searchParams.get("status") ?? "open";
    return [200, page(state.gapStatus === status ? [gap(state)] : [])];
  }
  if (path === "/api/v1/knowledge/gaps/summary" && get) return [200, gapSummary(state)];
  if (path === `/api/v1/knowledge/gaps/${F3_IDS.gap}/close` && post) {
    state.gapStatus = body.resolution === "dismissed" ? "dismissed" : "closed";
    return [200, gap(state)];
  }

  // FA-08: fix-owner letters.
  const letters = /^\/api\/v1\/dq\/readiness\/([a-z0-9-]+)\/fix-letters$/.exec(path);
  if (letters && get) return [200, fixLetters(letters[1] ?? "bseap-ssc-2027")];
  const sent =
    /^\/api\/v1\/dq\/readiness\/[a-z0-9-]+\/fix-letters\/(parent_aadhaar|board_registration)\/sent$/.exec(
      path,
    );
  if (sent && post) {
    const parent = sent[1] === "parent_aadhaar";
    return [
      200,
      {
        recorded: Array.isArray(body.student_ids) ? body.student_ids.length : 0,
        fix_owner: parent ? "parent_aadhaar" : "school_udise",
        event: parent ? "slip_given" : "request_sent",
        channel: "letter",
      } satisfies Schemas["FixLetterSentOut"],
    ];
  }

  // FM-07, FM-08, FM-10: fees (the rest of the counter is the R2 cashier's).
  if (path === `/api/v1/fees/students/${R2_IDS.student}/family` && get) return [200, family()];
  if (path === "/api/v1/fees/heads" && get) return [200, HEADS];
  if (path === "/api/v1/fees/plans" && get) return [200, []];
  if (path === "/api/v1/fees/structure-notice" && get) return [200, structureNotice()];
  if (path === "/api/v1/fees/structure-changes" && get) return [200, structureChanges(url)];
  if (path === "/api/v1/fees/cheques" && get) return [200, cheques(state, url)];
  if (path === "/api/v1/fees/cheques/deposited" && post) {
    const ids = Array.isArray(body.receipt_ids) ? body.receipt_ids : [];
    state.deposited = state.deposited || ids.includes(id(0xa01));
    return [200, { deposited: ids.length, already_deposited: 0, skipped: [] }];
  }

  // FT-13, FT-14: completeness and follow-up counts.
  if (path === "/api/v1/completeness/attendance" && get) return [200, attendanceCompleteness(url)];
  if (path === `/api/v1/completeness/exams/${EXAM_ID}` && get) return [200, marksCompleteness()];
  if (path === "/api/v1/completeness/reminders" && post) {
    const key = `${String(body.kind)}:${String(body.section_id)}`;
    const first = !state.reminders.has(key);
    state.reminders.add(key);
    return [200, { sent: first ? 1 : 0 }];
  }
  if (path === "/api/v1/insights/counts" && get) return [200, followUpCounts()];

  // FT-12: the flag's next review.
  if (path === `/api/v1/insights/flags/${FLAG_ID}` && get) return [200, flagDetail(state)];
  if (path === `/api/v1/insights/flags/${FLAG_ID}/actions` && post) {
    state.reviewOn = typeof body.review_on === "string" ? body.review_on : state.reviewOn;
    state.actions = [
      ...state.actions,
      {
        id: id(0xb02 + state.actions.length),
        kind: typeof body.kind === "string" ? (body.kind as "called_parent") : "called_parent",
        acted_on: typeof body.acted_on === "string" ? body.acted_on : today(),
        note: typeof body.note === "string" ? body.note : null,
        by: { membership_id: MEMBERSHIP, display_name: "Synthetica Principal" },
        created_at: new Date().toISOString(),
      },
    ];
    return [200, flagDetail(state)];
  }

  // FP-02 follow-up: the closure list and the reopening message preview.
  if (path === "/api/v1/messaging/closures" && get) return [200, closures(state)];
  if (path === `/api/v1/messaging/closures/${F3_IDS.closure}/reopening/preview` && post)
    return [200, reopeningPreview(body)];

  // The TC request log's student picker (R1's student, synthetic).
  if (path === "/api/v1/students/search" && post) {
    const query = typeof body.query === "string" ? body.query.toLowerCase() : "";
    const rows: Schemas["StudentSummary"][] = "synthetica anjali devi".includes(query)
      ? [
          {
            id: id(0xc01),
            display_name: "Synthetica Anjali Devi",
            admission_no: "SYN-2026-305",
            class_section: "X-A",
            section_id: SECTION_A,
            status: "active",
            match: { field: "name", score: 1 },
          },
        ]
      : [];
    return [200, page(rows)];
  }
  if (path.startsWith("/api/v1/compliance/") && get) return problem(404, "not_found");
  return undefined;
}
