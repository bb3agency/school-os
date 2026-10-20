/**
 * Canned API for the R2 journeys (e2e/fee-counter.spec.ts, e2e/parent-pages.spec.ts; roadmap
 * R2-3I): the fee counter (US-2102; FR-FEE-010, FR-FEE-022) for the stand-in IdP subject
 * `cashier`, and the public parent-link routes (`/api/v1/public/links/*`; ADR-0046, FR-PUB-010,
 * FR-PAY-005, FR-LANG-003) for three fixed synthetic link tokens.
 *
 * The staff routes are answered only for `cashier`, so the other journeys and their /me stay as
 * they were. The public routes are keyed by the `X-SOS-Public-Token` header the public BFF client
 * sends (never a user token). Stateful only where a journey needs it (a saved receipt, the
 * parent's message language); `resetR2()` (POST /__e2e/reset on the stand-in API) starts again.
 *
 * Synthetic data only: "Synthetica …" names, SYN-… admission numbers, a made-up school phone
 * and UPI id. Shapes follow apps/api/openapi.json through the generated types.
 */
import type { components } from "@schoolos/api-client";

type Schemas = components["schemas"];
type Body = Record<string, unknown>;

/** Stand-in IdP subject of the fee counter: a cashier with `fee.collect`. */
export const CASHIER = "cashier";

const T1 = "0192f3a4-0000-7000-8000-000000000001";
const YEAR_ID = "0192f3a4-0000-7000-8000-0000000000a1";
const PLAN_ID = "0192f3a4-0000-7000-8000-00000000fa01";
const TUITION = "0192f3a4-0000-7000-8000-00000000fb01";
const ME_USER = "0192f3a4-0000-7000-8000-0000000000d7";
const ME_MEMBERSHIP = "0192f3a4-0000-7000-8000-0000000000e7";

export const R2_IDS = {
  /** The account the counter journey collects for. */
  account: "0192f3a4-0000-7000-8000-00000000fc01",
  student: "0192f3a4-0000-7000-8000-00000000fd01",
  /** A second match for the same search, so the arrow keys have somewhere to go. */
  otherAccount: "0192f3a4-0000-7000-8000-00000000fc02",
  otherStudent: "0192f3a4-0000-7000-8000-00000000fd02",
  receipt: "0192f3a4-0000-7000-8000-00000000fe01",
} as const;

export const R2_STUDENT = {
  name: "Synthetica Lakshmi Prasanna",
  admissionNo: "SYN-2026-201",
  classSection: "VII-A",
  other: "Synthetica Lakshman Rao",
} as const;

export const R2_RECEIPT_NUMBER = "SSHS/2026-27/000042";

/**
 * Parent link tokens: 43 base64url characters like a real one (ADR-0046 decision 3), made up
 * for the stand-in only. Not secrets: they open nothing outside this canned API.
 */
const fixedToken = (tag: string) => `e2e-${tag}-${"x".repeat(43)}`.slice(0, 43);
export const PARENT_TOKENS = {
  notice: fixedToken("parent-notice"),
  dues: fixedToken("parent-dues"),
  optin: fixedToken("parent-optin"),
} as const;

export const PARENT_SCHOOL = "Sri Saraswati High School";
export const PARENT_NOTICE = {
  title: "Sports day on Saturday",
  firstParagraph: "Synthetic notice: children come in house colours at 8:30 am.",
} as const;
/** Razorpay's hosted page the stand-in hands out (https on rzp.io, as `isPayUrl` requires). */
export const PAY_REDIRECT = "https://rzp.io/i/e2eSyntheticLink";

const page = <T>(data: T[]) => ({ data, next_cursor: null });
const notFound: [number, unknown] = [
  404,
  { type: "about:blank", title: "Not found", status: 404, code: "not_found" },
];
const iso = (offsetMs = 0) => new Date(Date.now() + offsetMs).toISOString();
/** Today's date in IST (receipts are always dated today, FR-FEE-022). */
const todayIst = () =>
  new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" }).format(new Date());

/* ------------------------------------------------------------------ fee counter */

const PERMISSIONS = ["support.ticket.create", "student.read_basic", "finance.read", "fee.collect"];

