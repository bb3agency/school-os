/**
 * Incremental Server-Sent Events parser for `POST /knowledge/ask` read through the BFF
 * (docs/06 §5.1). `EventSource` cannot send a POST body or the CSRF header, so the page reads
 * the fetch body stream and feeds decoded text chunks here.
 *
 * Follows the HTML "event stream" rules that matter for the API: lines end in LF, CRLF or CR;
 * a blank line dispatches; `event:` names the event (default `message`); several `data:` lines
 * are joined with LF; lines starting with `:` are comments; `id:`/`retry:` are ignored (the
 * page never reconnects: a question is asked once). A chunk may split a line anywhere.
 *
 * Below the parser: the typed payloads of the Ask events (`parseAskEvent`).
 */

import { z } from "zod";

export interface SseMessage {
  event: string;
  data: string;
}

export interface SseParser {
  /** Feed decoded text; returns the events completed by it (possibly none). */
  push(chunk: string): SseMessage[];
  /** End of stream: an event without its final blank line is dropped (it may be cut off). */
  end(): SseMessage[];
}

export function createSseParser(): SseParser {
  let buffer = "";
  let event = "";
  let data: string[] = [];
  let pendingCr = false;

  function line(raw: string, out: SseMessage[]): void {
    if (raw === "") {
      if (data.length > 0) out.push({ event: event || "message", data: data.join("\n") });
      event = "";
      data = [];
      return;
    }
    if (raw.startsWith(":")) return;
    const colon = raw.indexOf(":");
    const field = colon === -1 ? raw : raw.slice(0, colon);
    let value = colon === -1 ? "" : raw.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") event = value;
    else if (field === "data") data.push(value);
  }

  return {
    push(chunk) {
      const out: SseMessage[] = [];
      let text = chunk;
      // A CR at the end of the previous chunk may be the first half of CRLF.
      if (pendingCr && text.startsWith("\n")) text = text.slice(1);
      pendingCr = false;
      buffer += text;
      for (;;) {
        const match = /\r\n|\r|\n/.exec(buffer);
        if (!match) break;
        if (match[0] === "\r" && match.index === buffer.length - 1) {
          pendingCr = true;
        }
        line(buffer.slice(0, match.index), out);
        buffer = buffer.slice(match.index + match[0].length);
      }
      return out;
    },
    end() {
      buffer = "";
      event = "";
      data = [];
      return [];
    },
  };
}

// --- the Ask events (docs/06 §5.1, docs/09 Knowledge; apps/api/app/knowledge/domain.py) ------

/*
 * Payloads of the `POST /knowledge/ask` events. SSE frames are not in the OpenAPI document, so
 * they are described here, field for field as the API sends them (`MetaEvent`, `StatusEvent`
 * ... in `knowledge/domain.py`), in this order: `meta`, `status`*, `delta`*, `error`?, `final`,
 * `token`*, `citation`*, `followups`, `memory`?, `done`. After an internal error the API sends
 * only `meta`, `error` and `done` (`status: "error"`).
 *
 * Each event needs the fields that make it what it is (`meta.query_id`, `delta.text`,
 * `citation.index` and `source` ...). The other fields are optional here only so that an older
 * API (no `final`, `done` without `status`) still works, and a field with an unexpected value
 * is ignored rather than the whole event (the API may add values without breaking the page).
 * Unknown fields are dropped; nothing here is ever rendered as HTML.
 */

/** An optional field: absent, or ignored when it does not have the API's type. */
function lenient<T extends z.ZodType>(schema: T) {
  return schema.optional().catch(undefined);
}

export const ASK_MODES = ["full", "search_only"] as const;
export const ASK_LANGUAGES = ["en", "te", "mixed"] as const;

/** `status.step` (`StatusStep`): codes only, never text. */
export const ASK_STEPS = [
  "understanding",
  "searching_documents",
  "reading_records",
  "searching_chats",
  "writing",
] as const;
export type AskStep = (typeof ASK_STEPS)[number];

/** `final.status`. */
export const FINAL_STATUSES = ["answered", "not_found", "refused", "search_only"] as const;
/** `done.status`: `final`'s, or `error` when the stream failed. */
export const DONE_STATUSES = [...FINAL_STATUSES, "error"] as const;

/** Most follow-up questions the API sends. */
export const MAX_FOLLOWUPS = 3;

