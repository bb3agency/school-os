import type { components, operations } from "@schoolos/api-client";
import type { BadgeTone } from "@/components/ui/Badge";

/** Import types from the generated OpenAPI schema (never hand-written). */
type Schemas = components["schemas"];

export type ImportBatch = Schemas["ImportOut"];
export type ImportSummary = Schemas["ImportSummary"];
export type ImportColumn = Schemas["ColumnOut"];
export type ImportRow = Schemas["ImportRowOut"];
export type RowIssue = Schemas["Issue"];
export type ImportTemplate = Schemas["TemplateOut"];
export type ImportStatus = ImportBatch["status"];
export type ImportSource = Schemas["ImportCreate"]["source"];
type RowQuery = NonNullable<
  operations["list_rows_api_v1_imports__import_id__rows_get"]["parameters"]["query"]
>;
export type RowStatusFilter = NonNullable<RowQuery["status"]>;

type Exhaustive<T extends string, L extends readonly T[]> = [T] extends [L[number]] ? L : never;

const importStatuses = [
  "uploaded",
  "parsing",
  "parsed",
  "validating",
  "validated",
  "committing",
  "committed",
  "reverting",
  "reverted",
  "failed",
] as const satisfies readonly ImportStatus[];
export const IMPORT_STATUSES: Exhaustive<ImportStatus, typeof importStatuses> = importStatuses;

export const importTone: Record<ImportStatus, BadgeTone> = {
  uploaded: "info",
  parsing: "info",
  parsed: "warning",
  validating: "info",
  validated: "info",
  committing: "info",
  committed: "success",
  reverting: "info",
  reverted: "neutral",
  failed: "danger",
};

/** A worker is busy with the batch: poll until it settles. */
export const IMPORT_BUSY: ReadonlySet<ImportStatus> = new Set([
  "uploaded",
  "parsing",
  "validating",
  "committing",
  "reverting",
]);

/** The mapping may be changed (API: EDITABLE statuses). */
export const MAPPING_EDITABLE: ReadonlySet<ImportStatus> = new Set(["parsed", "validated"]);

/** Mapping targets that are not student attributes (imports/mapping.py SPECIAL_TARGETS). */
export const SPECIAL_TARGETS = ["class", "section", "class_section", "roll_no"] as const;
export type SpecialTarget = (typeof SPECIAL_TARGETS)[number];

export function isSpecialTarget(value: string): value is SpecialTarget {
  return (SPECIAL_TARGETS as readonly string[]).includes(value);
}

/** Sources whose rows may create students (imports/config.yaml creating_sources). */
export const CREATING_SOURCES: readonly ImportSource[] = [
  "admission_register",
  "tc_incoming",
  "manual_entry",
];