function me(): Schemas["app__identity__schemas__MeOut"] {
  return {
    user_id: ME_USER,
    membership_id: ME_MEMBERSHIP,
    tenant_id: T1,
    tenant_ids: [T1],
    display_name: "Synthetica Cashier",
    preferred_language: "en",
    roles: ["cashier"],
    permissions: PERMISSIONS,
    scopes: [{ type: "school", ref: null }],
    mfa: true,
    tenant_status: "active",
    settings: { idle_timeout_minutes: 15, date_format: "DD/MM/YYYY", languages: ["en", "te"] },
  };
}

function line(
  instalment: number,
  label: string,
  head: { id: string; code: string; name: string } | null,
  amounts: { charged: string; lateFee?: string; paid?: string; due: string },
  dueOn: string,
  overdue: boolean,
): Schemas["DueLine"] {
  return {
    instalment_no: instalment,
    label,
    due_on: dueOn,
    head_id: head?.id ?? null,
    head_code: head?.code ?? null,
    head_name: head?.name ?? null,
    collectable: true,
    charged: amounts.charged,
    concession: "0.00",
    late_fee: amounts.lateFee ?? "0.00",
    adjustment: "0.00",
    paid: amounts.paid ?? "0.00",
    due: amounts.due,
    overdue,
  };
}

const TUITION_HEAD = { id: TUITION, code: "tuition", name: "Tuition fee" };

/** Term 1 tuition part-paid and overdue with a late fee; term 2 not yet due. ₹19,600 due now. */
function freshAccount(): Schemas["AccountOut"] {
  return {
    id: R2_IDS.account,
    student_id: R2_IDS.student,
    display_name: R2_STUDENT.name,
    admission_no: R2_STUDENT.admissionNo,
    class_section: R2_STUDENT.classSection,
    academic_year_id: YEAR_ID,
    plan_id: PLAN_ID,
    plan_name: "Class VII day scholar",
    category_code: null,
    status: "open",
    lines: [
      line(
        1,
        "Term 1",
        TUITION_HEAD,
        { charged: "12000.00", paid: "4500.00", due: "7500.00" },
        "2026-06-15",
        true,
      ),
      line(
        1,
        "Term 1",
        null,
        { charged: "0.00", lateFee: "100.00", due: "100.00" },
        "2026-06-15",
        true,
      ),
      line(
        2,
        "Term 2",
        TUITION_HEAD,
        { charged: "12000.00", due: "12000.00" },
        "2026-11-15",
        false,
      ),
    ],
    totals: {
      charged: "24000.00",
      concessions: "0.00",
      late_fees: "100.00",
      adjustments: "0.00",
      paid: "4500.00",
      refunded: "0.00",
      balance: "19600.00",
      overdue: "7600.00",
      collectable: "19600.00",
    },
    pending_requests: 0,
    version: 4,
  };
}

const OTHER_ACCOUNT: Schemas["AccountSummary"] = {
  account_id: R2_IDS.otherAccount,
  student_id: R2_IDS.otherStudent,
  display_name: R2_STUDENT.other,
  admission_no: "SYN-2026-202",
  class_section: "VII-B",
  academic_year_id: YEAR_ID,
  balance: "3000.00",
  overdue: "0.00",
  status: "open",
};

function summary(account: Schemas["AccountOut"]): Schemas["AccountSummary"] {
  return {
    account_id: account.id,
    student_id: account.student_id,
    display_name: account.display_name,
    admission_no: account.admission_no,
    class_section: account.class_section,
    academic_year_id: account.academic_year_id,
    balance: account.totals.balance,
    overdue: account.totals.overdue,
    status: account.status,
  };
}

/* ------------------------------------------------------------------ parent links */

function envelopeBase(kind: Schemas["LinkEnvelopeOut"]["kind"]): Schemas["LinkEnvelopeOut"] {
  return {
    kind,
    expires_at: iso(7 * 24 * 3600_000),
    language: "en",
    school_name: PARENT_SCHOOL,
    notice: null,
    receipt: null,
    dues: null,
    optin: null,
  };
}

const NOTICE: Schemas["ParentNoticeOut"] = {
  title: PARENT_NOTICE.title,
  body: `${PARENT_NOTICE.firstParagraph}\n\nSynthetic notice: parents are welcome from 10 am. Lunch is served at school.`,
  date: "2026-10-20",
  has_pdf: true,
};