const mode = z.enum(ASK_MODES);

/** `meta`: first and at once. `conversation_id` is a new one when the question named none. */
export const metaEventSchema = z.object({
  query_id: z.string().min(1),
  language: lenient(z.enum(ASK_LANGUAGES)),
  /** Always `full` here (sent before anything is known); the outcome is in final/done. */
  mode: lenient(mode),
  conversation_id: lenient(z.string().min(1).nullable()),
  /** The conversation's title (for a new one: the question cut at a word boundary). */
  title: lenient(z.string().nullable()),
  /** An exact repeat answered from the answer cache. */
  cached: lenient(z.boolean()),
  /** The earlier question whose answer is reused (only when `cached`). */
  cached_from: lenient(z.string().nullable()),
  /** Older messages reached the model as the conversation's summary only. */
  summarized: lenient(z.boolean()),
});

/** `status`: a step, the tool about to run, and its result count (null before it ran). */
export const statusEventSchema = z.object({
  step: z.enum(ASK_STEPS),
  tool: lenient(z.string().nullable()),
  count: lenient(z.number().int().nonnegative().nullable()),
});

/** `delta`: unchecked preview text, appended verbatim (whitespace included). */
export const deltaEventSchema = z.object({ text: z.string() });

/** `error`: codes only (`type` e.g. `ai_budget_exhausted`; `message_key` e.g. `kb.errors.budget`). */
export const errorEventSchema = z.object({
  type: lenient(z.string()),
  message_key: lenient(z.string()),
});

/** `final`: the checked answer that REPLACES the preview; later `token` events are ignored. */
export const finalEventSchema = z.object({
  text: z.string(),
  replaced: lenient(z.boolean()),
  status: lenient(z.enum(FINAL_STATUSES)),
  mode: lenient(mode),
  summarized: lenient(z.boolean()),
});

/** `token`: one checked segment without surrounding whitespace (for clients without `final`). */
export const tokenEventSchema = z.object({ text: z.string() });

/** `citation`: the `[n]` marker's source (`sos://` URI, ids only), title and quote. */
export const citationEventSchema = z.object({
  index: z.number().int().positive(),
  source: z.string().min(1),
  title: lenient(z.string()),
  snippet: lenient(z.string()),
});

/** `followups`: 0-3 next questions in the answer's language (always sent, may be empty). */
export const followupsEventSchema = z.object({ questions: z.array(z.string()) });

/** `memory`: an item saved ("remember that ...") or suggested (pending until confirmed). */
export const memoryEventSchema = z.object({
  action: z.enum(["saved", "suggested"]),
  item_id: z.string().min(1),
  text: z.string(),
});

/** `done`: last; `status` and `mode` are the source of truth for how the question ended. */
export const doneEventSchema = z.object({
  latency_ms: lenient(z.number().nonnegative()),
  cited_sources: lenient(z.number().int().nonnegative()),
  status: lenient(z.enum(DONE_STATUSES)),
  mode: lenient(mode),
});

export type MemoryEvent = z.infer<typeof memoryEventSchema>;

const EVENT_SCHEMAS = {
  meta: metaEventSchema,
  status: statusEventSchema,
  delta: deltaEventSchema,
  error: errorEventSchema,
  final: finalEventSchema,
  token: tokenEventSchema,
  citation: citationEventSchema,
  followups: followupsEventSchema,
  memory: memoryEventSchema,
  done: doneEventSchema,
} as const;

type EventSchemas = typeof EVENT_SCHEMAS;

/** One Ask event with its checked payload (a union discriminated by `event`). */
export type AskEvent = {
  [K in keyof EventSchemas]: { event: K; data: z.output<EventSchemas[K]> };
}[keyof EventSchemas];

function isAskEventName(name: string): name is keyof EventSchemas {
  return Object.hasOwn(EVENT_SCHEMAS, name);
}

/** The Ask event in an SSE message; null for an unknown event or a malformed payload. */
export function parseAskEvent(message: SseMessage): AskEvent | null {
  if (!isAskEventName(message.event)) return null;
  let raw: unknown;
  try {
    raw = JSON.parse(message.data);
  } catch {
    return null;
  }
  const parsed = EVENT_SCHEMAS[message.event].safeParse(raw);
  if (!parsed.success) return null;
  // The payload was checked by this event's own schema.
  return { event: message.event, data: parsed.data } as AskEvent;
}
