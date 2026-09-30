"use client";

import type { components } from "@schoolos/api-client";
import { useQuery, type Query } from "@tanstack/react-query";
import { z } from "zod";
import { AuthRedirectError } from "@/lib/bff/fetch";
import { ApiError, NotAvailableError, toLoadable, unwrap, useBffClient } from "@/lib/bff/query";
import type { Loadable } from "@/lib/loadable";

/**
 * Circulars, tasks and parent notices (M4; US-1601..US-1606). The AI reading of a circular and
 * an AI notice draft are suggestions only: tasks and approved notices exist after a person
 * confirms through these screens (invariant 9). The API checks every permission; these values
 * only decide what the screens offer.
 */

export const READ = "document.read";
export const REVIEW = "circular.review";
export const TASK_READ = "task.read";
export const TASK_ALL = "task.read_all";
export const TASK_MANAGE = "task.manage";
export const NOTICE_DRAFT = "notice.draft";
export const NOTICE_APPROVE = "notice.approve";

type S = components["schemas"];
export type Circular = S["CircularOut"];
export type CircularDetail = S["CircularDetail"];
export type Reading = S["ReadingOut"];
export type Suggestion = S["SuggestionOut"];
export type Citation = S["CitationOut"];
export type ReadingStatus = Circular["reading_status"];
export type Task = S["TaskOut"];
export type TaskStatus = Task["status"];
export type Assignee = S["AssigneeOut"];
export type Notice = S["NoticeOut"];
export type NoticeStatus = Notice["status"];

export interface Paged<T> {
  data: T[];
  next_cursor: string | null;
}

export const KEYS = {
  circulars: ["staff", "circulars"] as const,
  circularList: (cursor: string | undefined) =>
    ["staff", "circulars", "list", cursor ?? null] as const,
  circular: (id: string) => ["staff", "circulars", "detail", id] as const,
  tasks: ["staff", "tasks"] as const,
  taskList: (filters: TaskFilters) => ["staff", "tasks", "list", filters] as const,
  assignees: ["staff", "task-assignees"] as const,
  notices: ["staff", "notices"] as const,
  noticeList: ["staff", "notices", "list"] as const,
  notice: (id: string) => ["staff", "notices", "detail", id] as const,
} as const;

/** `W/"3"` for If-Match (the API's ETag format). */
export function ifMatch(version: number): string {
  return `W/"${version}"`;
}

export function isReadingBusy(status: ReadingStatus | undefined): boolean {
  return status === "queued" || status === "running";
}

/** True while the AI drafts a notice in the background (FR-NOTICE-003). */
export function isNoticeDrafting(status: NoticeStatus | undefined): boolean {
  return status === "drafting";
}

/** A notice still drafting after this long gets a "taking longer than usual" note. */
export const NOTICE_SLOW_MS = 90_000;

/**
 * Pause before asking again while a circular is read or a notice drafted or rendered: 2 s,
 * then 1.5 times longer each time, at most 15 s (tests shorten it).
 */
let pollDelay: (attempt: number) => number = (attempt) =>
  Math.min(2000 * 1.5 ** Math.max(attempt - 1, 0), 15_000);
export function setCircularsPollDelayForTesting(delay: ((attempt: number) => number) | null) {
  pollDelay = delay ?? ((attempt) => Math.min(2000 * 1.5 ** Math.max(attempt - 1, 0), 15_000));
}

function retry(count: number, error: unknown): boolean {
  return (
    count < 1 &&
    !(error instanceof NotAvailableError) &&
    !(error instanceof AuthRedirectError) &&
    !(error instanceof ApiError && error.status < 500)
  );
}

