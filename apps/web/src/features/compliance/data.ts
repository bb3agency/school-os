import type { ApiClient } from "@schoolos/api-client";
import { unwrap } from "@/lib/bff/query";
import * as z from "@/lib/zod";

/**
 * School compliance certificate register (FC-01; FR-COMP-001..FR-COMP-022): the school's own
 * certificates (fire NOC, building safety, recognition, affiliation, water and sanitation).
 * `compliance.read` (owner, principal, office admin, auditor) sees the register;
 * `compliance.manage` (owner, principal, office admin) records, renews, corrects and removes.
 *
 * LOCAL WRAPPER: the routes under /api/v1/compliance are new in this wave and not yet in the
 * generated client (`packages/api-client`). The types below are written by hand from
 * `app/compliance/schemas.py` and the answers are checked with zod. After `make openapi` the
 * lead swaps `looseClient` and these types for `components["schemas"][...]` and `api.GET(...)`.
 */

export const COMPLIANCE_READ = "compliance.read";
export const COMPLIANCE_MANAGE = "compliance.manage";
export const COMPLIANCE_KEYS = {
  all: ["staff", "compliance"] as const,
  register: ["staff", "compliance", "register"] as const,
  summary: ["staff", "compliance", "summary"] as const,
};
const BASE = "/api/v1/compliance";

const states = ["valid", "expiring", "expired", "no_expiry", "missing"] as const;
export type CertificateState = (typeof states)[number];

const ownerSchema = z.object({
  membership_id: z.string(),
  display_name: z.string().nullable(),
  active: z.boolean(),
});

const certificateSchema = z.object({
  id: z.string(),
  kind: z.string(),
  kind_label: z.string(),
  label: z.string().nullable(),
  reference_no: z.string().nullable(),
  issued_by: z.string().nullable(),
  valid_from: z.string().nullable(),
  valid_until: z.string().nullable(),
  state: z.enum(states),
  days_left: z.number().nullable(),
  document: z.object({ id: z.string(), title: z.string().nullable() }).nullable(),
  owner: ownerSchema.nullable(),
  notes: z.string().nullable(),
  status: z.enum(["current", "superseded", "removed"]),
  superseded_by: z.string().nullable(),
  recorded_by_name: z.string().nullable(),
  created_at: z.string(),
  updated_at: z.string(),
  version: z.number(),
});

const registerSchema = z.object({
  rows: z.array(
    z.object({
      kind: z.string(),
      kind_label: z.string(),
      state: z.enum(states),
      certificate: certificateSchema.nullable(),
    }),
  ),
  counts: z.object({
    expired: z.number(),
    expiring: z.number(),
    missing: z.number(),
    valid: z.number(),
    no_expiry: z.number(),
  }),
  kinds: z.array(
    z.object({
      kind: z.string(),
      label: z.string(),
      authority: z.string(),
      typical_validity_months: z.number().nullable(),
      note: z.string(),
      disclosure_items: z.array(z.string()),
      applies: z.boolean(),
    }),
  ),
  expiring_days: z.number(),
  verified: z.boolean(),
  today: z.string(),
  owners: z.array(ownerSchema),
});

const summarySchema = z.object({
  expired: z.number(),
  expiring: z.number(),
  missing: z.number(),
});

export type Certificate = z.output<typeof certificateSchema>;
export type Register = z.output<typeof registerSchema>;
export type RegisterRow = Register["rows"][number];
export type CertificateKind = Register["kinds"][number];
export type ComplianceSummary = z.output<typeof summarySchema>;

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

export interface CertificateBody {
  label: string | null;
  reference_no: string | null;
  issued_by: string | null;
  valid_from: string | null;
  valid_until: string | null;
  document_id: string | null;
  owner_membership_id: string | null;
  notes: string | null;
}

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

// --- local wrapper (see the note at the top) ------------------------------------------------

type ClientResult = { data?: unknown; error?: unknown; response: Response };
interface LooseInit {
  body?: unknown;
  headers?: Record<string, string>;
}
interface LooseClient {
  GET(path: string, init?: LooseInit): Promise<ClientResult>;
  POST(path: string, init?: LooseInit): Promise<ClientResult>;
  PUT(path: string, init?: LooseInit): Promise<ClientResult>;
}

function looseClient(api: ApiClient): LooseClient {
  return api as unknown as LooseClient;
}

export async function fetchRegister(api: ApiClient): Promise<Register> {
  return registerSchema.parse(await unwrap(looseClient(api).GET(`${BASE}/certificates`)));
}

export async function fetchSummary(api: ApiClient): Promise<ComplianceSummary> {
  return summarySchema.parse(await unwrap(looseClient(api).GET(`${BASE}/summary`)));
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
  return certificateSchema.parse(
    await unwrap(
      looseClient(api).POST(`${BASE}/certificates`, {
        body,
        headers: { "Idempotency-Key": idempotencyKey },
      }),
    ),
  );
}

export async function updateCertificate(
  api: ApiClient,
  certificate: Certificate,
  input: CertificateInput,
): Promise<Certificate> {
  return certificateSchema.parse(
    await unwrap(
      looseClient(api).PUT(`${BASE}/certificates/${certificate.id}`, {
        body: certificateBody({ ...input, kind: certificate.kind }),
        headers: { "If-Match": ifMatch(certificate.version) },
      }),
    ),
  );
}

export async function removeCertificate(
  api: ApiClient,
  certificate: Certificate,
): Promise<Certificate> {
  return certificateSchema.parse(
    await unwrap(
      looseClient(api).POST(`${BASE}/certificates/${certificate.id}:remove`, {
        headers: { "If-Match": ifMatch(certificate.version) },
      }),
    ),
  );
}
