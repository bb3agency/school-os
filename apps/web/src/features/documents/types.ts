/**
 * Document types from the generated API client (apps/api/openapi.json; docs/09 Documents,
 * FR-DOC-001..008). Runtime lists carry `satisfies` checks so they fail to compile if the API
 * adds a value. Plain module (no "use client"): server pages may import from it.
 */
import type { components } from "@schoolos/api-client";
import type { BadgeTone } from "@/components/ui/Badge";

type Schemas = components["schemas"];

export type DocumentRow = Schemas["DocumentOut"];
export type DocumentDetail = Schemas["DocumentDetail"];
export type DocumentVersion = Schemas["VersionOut"];
export type AclEntry = Schemas["AclEntryOut"];
export type DocType = DocumentRow["doc_type"];
export type Purpose = DocumentRow["purpose"];
export type Sensitivity = DocumentRow["sensitivity"];
export type DocLanguage = NonNullable<DocumentRow["language"]>;
export type DocStatus = DocumentRow["status"];
export type VersionStatus = DocumentVersion["status"];
export type PrincipalType = AclEntry["principal_type"];

type Exhaustive<T extends string, L extends readonly T[]> = [T] extends [L[number]] ? L : never;

const docTypes = [
  "circular",
  "policy",
  "minutes",
  "register_scan",
  "certificate",
  "letter",
  "form",
  "report",
  "verified_answer",
  "other",
  "evidence",
  "import_file",
] as const satisfies readonly DocType[];
export const DOC_TYPES: Exhaustive<DocType, typeof docTypes> = docTypes;

const purposes = [
  "circular",
  "policy",
  "other",
  "evidence",
  "register_scan",
  "import_file",
] as const satisfies readonly Purpose[];
export const PURPOSES: Exhaustive<Purpose, typeof purposes> = purposes;

const sensitivities = ["C1", "C2", "C3"] as const satisfies readonly Sensitivity[];
export const SENSITIVITIES: Exhaustive<Sensitivity, typeof sensitivities> = sensitivities;

const languages = ["en", "te", "mixed"] as const satisfies readonly DocLanguage[];
export const DOC_LANGUAGES: Exhaustive<DocLanguage, typeof languages> = languages;

const docStatuses = ["active", "archived"] as const satisfies readonly DocStatus[];
export const DOC_STATUSES: Exhaustive<DocStatus, typeof docStatuses> = docStatuses;

const versionStatuses = [
  "queued",
  "scanning",
  "extracting",
  "chunking",
  "embedding",
  "ready",
  "failed",
  "quarantined",
] as const satisfies readonly VersionStatus[];
export const VERSION_STATUSES: Exhaustive<VersionStatus, typeof versionStatuses> = versionStatuses;

const principalTypes = [
  "role",
  "class",
  "section",
  "membership",
] as const satisfies readonly PrincipalType[];
export const PRINCIPAL_TYPES: Exhaustive<PrincipalType, typeof principalTypes> = principalTypes;

/**
 * Document types a member may choose on the documents screen (the API's general purposes:
 * circular, policy, other). Evidence, register photos and imports have their own screens.
 */
export const GENERAL_DOC_TYPES = [
  "circular",
  "policy",
  "minutes",
  "certificate",
  "letter",
  "form",
  "report",
  "other",
] as const satisfies readonly DocType[];
export type GeneralDocType = (typeof GENERAL_DOC_TYPES)[number];

/** Upload purpose for a general document type (circulars and policies have their own). */
export function purposeFor(docType: GeneralDocType): Purpose {
  if (docType === "circular" || docType === "policy") return docType;
  return "other";
}

/** Permission keys (apps/api/app/authz/permissions.yaml). */
export const DOCUMENT_PERM = {
  read: "document.read",
  upload: "document.upload",
  manage: "document.manage_acl",
  readSensitive: "student.read_sensitive",
  userManage: "user.manage",
  readBasic: "student.read_basic",
} as const;

