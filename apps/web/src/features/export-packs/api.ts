import type { ApiClient, components } from "@schoolos/api-client";
import { unwrap } from "@/lib/bff/query";

/**
 * FO-06 enrolment statistics (FR-EXP-009..019) and FO-11 scheme verification pack
 * (FR-EXP-020..030). The calls go through the BFF client, so CSRF, step-up (428) and sign-in
 * handling apply.
 */

export const STATS_PERM = "export.portal";
export const SCHEME_PERMS = ["attendance.read", "certificate.issue"] as const;
export const ATTENDANCE_PERM = "attendance.read";
export const CERTIFICATE_PERM = "certificate.issue";

const STATS = "/api/v1/reports/enrolment-statistics" as const;
const SCHEMES = "/api/v1/scheme-packs" as const;

type Schemas = components["schemas"];

export type EnrolmentStatistics = Schemas["EnrolmentStatisticsOut"];
export type EnrolmentStatsTable = Schemas["EnrolmentStatsTableOut"];
export type SchemePackConfig = Schemas["SchemePackConfigOut"];
export type Scheme = Schemas["SchemeOut"];
export type SchemeAttendanceList = Schemas["SchemeAttendanceListOut"];
export type SchemeAttendanceStudent = Schemas["SchemeAttendanceStudentOut"];
export type SchemeCertificates = Schemas["SchemeCertificatesOut"];

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

interface StatsParams {
  academic_year_id?: string;
  as_of?: string;
}

interface AttendanceParams {
  scheme_key: string;
  section_id: string;
  from_date: string;
  to_date: string;
}

export function statsQuery({ yearId, asOf }: StatsQuery): StatsParams {
  const query: StatsParams = {};
  if (yearId) query.academic_year_id = yearId;
  if (asOf) query.as_of = asOf;
  return query;
}

export function attendanceQuery(q: AttendanceQuery): AttendanceParams {
  return {
    scheme_key: q.schemeKey,
    section_id: q.sectionId,
    from_date: q.fromDate,
    to_date: q.toDate,
  };
}

function search(query: StatsParams | AttendanceParams): string {
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
  stats: (api: ApiClient, q: StatsQuery) =>
    unwrap(api.GET(STATS, { params: { query: statsQuery(q) } })),
  /** Checks the sheet can be printed now (step-up); 204, nothing audited. */
  checkStatsPrint: (api: ApiClient, q: StatsQuery) =>
    unwrap(
      api.GET(`${STATS}/print`, {
        params: { query: { ...statsQuery(q), check: true } },
        parseAs: "text",
      }),
    ),
  schemes: (api: ApiClient) => unwrap(api.GET(SCHEMES)),
  attendance: (api: ApiClient, q: AttendanceQuery) =>
    unwrap(api.GET(`${SCHEMES}/attendance`, { params: { query: attendanceQuery(q) } })),
  /** Checks the list can be printed now (step-up); 204, nothing audited. */
  checkAttendancePrint: (api: ApiClient, q: AttendanceQuery) =>
    unwrap(
      api.GET(`${SCHEMES}/attendance/print`, {
        params: { query: { ...attendanceQuery(q), check: true } },
        parseAs: "text",
      }),
    ),
  issueCertificates: (
    api: ApiClient,
    schemeKey: string,
    studentIds: readonly string[],
    idempotencyKey: string,
  ) =>
    unwrap(
      api.POST(`${SCHEMES}/certificates`, {
        body: { scheme_key: schemeKey, student_ids: [...studentIds] },
        headers: { "Idempotency-Key": idempotencyKey },
      }),
    ),
};

/** A percentage as the list prints it; null means no counted day ("no marks"), never 0%. */
export function percentText(value: number | null, noMarks: string): string {
  return value === null ? noMarks : `${value.toFixed(1)}%`;
}
