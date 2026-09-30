import type { ConversationMessage } from "./data";
import {
  MAX_FOLLOWUPS,
  parseAskEvent,
  type AskStep,
  type MemoryEvent,
  type SseMessage,
} from "./sse";

/**
 * The Ask answer as the page builds it from the SSE events of `POST /knowledge/ask`
 * (docs/06 §5.1, docs/09 Knowledge), in this order: `meta`, `status`*, `delta`*, `error`?,
 * `final`, `token`*, `citation`*, `followups`, `memory`?, `done` (payloads typed and checked in
 * sse.ts). `status` events report the step the server is on (understanding, searching
 * documents, reading records, searching your chats, writing). Unknown event types and fields
 * are ignored, so the API may add them without breaking this page. Nothing here is rendered as
 * HTML: answer text is shown as text, and the only links are the ones this module builds from
 * validated `sos://` sources (never from the model's prose).
 *
 * `meta.mode` is always `full` now (it is sent before anything is known); the real outcome is
 * in `final` and, as the source of truth, `done` (`status`, `mode`). `delta` is an unchecked
 * preview; `final` replaces it with the validated answer, after which `token` is ignored. A
 * server without `final` still works: its `token` segments are joined with one space.
 */

export type AskMode = "full" | "search_only";
export type AskLanguage = "en" | "te" | "mixed";
/** `final.status` / `done.status` (`error` only in `done`, after `internal_error`). */
export type AskStatus = "answered" | "not_found" | "refused" | "search_only" | "error";

export type AskPhase =
  /** Nothing asked yet. */
  | "idle"
  /** Request sent, no event yet. */
  | "waiting"
  | "streaming"
  | "done"
  /** The user pressed Stop. */
  | "stopped"
  /** The stream ended (or broke) before `done`. */
  | "interrupted"
  /** The request itself failed (HTTP problem, offline). */
  | "failed"
  /**
   * A stored message still `streaming`: its answer is being written in another window, or its
   * stream ended without being recorded. In progress or unfinished, never an error.
   */
  | "incomplete";

export interface AskCitation {
  index: number;
  source: string;
  title: string;
  snippet: string;
  /** From a stored conversation: the caller can no longer open this source. */
  withheld?: boolean;
}

/** One step the server reported (`status` events), in order; a repeated step updates its count. */
export interface AskStepRecord {
  step: AskStep;
  count: number | null;
}

/** `kb.errors.*` message keys the API sends in the `error` event (docs/09 Knowledge error codes). */
export const KB_MESSAGES = [
  "budget",
  "disabled",
  "rate_limited",
  "unavailable",
  "internal",
] as const;
export type KbMessage = (typeof KB_MESSAGES)[number];

export interface AskState {
  phase: AskPhase;
  queryId: string | null;
  language: AskLanguage | null;
  mode: AskMode | null;
  /** The validated answer: `final.text`, or the legacy `token` segments joined. */
  text: string;
  /** Unchecked preview from `delta` events (exact text); cleared by `final`. */
  preview: string;
  /** `final` arrived: the preview is gone and later `token`/`delta` events are ignored. */
  finalized: boolean;
  /** `final.replaced`: the checked answer differs from the preview. */
  replaced: boolean;
  /** From `final`, then `done` (the source of truth). */
  status: AskStatus | null;
  citations: AskCitation[];
  /** Why AI prose is missing (search-only) or the stream failed, from the `error` event. */
  notice: KbMessage | null;
  /** `error.type` as sent (a code, never free text). */
  errorType: string | null;
  latencyMs: number | null;
  /** The failed request (ApiError, AuthRedirectError, TypeError...) when phase is "failed". */
  error: unknown;
  /** `meta.conversation_id`: the conversation this answer belongs to (new or existing). */
  conversationId: string | null;
  /** `meta.title`: the conversation's title (set by the server for a new conversation). */
  title: string | null;
  /** `status` events: what the server is doing, for the live status line. */
  steps: AskStepRecord[];
  /** `followups` event: suggested next questions (plain text, sent as a new question). */
  followups: string[];
  /** `memory` events: what the server remembered or suggests remembering. */
  memory: MemoryEvent[];
  /** `meta`/`final` said that earlier turns were summarised for this answer (optional). */
  summarized: boolean;
  /** `meta.cached`: reused from an identical earlier question on the same documents. */
  cached: boolean;
  /**
   * A stored answer the member may no longer read (`answer_withheld`): a source it cited is
   * no longer visible to them, so its text and follow-ups are withheld too.
   */
  withheld: boolean;
}

