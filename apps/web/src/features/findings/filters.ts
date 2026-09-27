import { UUID_PATTERN } from "@/lib/validation";
import {
  FINDING_STATUSES,
  RULE_IDS,
  SEVERITIES,
  UNRESOLVED,
  type FindingStatus,
  type RuleId,
  type Severity,
} from "./types";

/**
 * Findings list filters, read from the URL (GET form: bookmarkable, works without JavaScript).
 * Unknown values are dropped rather than sent to the API. Plain module (no "use client"), so
 * server pages can call it.
 */
export interface FindingFilters {
  status: FindingStatus[];
  severity: Severity[];
  ruleId: RuleId | null;
  profileKey: string | null;
  sectionId: string | null;
  studentId: string | null;
}

export type SearchParams = Record<string, string | string[] | undefined>;

/** Same pattern as the API's export-profile keys (e.g. `cisce-registration-2026`). */
const PROFILE_KEY = /^[a-z0-9][a-z0-9-]{1,63}$/;

function all(value: string | string[] | undefined): string[] {
  if (value === undefined) return [];
  return (Array.isArray(value) ? value : [value]).map((item) => item.trim());
}

function first(value: string | string[] | undefined): string | null {
  const [head] = all(value);
  return head ? head : null;
}

function allowed<T extends string>(values: string[], list: readonly T[]): T[] {
  return list.filter((item) => values.includes(item));
}

export function parseFindingFilters(params: SearchParams): FindingFilters {
  const status = allowed(all(params.status), FINDING_STATUSES);
  const rule = first(params.rule_id);
  const profile = first(params.profile_key);
  const section = first(params.section_id);
  const student = first(params.student_id);
  return {
    status: status.length > 0 ? status : [...UNRESOLVED],
    severity: allowed(all(params.severity), [...(SEVERITIES as readonly Severity[])]),
    ruleId: rule && (RULE_IDS as readonly string[]).includes(rule) ? (rule as RuleId) : null,
    profileKey: profile && PROFILE_KEY.test(profile) ? profile : null,
    sectionId: section && UUID_PATTERN.test(section) ? section : null,
    studentId: student && UUID_PATTERN.test(student) ? student : null,
  };
}

/** Query parameters for GET /dq/findings. */
export function findingsQuery(filters: FindingFilters) {
  return {
    status: filters.status,
    ...(filters.severity.length > 0 ? { severity: filters.severity } : {}),
    ...(filters.ruleId ? { rule_id: [filters.ruleId] } : {}),
    ...(filters.profileKey ? { profile_key: filters.profileKey } : {}),
    ...(filters.sectionId ? { section_id: filters.sectionId } : {}),
    ...(filters.studentId ? { student_id: filters.studentId } : {}),
  };
}
