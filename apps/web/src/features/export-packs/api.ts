import type { ApiClient } from "@schoolos/api-client";
import { unwrap } from "@/lib/bff/query";
import * as z from "@/lib/zod";

/**
 * FO-06 enrolment statistics (FR-EXP-009..019) and FO-11 scheme verification pack
 * (FR-EXP-020..030). The calls go through the BFF client, so CSRF, step-up (428) and sign-in
 * handling apply.
 *
 * LEAD: these routes are not in the committed apps/api/openapi.json yet. Until `make openapi`
 * regenerates the client, the paths are called through a loosely typed view of the client and
 * the answers are checked with the zod shapes below. After regeneration, replace `loose()` with
 * the typed `api.GET`/`api.POST` and the zod types with `components["schemas"][...]`
 * (EnrolmentStatisticsOut, SchemePackConfigOut, SchemeAttendanceListOut, SchemeCertificatesOut).
 */

export const STATS_PERM = "export.portal";
export const SCHEME_PERMS = ["attendance.read", "certificate.issue"] as const;
export const ATTENDANCE_PERM = "attendance.read";
export const CERTIFICATE_PERM = "certificate.issue";

const STATS = "/api/v1/reports/enrolment-statistics";
const SCHEMES = "/api/v1/scheme-packs";

const row = z.object({
  class_id: z.string().nullable(),
  label: z.string(),
  counts: z.array(z.number()),
  total: z.number(),
});

export const statsSchema = z.object({
  year: z.object({ id: z.string(), label: z.string(), starts_on: z.string(), ends_on: z.string() }),
  as_of: z.string(),
  reference_date: z.string(),
  students: z.number(),
  verified: z.boolean(),
  source: z.array(z.string()),
  restricted_hidden: z.boolean(),
  tables: z.array(
    z.object({
      key: z.enum(["gender", "age", "category", "minority", "cwsn", "repeaters", "movement"]),
      title: z.string(),
      restricted: z.boolean(),
      columns: z.array(z.object({ key: z.string(), label: z.string() })),
      rows: z.array(row),
      totals: row,
    }),
  ),
  gaps: z.array(z.object({ attribute_key: z.string(), label: z.string(), count: z.number() })),
});
export type EnrolmentStatistics = z.infer<typeof statsSchema>;
export type EnrolmentStatsTable = EnrolmentStatistics["tables"][number];

const scheme = z.object({
  key: z.string(),
  label: z.string(),
  certificate_type: z.enum(["study", "bonafide"]),
  purpose_note: z.string(),
  note: z.string(),
  verified: z.boolean(),
  source: z.array(z.string()),
});

export const schemeConfigSchema = z.object({
  verified: z.boolean(),
  note: z.string(),
  present_statuses: z.array(z.string()),
  excluded_statuses: z.array(z.string()),
  max_certificates: z.number(),
  schemes: z.array(scheme),
});
export type SchemePackConfig = z.infer<typeof schemeConfigSchema>;
export type Scheme = SchemePackConfig["schemes"][number];

export const attendanceSchema = z.object({
  scheme,
  section_id: z.string(),
  section_label: z.string(),
  academic_year_label: z.string(),
  from_date: z.string(),
  to_date: z.string(),
  students: z.array(
    z.object({
      student_id: z.string(),
      roll_no: z.string().nullable(),
      full_name: z.string().nullable(),
      admission_no: z.string().nullable(),
      days_counted: z.number(),
      days_present: z.number(),
      leave_days: z.number(),
      percent: z.number().nullable(),
    }),
  ),
});
export type SchemeAttendanceList = z.infer<typeof attendanceSchema>;
export type SchemeAttendanceStudent = SchemeAttendanceList["students"][number];

export const certificatesSchema = z.object({
  scheme_key: z.string(),
  certificate_type: z.enum(["study", "bonafide"]),
  issued: z.number(),
  refused: z.number(),
  results: z.array(
    z.object({
      student_id: z.string(),
      status: z.enum(["issued", "pending", "refused"]),
      certificate_id: z.string().nullable(),
      serial: z.string().nullable(),
      error_code: z.string().nullable(),
    }),
  ),
});
export type SchemeCertificates = z.infer<typeof certificatesSchema>;

export interface StatsQuery {
  yearId?: string;
  asOf?: string;
}

