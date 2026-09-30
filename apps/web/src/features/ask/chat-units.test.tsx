import { act, renderHook, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderWithIntl } from "@/test/render";
import {
  applyEvent,
  INITIAL_ASK,
  messageFromState,
  outcomeOf,
  parseSource,
  sourceHref,
  stateFromMessage,
  type AskState,
} from "./answer";
import { buildGroups } from "./ChatScreen";
import { shouldSend } from "./Composer";
import { sortConversations, writingElsewhere } from "./conversations";
import type { ConversationMessage } from "./data";
import { matchesFilter } from "./HistoryScreen";
import { Markdown } from "./Markdown";
import { parseMarkdown, stableStreamingText, toPlainText } from "./markdown-parse";
import { copyText } from "./MessageActions";
import { nextRevealLength, prefersReducedMotion, useReducedMotion, useSmoothText } from "./motion";
import { isNearBottom, stickReducer } from "./scroll";
import { sameTurn } from "./Turn";
import { createSseParser } from "./sse";
import { CHAT, message, sse, summary } from "./chat-test-utils";

const CITES = new Set([1, 2]);

function stubMatchMedia(reduce: boolean) {
  const listeners = new Set<() => void>();
  vi.stubGlobal(
    "matchMedia",
    vi.fn((query: string) => ({
      matches: query.includes("reduce") ? reduce : false,
      media: query,
      addEventListener: (_: string, cb: () => void) => listeners.add(cb),
      removeEventListener: (_: string, cb: () => void) => listeners.delete(cb),
    })),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

function fold(events: string[]): AskState {
  const parser = createSseParser();
  return parser.push(events.join("")).reduce(applyEvent, { ...INITIAL_ASK, phase: "waiting" });
}

describe("restricted markdown (docs/06 §9 rule 5)", () => {
  it("parses paragraphs, bold, italic, lists, tables and citation markers", () => {
    const blocks = parseMarkdown(
      "**Exams** begin *soon* [1].\nSecond line\n\n- one [2]\n- two\n  - nested\n\n1. first\n2. second\n\n| Class | Time |\n|:--|--:|\n| 6 | 9 am |\n| 7 | 10 am |",
      CITES,
    );
    expect(blocks.map((b) => b.type)).toEqual(["paragraph", "list", "list", "table"]);
    const [paragraph, bullets, numbers, table] = blocks;
    expect(paragraph?.type === "paragraph" && paragraph.children.map((c) => c.type)).toEqual([
      "strong",
      "text",
      "em",
      "text",
      "cite",
      "text",
      "br",
      "text",
    ]);
    expect(bullets?.type === "list" && bullets.items[0]?.sublist).toBeNull();
    expect(bullets?.type === "list" && bullets.items[1]?.sublist?.items).toHaveLength(1);
    expect(numbers?.type === "list" && numbers.ordered).toBe(true);
    expect(table?.type === "table" && table.align).toEqual(["left", "right"]);
    expect(table?.type === "table" && table.rows).toHaveLength(2);
  });

  it("drops raw HTML, keeps only the label of non-sos links, and never renders script", () => {
    const { container } = renderWithIntl(
      <Markdown
        text={
          'Hi <script>alert(1)</script><img src=x onerror="alert(1)"> [click](javascript:alert(1)) [site](https://evil.example) <a href="https://evil.example">x</a> [doc](sos://doc/0192f3a4-0000-7000-8000-00000000c701/v2#p1)'
        }
        citations={CITES}
        cite={(n) => <sup data-testid="cite">{n}</sup>}
      />,
    );
    expect(container.querySelector("script, img, iframe")).toBeNull();
    const hrefs = [...container.querySelectorAll("a")].map((a) => a.getAttribute("href"));
    expect(hrefs).toEqual(["/en/documents/0192f3a4-0000-7000-8000-00000000c701"]);
    expect(container).toHaveTextContent("Hi alert(1) click site x doc");
    expect(container.innerHTML).not.toContain("javascript:");
  });

  it("turns only known [n] markers into citations; others stay text", () => {
    renderWithIntl(
      <Markdown
        text="See [1] and [7]."
        citations={new Set([1])}
        cite={(n) => <sup data-testid="cite">{n}</sup>}
      />,
    );
    expect(screen.getAllByTestId("cite")).toHaveLength(1);
    expect(screen.getByText(/and \[7\]\./)).toBeInTheDocument();
  });

  it("renders a table in its own labelled, focusable scroll region", () => {
    renderWithIntl(
      <Markdown text={"| A | B |\n|---|---|\n| 1 | 2 |"} citations={CITES} cite={() => null} />,
    );
    const region = screen.getByRole("region", { name: "Table in the answer" });
    expect(region).toHaveAttribute("tabindex", "0");
    expect(screen.getAllByRole("columnheader")).toHaveLength(2);
  });

  it("keeps Telugu text whole", () => {
    renderWithIntl(
      <Markdown text="**పరీక్షలు** 22/09 నుండి [1]" citations={CITES} cite={() => "*"} />,
      "te",
    );
    expect(screen.getByText("పరీక్షలు", { selector: "strong" })).toBeInTheDocument();
  });

  it("holds back half-typed syntax while streaming so the layout does not flicker", () => {
    expect(stableStreamingText("Exams **begin")).toBe("Exams ");
    expect(stableStreamingText("Exams **begin** soon")).toBe("Exams **begin** soon");
    expect(stableStreamingText("See [the circ")).toBe("See ");
    expect(stableStreamingText("See [1")).toBe("See ");
    expect(stableStreamingText("See [1] and")).toBe("See [1] and");
    expect(stableStreamingText("Rows:\n| a | b")).toBe("Rows:\n");
    expect(stableStreamingText("List:\n- ")).toBe("List:\n");
    expect(stableStreamingText("Code `x")).toBe("Code ");
    expect(stableStreamingText("పరీక్షలు **త్వర")).toBe("పరీక్షలు ");
  });

  it("gives plain text for the clipboard, with or without markers", () => {
    const text = "Exams **begin** on 22/09. [1]\n\n- Class 6 [2]";
    expect(toPlainText(text, CITES, true)).toBe("Exams begin on 22/09. [1]\n\n- Class 6 [2]");
    expect(toPlainText(text, CITES, false)).toBe("Exams begin on 22/09.\n\n- Class 6");
    expect(
      copyText(
        "A. [1]",
        [{ index: 1, source: "sos://doc/x/v1#p3", title: "", snippet: "" }],
        "Sources",
        "Untitled",
        (p) => `page ${p}`,
      ),
    ).toBe("A. [1]\n\nSources:\n[1] Untitled, page 3");
  });
});

describe("new SSE events (status, followups, memory, meta extras)", () => {
  it("folds status steps (a repeated step updates its count) and stops after final", () => {
    const state = fold([
      sse("meta", {
        query_id: CHAT.query,
        mode: "full",
        conversation_id: CHAT.conversation,
        title: "Exams",
        summarized: true,
        cached: true,
      }),
      sse("status", { step: "understanding" }),
      sse("status", { step: "searching_documents", count: 2 }),
      sse("status", { step: "searching_documents", count: 4 }),
      sse("status", { step: "searching_chats" }),
      sse("status", { step: "teleporting" }),
      sse("final", { text: "Done. [1]", replaced: false, status: "answered", mode: "full" }),
      sse("status", { step: "writing" }),
      sse("followups", { questions: ["A?", "", "B?", "C?", "D?", "E?"] }),
      sse("memory", { action: "suggested", item_id: CHAT.memory, text: "x" }),
      sse("memory", { action: "saved", item_id: CHAT.memory, text: "x" }),
      sse("memory", { action: "forgotten", item_id: CHAT.memory2, text: "y" }),
    ]);
    expect(state.conversationId).toBe(CHAT.conversation);
    expect(state.title).toBe("Exams");
    expect(state.summarized).toBe(true);
    expect(state.cached).toBe(true);
    expect(state.steps).toEqual([
      { step: "understanding", count: null },
      { step: "searching_documents", count: 4 },
      { step: "searching_chats", count: null },
    ]);
    // At most 3, as the API sends (FollowupsEvent: 0-3 questions).
    expect(state.followups).toEqual(["A?", "B?", "C?"]);
    expect(state.memory).toEqual([{ action: "saved", item_id: CHAT.memory, text: "x" }]);
  });

  it("round-trips a finished answer through a stored message", () => {
    const state = fold([
      sse("meta", { query_id: CHAT.query, mode: "full", language: "te" }),
      sse("final", { text: "Hi [1]", replaced: false, status: "answered", mode: "full" }),
      sse("citation", { index: 1, source: "sos://doc/a/v1", title: "T", snippet: "S" }),
      sse("done", { status: "answered", mode: "full", latency_ms: 10 }),
    ]);
    const stored = messageFromState(state, "Q?", "2026-09-30T00:00:00Z");
    expect(stored).toMatchObject({
      query_id: CHAT.query,
      answer: "Hi [1]",
      status: "answered",
      language: "te",
    });
    const back = stateFromMessage(stored!);
    expect(back.text).toBe("Hi [1]");
    expect(back.phase).toBe("done");
    expect(stateFromMessage({ ...stored!, status: "cancelled", answer: null }).phase).toBe(
      "stopped",
    );
  });

  it("reads the events exactly as the API sends them (null tool and count, summarized, cached_from null)", () => {
    const state = fold([
      sse("meta", {
        query_id: CHAT.query,
        language: "en",
        mode: "full",
        conversation_id: CHAT.conversation,
        title: "Exams",
        cached: false,
        cached_from: null,
        summarized: false,
      }),
      sse("status", { step: "understanding", tool: null, count: null }),
      sse("status", { step: "searching_documents", tool: "search_documents", count: null }),
      sse("status", { step: "searching_documents", tool: "search_documents", count: 4 }),
      sse("status", { step: "writing", tool: null, count: null }),
      sse("delta", { text: "Exams " }),
      sse("final", {
        text: "Exams. [1]",
        replaced: false,
        status: "answered",
        mode: "full",
        summarized: true,
      }),
      sse("token", { text: "Exams. [1]" }),
      sse("citation", { index: 1, source: "sos://doc/a/v1", title: "T", snippet: "S" }),
      sse("followups", { questions: [] }),
      sse("done", { latency_ms: 10, cited_sources: 1, status: "answered", mode: "full" }),
    ]);
    expect(state).toMatchObject({
      phase: "done",
      text: "Exams. [1]",
      cached: false,
      summarized: true,
      conversationId: CHAT.conversation,
      followups: [],
    });
    expect(state.steps).toEqual([
      { step: "understanding", count: null },
      { step: "searching_documents", count: 4 },
      { step: "writing", count: null },
    ]);
    const stored = messageFromState(state, "Q?", "2026-09-30T00:00:00Z");
    expect(stored).toMatchObject({ answer_withheld: false, cached: false, summarized: true });
  });

  it("a stored answer still streaming is in progress, not an error; a withheld one hides its text", () => {
    const base = message({ query_id: CHAT.query }) as unknown as ConversationMessage;
    const streaming = stateFromMessage({ ...base, status: "streaming", answer: null });
    expect(streaming.phase).toBe("incomplete");
    expect(streaming.status).toBeNull();
    expect(outcomeOf(streaming)).toBe("pending");

    const withheld = stateFromMessage({
      ...base,
      answer: null,
      answer_withheld: true,
      followups: ["Next?"],
      citations: [
        { index: 1, source: "sos://doc/a/v1", title: null, snippet: null, withheld: true },
      ],
    });
    expect(withheld).toMatchObject({ phase: "done", withheld: true, text: "", followups: [] });
    expect(withheld.citations[0]?.withheld).toBe(true);
    expect(stateFromMessage({ ...base, cached: true }).cached).toBe(true);
  });

  it("reads an answer being written elsewhere again, but not one left streaming long ago", () => {
    const now = Date.parse("2026-09-30T10:00:00Z");
    const recent = message({ status: "streaming", created_at: "2026-09-30T09:58:00Z" });
    const old = message({ status: "streaming", created_at: "2026-09-30T09:00:00Z" });
    expect(writingElsewhere([recent] as never, now)).toBe(true);
    expect(writingElsewhere([old] as never, now)).toBe(false);
    expect(writingElsewhere([{ ...recent, superseded: true }] as never, now)).toBe(false);
    expect(writingElsewhere([message()] as never, now)).toBe(false);
  });

  it("parses past-chat sources to the message link", () => {
    const ref = parseSource(`sos://conversation/${CHAT.conversation}#q${CHAT.query}`);
    expect(ref).toEqual({ kind: "conversation", id: CHAT.conversation, queryId: CHAT.query });
    expect(ref && sourceHref(ref)).toBe(`/ask/c/${CHAT.conversation}#m-${CHAT.query}`);
    expect(parseSource("sos://conversation/not-a-uuid")).toBeNull();
  });
});

describe("thread versions and the conversation list", () => {
  it("groups superseded answers with the one that replaced them", () => {
    const groups = buildGroups(
      [
        message({ query_id: "a", superseded: true }),
        message({ query_id: "b" }),
        message({ query_id: "c" }),
        message({ query_id: "d", superseded: true }),
      ] as never,
      null,
    );
    expect(groups.map((g) => g.versions.length)).toEqual([2, 1, 1]);
    expect(groups[0]?.key).toBe("a");
  });

  it("sorts pinned first, then newest activity; filters titles by every word", () => {
    const items = sortConversations([
      summary({ id: "1", updated_at: "2026-09-01T00:00:00Z" }),
      summary({ id: "2", updated_at: "2026-09-03T00:00:00Z" }),
      summary({ id: "3", pinned: true, updated_at: "2026-08-01T00:00:00Z" }),
    ] as never);
    expect(items.map((i) => i.id)).toEqual(["3", "2", "1"]);
    expect(matchesFilter("Half-yearly EXAM dates", "exam half")).toBe(true);
    expect(matchesFilter("పరీక్షల తేదీలు", "తేదీలు")).toBe(true);
    expect(matchesFilter("Fees", "exam")).toBe(false);
  });
});

describe("composer keys (IME-safe, FR-KB-008)", () => {
  const key = (over: Partial<Parameters<typeof shouldSend>[0]> = {}) => ({
    key: "Enter",
    shiftKey: false,
    ctrlKey: false,
    metaKey: false,
    altKey: false,
    nativeEvent: { isComposing: false, keyCode: 13 },
    ...over,
  });
  it("Enter sends, Shift+Enter does not, Ctrl+Enter always sends", () => {
    expect(shouldSend(key(), false, false)).toBe(true);
    expect(shouldSend(key({ shiftKey: true }), false, false)).toBe(false);
    expect(shouldSend(key({ ctrlKey: true }), false, true)).toBe(true);
    expect(shouldSend(key({ key: "a" }), false, false)).toBe(false);
  });
  it("never sends while an input method composes (Telugu)", () => {
    expect(shouldSend(key({ nativeEvent: { isComposing: true } }), false, false)).toBe(false);
    expect(shouldSend(key({ nativeEvent: { keyCode: 229 } }), false, false)).toBe(false);
    expect(shouldSend(key(), true, false)).toBe(false);
  });
  it("on touch-first screens Enter adds a line", () => {
    expect(shouldSend(key(), false, true)).toBe(false);
  });
});

describe("smooth reveal and reduced motion", () => {
  it("reveals whole words at a pace that grows with the backlog", () => {
    const text = "Exams begin on 22/09/2026 for every class in the school";
    const first = nextRevealLength(text, 0, 16);
    expect(first).toBeGreaterThan(0);
    expect(text[first] === " " || first === text.length).toBe(true);
    // A big backlog catches up within about 0.7 s.
    const long = "word ".repeat(400);
    expect(nextRevealLength(long, 0, 700)).toBe(long.length);
    expect(nextRevealLength(text, text.length, 16)).toBe(text.length);
    // Telugu words are never cut inside.
    const te = "పరీక్షలు సోమవారం నుండి ప్రారంభమవుతాయి";
    const cut = nextRevealLength(te, 0, 16);
    expect(te.slice(0, cut)).toBe("పరీక్షలు");
  });

  it("without matchMedia (or with reduce) the whole text shows at once", () => {
    expect(prefersReducedMotion()).toBe(true);
    stubMatchMedia(true);
    const { result } = renderHook(() => useSmoothText("All of it", true));
    expect(result.current).toBe("All of it");
    const { result: reduced } = renderHook(() => useReducedMotion());
    expect(reduced.current).toBe(true);
  });

  it("with motion welcome the text grows frame by frame", () => {
    stubMatchMedia(false);
    const frames: FrameRequestCallback[] = [];
    vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => frames.push(cb));
    vi.stubGlobal("cancelAnimationFrame", () => undefined);
    const { result } = renderHook(({ text }) => useSmoothText(text, true), {
      initialProps: { text: "one two three four five six seven eight nine ten" },
    });
    expect(result.current).toBe("");
    act(() => frames.shift()?.(performance.now() + 50));
    expect(result.current.length).toBeGreaterThan(0);
    expect(result.current.length).toBeLessThan(49);
  });
});

describe("render isolation while streaming", () => {
  it("a turn re-renders only when its own fields or state change", () => {
    const handlers = {
      busy: false,
      canVerify: false,
      onVersion: () => undefined,
      onRegenerate: () => undefined,
      onEdit: () => null,
      onRetry: () => undefined,
      onFollowUp: () => undefined,
    };
    const state = { ...INITIAL_ASK };
    const turn = {
      key: "k",
      question: "Q?",
      state,
      live: false,
      latest: false,
      feedback: null,
      versions: null,
      past: false,
    };
    expect(sameTurn({ turn, handlers }, { turn: { ...turn }, handlers })).toBe(true);
    expect(sameTurn({ turn, handlers }, { turn: { ...turn, state: { ...state } }, handlers })).toBe(
      false,
    );
    expect(sameTurn({ turn, handlers }, { turn: { ...turn, latest: true }, handlers })).toBe(false);
  });
});

describe("stick to bottom", () => {
  it("is near the bottom within the slack", () => {
    expect(isNearBottom({ scrollTop: 900, clientHeight: 100, scrollHeight: 1050 })).toBe(true);
    expect(isNearBottom({ scrollTop: 100, clientHeight: 100, scrollHeight: 1050 })).toBe(false);
  });

  it("only the reader unpins; new text while unpinned is unread; jump pins again", () => {
    let state = { pinned: true, unread: false };
    state = stickReducer(state, { type: "scrolled", nearBottom: false, userInitiated: false });
    expect(state.pinned).toBe(true);
    state = stickReducer(state, { type: "scrolled", nearBottom: false, userInitiated: true });
    expect(state).toEqual({ pinned: false, unread: false });
    state = stickReducer(state, { type: "grew" });
    expect(state.unread).toBe(true);
    state = stickReducer(state, { type: "jumped" });
    expect(state).toEqual({ pinned: true, unread: false });
    state = stickReducer(
      { pinned: false, unread: true },
      { type: "scrolled", nearBottom: true, userInitiated: true },
    );
    expect(state).toEqual({ pinned: true, unread: false });
  });
});

describe("render safety", () => {
  it("Markdown never writes a style attribute, even for aligned tables (CSP, SEC-010)", () => {
    const { container } = renderWithIntl(
      <Markdown text={"| A | B |\n|:-:|--:|\n| 1 | 2 |"} citations={CITES} cite={() => null} />,
    );
    expect(container.querySelectorAll("[style]")).toHaveLength(0);
    expect(container.querySelector("td")).toHaveClass("text-center");
  });
});
