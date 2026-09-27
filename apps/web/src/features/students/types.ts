import type { components } from "@schoolos/api-client";

/**
 * Student-record types, all taken from the generated OpenAPI schema (apps/api/openapi.json →
 * @schoolos/api-client). Only runtime lists for forms are written here, and each is checked
 * against the generated union so it fails to compile if the API adds a value.
 */
type Schemas = components["schemas"];

export type StudentSummary = Schemas["StudentSummary"];
export type Student = Schemas["StudentOut"];
export type StudentCreate = Schemas["StudentCreate"];
export type StudentStatus = NonNullable<StudentCreate["status"]>;
export type CanonicalValue = Schemas["CanonicalOut"];
export type SourceValue = Schemas["ValueOut"];
export type ValueInput = Schemas["ValueIn"];
export type ValueSource = ValueInput["source"];
export type Attribute = Schemas["AttributeOut"];
export type Guardian = Schemas["GuardianOut"];
export type RevealResult = Schemas["RevealOut"];
export type ClassSection = Schemas["ClassSection"];
export type StaffMe = Schemas["app__identity__schemas__MeOut"];

type Exhaustive<T extends string, L extends readonly T[]> = [T] extends [L[number]] ? L : never;

const valueSources = [
  "admission_register",
  "aadhaar_as_printed",
  "udise_plus",
  "board_registration",
  "birth_certificate",
  "parent_form",
  "tc_incoming",
  "manual_entry",
] as const satisfies readonly ValueSource[];
/** FR-STU-002 sources, in the order offices think of them (register first, BR-01). */
export const VALUE_SOURCES: Exhaustive<ValueSource, typeof valueSources> = valueSources;

const studentStatuses = [
  "provisional",
  "active",
  "left",
  "graduated",
] as const satisfies readonly StudentStatus[];
export const STUDENT_STATUSES: Exhaustive<StudentStatus, typeof studentStatuses> = studentStatuses;

export function isValueSource(value: string): value is ValueSource {
  return (VALUE_SOURCES as readonly string[]).includes(value);
}

export function isStudentStatus(value: string): value is StudentStatus {
  return (STUDENT_STATUSES as readonly string[]).includes(value);
}