/** GET /circulars (newest first; polls while one is being read). */
export function useCirculars(
  cursor: string | undefined,
  enabled: boolean,
): Loadable<Paged<Circular>> {
  const api = useBffClient("staff");
  const query = useQuery({
    queryKey: KEYS.circularList(cursor),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/circulars", {
          params: { query: { limit: 50, ...(cursor ? { cursor } : {}) } },
        }),
      ),
    enabled,
    retry,
    refetchInterval: (q: Query<Paged<Circular>, Error, Paged<Circular>, readonly unknown[]>) =>
      q.state.status !== "error" && q.state.data?.data.some((c) => isReadingBusy(c.reading_status))
        ? pollDelay(q.state.dataUpdateCount)
        : false,
    refetchIntervalInBackground: false,
  });
  return toLoadable(query);
}

/** GET /circulars/{id} (polls while its reading is queued or running). */
export function useCircular(id: string, enabled: boolean): Loadable<CircularDetail> {
  const api = useBffClient("staff");
  const query = useQuery({
    queryKey: KEYS.circular(id),
    queryFn: () =>
      unwrap(api.GET("/api/v1/circulars/{document_id}", { params: { path: { document_id: id } } })),
    enabled,
    retry,
    refetchInterval: (q: Query<CircularDetail, Error, CircularDetail, readonly unknown[]>) =>
      q.state.status !== "error" && isReadingBusy(q.state.data?.reading_status)
        ? pollDelay(q.state.dataUpdateCount)
        : false,
    refetchIntervalInBackground: false,
  });
  return toLoadable(query);
}

export interface TaskFilters {
  view: "mine" | "all";
  status: TaskStatus | "active";
  due: "overdue" | "week" | "later" | "any";
}

/** GET /tasks with the screen's filters (soonest due first). */
export function useTasks(filters: TaskFilters, enabled: boolean): Loadable<Paged<Task>> {
  const api = useBffClient("staff");
  const query = useQuery({
    queryKey: KEYS.taskList(filters),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/tasks", {
          params: {
            query: {
              view: filters.view,
              limit: 100,
              ...(filters.status !== "active" ? { status: filters.status } : {}),
              ...(filters.due !== "any" ? { due: filters.due } : {}),
            },
          },
        }),
      ),
    enabled,
    retry,
  });
  return toLoadable(query);
}

/** GET /task-assignees (active staff; `task.manage` or `circular.review`). */
export function useAssignees(enabled: boolean): Loadable<Assignee[]> {
  const api = useBffClient("staff");
  const query = useQuery({
    queryKey: KEYS.assignees,
    queryFn: () => unwrap(api.GET("/api/v1/task-assignees")),
    enabled,
    retry,
    staleTime: 60_000,
  });
  return toLoadable(query);
}

/** GET /notices (newest first; polls, with backoff, while one is being drafted). */
export function useNotices(enabled: boolean): Loadable<Paged<Notice>> {
  const api = useBffClient("staff");
  const query = useQuery({
    queryKey: KEYS.noticeList,
    queryFn: () => unwrap(api.GET("/api/v1/notices", { params: { query: { limit: 50 } } })),
    enabled,
    retry,
    refetchInterval: (q: Query<Paged<Notice>, Error, Paged<Notice>, readonly unknown[]>) =>
      q.state.status !== "error" && q.state.data?.data.some((n) => isNoticeDrafting(n.status))
        ? pollDelay(q.state.dataUpdateCount)
        : false,
    refetchIntervalInBackground: false,
  });
  return toLoadable(query);
}

/** GET /notices/{id} (polls, with backoff, while it is drafted or its files are made). */
export function useNotice(id: string, enabled: boolean): Loadable<Notice> {
  const api = useBffClient("staff");
  const query = useQuery({
    queryKey: KEYS.notice(id),
    queryFn: () =>
      unwrap(api.GET("/api/v1/notices/{notice_id}", { params: { path: { notice_id: id } } })),
    enabled,
    retry,
    refetchInterval: (q: Query<Notice, Error, Notice, readonly unknown[]>) =>
      q.state.status !== "error" &&
      (isNoticeDrafting(q.state.data?.status) || q.state.data?.render_status === "queued")
        ? pollDelay(q.state.dataUpdateCount)
        : false,
    refetchIntervalInBackground: false,
  });
  return toLoadable(query);
}

