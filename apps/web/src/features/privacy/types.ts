import type { components, paths } from "@schoolos/api-client";

/**
 * Privacy requests and complaints, notices and the school's privacy contact (R3-1A;
 * FR-DPDP-001..018, ADR-0048), all from the generated OpenAPI schema. Runtime lists are
 * checked against the generated unions so they fail to compile if the API adds a value.
 */
type Schemas = components["schemas"];

export type PrivacyRequest = Schemas["PrivacyRequestOut"];
export type PrivacyRequestRow = Schemas["PrivacyRequestRowOut"];
export type PrivacyRequestCreate = Schemas["PrivacyRequestCreate"];
export type PrivacyRequestPatch = Schemas["PrivacyRequestPatch"];
export type PrivacyEvent = Schemas["PrivacyEventOut"];
export type PrivacyLink = Schemas["PrivacyLinkOut"];
export type PrivacyFile = Schemas["PrivacyModuleFileOut"];
export type ErasurePlanRow = Schemas["PrivacyErasurePlanRowOut"];
export type ErasureResult = Schemas["PrivacyErasureResultOut"];
export type PrivacyTemplate = Schemas["PrivacyTemplateOut"];
export type PrivacyNotice = Schemas["PrivacyNoticeOut"];
export type PrivacyNoticeIn = Schemas["PrivacyNoticeIn"];
export type RecordClass = Schemas["PrivacyRecordClassOut"];
export type PrivacyContact = Schemas["PrivacyContactOut"];

export type RequestKind = PrivacyRequest["kind"];
export type RequestStatus = PrivacyRequest["status"];
export type Relationship = PrivacyRequest["requester_relationship"];
export type VerificationMethod = NonNullable<PrivacyRequest["verification_method"]>;
export type ErasureOutcome = ErasurePlanRow["outcome"];
export type TemplateKey = PrivacyTemplate["key"];
export type NoticeChannel = PrivacyNoticeIn["channel"];
/** The letter keys of `GET /privacy/requests/{request_id}/letters/{letter}` (generated). */
export type Letter =
  paths["/api/v1/privacy/requests/{request_id}/letters/{letter}"]["get"]["parameters"]["path"]["letter"];
export type RecordErasure = RecordClass["erasure"];

type Exhaustive<T extends string, L extends readonly T[]> = [T] extends [L[number]] ? L : never;

const kinds = [
  "access",
  "correction",
  "erasure",
  "nomination",
  "consent_withdrawal",
  "grievance",
  "other",
] as const satisfies readonly RequestKind[];
export const REQUEST_KINDS: Exhaustive<RequestKind, typeof kinds> = kinds;

const statuses = [
  "received",
  "in_progress",
  "waiting_for_requester",
  "decided",
  "closed",
  "withdrawn",
] as const satisfies readonly RequestStatus[];
export const REQUEST_STATUSES: Exhaustive<RequestStatus, typeof statuses> = statuses;

const relationships = [
  "self",
  "father",
  "mother",
  "guardian",
  "nominee",
  "other",
] as const satisfies readonly Relationship[];
export const RELATIONSHIPS: Exhaustive<Relationship, typeof relationships> = relationships;

const methods = [
  "in_person_document",
  "guardian_phone_on_record",
  "staff_sign_in",
] as const satisfies readonly VerificationMethod[];
export const VERIFICATION_METHODS: Exhaustive<VerificationMethod, typeof methods> = methods;

const channels = [
  "paper",
  "school_whatsapp_group",
  "sos_notice",
  "email",
  "other",
] as const satisfies readonly NoticeChannel[];
export const NOTICE_CHANNELS: Exhaustive<NoticeChannel, typeof channels> = channels;

/** The statuses a person may set by hand (PATCH); the others follow from actions. */
export const SETTABLE_STATUSES = ["in_progress", "waiting_for_requester"] as const;

export const OPEN_STATUSES: readonly RequestStatus[] = [
  "received",
  "in_progress",
  "waiting_for_requester",
  "decided",
];
export const CLOSED_STATUSES: readonly RequestStatus[] = ["closed", "withdrawn"];

export const PRIVACY_READ = "privacy.read";
export const PRIVACY_MANAGE = "privacy.request.manage";
export const PRIVACY_DECIDE = "privacy.request.decide";
export const SETTINGS_MANAGE = "tenant.settings.manage";

/** Kinds whose closing is a decision (owner or principal, not the person who logged it). */
export const DECIDED_KINDS: readonly RequestKind[] = ["access", "erasure", "grievance"];

export const PRIVACY_KEYS = {
  all: ["staff", "privacy"] as const,
  list: (tab: string, kind: string) => ["staff", "privacy", "requests", tab, kind] as const,
  request: (id: string) => ["staff", "privacy", "request", id] as const,
  erasurePlan: (id: string) => ["staff", "privacy", "request", id, "erasure-plan"] as const,
  templates: ["staff", "privacy", "templates"] as const,
  notices: ["staff", "privacy", "notices"] as const,
  recordClasses: ["staff", "privacy", "record-classes"] as const,
  file: (id: string) => ["staff", "privacy", "file", id] as const,
};

export type RequestTab = "open" | "overdue" | "closed";
export const REQUEST_TABS: readonly RequestTab[] = ["open", "overdue", "closed"];

export function parseTab(value: string | string[] | undefined): RequestTab {
  const text = Array.isArray(value) ? value[0] : value;
  return REQUEST_TABS.find((tab) => tab === text) ?? "open";
}

export function isRequestKind(value: string): value is RequestKind {
  return (REQUEST_KINDS as readonly string[]).includes(value);
}

/** The search body for one tab (POST: nothing about the person goes in a URL). */
export function searchBody(tab: RequestTab, kind: RequestKind | "") {
  return {
    limit: 50,
    overdue: tab === "overdue",
    status: [...(tab === "closed" ? CLOSED_STATUSES : OPEN_STATUSES)],
    ...(kind ? { kind: [kind] } : {}),
  };
}

/** The answer letter for a kind; `other` has only the acknowledgement. */
export function answerLetter(kind: RequestKind): Letter | null {
  switch (kind) {
    case "access":
      return "access";
    case "correction":
      return "correction";
    case "erasure":
      return "erasure";
    case "nomination":
      return "nomination";
    case "consent_withdrawal":
      return "consent_withdrawn";
    case "grievance":
      return "grievance";
    default:
      return null;
  }
}

/** Print views (A4, the API's own strict CSP) open through the BFF in a new tab. */
export function letterHref(requestId: string, letter: Letter): string {
  return `/bff/api/v1/privacy/requests/${encodeURIComponent(requestId)}/letters/${letter}`;
}

export function templatePreviewHref(key: TemplateKey): string {
  return `/bff/api/v1/privacy/templates/${key}/preview`;
}

/** Whether the request is still open (closed and withdrawn are final). */
export function isOpen(status: RequestStatus): boolean {
  return !CLOSED_STATUSES.includes(status);
}

/** The total count of the erasable classes chosen (for the consequence line). */
export function chosenCount(rows: readonly ErasurePlanRow[], chosen: readonly string[]): number {
  return rows
    .filter((row) => row.outcome === "erasable" && chosen.includes(row.class_key))
    .reduce((total, row) => total + (row.count ?? 0), 0);
}
