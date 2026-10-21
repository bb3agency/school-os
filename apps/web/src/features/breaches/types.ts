import type { components } from "@schoolos/api-client";

/**
 * Breach register (ADR-0050; FR-DPDP-030..035), all types from the generated OpenAPI schema.
 * Runtime lists are checked against the generated unions so they fail to compile if the API
 * adds a value.
 */
type Schemas = components["schemas"];

export type Breach = Schemas["BreachOut"];
export type BreachDetail = Schemas["BreachDetailOut"];
export type BreachCreate = Schemas["BreachCreate"];
export type BreachUpdate = Schemas["BreachUpdate"];
export type Duty = Schemas["DutyOut"];
export type DutyKey = Duty["key"];
export type DutyIn = Schemas["DutyIn"];
export type DutyMethod = DutyIn["method"];
export type NextDuty = Schemas["NextDutyOut"];
export type BreachCategory = BreachCreate["categories"][number];
export type BreachStatus = Breach["status"];
export type BreachOrigin = Breach["origin"];
export type DraftKind = Schemas["DraftOut"]["draft"];

type Exhaustive<T extends string, L extends readonly T[]> = [T] extends [L[number]] ? L : never;

const categories = [
  "identity",
  "contact",
  "aadhaar_partial",
  "academic",
  "attendance",
  "financial",
  "documents",
  "insights",
  "staff",
  "other",
] as const satisfies readonly BreachCategory[];
export const BREACH_CATEGORIES: Exhaustive<BreachCategory, typeof categories> = categories;

const duties = [
  "board_notice",
  "board_report",
  "people_notified",
] as const satisfies readonly DutyKey[];
/** The three duties in the order they fall due (ADR-0050). */
export const DUTY_KEYS: Exhaustive<DutyKey, typeof duties> = duties;

const methods = [
  "email",
  "portal",
  "letter",
  "phone",
  "in_person",
  "whatsapp",
  "sos_notice",
  "other",
] as const satisfies readonly DutyMethod[];
export const DUTY_METHODS: Exhaustive<DutyMethod, typeof methods> = methods;

const statuses = ["open", "contained", "closed"] as const satisfies readonly BreachStatus[];
export const BREACH_STATUSES: Exhaustive<BreachStatus, typeof statuses> = statuses;

const drafts = ["family_notice", "board_outline"] as const satisfies readonly DraftKind[];
export const DRAFT_KINDS: Exhaustive<DraftKind, typeof drafts> = drafts;

export const BREACH_READ = "privacy.breach.read";
export const BREACH_MANAGE = "privacy.breach.manage";
export const NOTICE_DRAFT = "notice.draft";

export const BREACH_KEYS = {
  all: ["staff", "breaches"] as const,
  list: ["staff", "breaches", "list"] as const,
  detail: (id: string) => ["staff", "breaches", "detail", id] as const,
  draft: (id: string, draft: DraftKind) => ["staff", "breaches", "draft", id, draft] as const,
};

export function isBreachCategory(value: string): value is BreachCategory {
  return (BREACH_CATEGORIES as readonly string[]).includes(value);
}

export function isDutyMethod(value: string): value is DutyMethod {
  return (DUTY_METHODS as readonly string[]).includes(value);
}

/** `W/"3"` for If-Match from a resource's `version` (the API's ETag form). */
export function ifMatch(version: number): string {
  return `W/"${version}"`;
}

const HOUR_MS = 3_600_000;

/**
 * A duty's clock in words (docs/17 §5.10: "Board report due in 41 h", "Board notice overdue
 * by 6 h"). Whole hours, rounded down; under an hour left reads "due within the hour".
 * `noClock`: "without delay" duties carry no fixed time (people affected are told as soon as
 * possible).
 */
export type DutyClock =
  | { state: "done"; doneAt: string }
  | { state: "noClock" }
  | { state: "due"; hours: number }
  | { state: "dueSoon" }
  | { state: "overdue"; hours: number };

export function dutyClock(
  duty: Pick<Duty, "due_at" | "done_at">,
  now: Date = new Date(),
): DutyClock {
  if (duty.done_at) return { state: "done", doneAt: duty.done_at };
  if (!duty.due_at) return { state: "noClock" };
  const left = new Date(duty.due_at).getTime() - now.getTime();
  if (Number.isNaN(left)) return { state: "noClock" };
  if (left < 0) return { state: "overdue", hours: Math.max(1, Math.floor(-left / HOUR_MS)) };
  const hours = Math.floor(left / HOUR_MS);
  return hours === 0 ? { state: "dueSoon" } : { state: "due", hours };
}

/** The register's "next duty" from the API's whole hours (negative when overdue). */
export function nextDutyClock(next: NextDuty): DutyClock {
  if (next.hours_left === null) return { state: "noClock" };
  if (next.hours_left < 0) return { state: "overdue", hours: -next.hours_left };
  return next.hours_left === 0 ? { state: "dueSoon" } : { state: "due", hours: next.hours_left };
}

/**
 * Open processor notices first (SchoolOS told the school about a breach on its side; they
 * arrive as urgent notifications and stay on top), then the API's order (open breaches by
 * their next clock, then closed ones).
 */
export function registerOrder(rows: readonly Breach[]): Breach[] {
  const urgent = (row: Breach) => row.origin === "processor_notice" && row.status !== "closed";
  return [...rows.filter(urgent), ...rows.filter((row) => !urgent(row))];
}
