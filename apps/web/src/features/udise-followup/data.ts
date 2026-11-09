import type { ApiClient } from "@schoolos/api-client";
import { unwrap } from "@/lib/bff/query";
import * as z from "@/lib/zod";

/**
 * Follow-ups on fixes outside the school (FO-01) and the UDISE+ to-do list (FO-03),
 * FR-UDI-021..FR-UDI-040. The routes are new in this release, so the generated client does not
 * know them yet: the shapes below mirror `app/dq/followup_schemas.py` and every answer is
 * checked with zod. After the lead regenerates `packages/api-client`, swap `loose()` for the
 * typed client.
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

const studentRef = z.object({
  id: z.string(),
  display_name: z.string().nullable(),
  admission_no: z.string().nullable(),
});

const countsSchema = z.object({
  open: z.number(),
  not_started: z.number(),
  waiting: z.number(),
  overdue: z.number(),
  update_reported: z.number(),
});
export type FollowupCounts = z.infer<typeof countsSchema>;

const itemSchema = z.object({
  student: studentRef,
  section_id: z.string().nullable(),
  readiness_status: z.string(),
  fix_owner: z.enum(FOLLOWUP_OWNERS),
  state: z.enum(FOLLOWUP_STATES),
  overdue_reason: z.enum(["no_contact", "promise_passed"]).nullable(),
  last_event: z.enum(FOLLOWUP_EVENTS).nullable(),
  last_on: z.string().nullable(),
  promised_on: z.string().nullable(),
  steps: z.number(),
});
export type FollowupItem = z.infer<typeof itemSchema>;

const listSchema = z.object({
  profile_key: z.string(),
  today: z.string(),
  overdue_after_days: z.number(),
  totals: z.array(z.object({ fix_owner: z.enum(FOLLOWUP_OWNERS), counts: countsSchema })),
  items: z.array(itemSchema),
});
export type FollowupList = z.infer<typeof listSchema>;

const stepSchema = z.object({
  id: z.string(),
  fix_owner: z.enum(FOLLOWUP_OWNERS),
  event: z.enum(FOLLOWUP_EVENTS),
  channel: z.enum(FOLLOWUP_CHANNELS).nullable(),
  occurred_on: z.string(),
  promised_on: z.string().nullable(),
  recorded_at: z.string(),
});
export type FollowupStep = z.infer<typeof stepSchema>;

const historySchema = z.object({
  student_id: z.string(),
  profile_key: z.string(),
  open: z.array(itemSchema),
  steps: z.array(stepSchema),
});
export type FollowupHistory = z.infer<typeof historySchema>;

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

/** The body the API takes: no empty channel or dates. */
export function stepBody(input: StepInput): Record<string, unknown> {
  const body: Record<string, unknown> = {
    student_ids: input.studentIds,
    fix_owner: input.owner,
    event: input.event,
  };
  if (input.channel && CHANNELS_BY_EVENT[input.event].length > 0) body.channel = input.channel;
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

const todoItemSchema = z.object({
  item_key: z.string(),
  kind: z.enum(TODO_KINDS),
  action: z.enum(TODO_ACTIONS),
  student: studentRef.nullable(),
  class_section: z.string().nullable(),
  event_on: z.string(),
  certificate_serial: z.string().nullable(),
  progression: z
    .object({ promoted: z.number(), held_back: z.number(), graduated: z.number() })
    .nullable(),
  state: z.enum(TODO_STATES),
  overdue: z.boolean(),
  marked_at: z.string().nullable(),
  version: z.number().nullable(),
});
export type TodoItem = z.infer<typeof todoItemSchema>;

const todoListSchema = z.object({
  verified: z.boolean(),
  source: z.array(z.string()),
  since: z.string().nullable(),
  today: z.string(),
  overdue_after_days: z.number(),
  totals: z.object({
    open: z.number(),
    overdue: z.number(),
    done: z.number(),
    not_needed: z.number(),
  }),
  items: z.array(todoItemSchema),
});
export type TodoList = z.infer<typeof todoListSchema>;

type Result = { data?: unknown; error?: unknown; response: Response };
interface LooseClient {
  GET(path: string, init?: { params?: { query?: Record<string, unknown> } }): Promise<Result>;
  POST(path: string, init?: { body?: unknown; headers?: Record<string, string> }): Promise<Result>;
  PUT(path: string, init?: { body?: unknown; headers?: Record<string, string> }): Promise<Result>;
  DELETE(path: string, init?: { headers?: Record<string, string> }): Promise<Result>;
}

function loose(api: ApiClient): LooseClient {
  return api as unknown as LooseClient;
}

function base(profile: string): string {
  return `/api/v1/dq/readiness/${encodeURIComponent(profile)}/follow-ups`;
}

export function ifMatch(version: number): string {
  return `W/"${version}"`;
}

export async function fetchFollowups(
  api: ApiClient,
  profile: string,
  sectionId: string | null,
): Promise<FollowupList> {
  const query = sectionId ? { section_id: sectionId } : {};
  return listSchema.parse(await unwrap(loose(api).GET(base(profile), { params: { query } })));
}

export async function fetchFollowupHistory(
  api: ApiClient,
  profile: string,
  studentId: string,
): Promise<FollowupHistory> {
  const path = `${base(profile)}/${encodeURIComponent(studentId)}`;
  return historySchema.parse(await unwrap(loose(api).GET(path)));
}

export async function recordStep(
  api: ApiClient,
  profile: string,
  input: StepInput,
  idempotencyKey: string,
): Promise<{ recorded: number }> {
  const body = await unwrap(
    loose(api).POST(base(profile), {
      body: stepBody(input),
      headers: { "Idempotency-Key": idempotencyKey },
    }),
  );
  return z.object({ recorded: z.number() }).parse(body);
}

export async function fetchTodo(api: ApiClient): Promise<TodoList> {
  return todoListSchema.parse(await unwrap(loose(api).GET("/api/v1/dq/udise-todo")));
}

export async function markTodo(
  api: ApiClient,
  item: TodoItem,
  state: Exclude<TodoState, "open">,
): Promise<TodoItem> {
  const path = `/api/v1/dq/udise-todo/${encodeURIComponent(item.item_key)}`;
  const headers: Record<string, string> =
    item.version !== null ? { "If-Match": ifMatch(item.version) } : {};
  return todoItemSchema.parse(await unwrap(loose(api).PUT(path, { body: { state }, headers })));
}

export async function reopenTodo(api: ApiClient, item: TodoItem): Promise<void> {
  if (item.version === null) return;
  const path = `/api/v1/dq/udise-todo/${encodeURIComponent(item.item_key)}`;
  await unwrap(loose(api).DELETE(path, { headers: { "If-Match": ifMatch(item.version) } }));
}

/** Overdue and open counts of one fix owner ("Waiting on parents: 18 (7 overdue)"). */
export function ownerCounts(list: FollowupList | undefined, owner: FollowupOwner) {
  return list?.totals.find((t) => t.fix_owner === owner)?.counts;
}
