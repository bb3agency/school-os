"use client";

/**
 * ===========================================================================================
 * TEMPORARY CONTRACT MODULE: replace with the generated `@schoolos/api-client` types at merge.
 * ===========================================================================================
 *
 * The Ask conversations, memory and new SSE events (FR-KB-005, FR-KB-008, FR-KB-012) are being
 * built by the API in parallel. Until `apps/api/openapi.json` carries them, this file holds:
 *
 * - zod schemas and TypeScript types for the NEW endpoints (conversations, memories, memory
 *   settings) and the NEW `POST /knowledge/ask` body fields (`conversation_id`,
 *   `regenerate_of`, `edit_of`);
 * - the NEW SSE event payloads (`status` incl. `searching_chats`, `followups`, `memory`,
 *   `meta.conversation_id`, `meta.title`, optional `summarized`, `cached` and `cached_from`);
 * - the NEW source kind `sos://conversation/{id}#q{query_id}` (your own past chat; answer.ts);
 * - `useAskContractApi()`: the calls, sent through the same BFF fetch as the generated client
 *   (`createBffFetch`: CSRF header, Accept-Language, 401 → sign-in, 428 → step-up), with every
 *   response parsed by zod (unknown fields ignored, a malformed body is an error).
 *
 * At merge: swap these types for `components["schemas"][…]`, move the calls onto the typed
 * client (`api.GET("/api/v1/knowledge/conversations")` …) and delete this file. Existing
 * endpoints (feedback, verified answers, search) already use the generated client (data.ts).
 */

import type { Problem } from "@schoolos/api-client";
import { useLocale } from "next-intl";
import { useMemo } from "react";
import { z } from "zod";
import { createBffFetch } from "@/lib/bff/fetch";
import { ApiError, NotAvailableError } from "@/lib/bff/query";

// --- conversations -------------------------------------------------------------------------

export const conversationSummarySchema = z.object({
  id: z.string(),
  title: z.string().nullable().catch(null),
  pinned: z.boolean().catch(false),
  created_at: z.string(),
  updated_at: z.string(),
  message_count: z.number().int().nonnegative().catch(0),
  version: z.number().int().nonnegative(),
});
export type ConversationSummary = z.infer<typeof conversationSummarySchema>;

export const conversationPageSchema = z.object({
  data: z.array(conversationSummarySchema),
  next_cursor: z.string().nullable().catch(null),
});
export type ConversationPage = z.infer<typeof conversationPageSchema>;

export const MESSAGE_STATUSES = [
  "answered",
  "not_found",
  "refused",
  "search_only",
  "error",
  "cancelled",
] as const;
export type MessageStatus = (typeof MESSAGE_STATUSES)[number];

export const messageCitationSchema = z.object({
  index: z.number().int().positive(),
  source: z.string(),
  title: z.string().nullable().catch(null),
  snippet: z.string().nullable().catch(null),
  withheld: z.boolean().optional().catch(undefined),
});
export type MessageCitation = z.infer<typeof messageCitationSchema>;

export const conversationMessageSchema = z.object({
  query_id: z.string(),
  question: z.string(),
  answer: z.string().nullable().catch(null),
  status: z.enum(MESSAGE_STATUSES).catch("error"),
  mode: z.enum(["full", "search_only"]).catch("full"),
  language: z.enum(["en", "te", "mixed"]).nullable().catch(null),
  citations: z.array(messageCitationSchema).catch([]),
  feedback: z.enum(["helpful", "not_helpful"]).nullable().catch(null),
  followups: z.array(z.string()).catch([]),
  created_at: z.string(),
  superseded: z.boolean().catch(false),
  /** Optional: earlier turns were summarised before this answer (context management). */
  summarized: z.boolean().optional().catch(undefined),
});
export type ConversationMessage = z.infer<typeof conversationMessageSchema>;

export const conversationDetailSchema = conversationSummarySchema.extend({
  messages: z.array(conversationMessageSchema).catch([]),
});
export type ConversationDetail = z.infer<typeof conversationDetailSchema>;

export interface ConversationPatch {
  title?: string;
  pinned?: boolean;
}

// --- ask body and new SSE events ------------------------------------------------------------

