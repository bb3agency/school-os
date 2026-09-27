/**
 * Data-quality types, all taken from the generated API client (apps/api/openapi.json).
 * Runtime lists carry `satisfies` checks so they fail to compile if the API adds a value.
 */
import type { components, operations } from "@schoolos/api-client";

type Schemas = components["schemas"];
type FindingsQuery = NonNullable<
  operations["list_findings_api_v1_dq_findings_get"]["parameters"]["query"]
>;

export type Finding = Schemas["FindingOut"];
export type FindingValue = Schemas["FindingValue"];
export type Bilingual = Schemas["Bilingual"];
export type DqRule = Schemas["RuleOut"];
export type DqProfile = Schemas["ProfileOut"];
export type DqRun = Schemas["RunOut"];
export type DqSummary = Schemas["SummaryOut"];
export type AttributeDef = Schemas["AttributeOut"];
export type Severity = Finding["severity"];
export type FindingStatus = Finding["status"];
export type RuleId = NonNullable<FindingsQuery["rule_id"]>[number];
export type RunStatus = DqRun["status"];
/** Where a value came from (docs/05 §5 `sis.attribute_values.source`). */
export type SourceKey = Schemas["ChangeRequestCreate"]["target_source"];

type Exhaustive<T extends string, L extends readonly T[]> = [T] extends [L[number]] ? L : never;

const severities = [
  "blocker",
  "high",
  "medium",
  "low",
  "info",
] as const satisfies readonly Severity[];
/** Most severe first (the API's order). */
export const SEVERITIES: Exhaustive<Severity, typeof severities> = severities;

const statuses = [
  "open",
  "reopened",
  "resolved",
  "waived",
] as const satisfies readonly FindingStatus[];
export const FINDING_STATUSES: Exhaustive<FindingStatus, typeof statuses> = statuses;

/** Unresolved findings: the API's default when no status is given. */
export const UNRESOLVED: readonly FindingStatus[] = ["open", "reopened"];

const ruleIds = [
  "DQ-001",
  "DQ-002",
  "DQ-003",
  "DQ-004",
  "DQ-005",
  "DQ-006",
  "DQ-007",
  "DQ-008",
  "DQ-009",
  "DQ-010",
  "DQ-011",
  "DQ-012",
] as const satisfies readonly RuleId[];
export const RULE_IDS: Exhaustive<RuleId, typeof ruleIds> = ruleIds;

const sources = [
  "admission_register",
  "aadhaar_as_printed",
  "udise_plus",
  "board_registration",
  "birth_certificate",
  "parent_form",
  "tc_incoming",
  "manual_entry",
] as const satisfies readonly SourceKey[];
export const SOURCES: Exhaustive<SourceKey, typeof sources> = sources;

export function isSourceKey(value: string): value is SourceKey {
  return (SOURCES as readonly string[]).includes(value);
}

/** The text of a bilingual API message in the reader's language. */
export function pick(text: Pick<Bilingual, "en" | "te">, locale: string): string {
  return locale === "te" && text.te ? text.te : text.en;
}

export function isUnresolved(status: FindingStatus): boolean {
  return status === "open" || status === "reopened";
}

/** Correction route that means "change the school record through a change request". */
export const SCHOOL_RECORD_ROUTE = "ROUTE-SCHOOL-CR";
