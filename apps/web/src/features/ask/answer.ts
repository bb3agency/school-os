import type { SseMessage } from "./sse";

/**
 * The Ask answer as the page builds it from the SSE events of `POST /knowledge/ask`
 * (docs/06 §5.1, docs/09 Knowledge): `meta`, `token`, `citation`, `error`, `done`. Unknown
 * event types are ignored, so the API may add events (e.g. finer-grained token deltas) without
 * breaking this page. Nothing here is rendered as HTML: answer text is shown as text, and the
 * only links are the ones this module builds from validated `sos://` sources (never from the
 * model's prose).
 */

export type AskMode = "full" | "search_only";
export type AskLanguage = "en" | "te" | "mixed";

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

/** Reasons the API gives for an answer without AI prose (`error.message_key`). */
export const KB_MESSAGES = ["budget", "disabled", "rate_limited", "unavailable"] as const;
export type KbMessage = (typeof KB_MESSAGES)[number];

export interface AskState {
  phase: AskPhase;
  queryId: string | null;
  language: AskLanguage | null;
  mode: AskMode | null;
  text: string;
  citations: AskCitation[];
  /** Why AI prose is missing (search-only), from the `error` event. */
  notice: KbMessage | null;
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
  citations: [],
  notice: null,
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

const SENTENCE_END = /[.!?।:;,)\]]$/u;

/**
 * Append one `token` text. The API currently sends each answer segment as one `token` (the
 * whole answer arrives in a burst, docs/06 §5 as built) and joins segments with a space; a
 * cited segment ends in `[n]` without trailing space. So: keep the text as sent when either
 * side has whitespace at the joint, and add one space only after sentence punctuation or a
 * citation marker. Finer-grained deltas that carry their own spaces are joined as sent.
 */
export function joinToken(previous: string, next: string): string {
  if (!previous || !next) return previous + next;
  if (/\s$/u.test(previous) || /^\s/u.test(next)) return previous + next;
  return SENTENCE_END.test(previous) ? `${previous} ${next}` : previous + next;
}

const MODES = new Set<string>(["full", "search_only"]);
const LANGUAGES = new Set<string>(["en", "te", "mixed"]);

/** Fold one SSE event into the answer. Malformed or unknown events change nothing. */
export function applyEvent(state: AskState, message: SseMessage): AskState {
  const data = record(message.data);
  if (!data) return state;
  switch (message.event) {
    case "meta": {
      const queryId = typeof data.query_id === "string" ? data.query_id : state.queryId;
      const mode =
        typeof data.mode === "string" && MODES.has(data.mode) ? (data.mode as AskMode) : state.mode;
      const language =
        typeof data.language === "string" && LANGUAGES.has(data.language)
          ? (data.language as AskLanguage)
          : state.language;
      return { ...state, phase: "streaming", queryId, mode, language };
    }
    case "token": {
      if (typeof data.text !== "string") return state;
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
      return { ...state, phase: "streaming", notice: kbMessage(data.message_key) };
    case "done":
      return {
        ...state,
        phase: "done",
        latencyMs: typeof data.latency_ms === "number" ? data.latency_ms : null,
      };
    default:
      return state;
  }
}

export type AskOutcome = "pending" | "answered" | "not_found" | "search_only";

/**
 * What the finished answer is: search-only passages (budget used up, AI off, outage), "not
 * found in the school records you can access" (the API replaces an answer without any valid
 * citation by that text, docs/06 §9), or an answer with sources.
 */
export function outcomeOf(state: AskState): AskOutcome {
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
const STUDENT = new RegExp(`^sos://student/(${UUID})(?:/field/([a-z0-9_]{1,64}))?(?:\\?[a-z0-9_=&-]*)?$`);
const OTHER = new RegExp(`^sos://(finding|change|verified)/(${UUID})$`);

export type SourceRef =
  | { kind: "doc"; id: string; version: number; page: number | null }
  | { kind: "student"; id: string; field: string | null }
  | { kind: "finding" | "change" | "verified"; id: string };

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
  if (other) return { kind: other[1] as "finding" | "change" | "verified", id: other[2] as string };
  return null;
}

/** The existing screen that opens a source (locale-relative path for the i18n Link). */
export function sourceHref(ref: SourceRef): string {
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