export const INITIAL_ASK: AskState = {
  phase: "idle",
  queryId: null,
  language: null,
  mode: null,
  text: "",
  preview: "",
  finalized: false,
  replaced: false,
  status: null,
  citations: [],
  notice: null,
  errorType: null,
  latencyMs: null,
  error: undefined,
  conversationId: null,
  title: null,
  steps: [],
  followups: [],
  memory: [],
  summarized: false,
  cached: false,
  withheld: false,
};

/** `kb.errors.budget` → `budget`; unknown keys read as "AI answers unavailable". */
export function kbMessage(messageKey: unknown): KbMessage {
  if (typeof messageKey === "string") {
    const suffix = messageKey.replace(/^kb\.errors\./, "");
    const known = KB_MESSAGES.find((key) => key === suffix);
    if (known) return known;
  }
  return "unavailable";
}

/**
 * Append one legacy `token` segment. Segments come without surrounding whitespace and joining
 * them with ONE space gives `final.text` (docs/06 §5.1); text that already has whitespace at
 * the joint is kept as sent.
 */
export function joinToken(previous: string, next: string): string {
  if (!previous || !next) return previous + next;
  if (/\s$/u.test(previous) || /^\s/u.test(next)) return previous + next;
  return `${previous} ${next}`;
}

/** Fold one SSE event into the answer. Malformed or unknown events change nothing. */
export function applyEvent(state: AskState, message: SseMessage): AskState {
  const parsed = parseAskEvent(message);
  if (!parsed) return state;
  switch (parsed.event) {
    case "meta": {
      const meta = parsed.data;
      // Provisional only: the API says `full` here and gives the real mode in final/done.
      return {
        ...state,
        phase: "streaming",
        queryId: meta.query_id,
        mode: meta.mode ?? state.mode,
        language: meta.language ?? state.language,
        conversationId: meta.conversation_id ?? state.conversationId,
        title: meta.title ?? state.title,
        summarized: state.summarized || meta.summarized === true,
        cached: state.cached || meta.cached === true || typeof meta.cached_from === "string",
      };
    }
    case "status": {
      if (state.finalized) return state;
      const { step, count } = parsed.data;
      const last = state.steps[state.steps.length - 1];
      // `count` is null until the step's tool has run; a repeated step keeps the last count.
      const steps =
        last && last.step === step
          ? [...state.steps.slice(0, -1), { step, count: count ?? last.count }]
          : [...state.steps, { step, count: count ?? null }];
      return { ...state, phase: "streaming", steps };
    }
    case "followups": {
      const followups = parsed.data.questions
        .map((question) => question.trim())
        .filter((question) => question.length > 0 && question.length <= 1000)
        .slice(0, MAX_FOLLOWUPS);
      return { ...state, followups };
    }
    case "memory": {
      const item = parsed.data;
      const memory = [...state.memory.filter((m) => m.item_id !== item.item_id), item];
      return { ...state, memory };
    }
    case "delta": {
      if (state.finalized) return state;
      return { ...state, phase: "streaming", preview: state.preview + parsed.data.text };
    }
    case "final": {
      const final = parsed.data;
      return {
        ...state,
        phase: "streaming",
        text: final.text,
        preview: "",
        finalized: true,
        replaced: final.replaced === true,
        status: final.status ?? state.status,
        mode: final.mode ?? state.mode,
        summarized: state.summarized || final.summarized === true,
      };
    }
    case "token": {
      if (state.finalized) return state;
      return { ...state, phase: "streaming", text: joinToken(state.text, parsed.data.text) };
    }
    case "citation": {
      const { index, source, title, snippet } = parsed.data;
      const citation: AskCitation = { index, source, title: title ?? "", snippet: snippet ?? "" };
      const citations = [...state.citations.filter((c) => c.index !== index), citation].sort(
        (a, b) => a.index - b.index,
      );
      return { ...state, phase: "streaming", citations };
    }
    case "error":
      return {
        ...state,
        phase: "streaming",
        notice: kbMessage(parsed.data.message_key),
        errorType: parsed.data.type ?? state.errorType,
      };
    case "done": {
      const done = parsed.data;
      return {
        ...state,
        phase: "done",
        preview: "",
        status: done.status ?? state.status,
        mode: done.mode ?? state.mode,
        latencyMs: done.latency_ms ?? null,
      };
    }
  }
}

