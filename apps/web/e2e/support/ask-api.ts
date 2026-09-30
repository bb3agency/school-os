/**
 * Stand-in for the Ask chat API (FR-KB-008, FR-KB-012, ADR-0034), in the shapes of the real API:
 * the SSE events of `POST /knowledge/ask` as `knowledge/domain.py` sends them (docs/06 §5.1)
 * and the conversation and memory endpoints as in `apps/api/openapi.json` (ConversationOut,
 * ConversationDetailOut / MessageOut, Page[MemoryOut], MemorySettingsOut) with their problem
 * codes (docs/09 Knowledge). In memory only, reset with the journey (`POST /__e2e/reset`).
 * Synthetic content only.
 */

type Json = Record<string, unknown>;

const CONVERSATION_ID = "0192f3a4-0000-7000-8000-00000000e901";
const MEMORY_ID = "0192f3a4-0000-7000-8000-00000000e801";
/** Most memory items one member keeps (409 memory_full). */
const MEMORY_LIMIT = 30;

interface Conversation {
  id: string;
  title: string;
  pinned: boolean;
  created_at: string;
  updated_at: string;
  version: number;
  messages: Json[];
}

let conversations: Conversation[] = [];
let memories: Json[] = [];
let memoryEnabled = true;
let asked = 0;

const now = () => new Date().toISOString();

export function resetAsk(): void {
  conversations = [];
  memories = [
    {
      id: MEMORY_ID,
      text: "I work in the school office and prepare circulars.",
      source: "explicit",
      status: "active",
      created_at: "2026-09-20T05:00:00Z",
      updated_at: "2026-09-20T05:00:00Z",
      expires_at: null,
      version: 1,
    },
  ];
  memoryEnabled = true;
  asked = 0;
}
resetAsk();

function queryId(n: number): string {
  return `0192f3a4-0000-7000-8000-${(0xe000 + n).toString(16).padStart(12, "0")}`;
}

function summaryOf(c: Conversation): Json {
  const { messages, ...rest } = c;
  return { ...rest, message_count: messages.filter((m) => m.superseded !== true).length };
}

function problem(status: number, code: string, extra: Json = {}): [number, Json] {
  return [status, { type: "about:blank", title: code, status, code, ...extra }];
}

/** 422 validation_error with the reason as the field error's code (as the API refuses). */
function refused(field: string, code: string): [number, Json] {
  return problem(422, "validation_error", {
    errors: [{ field, code, message_key: `errors.${code}` }],
  });
}

const NOT_FOUND = problem(404, "not_found");

/**
 * The events of one answer, and what to store once the stream has been written; null when the
 * question names a conversation that does not exist (the API answers 404).
 */
export function askEvents(
  body: Json,
  citation: Json,
  searchOnly: boolean,
): { events: Array<[string, unknown]>; commit: () => void } | null {
  const existing =
    typeof body.conversation_id === "string"
      ? conversations.find((c) => c.id === body.conversation_id)
      : undefined;
  if (typeof body.conversation_id === "string" && !existing) return null;
  asked += 1;
  const id = queryId(asked);
  const question = typeof body.question === "string" ? body.question : "";
  const conversationId = existing?.id ?? (asked === 1 ? CONVERSATION_ID : queryId(900 + asked));
  const title = existing?.title ?? "Dasara holidays";
  const text =
    "Dasara holidays run from **02/10/2026** to **12/10/2026**. [1]\n\n- School reopens on 13/10/2026. [1]\n- Offices stay open on weekdays.";
  const replaces =
    typeof body.regenerate_of === "string"
      ? body.regenerate_of
      : typeof body.edit_of === "string"
        ? body.edit_of
        : null;
  const meta = {
    query_id: id,
    language: "en",
    mode: "full",
    conversation_id: conversationId,
    title,
    cached: false,
    cached_from: null,
    summarized: false,
  };
  // meta, status*, delta*, error?, final, token*, citation*, followups, memory?, done.
  const events: Array<[string, unknown]> = searchOnly
    ? [
        ["meta", meta],
        ["status", { step: "understanding", tool: null, count: null }],
        ["status", { step: "searching_documents", tool: "search_documents", count: null }],
        ["status", { step: "searching_documents", tool: "search_documents", count: 1 }],
        ["delta", { text: "Dasara holidays run " }],
        ["error", { type: "ai_budget_exhausted", message_key: "kb.errors.budget" }],
        [
          "final",
          {
            text: "",
            replaced: true,
            status: "search_only",
            mode: "search_only",
            summarized: false,
          },
        ],
        ["citation", citation],
        ["followups", { questions: [] }],
        ["done", { latency_ms: 310, cited_sources: 1, status: "search_only", mode: "search_only" }],
      ]
    : [
        ["meta", meta],
        ["status", { step: "understanding", tool: null, count: null }],
        ["status", { step: "searching_documents", tool: "search_documents", count: null }],
        ["status", { step: "searching_documents", tool: "search_documents", count: 1 }],
        ["status", { step: "writing", tool: null, count: null }],
        ["delta", { text: "Dasara holidays run " }],
        ["delta", { text: "from **02/10/2026** to **12/10/2026**. [1]\n\n" }],
        [
          "delta",
          { text: "- School reopens on 13/10/2026. [1]\n- Offices stay open on weekdays." },
        ],
        ["final", { text, replaced: false, status: "answered", mode: "full", summarized: false }],
        // (`token` events repeat `final` for older clients; the page ignores them after it.)
        ["citation", citation],
        [
          "followups",
          {
            questions: [
              "When does school reopen after Dasara?",
              "Is the office open during Dasara?",
            ],
          },
        ],
        ["done", { latency_ms: 1420, cited_sources: 1, status: "answered", mode: "full" }],
      ];
  const commit = () => {
    let conversation = existing ?? conversations.find((c) => c.id === conversationId);
    if (!conversation) {
      conversation = {
        id: conversationId,
        title,
        pinned: false,
        created_at: now(),
        updated_at: now(),
        version: 1,
        messages: [],
      };
      conversations.unshift(conversation);
    }
    conversation.messages = conversation.messages.map((m) =>
      m.query_id === replaces ? { ...m, superseded: true } : m,
    );
    // MessageOut.
    conversation.messages.push({
      query_id: id,
      question,
      answer: searchOnly ? null : text,
      answer_withheld: false,
      status: searchOnly ? "search_only" : "answered",
      mode: searchOnly ? "search_only" : "full",
      language: "en",
      citations: [{ ...citation, withheld: false }],
      feedback: null,
      followups: searchOnly
        ? []
        : ["When does school reopen after Dasara?", "Is the office open during Dasara?"],
      created_at: now(),
      superseded: false,
      cached: false,
      summarized: false,
    });
    conversation.updated_at = now();
  };
  return { events, commit };
}