export interface AttendanceQuery {
  schemeKey: string;
  sectionId: string;
  fromDate: string;
  toDate: string;
}

type Query = Record<string, string | boolean>;
type Result = Promise<{ data?: unknown; error?: unknown; response: Response }>;
interface LooseClient {
  GET(path: string, init?: { params?: { query?: Query }; parseAs?: "text" }): Result;
  POST(path: string, init: { body: unknown; headers?: Record<string, string> }): Result;
}

/** The generated client, called with paths it does not list yet (see LEAD above). */
function loose(api: ApiClient): LooseClient {
  return api as unknown as LooseClient;
}

export function statsQuery({ yearId, asOf }: StatsQuery): Query {
  const query: Query = {};
  if (yearId) query.academic_year_id = yearId;
  if (asOf) query.as_of = asOf;
  return query;
}

export function attendanceQuery(q: AttendanceQuery): Query {
  return {
    scheme_key: q.schemeKey,
    section_id: q.sectionId,
    from_date: q.fromDate,
    to_date: q.toDate,
  };
}

function search(query: Query): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) params.set(key, String(value));
  const text = params.toString();
  return text ? `?${text}` : "";
}

/** The print pages (API pages with their own strict CSP) through the BFF, in a new tab. */
export function statsPrintHref(q: StatsQuery): string {
  return `/bff${STATS}/print${search(statsQuery(q))}`;
}

export function attendancePrintHref(q: AttendanceQuery): string {
  return `/bff${SCHEMES}/attendance/print${search(attendanceQuery(q))}`;
}

/** API paths of the XLSX downloads (fetched through the BFF by `downloadSheet`). */
export function statsXlsxPath(q: StatsQuery): string {
  return `${STATS}/xlsx${search(statsQuery(q))}`;
}

export function attendanceXlsxPath(q: AttendanceQuery): string {
  return `${SCHEMES}/attendance/xlsx${search(attendanceQuery(q))}`;
}

export const PACK_KEYS = {
  stats: (q: StatsQuery) => ["staff", "exports", "enrolment-stats", q.yearId ?? "", q.asOf ?? ""],
  schemes: ["staff", "exports", "scheme-packs"] as const,
  attendance: (q: AttendanceQuery) =>
    [
      "staff",
      "exports",
      "scheme-attendance",
      q.schemeKey,
      q.sectionId,
      q.fromDate,
      q.toDate,
    ] as const,
};

export const packApi = {
  stats: async (api: ApiClient, q: StatsQuery) =>
    statsSchema.parse(await unwrap(loose(api).GET(STATS, { params: { query: statsQuery(q) } }))),
  /** Checks the sheet can be printed now (step-up); 204, nothing audited. */
  checkStatsPrint: (api: ApiClient, q: StatsQuery) =>
    unwrap(
      loose(api).GET(`${STATS}/print`, {
        params: { query: { ...statsQuery(q), check: true } },
        parseAs: "text",
      }),
    ),
  schemes: async (api: ApiClient) =>
    schemeConfigSchema.parse(await unwrap(loose(api).GET(SCHEMES))),
  attendance: async (api: ApiClient, q: AttendanceQuery) =>
    attendanceSchema.parse(
      await unwrap(
        loose(api).GET(`${SCHEMES}/attendance`, { params: { query: attendanceQuery(q) } }),
      ),
    ),
  /** Checks the list can be printed now (step-up); 204, nothing audited. */
  checkAttendancePrint: (api: ApiClient, q: AttendanceQuery) =>
    unwrap(
      loose(api).GET(`${SCHEMES}/attendance/print`, {
        params: { query: { ...attendanceQuery(q), check: true } },
        parseAs: "text",
      }),
    ),
  issueCertificates: async (
    api: ApiClient,
    schemeKey: string,
    studentIds: readonly string[],
    idempotencyKey: string,
  ) =>
    certificatesSchema.parse(
      await unwrap(
        loose(api).POST(`${SCHEMES}/certificates`, {
          body: { scheme_key: schemeKey, student_ids: studentIds },
          headers: { "Idempotency-Key": idempotencyKey },
        }),
      ),
    ),
};

/** A percentage as the list prints it; null means no counted day ("no marks"), never 0%. */
export function percentText(value: number | null, noMarks: string): string {
  return value === null ? noMarks : `${value.toFixed(1)}%`;
}
