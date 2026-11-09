import type { ApiClient, components } from "@schoolos/api-client";
import { unwrap } from "@/lib/bff/query";
import * as z from "@/lib/zod";

/**
 * Follow-ups on fixes outside the school (FO-01) and the UDISE+ to-do list (FO-03),
 * FR-UDI-021..FR-UDI-040.
 */

export const READINESS_MANAGE = "dq.readiness.manage";
export const FOLLOWUP_KEYS = {
  all: ["staff", "dq", "follow-ups"] as const,
  list: (profile: string, section: string | null) =>
    ["staff", "dq", "follow-ups", profile, section ?? "all"] as const,
  history: (profile: string, student: string) =>
    ["staff", "dq", "follow-ups", profile, "student", student] as const,
};
export const TODO_KEYS = { list: ["staff", "dq", "udise-todo"] as const };

export const FOLLOWUP_OWNERS = ["parent_aadhaar", "school_udise"] as const;
export type FollowupOwner = (typeof FOLLOWUP_OWNERS)[number];
export const FOLLOWUP_EVENTS = [
  "slip_given",
  "parent_contacted",
  "promised_by",
  "update_reported",
  "request_sent",
  "followed_up",
] as const;
export type FollowupEvent = (typeof FOLLOWUP_EVENTS)[number];
export const FOLLOWUP_CHANNELS = ["in_person", "phone", "whatsapp", "letter", "email"] as const;
export type FollowupChannel = (typeof FOLLOWUP_CHANNELS)[number];
export const FOLLOWUP_STATES = ["overdue", "not_started", "update_reported", "waiting"] as const;
export type FollowupState = (typeof FOLLOWUP_STATES)[number];

/** Which steps each fix owner takes, and the channels a step names (udise_followup.yaml). */
export const EVENTS_BY_OWNER: Record<FollowupOwner, readonly FollowupEvent[]> = {
  parent_aadhaar: ["slip_given", "parent_contacted", "promised_by", "update_reported"],
  school_udise: ["request_sent", "followed_up", "update_reported"],
};
export const CHANNELS_BY_EVENT: Record<FollowupEvent, readonly FollowupChannel[]> = {
  slip_given: [],
  parent_contacted: ["in_person", "phone", "whatsapp", "letter"],
  promised_by: [],
  update_reported: [],
  request_sent: ["in_person", "email", "letter", "phone"],
  followed_up: ["in_person", "email", "letter", "phone"],
};

type S = components["schemas"];
export type FollowupCounts = S["FollowupCounts"];
export type FollowupItem = S["FollowupItemOut"];
export type FollowupList = S["FollowupListOut"];
export type FollowupStep = S["FollowupStepOut"];
export type FollowupHistory = S["FollowupHistoryOut"];

/** The record dialog's fields (the API checks them again: FR-UDI-027). */
export const stepFormSchema = z.object({
  event: z.enum(FOLLOWUP_EVENTS, { error: "chooseOption" }),
  channel: z.string().optional(),
  occurred_on: z.string().min(1, { error: "required" }),
  promised_on: z.string().optional(),
});
export type StepForm = z.output<typeof stepFormSchema>;

export interface StepInput {
  studentIds: string[];
  owner: FollowupOwner;
  event: FollowupEvent;
  channel?: string | undefined;
  occurredOn?: string | undefined;
  promisedOn?: string | undefined;
}

function isChannel(value: string | undefined): value is FollowupChannel {
  return (FOLLOWUP_CHANNELS as readonly string[]).includes(value ?? "");
}

/** The body the API takes: no empty channel or dates. */
export function stepBody(input: StepInput): S["FollowupIn"] {
  const body: S["FollowupIn"] = {
    student_ids: input.studentIds,
    fix_owner: input.owner,
    event: input.event,
  };
  if (isChannel(input.channel) && CHANNELS_BY_EVENT[input.event].length > 0) {
    body.channel = input.channel;
  }
  if (input.occurredOn) body.occurred_on = input.occurredOn;
  if (input.event === "promised_by" && input.promisedOn) body.promised_on = input.promisedOn;
  return body;
}

// --- UDISE+ to-do (FO-03) ---------------------------------------------------------------------

export const TODO_KINDS = ["transfer_out", "left_without_tc", "admission", "progression"] as const;
export const TODO_ACTIONS = [
  "transfer_out",
  "left_without_tc",
  "admission_without_pen",
  "admission_with_pen",
  "progression",
] as const;
export type TodoAction = (typeof TODO_ACTIONS)[number];
export const TODO_STATES = ["open", "done", "not_needed"] as const;
export type TodoState = (typeof TODO_STATES)[number];

export type TodoItem = S["MovementItemOut"];
export type TodoList = S["MovementListOut"];

export function ifMatch(version: number): string {
  return `W/"${version}"`;
}

export async function fetchFollowups(
  api: ApiClient,
  profile: string,
  sectionId: string | null,
): Promise<FollowupList> {
  const query = sectionId ? { section_id: sectionId } : {};
  return unwrap(
    api.GET("/api/v1/dq/readiness/{profile_key}/follow-ups", {
      params: { path: { profile_key: profile }, query },
    }),
  );
}

export async function fetchFollowupHistory(
  api: ApiClient,
  profile: string,
  studentId: string,
): Promise<FollowupHistory> {
  return unwrap(
    api.GET("/api/v1/dq/readiness/{profile_key}/follow-ups/{student_id}", {
      params: { path: { profile_key: profile, student_id: studentId } },
    }),
  );
}

export async function recordStep(
  api: ApiClient,
  profile: string,
  input: StepInput,
  idempotencyKey: string,
): Promise<{ recorded: number }> {
  return unwrap(
    api.POST("/api/v1/dq/readiness/{profile_key}/follow-ups", {
      params: { path: { profile_key: profile } },
      body: stepBody(input),
      headers: { "Idempotency-Key": idempotencyKey },
    }),
  );
}

export async function fetchTodo(api: ApiClient): Promise<TodoList> {
  return unwrap(api.GET("/api/v1/dq/udise-todo"));
}

export async function markTodo(
  api: ApiClient,
  item: TodoItem,
  state: Exclude<TodoState, "open">,
): Promise<TodoItem> {
  const headers: Record<string, string> =
    item.version !== null ? { "If-Match": ifMatch(item.version) } : {};
  return unwrap(
    api.PUT("/api/v1/dq/udise-todo/{item_key}", {
      params: { path: { item_key: item.item_key } },
      body: { state },
      headers,
    }),
  );
}

export async function reopenTodo(api: ApiClient, item: TodoItem): Promise<void> {
  if (item.version === null) return;
  await unwrap(
    api.DELETE("/api/v1/dq/udise-todo/{item_key}", {
      params: { path: { item_key: item.item_key } },
      headers: { "If-Match": ifMatch(item.version) },
    }),
  );
}

/** Overdue and open counts of one fix owner ("Waiting on parents: 18 (7 overdue)"). */
export function ownerCounts(list: FollowupList | undefined, owner: FollowupOwner) {
  return list?.totals.find((t) => t.fix_owner === owner)?.counts;
}
