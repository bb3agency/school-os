/**
 * Certificate types from the generated API client (apps/api/openapi.json; docs/09 Certificates,
 * US-1101..US-1108). Runtime lists carry `satisfies` checks so they fail to compile if the API
 * adds a value. Plain module (no "use client"): server pages may import from it.
 */
import type { components } from "@schoolos/api-client";
import type { PillVariant } from "@/components/ui/Badge";

type Schemas = components["schemas"];

export type Certificate = Schemas["CertificateOut"];
export type CertificateType = Certificate["certificate_type"];
export type CertificateStatus = Certificate["status"];
export type PdfStatus = Certificate["pdf_status"];
export type CertificateContent = Schemas["CertificateContent"];
export type ContentLine = Schemas["ContentLine"];
export type CertificateTypeInfo = Schemas["CertificateTypeOut"];
export type CertificateInput = Schemas["InputOut"];
export type CertificatePreview = Schemas["CertificatePreview"];
export type PrintedField = Schemas["PrintedField"];
export type Blocker = Schemas["Blocker"];

type Exhaustive<T extends string, L extends readonly T[]> = [T] extends [L[number]] ? L : never;

const types = [
  "bonafide",
  "study",
  "conduct",
  "transfer",
] as const satisfies readonly CertificateType[];
export const CERTIFICATE_TYPES: Exhaustive<CertificateType, typeof types> = types;

const statuses = [
  "pending",
  "issued",
  "rejected",
  "withdrawn",
  "cancelled",
] as const satisfies readonly CertificateStatus[];
export const CERTIFICATE_STATUSES: Exhaustive<CertificateStatus, typeof statuses> = statuses;

/** Workflow pill: waiting = review (violet), issued = done (teal), rejected/cancelled = negative. */
export const certificatePill: Record<CertificateStatus, PillVariant> = {
  pending: "review",
  issued: "done",
  rejected: "negative",
  withdrawn: "tag",
  cancelled: "negative",
};

/** Permission keys (apps/api/app/authz/permissions.yaml). */
export const CERT_READ = "certificate.read";
export const CERT_ISSUE = "certificate.issue";
export const CERT_APPROVE = "certificate.approve";
export const REGISTER_READ = "register.read";
export const CERT_ANY = [CERT_READ, CERT_ISSUE, CERT_APPROVE] as const;

/** Reason bounds (apps/api/app/certificates/config.yaml; DB CHECKs in 0033_certificates). */
export const REASON_MIN = 10;
export const REASON_MAX = 1000;

/** `W/"3"` for If-Match from a resource's `version` (the API's ETag format). */
export function ifMatch(version: number): string {
  return `W/"${version}"`;
}

export const CERT_KEYS = {
  all: ["staff", "certificates"] as const,
  types: ["staff", "certificates", "types"] as const,
  one: (id: string) => ["staff", "certificates", "one", id] as const,
  preview: (studentId: string, type: string) =>
    ["staff", "certificates", "preview", studentId, type] as const,
} as const;

/** The three register print views (FR-REG-001..003). */
export const REGISTERS = ["transfer", "certificates", "admission"] as const;
export type RegisterKind = (typeof REGISTERS)[number];

export const REGISTER_PATHS: Record<RegisterKind, string> = {
  transfer: "/api/v1/registers/transfer-certificates",
  certificates: "/api/v1/registers/certificates",
  admission: "/api/v1/registers/admission-withdrawal",
};
