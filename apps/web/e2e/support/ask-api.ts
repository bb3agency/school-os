/**
 * Stand-in for the Ask chat API (FR-KB-008, FR-KB-012; docs/06 §5.1 and the conversations
 * and memory contract in apps/web/src/features/ask/contract.ts): conversations that remember
 * their questions, `status`/`followups` events, and the member's memory. In memory only,
 * reset with the journey (`POST /__e2e/reset`). Synthetic content only.
 */

type Json = Record<string, unknown>;

const CONVERSATION_ID = "0192f3a4-0000-7000-8000-00000000e901";
const MEMORY_ID = "0192f3a4-0000-7000-8000-00000000e801";

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
      status: "saved",
      created_at: "2026-09-20T05:00:00Z",
      updated_at: "2026-09-20T05:00:00Z",
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

/** The events of one answer, and what to store once the stream has been written. */
export function askEvents(
  body: Json,
  citation: Json,
  searchOnly: boolean,
): { events: Array<[string, unknown]>; commit: () => void } {
  asked += 1;
  const id = queryId(asked);
  const question = typeof body.question === "string" ? body.question : "";
  const existing =
    typeof body.conversation_id === "string"
      ? conversations.find((c) => c.id === body.conversation_id)
      : undefined;
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
  const events: Array<[string, unknown]> = searchOnly
    ? [
        [
          "meta",
          { query_id: id, language: "en", mode: "full", conversation_id: conversationId, title },
        ],
        ["status", { step: "searching_documents", count: 1 }],
        ["delta", { text: "Dasara holidays run " }],
        ["error", { type: "ai_budget_exhausted", message_key: "kb.errors.budget" }],
        ["final", { text: "", replaced: true, status: "search_only", mode: "search_only" }],
        ["citation", citation],
        ["done", { latency_ms: 310, cited_sources: 1, status: "search_only", mode: "search_only" }],
      ]
    : [
        [
          "meta",
          { query_id: id, language: "en", mode: "full", conversation_id: conversationId, title },
        ],
        ["status", { step: "understanding" }],
        ["status", { step: "searching_documents", tool: "search_documents", count: 1 }],
        ["status", { step: "writing" }],
        ["delta", { text: "Dasara holidays run " }],
        ["delta", { text: "from **02/10/2026** to **12/10/2026**. [1]\n\n" }],
        [
          "delta",
          { text: "- School reopens on 13/10/2026. [1]\n- Offices stay open on weekdays." },
        ],
        ["final", { text, replaced: false, status: "answered", mode: "full" }],
        ["citation", citation],
        ["done", { latency_ms: 1420, cited_sources: 1, status: "answered", mode: "full" }],
        [
          "followups",
          {
            questions: [
              "When does school reopen after Dasara?",
              "Is the office open during Dasara?",
            ],
          },
        ],
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
    conversation.messages.push({
      query_id: id,
      question,
      answer: searchOnly ? "" : text,
      status: searchOnly ? "search_only" : "answered",
      mode: searchOnly ? "search_only" : "full",
      language: "en",
      citations: [citation],
      feedback: null,
      followups: [],
      created_at: now(),
      superseded: false,
    });
    conversation.updated_at = now();
  };
  return { events, commit };
}

const CONVERSATION_PATH = /^\/api\/v1\/knowledge\/conversations\/([^/]+)$/;
const MEMORY_PATH = /^\/api\/v1\/knowledge\/memories\/([^/]+)(\/confirm)?$/;
const NOT_FOUND: [number, unknown] = [
  404,
  { type: "about:blank", title: "Not found", status: 404, code: "not_found" },
];

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
      if (typeof body.title === "string") conversation.title = body.title;
      if (typeof body.pinned === "boolean") conversation.pinned = body.pinned;
      conversation.version += 1;
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
    if (method === "GET") return [200, memories];
    if (method === "DELETE") {
      memories = [];
      return [204, ""];
    }
    if (method === "POST") {
      const text = typeof body.text === "string" ? body.text : "";
      if (/class \d+ .*(has|is)|aadhaar/i.test(text)) {
        return [
          422,
          { type: "about:blank", title: "Not allowed", status: 422, code: "memory_not_allowed" },
        ];
      }
      const item = {
        id: queryId(800 + memories.length + 2),
        text,
        source: "explicit",
        status: "saved",
        created_at: now(),
        updated_at: now(),
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
      item.status = "saved";
      return [200, item];
    }
    if (method === "PATCH" && typeof body.text === "string") {
      item.text = body.text;
      item.version = Number(item.version) + 1;
      return [200, item];
    }
    if (method === "DELETE") {
      memories = memories.filter((m) => m !== item);
      return [204, ""];
    }
  }
  return undefined;
}