/** Today in India (IST), as `YYYY-MM-DD`, for due and overdue labels. */
export function todayIst(now: Date = new Date()): string {
  const ist = new Date(now.getTime() + 330 * 60_000);
  return ist.toISOString().slice(0, 10);
}

export type DueState = "overdue" | "today" | "soon" | "later" | "closed";

/** How urgent a task is: overdue, due today, due within 2 days, later (closed = done/cancelled). */
export function dueState(
  task: Pick<Task, "due_on" | "status">,
  today: string = todayIst(),
): DueState {
  if (task.status === "done" || task.status === "cancelled") return "closed";
  if (task.due_on < today) return "overdue";
  if (task.due_on === today) return "today";
  const days = (Date.parse(task.due_on) - Date.parse(today)) / 86_400_000;
  return days <= 2 ? "soon" : "later";
}

/**
 * The approved notice as plain text for WhatsApp-style parent groups: English, then Telugu
 * (only while Telugu is switched on, ADR-0036), each title on its own line. No links, no
 * names: exactly what the school approved.
 */
export function noticeText(
  notice: Pick<Notice, "title_en" | "body_en" | "title_te" | "body_te">,
  { telugu = false }: { telugu?: boolean } = {},
) {
  const lines = [notice.title_en, notice.body_en];
  if (telugu) lines.push("", notice.title_te, notice.body_te);
  return lines
    .map((line) => line.trim())
    .join("\n")
    .trim();
}

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

/** Confirm a suggested deadline: an owner is required; title and due date may be changed. */
export const confirmSchema = z.object({
  owner_membership_id: z.string().uuid({ error: "required" }),
  title: z.string().trim().min(1, { error: "required" }).max(200, { error: "tooLong" }),
  due_on: z.string().regex(ISO_DATE, { error: "invalidDate" }),
});

/** Add a task by hand (task.manage). */
export const taskSchema = confirmSchema.extend({
  details: z.string().trim().max(2000, { error: "tooLong" }),
});

/** A notice's English texts: all a notice has while Telugu is switched off (ADR-0036). */
export const noticeEnglishSchema = z.object({
  title_en: z.string().trim().max(120, { error: "tooLong" }),
  body_en: z.string().trim().max(1500, { error: "tooLong" }),
});

/** A notice's four texts (both languages must be filled before approval, FR-NOTICE-004). */
export const noticeSchema = noticeEnglishSchema.extend({
  title_te: z.string().trim().max(120, { error: "tooLong" }),
  body_te: z.string().trim().max(1500, { error: "tooLong" }),
});

/**
 * True when every title and body is filled (the API refuses approval otherwise): English, and
 * Telugu too only while Telugu is switched on (ADR-0036).
 */
export function noticeComplete(
  notice: Pick<Notice, "title_en" | "body_en"> & Partial<Pick<Notice, "title_te" | "body_te">>,
  { telugu = false }: { telugu?: boolean } = {},
): boolean {
  const texts = telugu
    ? [notice.title_en, notice.body_en, notice.title_te ?? "", notice.body_te ?? ""]
    : [notice.title_en, notice.body_en];
  return texts.every((value) => value.trim().length > 0);
}

/** Phone numbers, emails and 12-digit numbers never go into a parent notice (FR-NOTICE-002). */
export function looksPersonal(text: string): boolean {
  return (
    /[\w.%+-]+@[\w-]+(\.[\w-]+)+/.test(text) ||
    /(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?!\d)/.test(text) ||
    /(?<!\d)\d{4}[\s-]?\d{4}[\s-]?\d{4}(?!\d)/.test(text)
  );
}

/**
 * Open a short-lived download link at once. The presigned URL is never kept in state,
 * storage or logs. Tests replace the opener (jsdom cannot navigate).
 */
let openDownload: (url: string) => void = (url) => window.location.assign(url);
export function setNoticeDownloadOpenerForTesting(opener: ((url: string) => void) | null) {
  openDownload = opener ?? ((url) => window.location.assign(url));
}
export function startDownload(url: string): void {
  openDownload(url);
}