/** `POST /knowledge/ask` body (the old `session_id` is still accepted but no longer sent). */
export interface AskBody {
  question: string;
  conversation_id?: string;
  regenerate_of?: string;
  edit_of?: string;
}

export const ASK_STEPS = [
  "understanding",
  "searching_documents",
  "searching_chats",
  "reading_records",
  "writing",
] as const;
export type AskStep = (typeof ASK_STEPS)[number];

export const statusEventSchema = z.object({
  step: z.enum(ASK_STEPS),
  tool: z.string().optional().catch(undefined),
  count: z.number().int().nonnegative().optional().catch(undefined),
});
export type StatusEvent = z.infer<typeof statusEventSchema>;

export const followupsEventSchema = z.object({
  questions: z.array(z.string()),
});

/** Extra `meta` fields: the conversation (created by this question when it is new). */
export const metaExtrasSchema = z.object({
  conversation_id: z.string().optional().catch(undefined),
  title: z.string().nullable().optional().catch(undefined),
  /** Optional (context management): earlier turns were summarised for this answer. */
  summarized: z.boolean().optional().catch(undefined),
  /** The answer was reused from an identical earlier question on the same documents. */
  cached: z.boolean().optional().catch(undefined),
  /** …that question's `query_id`. */
  cached_from: z.string().optional().catch(undefined),
});

export const memoryEventSchema = z.object({
  action: z.enum(["saved", "suggested"]),
  item_id: z.string(),
  text: z.string(),
});
export type MemoryEvent = z.infer<typeof memoryEventSchema>;

// --- memory ----------------------------------------------------------------------------------

export const memoryItemSchema = z.object({
  id: z.string(),
  text: z.string(),
  source: z.enum(["explicit", "suggested"]).catch("explicit"),
  status: z.enum(["saved", "pending"]).catch("saved"),
  created_at: z.string(),
  updated_at: z.string(),
  version: z.number().int().nonnegative(),
});
export type MemoryItem = z.infer<typeof memoryItemSchema>;

export const memoryListSchema = z.array(memoryItemSchema);

export const memorySettingsSchema = z.object({
  enabled: z.boolean(),
  school_enabled: z.boolean(),
});
export type MemorySettings = z.infer<typeof memorySettingsSchema>;

/** The API refuses memory about other people or Aadhaar-like numbers with this 422 code. */
export const MEMORY_NOT_ALLOWED = "memory_not_allowed";

// --- calls -----------------------------------------------------------------------------------

/** `W/"3"` for If-Match from a resource's `version` (the API's ETag format). */
function ifMatch(version: number): string {
  return `W/"${version}"`;
}

function bffUrl(path: string): string {
  const origin = typeof window === "undefined" ? "http://localhost" : window.location.origin;
  return `${origin}/bff${path}`;
}

async function problemOf(response: Response): Promise<Partial<Problem>> {
  try {
    const body: unknown = await response.clone().json();
    return body && typeof body === "object" ? (body as Partial<Problem>) : {};
  } catch {
    return {};
  }
}

export interface ContractApi {
  /** Raw response of `POST /knowledge/ask` (an SSE stream when it is ok). */
  ask: (body: AskBody, signal: AbortSignal) => Promise<Response>;
  listConversations: (cursor?: string, limit?: number) => Promise<ConversationPage>;
  getConversation: (id: string) => Promise<ConversationDetail>;
  patchConversation: (
    id: string,
    version: number,
    patch: ConversationPatch,
  ) => Promise<ConversationSummary | null>;
  deleteConversation: (id: string) => Promise<void>;
  listMemories: () => Promise<MemoryItem[]>;
  addMemory: (text: string) => Promise<MemoryItem | null>;
  editMemory: (item: MemoryItem, text: string) => Promise<MemoryItem | null>;
  deleteMemory: (id: string) => Promise<void>;
  confirmMemory: (id: string) => Promise<MemoryItem | null>;
  forgetAllMemories: () => Promise<void>;
  getMemorySettings: () => Promise<MemorySettings>;
  putMemorySettings: (enabled: boolean) => Promise<MemorySettings>;
}

/**
 * The new Ask calls through the BFF (see the header). Errors are the same `ApiError` /
 * `NotAvailableError` the generated client's `unwrap` throws, so screens show them with
 * `ApiErrorAlert` as usual.
 */