const CONVERSATION_PATH = /^\/api\/v1\/knowledge\/conversations\/([^/]+)$/;
const MEMORY_PATH = /^\/api\/v1\/knowledge\/memories\/([^/]+)(\/confirm)?$/;

/** The memory screen's refusal of text about other people (the API's `memory_others`). */
const ABOUT_OTHERS = /class \d+ .*(has|is)|aadhaar/i;

/** Answers for the conversation and memory routes; undefined for any other path. */
export function askAnswer(method: string, path: string, body: Json): [number, unknown] | undefined {
  if (path === "/api/v1/knowledge/conversations" && method === "GET") {
    const data = [...conversations]
      .sort((a, b) =>
        a.pinned !== b.pinned ? (a.pinned ? -1 : 1) : b.updated_at.localeCompare(a.updated_at),
      )
      .map(summaryOf);
    return [200, { data, next_cursor: null }];
  }
  const conversationMatch = CONVERSATION_PATH.exec(path);
  if (conversationMatch) {
    const conversation = conversations.find((c) => c.id === conversationMatch[1]);
    if (!conversation) return NOT_FOUND;
    if (method === "GET")
      return [200, { ...summaryOf(conversation), messages: conversation.messages }];
    if (method === "PATCH") {
      if (typeof body.title === "string") {
        const title = body.title.trim();
        if (!title || title.length > 120) return refused("title", "title_length");
        conversation.title = title;
      }
      if (typeof body.pinned === "boolean") conversation.pinned = body.pinned;
      conversation.version += 1;
      conversation.updated_at = now();
      return [200, summaryOf(conversation)];
    }
    if (method === "DELETE") {
      conversations = conversations.filter((c) => c !== conversation);
      return [204, ""];
    }
  }
  if (path === "/api/v1/knowledge/memory-settings") {
    if (method === "PUT" && typeof body.enabled === "boolean") memoryEnabled = body.enabled;
    return [200, { enabled: memoryEnabled, school_enabled: true }];
  }
  if (path === "/api/v1/knowledge/memories") {
    if (method === "GET") return [200, { data: memories, next_cursor: null }];
    if (method === "DELETE") {
      memories = [];
      return [204, ""];
    }
    if (method === "POST") {
      if (!memoryEnabled) return problem(409, "memory_off");
      const text = typeof body.text === "string" ? body.text.trim() : "";
      if (!text) return refused("text", "memory_empty");
      if (text.length > 200) return refused("text", "memory_too_long");
      if (ABOUT_OTHERS.test(text)) return refused("text", "memory_others");
      if (memories.length >= MEMORY_LIMIT) return problem(409, "memory_full");
      const item = {
        id: queryId(800 + memories.length + 2),
        text,
        source: "explicit",
        status: "active",
        created_at: now(),
        updated_at: now(),
        expires_at: null,
        version: 1,
      };
      memories = [item, ...memories];
      return [201, item];
    }
  }
  const memoryMatch = MEMORY_PATH.exec(path);
  if (memoryMatch) {
    const item = memories.find((m) => m.id === memoryMatch[1]);
    if (!item) return NOT_FOUND;
    if (memoryMatch[2] && method === "POST") {
      if (!memoryEnabled) return problem(409, "memory_off");
      item.status = "active";
      item.expires_at = null;
      return [200, item];
    }
    if (method === "PATCH" && typeof body.text === "string") {
      if (!memoryEnabled) return problem(409, "memory_off");
      if (ABOUT_OTHERS.test(body.text)) return refused("text", "memory_others");
      item.text = body.text;
      item.version = Number(item.version) + 1;
      item.updated_at = now();
      return [200, item];
    }
    if (method === "DELETE") {
      memories = memories.filter((m) => m !== item);
      return [204, ""];
    }
  }
  return undefined;
}