export type AskOutcome = "pending" | "answered" | "not_found" | "refused" | "search_only" | "error";

/**
 * What the answer is: the status from `done` (else `final`) when the API sent one; search-only
 * passages when the mode says so; otherwise (a server without status) "not found in the school
 * records you can access" when nothing is cited (the API replaces an answer without any valid
 * citation by that text, docs/06 §9), or an answer with sources.
 */
export function outcomeOf(state: AskState): AskOutcome {
  if (state.status) return state.status;
  if (state.mode === "search_only") return "search_only";
  if (state.phase !== "done") return "pending";
  return state.citations.length === 0 ? "not_found" : "answered";
}

// --- display text ----------------------------------------------------------------------------

const TAG = /<\/?[A-Za-z][^<>]*>/g;
const MD_LINK = /\[([^\]]*)\]\([^)]*\)/g;
const EMPHASIS = /(\*\*|__)/g;

/**
 * Answer prose for display as plain text: HTML tags and markdown link targets are removed
 * (the API already strips them, docs/06 §9 rule 5; this is the second line), emphasis markers
 * dropped. React escapes whatever is left; URLs stay inert text.
 */
export function displayText(text: string): string {
  return text.replace(TAG, "").replace(MD_LINK, "$1").replace(EMPHASIS, "");
}

export type AnswerPart = { kind: "text"; value: string } | { kind: "cite"; index: number };

/** Split `[n]` markers that match a citation event into citation parts; others stay text. */
export function splitMarkers(text: string, known: ReadonlySet<number>): AnswerPart[] {
  const parts: AnswerPart[] = [];
  let last = 0;
  for (const match of text.matchAll(/\[(\d{1,3})\]/g)) {
    const index = Number(match[1]);
    if (!known.has(index)) continue;
    const start = match.index;
    if (start > last) parts.push({ kind: "text", value: text.slice(last, start) });
    parts.push({ kind: "cite", index });
    last = start + match[0].length;
  }
  if (last < text.length) parts.push({ kind: "text", value: text.slice(last) });
  return parts;
}

// --- sources (docs/06 §8) ---------------------------------------------------------------------

const UUID = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}";
const DOC = new RegExp(`^sos://doc/(${UUID})/v(\\d{1,6})(?:#p(\\d{1,6}))?$`);
const STUDENT = new RegExp(
  `^sos://student/(${UUID})(?:/field/([a-z0-9_]{1,64}))?(?:\\?[a-z0-9_=&-]*)?$`,
);
const OTHER = new RegExp(`^sos://(finding|change|verified|count|fee)/(${UUID})$`);

export type SourceRef =
  | { kind: "doc"; id: string; version: number; page: number | null }
  | { kind: "student"; id: string; field: string | null }
  | { kind: "finding" | "change" | "verified"; id: string }
  /** A student count the `count_students` tool computed: numbers only, no screen to open. */
  | { kind: "count"; id: string }
  /** A student's fee dues synced from Tally (`get_fee_dues`, M6): opens the fee dues screen. */
  | { kind: "fee"; id: string }
  /** One of your own past Ask chats (`sos://conversation/{id}#q{query_id}`). */
  | { kind: "conversation"; id: string; queryId: string | null };

const CONVERSATION = new RegExp(`^sos://conversation/(${UUID})(?:#q(${UUID}))?$`);

/** Element id of a message in the thread (the target of a past-chat source link). */
export function messageAnchor(queryId: string): string {
  return `m-${queryId}`;
}

/** Parse a `sos://` source URI; anything else (or malformed) is null and gets no link. */
export function parseSource(source: string): SourceRef | null {
  const doc = DOC.exec(source);
  if (doc) {
    return {
      kind: "doc",
      id: doc[1] as string,
      version: Number(doc[2]),
      page: doc[3] ? Number(doc[3]) : null,
    };
  }
  const student = STUDENT.exec(source);
  if (student) return { kind: "student", id: student[1] as string, field: student[2] ?? null };
  const other = OTHER.exec(source);
  if (other) {
    return {
      kind: other[1] as "finding" | "change" | "verified" | "count" | "fee",
      id: other[2] as string,
    };
  }
  const chat = CONVERSATION.exec(source);
  if (chat) return { kind: "conversation", id: chat[1] as string, queryId: chat[2] ?? null };
  return null;
}

/**
 * The existing screen that opens a source (locale-relative path for the i18n Link); null when
 * there is none (a student count).
 */
