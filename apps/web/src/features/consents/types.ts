import type { components } from "@schoolos/api-client";

/**
 * Consent register for the school's optional purposes (ADR-0049; FR-DPDP-020..026), all types
 * from the generated OpenAPI schema. Runtime lists are checked against the generated unions so
 * they fail to compile if the API adds a value. The APAAR register (ADR-0039) is separate.
 */
type Schemas = components["schemas"];

export type Purpose = Schemas["PurposeOut"];
export type PurposeList = Schemas["PurposeListOut"];
export type Suggestion = Schemas["PurposeSuggestionOut"];
export type PurposeCreate = Schemas["PurposeCreate"];
export type PurposeUpdate = Schemas["PurposeUpdate"];
export type PurposeState = Schemas["PurposeStateOut"];
export type ConsentEntry = Schemas["PurposeConsentEntryOut"];
export type ConsentInput = Schemas["PurposeConsentIn"];
export type StudentConsents = Schemas["StudentConsentsOut"];
export type ConsentView = Schemas["ConsentViewOut"];
export type ConsentSummary = Schemas["PurposeConsentSummaryOut"];
export type PurposeSummary = Schemas["PurposeSummaryOut"];
export type AnswerCounts = Schemas["AnswerCounts"];
export type Answer = PurposeState["answer"];
export type DecisionStatus = ConsentInput["status"];
export type FormLanguage = NonNullable<ConsentInput["form_language"]>;
export type Relationship = NonNullable<ConsentInput["relationship"]>;

type Exhaustive<T extends string, L extends readonly T[]> = [T] extends [L[number]] ? L : never;

const answers = [
  "given",
  "refused",
  "withdrawn",
  "pending",
  "not_answered",
] as const satisfies readonly Answer[];
/** Every answer a student may have on a purpose, in the order the summary shows them. */
export const ANSWERS: Exhaustive<Answer, typeof answers> = answers;

const decisions = [
  "pending",
  "given",
  "refused",
  "withdrawn",
] as const satisfies readonly DecisionStatus[];
export const DECISIONS: Exhaustive<DecisionStatus, typeof decisions> = decisions;

const languages = ["en", "te"] as const satisfies readonly FormLanguage[];
export const FORM_LANGUAGES: Exhaustive<FormLanguage, typeof languages> = languages;

const relationships = ["mother", "father", "guardian"] as const satisfies readonly Relationship[];
export const RELATIONSHIPS: Exhaustive<Relationship, typeof relationships> = relationships;

export const CONSENT_READ = "consent.read";
export const CONSENT_RECORD = "consent.record";
export const SETTINGS_MANAGE = "tenant.settings.manage";
export const APAAR_READ = "apaar.consent.read";

export const CONSENT_KEYS = {
  all: ["staff", "consents"] as const,
  purposes: ["staff", "consents", "purposes"] as const,
  summary: (sectionId: string, purposeId: string) =>
    ["staff", "consents", "summary", sectionId, purposeId] as const,
  student: (studentId: string) => ["staff", "consents", "student", studentId] as const,
  view: (studentId: string) => ["staff", "consents", "view", studentId] as const,
};

export function isDecision(value: string): value is DecisionStatus {
  return (DECISIONS as readonly string[]).includes(value);
}

/**
 * What the family may decide next on one purpose (FR-DPDP-021): "withdrawn" only after
 * "given"; once anyone decided, never back to "pending" (the API answers 409 otherwise).
 */
export function nextDecisions(current: Answer): DecisionStatus[] {
  const decided = current === "given" || current === "refused" || current === "withdrawn";
  return DECISIONS.filter((status) => {
    if (status === "withdrawn") return current === "given";
    if (status === "pending") return !decided;
    return true;
  });
}

/** `W/"3"` for If-Match from a resource's `version` (the API's ETag form). */
export function ifMatch(version: number): string {
  return `W/"${version}"`;
}

/**
 * Print views of the consent forms (A4, the API's own strict CSP) open through the BFF in a
 * new tab. Ids and codes only, never names (no personal data in URLs).
 */
export function formsHref(
  target: { studentId: string } | { sectionId: string },
  options: { onlyUnanswered?: boolean; language?: FormLanguage } = {},
): string {
  const query = new URLSearchParams();
  if ("studentId" in target) query.set("student_id", target.studentId);
  else query.set("section_id", target.sectionId);
  if (options.onlyUnanswered) query.set("only_unanswered", "true");
  if (options.language) query.set("language", options.language);
  return `/bff/api/v1/consents/forms?${query.toString()}`;
}
