import type { components } from "@schoolos/api-client";

/**
 * APAAR consent register types (ADR-0039), all from the generated OpenAPI schema. Runtime lists
 * are checked against the generated unions so they fail to compile if the API adds a value.
 */
type Schemas = components["schemas"];

export type StudentConsent = Schemas["StudentConsentOut"];
export type ConsentEntry = Schemas["ConsentEntryOut"];
export type ConsentRow = Schemas["ConsentRowOut"];
export type ConsentSummary = Schemas["ConsentSummaryOut"];
export type StatusCounts = Schemas["StatusCounts"];
export type ConsentInput = Schemas["ConsentIn"];
export type ConsentStatus = ConsentInput["status"];
export type FormLanguage = Schemas["ApaarSettingsIn"]["form_language"];
export type Relationship = NonNullable<ConsentInput["relationship"]>;

type Exhaustive<T extends string, L extends readonly T[]> = [T] extends [L[number]] ? L : never;

const statuses = [
  "pending",
  "given",
  "refused",
  "withdrawn",
] as const satisfies readonly ConsentStatus[];
/** In the order the office meets them: a form goes home, then the parent decides. */
export const CONSENT_STATUSES: Exhaustive<ConsentStatus, typeof statuses> = statuses;

const languages = ["en", "te"] as const satisfies readonly FormLanguage[];
export const FORM_LANGUAGES: Exhaustive<FormLanguage, typeof languages> = languages;

const relationships = ["mother", "father", "guardian"] as const satisfies readonly Relationship[];
export const RELATIONSHIPS: Exhaustive<Relationship, typeof relationships> = relationships;

export const APAAR_READ = "apaar.consent.read";
export const APAAR_RECORD = "apaar.consent.record";
export const SETTINGS_MANAGE = "tenant.settings.manage";

export const APAAR_KEYS = {
  all: ["staff", "apaar"] as const,
  summary: (sectionId: string) => ["staff", "apaar", "summary", sectionId] as const,
  list: (sectionId: string, status: string) =>
    ["staff", "apaar", "list", sectionId, status] as const,
  student: (studentId: string) => ["staff", "apaar", "student", studentId] as const,
  settings: ["staff", "apaar", "settings"] as const,
};

export function isConsentStatus(value: string): value is ConsentStatus {
  return (CONSENT_STATUSES as readonly string[]).includes(value);
}

/**
 * What the parent may decide next (FR-APC-002): `withdrawn` only after `given`; once anyone
 * decided, never back to `pending`.
 */
export function nextStatuses(current: ConsentStatus): ConsentStatus[] {
  const decided = current !== "pending";
  return CONSENT_STATUSES.filter((status) => {
    if (status === "withdrawn") return current === "given";
    if (status === "pending") return !decided;
    return true;
  });
}

/** Print views (A4, the API's own strict CSP) open through the BFF in a new tab. */
export function studentFormHref(studentId: string, language?: FormLanguage): string {
  const query = language ? `?language=${language}` : "";
  return `/bff/api/v1/students/${encodeURIComponent(studentId)}/apaar-consent/form${query}`;
}

export function sectionFormsHref(sectionId: string, status?: ConsentStatus): string {
  const query = status ? `?status=${status}` : "";
  return `/bff/api/v1/sections/${encodeURIComponent(sectionId)}/apaar-consent-forms${query}`;
}
