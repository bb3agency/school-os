import { UUID_PATTERN } from "@/lib/validation";
import {
  CERTIFICATE_STATUSES,
  CERTIFICATE_TYPES,
  type CertificateStatus,
  type CertificateType,
} from "./types";

/**
 * URL parameters of the certificate screens (plain module: server pages call these). Unknown
 * values are dropped instead of being sent to the API.
 */
export type SearchParams = Record<string, string | string[] | undefined>;

function first(value: string | string[] | undefined): string | null {
  const head = Array.isArray(value) ? value[0] : value;
  const trimmed = head?.trim();
  return trimmed ? trimmed : null;
}

function oneOf<T extends string>(value: string | null, allowed: readonly T[]): T | null {
  return value !== null && (allowed as readonly string[]).includes(value) ? (value as T) : null;
}

export interface CertificateFilters {
  status: CertificateStatus | null;
  certificateType: CertificateType | null;
  studentId: string | null;
}

export function parseCertificateFilters(params: SearchParams): CertificateFilters {
  const student = first(params.student_id);
  return {
    status: oneOf(first(params.status), CERTIFICATE_STATUSES),
    certificateType: oneOf(first(params.certificate_type), CERTIFICATE_TYPES),
    studentId: student && UUID_PATTERN.test(student) ? student : null,
  };
}

/** `/students/{id}/certificates/new?type=` (from the student's page). */
export function parseIssueType(params: SearchParams): CertificateType | null {
  return oneOf(first(params.type), CERTIFICATE_TYPES);
}