/** The version still moves through the scan (and, from M2, search preparation). */
const BUSY: ReadonlySet<VersionStatus> = new Set([
  "queued",
  "scanning",
  "extracting",
  "chunking",
  "embedding",
]);

export function isVersionBusy(status: VersionStatus): boolean {
  return BUSY.has(status);
}

export const versionTone: Record<VersionStatus, BadgeTone> = {
  queued: "info",
  scanning: "info",
  extracting: "info",
  chunking: "info",
  embedding: "info",
  ready: "success",
  failed: "danger",
  quarantined: "danger",
};

/**
 * Why a version cannot be opened, as a message key under `documents.version.reason`. The
 * `error` of a version is a code only (never the file's content): `malware_detected`, the
 * Aadhaar codes of PRV-016 (`aadhaar_detected`, `aadhaar_redacted`, `aadhaar_unredactable`),
 * or a scan failure (`scan_unavailable`, `storage_unavailable`). Unknown codes get a generic
 * explanation.
 */
const REASONS = [
  "malware_detected",
  "aadhaar_detected",
  "aadhaar_redacted",
  "aadhaar_unredactable",
  "scan_unavailable",
  "storage_unavailable",
] as const;
export type VersionReason =
  (typeof REASONS)[number] | "quarantined_other" | "failed_other" | "busy" | null;

export function versionReason(version: Pick<DocumentVersion, "status" | "error">): VersionReason {
  if (version.status === "ready") return null;
  if (isVersionBusy(version.status)) return "busy";
  const code = version.error ?? "";
  if ((REASONS as readonly string[]).includes(code)) return code as VersionReason;
  return version.status === "quarantined" ? "quarantined_other" : "failed_other";
}

/** The badge key under `documents.version.status`: a withheld Aadhaar copy reads "Withheld". */
export type VersionBadge = VersionStatus | "withheld" | "replaced";

export function versionBadge(version: Pick<DocumentVersion, "status" | "error">): VersionBadge {
  if (version.status !== "quarantined") return version.status;
  if (version.error === "aadhaar_redacted") return "replaced";
  if (version.error === "aadhaar_detected" || version.error === "aadhaar_unredactable") {
    return "withheld";
  }
  return "quarantined";
}

export const badgeTone: Record<VersionBadge, BadgeTone> = {
  ...versionTone,
  withheld: "warning",
  replaced: "neutral",
};

/** `W/"3"` for If-Match from a document's `version` (the API's ETag format). */
export function ifMatch(version: number): string {
  return `W/"${version}"`;
}

/** Upload rules (FR-DOC-001; apps/api/app/documents/service.py purpose rules). */
export const MAX_UPLOAD_BYTES = 25 * 1024 * 1024;
export const GENERAL_TYPES: Readonly<Record<string, string>> = {
  pdf: "application/pdf",
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  png: "image/png",
  docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
};
/** Evidence and register photos: scans only. */
export const SCAN_EXTENSIONS = ["pdf", "jpg", "jpeg", "png"] as const;

export function acceptFor(purpose: Purpose): string {
  const extensions =
    purpose === "evidence" || purpose === "register_scan"
      ? SCAN_EXTENSIONS
      : Object.keys(GENERAL_TYPES);
  return extensions.map((ext) => `.${ext}`).join(",");
}

/** API bounds (apps/api/app/documents/schemas.py). */
export const MAX_TITLE = 200;
export const MAX_ISSUER = 200;
export const MAX_ACL = 50;

/** Short file-type names for chips ("PDF", "XLSX"); null for anything else. */
const FILE_KINDS: Readonly<Record<string, string>> = {
  "application/pdf": "PDF",
  "image/jpeg": "JPG",
  "image/png": "PNG",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "DOCX",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "XLSX",
  "text/csv": "CSV",
};

export function fileKind(mimeType: string | null | undefined): string | null {
  return (mimeType && FILE_KINDS[mimeType]) || null;
}
