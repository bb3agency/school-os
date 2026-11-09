import type { ApiClient, components } from "@schoolos/api-client";
import { unwrap } from "@/lib/bff/query";
import * as z from "@/lib/zod";

/**
 * School compliance certificate register (FC-01; FR-COMP-001..FR-COMP-022): the school's own
 * certificates (fire NOC, building safety, recognition, affiliation, water and sanitation).
 * `compliance.read` (owner, principal, office admin, auditor) sees the register;
 * `compliance.manage` (owner, principal, office admin) records, renews, corrects and removes.
 */

export const COMPLIANCE_READ = "compliance.read";
export const COMPLIANCE_MANAGE = "compliance.manage";
export const COMPLIANCE_KEYS = {
  all: ["staff", "compliance"] as const,
  register: ["staff", "compliance", "register"] as const,
  summary: ["staff", "compliance", "summary"] as const,
};

type S = components["schemas"];
export type Certificate = S["ComplianceCertificateOut"];
export type CertificateOwner = S["ComplianceOwnerOut"];
export type CertificateState = Certificate["state"];
export type Register = S["RegisterOut"];
export type RegisterRow = Register["rows"][number];
export type CertificateKind = Register["kinds"][number];
export type ComplianceSummary = S["RegisterSummaryOut"];

/** The record / correct dialog's fields; empty strings mean "not given". */
export const certificateFormSchema = z.object({
  kind: z.string().min(1, { error: "chooseOption" }),
  label: z.string().max(120, { error: "tooLong" }).optional(),
  reference_no: z.string().max(80, { error: "tooLong" }).optional(),
  issued_by: z.string().max(200, { error: "tooLong" }).optional(),
  valid_from: z.string().optional(),
  valid_until: z.string().optional(),
  document_id: z.string().optional(),
  owner_membership_id: z.string().optional(),
  notes: z.string().max(500, { error: "tooLong" }).optional(),
});
export type CertificateInput = z.output<typeof certificateFormSchema>;

export type CertificateBody = Required<S["CertificateUpdate"]>;

function text(value: string | undefined): string | null {
  const out = value?.trim();
  return out ? out : null;
}

/** The fields the API takes for both recording and correcting (FR-COMP-004, FR-COMP-008). */
export function certificateBody(input: CertificateInput): CertificateBody {
  return {
    label: input.kind === "other" ? text(input.label) : null,
    reference_no: text(input.reference_no),
    issued_by: text(input.issued_by),
    valid_from: text(input.valid_from),
    valid_until: text(input.valid_until),
    document_id: text(input.document_id),
    owner_membership_id: text(input.owner_membership_id),
    notes: text(input.notes),
  };
}

export function ifMatch(version: number): string {
  return `W/"${version}"`;
}

/** The order the register shows: most urgent first (the API sorts the same way). */
export const STATE_ORDER: Record<CertificateState, number> = {
  expired: 0,
  missing: 1,
  expiring: 2,
  valid: 3,
  no_expiry: 4,
};

export async function fetchRegister(api: ApiClient): Promise<Register> {
  return unwrap(api.GET("/api/v1/compliance/certificates"));
}

export async function fetchSummary(api: ApiClient): Promise<ComplianceSummary> {
  return unwrap(api.GET("/api/v1/compliance/summary"));
}

export async function recordCertificate(
  api: ApiClient,
  input: CertificateInput,
  renews: Certificate | null,
  idempotencyKey: string,
): Promise<Certificate> {
  const body = {
    kind: input.kind,
    ...certificateBody(input),
    ...(renews && input.kind === "other" ? { renews_id: renews.id } : {}),
  };
  return unwrap(
    api.POST("/api/v1/compliance/certificates", {
      body,
      headers: { "Idempotency-Key": idempotencyKey },
    }),
  );
}

export async function updateCertificate(
  api: ApiClient,
  certificate: Certificate,
  input: CertificateInput,
): Promise<Certificate> {
  return unwrap(
    api.PUT("/api/v1/compliance/certificates/{certificate_id}", {
      params: { path: { certificate_id: certificate.id } },
      body: certificateBody({ ...input, kind: certificate.kind }),
      headers: { "If-Match": ifMatch(certificate.version) },
    }),
  );
}

export async function removeCertificate(
  api: ApiClient,
  certificate: Certificate,
): Promise<Certificate> {
  return unwrap(
    api.POST("/api/v1/compliance/certificates/{certificate_id}:remove", {
      params: { path: { certificate_id: certificate.id } },
      headers: { "If-Match": ifMatch(certificate.version) },
    }),
  );
}