export function sourceHref(ref: SourceRef): string | null {
  switch (ref.kind) {
    case "doc":
      return `/documents/${ref.id}`;
    case "student":
      return `/students/${ref.id}`;
    case "finding":
      return `/findings/${ref.id}`;
    case "change":
      return `/change-requests/${ref.id}`;
    case "verified":
      return "/ask/verified";
    case "count":
      return null;
    case "fee":
      return "/fees";
    case "conversation":
      return `/ask/c/${ref.id}${ref.queryId ? `#${messageAnchor(ref.queryId)}` : ""}`;
  }
}

/**
 * Text to prefill a verified-answer citation from an Ask citation: the snippet is at most 300
 * characters of the cited text and ends in "…" when shortened; the API checks the quote against
 * the page text, so the ellipsis is dropped.
 */
export function quoteFromSnippet(snippet: string): string {
  return snippet.replace(/\s*…$/u, "").trim();
}

// --- stored conversations -------------------------------------------------------------------

/** What only the live stream knew about an answer (kept in memory for this page view). */
export interface AnswerExtras {
  steps: AskStepRecord[];
  latencyMs: number | null;
  notice: KbMessage | null;
  replaced: boolean;
  memory: MemoryEvent[];
  /** The unchecked text shown before a Stop (kept, marked "Stopped"). */
  preview: string;
  cached: boolean;
}

function phaseOf(status: ConversationMessage["status"]): AskPhase {
  if (status === "cancelled") return "stopped";
  if (status === "streaming") return "incomplete";
  return "done";
}

/**
 * A stored message (GET /knowledge/conversations/{id}) as the same state the stream builds,
 * so one component shows both. `cancelled` reads as a stopped answer; `streaming` as an answer
 * in progress or unfinished (`incomplete`); `answer_withheld` hides the text and follow-ups.
 */
export function stateFromMessage(
  message: ConversationMessage,
  extras?: AnswerExtras | null,
): AskState {
  const phase = phaseOf(message.status);
  const withheld = message.answer_withheld;
  const answer = withheld ? null : message.answer;
  return {
    ...INITIAL_ASK,
    phase,
    queryId: message.query_id,
    language: message.language,
    mode: message.mode,
    text: answer ?? "",
    finalized: answer !== null,
    replaced: extras?.replaced ?? false,
    status:
      message.status === "cancelled" || message.status === "streaming" ? null : message.status,
    citations: message.citations.map((c) => ({
      index: c.index,
      source: c.source,
      title: c.title ?? "",
      snippet: c.snippet ?? "",
      ...(c.withheld ? { withheld: true } : {}),
    })),
    notice: extras?.notice ?? null,
    latencyMs: extras?.latencyMs ?? null,
    steps: extras?.steps ?? [],
    followups: withheld ? [] : message.followups,
    memory: extras?.memory ?? [],
    summarized: message.summarized,
    preview: phase === "stopped" && answer === null && !withheld ? (extras?.preview ?? "") : "",
    cached: message.cached || (extras?.cached ?? false),
    withheld,
  };
}

/** The stored status of a finished live answer. */
function storedStatus(state: AskState): ConversationMessage["status"] {
  if (state.phase === "stopped" || state.phase === "interrupted") return "cancelled";
  if (state.phase === "incomplete") return "streaming";
  const outcome = outcomeOf(state);
  return outcome === "pending" ? "error" : outcome;
}

/** A finished live answer as a stored message (for the conversation cache after `done`). */
export function messageFromState(
  state: AskState,
  question: string,
  createdAt: string,
): ConversationMessage | null {
  if (!state.queryId) return null;
  return {
    query_id: state.queryId,
    question,
    answer: state.finalized || state.text ? state.text : null,
    answer_withheld: state.withheld,
    status: storedStatus(state),
    mode: state.mode ?? "full",
    language: state.language,
    citations: state.citations.map((c) => ({
      index: c.index,
      source: c.source,
      title: c.title || null,
      snippet: c.snippet || null,
      withheld: c.withheld === true,
    })),
    feedback: null,
    followups: state.followups,
    created_at: createdAt,
    superseded: false,
    cached: state.cached,
    summarized: state.summarized,
  };
}

/** The extras of a finished live answer, to keep beside its stored message. */
export function extrasOf(state: AskState): AnswerExtras {
  return {
    steps: state.steps,
    latencyMs: state.latencyMs,
    notice: state.notice,
    replaced: state.replaced,
    memory: state.memory,
    preview: state.finalized ? "" : state.preview,
    cached: state.cached,
  };
}
