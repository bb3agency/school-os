import type { SseMessage } from "./sse";

/**
 * The Ask answer as the page builds it from the SSE events of `POST /knowledge/ask`
 * (docs/06 §5.1, docs/09 Knowledge), in this order: `meta`, `delta`*, `error`?, `final`,
 * `token`*, `citation`*, `done`. Unknown event types and fields are ignored, so the API may add
 * them without breaking this page. Nothing here is rendered as HTML: answer text is shown as
 * text, and the only links are the ones this module builds from validated `sos://` sources
 * (never from the model's prose).
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
  | "failed";

export interface AskCitation {
  index: number;
  source: string;
  title: string;
  snippet: string;
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

function record(data: string): Record<string, unknown> | null {
  try {
    const value: unknown = JSON.parse(data);
    return value && typeof value === "object" && !Array.isArray(value)
      ? (value as Record<string, unknown>)
      : null;
  } catch {
    return null;
  }
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

const MODES = new Set<string>(["full", "search_only"]);
const LANGUAGES = new Set<string>(["en", "te", "mixed"]);
const FINAL_STATUSES = new Set<string>(["answered", "not_found", "refused", "search_only"]);
const DONE_STATUSES = new Set<string>([...FINAL_STATUSES, "error"]);

function modeOf(value: unknown, fallback: AskMode | null): AskMode | null {
  return typeof value === "string" && MODES.has(value) ? (value as AskMode) : fallback;
}

function statusOf(
  value: unknown,
  allowed: ReadonlySet<string>,
  fallback: AskStatus | null,
): AskStatus | null {
  return typeof value === "string" && allowed.has(value) ? (value as AskStatus) : fallback;
}

/** Fold one SSE event into the answer. Malformed or unknown events change nothing. */
export function applyEvent(state: AskState, message: SseMessage): AskState {
  const data = record(message.data);
  if (!data) return state;
  switch (message.event) {
    case "meta": {
      const queryId = typeof data.query_id === "string" ? data.query_id : state.queryId;
      const language =
        typeof data.language === "string" && LANGUAGES.has(data.language)
          ? (data.language as AskLanguage)
          : state.language;
      // Provisional only: the API says `full` here and gives the real mode in final/done.
      return {
        ...state,
        phase: "streaming",
        queryId,
        mode: modeOf(data.mode, state.mode),
        language,
      };
    }
    case "delta": {
      if (state.finalized || typeof data.text !== "string") return state;
      return { ...state, phase: "streaming", preview: state.preview + data.text };
    }
    case "final": {
      if (typeof data.text !== "string") return state;
      return {
        ...state,
        phase: "streaming",
        text: data.text,
        preview: "",
        finalized: true,
        replaced: data.replaced === true,
        status: statusOf(data.status, FINAL_STATUSES, state.status),
        mode: modeOf(data.mode, state.mode),
      };
    }
    case "token": {
      if (state.finalized || typeof data.text !== "string") return state;
      return { ...state, phase: "streaming", text: joinToken(state.text, data.text) };
    }
    case "citation": {
      const { index, source, title, snippet } = data;
      if (
        typeof index !== "number" ||
        !Number.isInteger(index) ||
        index < 1 ||
        typeof source !== "string"
      ) {
        return state;
      }
      const citation: AskCitation = {
        index,
        source,
        title: typeof title === "string" ? title : "",
        snippet: typeof snippet === "string" ? snippet : "",
      };
      const citations = [...state.citations.filter((c) => c.index !== index), citation].sort(
        (a, b) => a.index - b.index,
      );
      return { ...state, phase: "streaming", citations };
    }
    case "error":
      return {
        ...state,
        phase: "streaming",
        notice: kbMessage(data.message_key),
        errorType: typeof data.type === "string" ? data.type : state.errorType,
      };
    case "done":
      return {
        ...state,
        phase: "done",
        preview: "",
        status: statusOf(data.status, DONE_STATUSES, state.status),
        mode: modeOf(data.mode, state.mode),
        latencyMs: typeof data.latency_ms === "number" ? data.latency_ms : null,
      };
    default:
      return state;
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
const OTHER = new RegExp(`^sos://(finding|change|verified|count)/(${UUID})$`);

export type SourceRef =
  | { kind: "doc"; id: string; version: number; page: number | null }
  | { kind: "student"; id: string; field: string | null }
  | { kind: "finding" | "change" | "verified"; id: string }
  /** A student count the `count_students` tool computed: numbers only, no screen to open. */
  | { kind: "count"; id: string };

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
      kind: other[1] as "finding" | "change" | "verified" | "count",
      id: other[2] as string,
    };
  }
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