const DUES: Schemas["ParentDuesOut"] = {
  child_first_name: "Lakshmi",
  class_name: "Class VII",
  as_of: "2026-10-21",
  instalments: [
    {
      instalment_no: 1,
      label: "Term 1",
      due_on: "2026-06-15",
      due: "7600.00",
      late_fee: "100.00",
      overdue: true,
    },
    {
      instalment_no: 2,
      label: "Term 2",
      due_on: "2026-11-15",
      due: "12000.00",
      late_fee: "0.00",
      overdue: false,
    },
  ],
  total_due: "19600.00",
  late_fee_total: "100.00",
  pay_online: true,
  upi: null,
  office_phone: "+91 90000 00000",
};

function optin(): Schemas["ParentOptinOut"] {
  return {
    categories: [
      { category: "notices", status: "opted_in", stopped_by_parent: false },
      { category: "attendance", status: "not_asked", stopped_by_parent: false },
      { category: "fees", status: "opted_in", stopped_by_parent: false },
    ],
    language_choice: state.language,
    school_language: "en",
  };
}

const KIND_OF: Record<string, Schemas["LinkEnvelopeOut"]["kind"]> = {
  [PARENT_TOKENS.notice]: "parent_notice",
  [PARENT_TOKENS.dues]: "parent_dues",
  [PARENT_TOKENS.optin]: "parent_optin",
};

function envelope(token: string): Schemas["LinkEnvelopeOut"] | null {
  const kind = KIND_OF[token];
  if (kind === "parent_notice") return { ...envelopeBase(kind), notice: NOTICE };
  if (kind === "parent_dues") return { ...envelopeBase(kind), dues: DUES };
  if (kind === "parent_optin") return { ...envelopeBase(kind), optin: optin() };
  return null;
}

/* ------------------------------------------------------------------ state */

interface R2State {
  account: Schemas["AccountOut"];
  receipts: Map<string, Schemas["ReceiptOut"]>;
  language: "en" | "te" | "school";
}

const state: R2State = {
  account: freshAccount(),
  receipts: new Map(),
  language: "school",
};

export function resetR2(): void {
  state.account = freshAccount();
  state.receipts.clear();
  state.language = "school";
}

const MONEY = /^\d{1,12}\.\d{2}$/;

function paise(value: string): number {
  const [whole = "0", frac = "00"] = value.split(".");
  return Number(whole) * 100 + Number(frac.padEnd(2, "0").slice(0, 2));
}

function rupees(value: number): string {
  return `${Math.floor(value / 100)}.${String(value % 100).padStart(2, "0")}`;
}

/** Oldest due first, like the API's default allocation (FR-FEE-010). */
function saveReceipt(body: Body): [number, unknown] {
  const amount = typeof body.amount === "string" && MONEY.test(body.amount) ? body.amount : null;
  const mode = body.mode;
  if (body.account_id !== state.account.id || amount === null || mode !== "cash") {
    return [
      422,
      { type: "about:blank", title: "Unprocessable", status: 422, code: "validation_failed" },
    ];
  }
  let left = paise(amount);
  const lines: Schemas["ReceiptLineOut"][] = [];
  const nextLines = state.account.lines.map((due) => {
    const take = Math.min(paise(due.due), left);
    if (take <= 0) return due;
    left -= take;
    lines.push({
      instalment_no: due.instalment_no,
      label: due.label,
      head_code: due.head_code,
      head_name: due.head_name ?? "Late fee",
      amount: rupees(take),
    });
    return {
      ...due,
      paid: rupees(paise(due.paid) + take),
      due: rupees(paise(due.due) - take),
      overdue: due.overdue && paise(due.due) - take > 0,
    };
  });
  const collectable = paise(state.account.totals.collectable) - paise(amount);
  state.account = {
    ...state.account,
    lines: nextLines,
    totals: {
      ...state.account.totals,
      paid: rupees(paise(state.account.totals.paid) + paise(amount)),
      balance: rupees(collectable),
      collectable: rupees(collectable),
      overdue: rupees(Math.max(paise(state.account.totals.overdue) - paise(amount), 0)),
    },
    version: state.account.version + 1,
  };
  const receipt: Schemas["ReceiptOut"] = {
    id: R2_IDS.receipt,
    receipt_number: R2_RECEIPT_NUMBER,
    financial_year: "2026-27",
    receipt_date: todayIst(),
    amount,
    amount_in_words: "Rupees seven thousand six hundred only",
    mode: "cash",
    mode_details: {},
    utr: null,
    status: "issued",
    account_id: state.account.id,
    student_id: state.account.student_id,
    student_name: R2_STUDENT.name,
    class_section: R2_STUDENT.classSection,
    admission_no: R2_STUDENT.admissionNo,
    collected_by: ME_USER,
    collected_by_name: "Synthetica Cashier",
    issued_at: iso(),
    lines,
    content_sha256: "0".repeat(64),
    pdf_status: "queued",
    cancelled_on: null,
    cancellation: null,
    refunded: "0.00",
    refunds: [],
    version: 1,
  };
  state.receipts.set(receipt.id, receipt);
  return [201, receipt];
}