export function useAskContractApi(): ContractApi {
  const locale = useLocale();
  return useMemo(() => {
    const send = createBffFetch({ kind: "staff", locale });

    async function call(
      method: string,
      path: string,
      init: { body?: unknown; headers?: Record<string, string>; signal?: AbortSignal } = {},
    ): Promise<Response> {
      const headers = new Headers(init.headers);
      if (init.body !== undefined) headers.set("content-type", "application/json");
      if (!headers.has("accept")) headers.set("accept", "application/json");
      return send(
        new Request(bffUrl(path), {
          method,
          headers,
          ...(init.body !== undefined ? { body: JSON.stringify(init.body) } : {}),
          ...(init.signal ? { signal: init.signal } : {}),
        }),
      );
    }

    async function json<T>(response: Response, schema: z.ZodType<T>): Promise<T> {
      if (response.status === 405 || response.status === 501) throw new NotAvailableError();
      if (!response.ok) {
        const problem = await problemOf(response);
        throw new ApiError(response.status, problem.code, problem);
      }
      const body: unknown = await response.json();
      const parsed = schema.safeParse(body);
      if (!parsed.success) throw new ApiError(502, "invalid_response");
      return parsed.data;
    }

    /** 204 or a body we can read; a body that does not parse is ignored (null). */
    async function maybe<T>(response: Response, schema: z.ZodType<T>): Promise<T | null> {
      if (response.status === 405 || response.status === 501) throw new NotAvailableError();
      if (!response.ok) {
        const problem = await problemOf(response);
        throw new ApiError(response.status, problem.code, problem);
      }
      if (response.status === 204) return null;
      try {
        const parsed = schema.safeParse(await response.json());
        return parsed.success ? parsed.data : null;
      } catch {
        return null;
      }
    }

    const empty = z.unknown();
    const conv = (id: string) => `/api/v1/knowledge/conversations/${encodeURIComponent(id)}`;
    const mem = (id: string) => `/api/v1/knowledge/memories/${encodeURIComponent(id)}`;

    return {
      ask: (body, signal) =>
        call("POST", "/api/v1/knowledge/ask", {
          body,
          headers: { accept: "text/event-stream" },
          signal,
        }),
      listConversations: async (cursor, limit = 20) => {
        const query = new URLSearchParams({ limit: String(limit) });
        if (cursor) query.set("cursor", cursor);
        return json(
          await call("GET", `/api/v1/knowledge/conversations?${query.toString()}`),
          conversationPageSchema,
        );
      },
      getConversation: async (id) => json(await call("GET", conv(id)), conversationDetailSchema),
      patchConversation: async (id, version, patch) =>
        maybe(
          await call("PATCH", conv(id), { body: patch, headers: { "if-match": ifMatch(version) } }),
          conversationSummarySchema,
        ),
      deleteConversation: async (id) => {
        await maybe(await call("DELETE", conv(id)), empty);
      },
      listMemories: async () =>
        json(await call("GET", "/api/v1/knowledge/memories"), memoryListSchema),
      addMemory: async (text) =>
        maybe(
          await call("POST", "/api/v1/knowledge/memories", { body: { text } }),
          memoryItemSchema,
        ),
      editMemory: async (item, text) =>
        maybe(
          await call("PATCH", mem(item.id), {
            body: { text },
            headers: { "if-match": ifMatch(item.version) },
          }),
          memoryItemSchema,
        ),
      deleteMemory: async (id) => {
        await maybe(await call("DELETE", mem(id)), empty);
      },
      confirmMemory: async (id) =>
        maybe(await call("POST", `${mem(id)}/confirm`), memoryItemSchema),
      forgetAllMemories: async () => {
        await maybe(await call("DELETE", "/api/v1/knowledge/memories"), empty);
      },
      getMemorySettings: async () =>
        json(await call("GET", "/api/v1/knowledge/memory-settings"), memorySettingsSchema),
      putMemorySettings: async (enabled) =>
        json(
          await call("PUT", "/api/v1/knowledge/memory-settings", { body: { enabled } }),
          memorySettingsSchema,
        ),
    };
  }, [locale]);
}
