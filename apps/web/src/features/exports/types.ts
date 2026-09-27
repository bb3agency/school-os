/**
 * Export types from the generated API client (apps/api/openapi.json; docs/09 Exports,
 * ADR-0021). Runtime lists carry `satisfies` checks so they fail to compile if the API adds a
 * value. Plain module (no "use client"): server pages may import from it.
 */
import type { components, operations } from "@schoolos/api-client";
import type { BadgeTone } from "@/components/ui/Badge";

type Schemas = components["schemas"];
type ListQuery = NonNullable<operations["list_exports_api_v1_exports_get"]["parameters"]["query"]>;

export type Export = Schemas["ExportOut"];
export type ExportFile = Schemas["ExportFileOut"];
export type ExportProfile = Schemas["ExportProfileOut"];
export type ExportKind = Export["kind"];
export type ExportStatus = Export["status"];
export type FileFormat = Export["formats"][number];
export type PrecheckFormat = Schemas["PrecheckCreate"]["format"][number];
export type ListFormat = Schemas["StudentListCreate"]["format"];
export type ExportLanguage = Export["language"];
export type ExportScope = Schemas["ExportScopeIn"];
export type RequestedBy = NonNullable<ListQuery["requested_by"]>;

type Exhaustive<T extends string, L extends readonly T[]> = [T] extends [L[number]] ? L : never;

const statuses = [
  "queued",
  "running",
  "ready",
  "failed",
  "expired",
] as const satisfies readonly ExportStatus[];
export const EXPORT_STATUSES: Exhaustive<ExportStatus, typeof statuses> = statuses;

const kinds = [
  "board_precheck",
  "portal_precheck",
  "student_list",
] as const satisfies readonly ExportKind[];
export const EXPORT_KINDS: Exhaustive<ExportKind, typeof kinds> = kinds;

const precheckFormats = ["xlsx", "pdf"] as const satisfies readonly PrecheckFormat[];
export const PRECHECK_FORMATS: Exhaustive<PrecheckFormat, typeof precheckFormats> = precheckFormats;

const listFormats = ["xlsx", "csv"] as const satisfies readonly ListFormat[];
export const LIST_FORMATS: Exhaustive<ListFormat, typeof listFormats> = listFormats;

const languages = ["en", "te"] as const satisfies readonly ExportLanguage[];
export const EXPORT_LANGUAGES: Exhaustive<ExportLanguage, typeof languages> = languages;

export const exportTone: Record<ExportStatus, BadgeTone> = {
  queued: "info",
  running: "info",
  ready: "success",
  failed: "danger",
  expired: "neutral",
};

/** A worker is still making the files: poll until the export settles. */
export function isExportBusy(status: ExportStatus): boolean {
  return status === "queued" || status === "running";
}

/** Permission keys (apps/api/app/authz/permissions.yaml; ADR-0021). */
export const EXPORT_PERM = {
  board: "export.board",
  portal: "export.portal",
  studentList: "student.export",
  readAll: "export.read_all",
  downloadAny: "export.download_any",
  readBasic: "student.read_basic",
  readSensitive: "student.read_sensitive",
  findingsRead: "dq.findings.read",
} as const;

/** Any of these lets a member open the exports screen (GET /exports needs one of them). */
export const EXPORT_SCREEN_PERMISSIONS = [
  EXPORT_PERM.board,
  EXPORT_PERM.portal,
  EXPORT_PERM.studentList,
  EXPORT_PERM.readAll,
] as const;

/** Profile kind → the permission that makes it (docs/07 §6.2). */
export const PROFILE_PERMISSION: Record<ExportProfile["kind"], string> = {
  board: EXPORT_PERM.board,
  portal: EXPORT_PERM.portal,
};

/**
 * Attributes that never leave SchoolOS (apps/api/app/exports/config.yaml `never_exported`;
 * PRV-013..014). The screen does not offer them; the API refuses them anyway (422).
 */
export const NEVER_EXPORTED: readonly string[] = [
  "aadhaar_name_as_printed",
  "aadhaar_dob_as_printed",
  "aadhaar_gender_as_printed",
];

/** Structure columns of a student list (config.yaml `student_list.structure_columns`). */
export const STRUCTURE_COLUMNS = ["class", "section", "roll_no"] as const;
export type StructureColumn = (typeof STRUCTURE_COLUMNS)[number];

export function isStructureColumn(value: string): value is StructureColumn {
  return (STRUCTURE_COLUMNS as readonly string[]).includes(value);
}

/** Restricted (C3) attributes need `student.read_sensitive` and are listed in the audit log. */
export const RESTRICTED_CLASSIFICATION = "C3";

/** API bounds (apps/api/app/exports/schemas.py). */
export const MAX_COLUMNS = 60;
export const MAX_SECTIONS = 100;
export const MAX_CLASSES = 50;

/** Same pattern as the API's `PROFILE_PATTERN`. */
export const PROFILE_KEY_PATTERN = /^[a-z0-9][a-z0-9-]{0,63}$/;