/**
 * The R2 answer to a request, or undefined when this module does not own the path (the stand-in
 * then answers as before). `publicToken` is the `X-SOS-Public-Token` header, if any.
 */
export function r2Answer(
  method: string,
  url: URL,
  subject: string,
  body: Body,
  publicToken: string | undefined,
): [number, unknown] | undefined {
  const path = url.pathname;
  const get = method === "GET";
  const post = method === "POST";

  // Public parent links (no user; the BFF's service token and the link token only).
  if (path === "/api/v1/public/links/exchange" && post) {
    const token = typeof body.token === "string" ? body.token : "";
    const found = envelope(token);
    if (!found) return notFound;
    return [
      200,
      { kind: found.kind, expires_at: found.expires_at } satisfies Schemas["LinkExchangeOut"],
    ];
  }
  if (path.startsWith("/api/v1/public/links/current")) {
    const found = publicToken ? envelope(publicToken) : null;
    if (!found) return notFound;
    if (path === "/api/v1/public/links/current" && get) return [200, found];
    if (path === "/api/v1/public/links/current/pay" && post && found.kind === "parent_dues") {
      const chosen = Array.isArray(body.instalments)
        ? body.instalments.filter((n): n is number => typeof n === "number")
        : [];
      if (chosen.length === 0) return [422, { status: 422, code: "validation_failed" }];
      const amount = DUES.instalments
        .filter((due) => chosen.includes(due.instalment_no))
        .reduce((sum, due) => sum + paise(due.due), 0);
      return [
        200,
        {
          redirect_url: PAY_REDIRECT,
          amount: rupees(amount),
          reused: false,
        } satisfies Schemas["ParentPayOut"],
      ];
    }
    if (path === "/api/v1/public/links/current/optin" && post && found.kind === "parent_optin") {
      const language = body.language;
      if (language !== "en" && language !== "te" && language !== "school") {
        return [422, { status: 422, code: "validation_failed" }];
      }
      state.language = language;
      return [200, optin()];
    }
    return notFound;
  }

  if (subject !== CASHIER) return undefined;

  if (path === "/api/v1/me" && get) return [200, me()];
  if (path === "/api/v1/fees/status" && get) {
    return [
      200,
      {
        enabled: true,
        today: todayIst(),
        financial_year: "2026-27",
        day_closed: false,
        paper_size: "A5",
      } satisfies Schemas["FeeStatusOut"],
    ];
  }
  if (path === "/api/v1/fees/accounts/search" && post) {
    const query = typeof body.query === "string" ? body.query.trim().toLowerCase() : "";
    const rows = [summary(state.account), OTHER_ACCOUNT].filter(
      (row) =>
        query.length >= 2 &&
        [row.display_name, row.admission_no].some((v) => v?.toLowerCase().includes(query)),
    );
    return [200, rows];
  }
  if (path === `/api/v1/fees/accounts/${state.account.id}` && get) return [200, state.account];
  if (path.startsWith("/api/v1/fees/accounts/") && get) return notFound;
  if (path === "/api/v1/fees/receipts" && post) return saveReceipt(body);
  if (path.startsWith("/api/v1/fees/receipts/") && get) {
    const receipt = state.receipts.get(path.split("/")[5] ?? "");
    return receipt ? [200, receipt] : notFound;
  }
  if (path.startsWith("/api/v1/fees/")) return [200, page([])];
  return undefined;
}
