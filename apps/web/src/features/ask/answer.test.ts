import { describe, expect, it } from "vitest";
import {
  applyEvent,
  displayText,
  INITIAL_ASK,
  joinToken,
  kbMessage,
  outcomeOf,
  parseSource,
  quoteFromSnippet,
  sourceHref,
  splitMarkers,
  type AskState,
} from "./answer";
import { createSseParser, type SseMessage } from "./sse";

const DOC = "0192f3a4-0000-7000-8000-00000000c701";
const STUDENT = "0192f3a4-0000-7000-8000-00000000c101";

function fold(messages: SseMessage[], state: AskState = INITIAL_ASK): AskState {
  return messages.reduce(applyEvent, state);
}

const ev = (event: string, data: unknown): SseMessage => ({ event, data: JSON.stringify(data) });

describe("SSE parser (docs/06 §5.1, FR-KB-008)", () => {
  it("parses events split across chunks at any point, with LF, CRLF and CR", () => {
    const text =
      'event: meta\r\ndata: {"query_id":"q1","language":"en","mode":"full"}\r\n\r\n' +
      ": keep-alive comment\n" +
      'event: token\ndata: {"text":"Exams begin"}\n\n' +
      'event: token\rdata: {"text":" on 22/09/2026. [1]"}\r\r' +
      "data: line one\ndata: line two\n\n";
    for (const size of [1, 2, 3, 7, 1000]) {
      const parser = createSseParser();
      const out: SseMessage[] = [];
      for (let i = 0; i < text.length; i += size) out.push(...parser.push(text.slice(i, i + size)));
      out.push(...parser.end());
      expect(out, `chunk size ${size}`).toEqual([
        { event: "meta", data: '{"query_id":"q1","language":"en","mode":"full"}' },
        { event: "token", data: '{"text":"Exams begin"}' },
        { event: "token", data: '{"text":" on 22/09/2026. [1]"}' },
        { event: "message", data: "line one\nline two" },
      ]);
    }
  });

  it("drops an event cut off before its blank line", () => {
    const parser = createSseParser();
    expect(parser.push('event: done\ndata: {"latency_ms":1}\n')).toEqual([]);
    expect(parser.end()).toEqual([]);
  });
});

describe("answer state from SSE events (FR-KB-005, FR-KB-007, FR-KB-011)", () => {
  it("builds an answer with citations and finishes on done", () => {
    const state = fold([
      ev("meta", { query_id: "q1", language: "te", mode: "full" }),
      ev("token", { text: "Exams begin on 22/09/2026. [1]" }),
      ev("token", { text: "Timings are 9 to 12. [1]" }),
      ev("citation", {
        index: 1,
        source: `sos://doc/${DOC}/v2#p1`,
        title: "Circular",
        snippet: "s",
      }),
      ev("done", { latency_ms: 4120, cited_sources: 1 }),
    ]);
    expect(state).toMatchObject({
      phase: "done",
      queryId: "q1",
      language: "te",
      mode: "full",
      text: "Exams begin on 22/09/2026. [1] Timings are 9 to 12. [1]",
      latencyMs: 4120,
    });
    expect(state.citations).toHaveLength(1);
    expect(outcomeOf(state)).toBe("answered");
  });

  it("an answer without citations is 'not found in school records' (US-801 AC3, US-803)", () => {
    const state = fold([
      ev("meta", { query_id: "q1", language: "en", mode: "full" }),
      ev("token", { text: "I could not find this in the school records you can access." }),
      ev("done", { latency_ms: 900, cited_sources: 0 }),
    ]);
    expect(outcomeOf(state)).toBe("not_found");
  });

  it("search-only mode keeps the reason from the error event (FR-KB-011)", () => {
    const state = fold([
      ev("meta", { query_id: "q1", language: "en", mode: "search_only" }),
      ev("error", { type: "ai_budget_exhausted", message_key: "kb.errors.budget" }),
      ev("citation", { index: 1, source: `sos://doc/${DOC}/v1#p3`, title: "T", snippet: "x" }),
      ev("done", { latency_ms: 300, cited_sources: 1 }),
    ]);
    expect(state.notice).toBe("budget");
    expect(outcomeOf(state)).toBe("search_only");
  });

  it("ignores unknown events and malformed data (forward compatible)", () => {
    const before = fold([ev("meta", { query_id: "q1", language: "en", mode: "full" })]);
    const after = fold(
      [
        ev("delta", { text: 7 }),
        ev("final", { text: 7, status: "weird" }),
        ev("tool", { name: "search_documents" }),
        { event: "token", data: "not json" },
        ev("token", { text: 42 }),
        ev("citation", { index: "1", source: "x" }),
        ev("citation", { index: 0, source: "x" }),
        ev("meta", { query_id: 5, mode: "weird", language: "fr" }),
      ],
      before,
    );
    expect(after).toMatchObject({
      queryId: "q1",
      mode: "full",
      language: "en",
      text: "",
      preview: "",
      finalized: false,
      status: null,
    });
    expect(after.citations).toEqual([]);
  });

  it("maps message keys, unknown ones to 'unavailable'", () => {
    expect(kbMessage("kb.errors.disabled")).toBe("disabled");
    expect(kbMessage("kb.errors.rate_limited")).toBe("rate_limited");
    expect(kbMessage("kb.errors.internal")).toBe("internal");
    expect(kbMessage("kb.errors.something_new")).toBe("unavailable");
    expect(kbMessage(undefined)).toBe("unavailable");
  });

  it("joins legacy token segments with ONE space (docs/06 §5.1: tokens joined = final.text)", () => {
    expect(joinToken("One. [1]", "Two.")).toBe("One. [1] Two.");
    expect(joinToken("One.", " Two.")).toBe("One. Two.");
    expect(joinToken("Exams begin", "on 22/09/2026.")).toBe("Exams begin on 22/09/2026.");
    expect(joinToken("", "Hi")).toBe("Hi");
  });
});

describe("answer state, streamed contract v2 (docs/06 §5.1, FR-KB-008, FR-KB-011)", () => {
  const META = ev("meta", { query_id: "q1", language: "en", mode: "full" });
  const CITE = ev("citation", {
    index: 1,
    source: `sos://doc/${DOC}/v2#p1`,
    title: "Circular",
    snippet: "s",
  });

  it("appends delta text verbatim as an unchecked preview, then final replaces it", () => {
    const streaming = fold([
      META,
      ev("delta", { text: "Exams begin " }),
      ev("delta", { text: " on 22/09/2026 at 9 [3]" }),
    ]);
    expect(streaming).toMatchObject({
      phase: "streaming",
      preview: "Exams begin  on 22/09/2026 at 9 [3]",
      text: "",
      finalized: false,
    });
    expect(outcomeOf(streaming)).toBe("pending");

    const finished = fold(
      [
        ev("final", {
          text: "Exams begin on 22/09/2026. [1]",
          replaced: true,
          status: "answered",
          mode: "full",
        }),
        // Legacy segments after final are ignored (they would double the text).
        ev("token", { text: "Exams begin on 22/09/2026. [1]" }),
        ev("delta", { text: "late preview" }),
        CITE,
        ev("done", { latency_ms: 900, cited_sources: 1, status: "answered", mode: "full" }),
      ],
      streaming,
    );
    expect(finished).toMatchObject({
      phase: "done",
      preview: "",
      text: "Exams begin on 22/09/2026. [1]",
      finalized: true,
      replaced: true,
      status: "answered",
      mode: "full",
    });
    expect(outcomeOf(finished)).toBe("answered");
  });

  it("search-only comes from final/done, not from meta (meta always says full)", () => {
    const state = fold([
      META,
      ev("delta", { text: "Exams begin on" }),
      ev("error", { type: "ai_unavailable", message_key: "kb.errors.unavailable" }),
      ev("final", { text: "", replaced: true, status: "search_only", mode: "search_only" }),
      CITE,
      ev("done", { latency_ms: 300, cited_sources: 1, status: "search_only", mode: "search_only" }),
    ]);
    expect(state).toMatchObject({ mode: "search_only", status: "search_only", preview: "" });
    expect(state.notice).toBe("unavailable");
    expect(outcomeOf(state)).toBe("search_only");
  });

  it("done.status is the source of truth for the final state", () => {
    const base = [META, ev("final", { text: "x", replaced: false, status: "answered" })];
    for (const status of ["answered", "not_found", "refused", "search_only"] as const) {
      const state = fold([...base, ev("done", { latency_ms: 1, cited_sources: 0, status })]);
      expect(outcomeOf(state), status).toBe(status);
    }
    // An unknown status keeps what final said.
    expect(outcomeOf(fold([...base, ev("done", { latency_ms: 1, status: "new" })]))).toBe(
      "answered",
    );
  });

  it("final status is shown before done arrives (e.g. not_found, refused)", () => {
    const state = fold([
      META,
      ev("final", { text: "Not found.", replaced: false, status: "refused", mode: "full" }),
    ]);
    expect(state.phase).toBe("streaming");
    expect(outcomeOf(state)).toBe("refused");
  });

  it("internal_error sends no final and ends with done.status error", () => {
    const state = fold([
      META,
      ev("delta", { text: "Exams" }),
      ev("error", { type: "internal_error", message_key: "kb.errors.internal" }),
      ev("done", { latency_ms: 50, cited_sources: 0, status: "error", mode: "full" }),
    ]);
    expect(state).toMatchObject({ phase: "done", status: "error", notice: "internal" });
    expect(state.errorType).toBe("internal_error");
    expect(outcomeOf(state)).toBe("error");
  });

  it("a server without final still works: tokens joined with one space", () => {
    const state = fold([
      META,
      ev("token", { text: "Exams begin on 22/09/2026. [1]" }),
      ev("token", { text: "Timings are 9 to 12. [1]" }),
      CITE,
      ev("done", { latency_ms: 10, cited_sources: 1 }),
    ]);
    expect(state.text).toBe("Exams begin on 22/09/2026. [1] Timings are 9 to 12. [1]");
    expect(outcomeOf(state)).toBe("answered");
  });
});

describe("display text: never HTML, never links from model output (docs/06 §9 rule 5)", () => {
  it("removes tags and markdown link targets, keeps text", () => {
    expect(
      displayText('<script>alert(1)</script>See <a href="https://evil.example">here</a> **now**'),
    ).toBe("alert(1)See here now");
    expect(displayText("Read [the circular](https://evil.example/x) [1]")).toBe(
      "Read the circular [1]",
    );
  });

  it("turns only known [n] markers into citation parts", () => {
    expect(splitMarkers("A [1] B [7] C [2]", new Set([1, 2]))).toEqual([
      { kind: "text", value: "A " },
      { kind: "cite", index: 1 },
      { kind: "text", value: " B [7] C " },
      { kind: "cite", index: 2 },
    ]);
  });
});

describe("sos:// sources (docs/06 §8)", () => {
  it("parses document, record and other sources and links them to existing screens", () => {
    const doc = parseSource(`sos://doc/${DOC}/v2#p4`);
    expect(doc).toEqual({ kind: "doc", id: DOC, version: 2, page: 4 });
    expect(sourceHref(doc!)).toBe(`/documents/${DOC}`);
    const student = parseSource(`sos://student/${STUDENT}/field/admission_date?src=register`);
    expect(student).toEqual({ kind: "student", id: STUDENT, field: "admission_date" });
    expect(sourceHref(student!)).toBe(`/students/${STUDENT}`);
    expect(sourceHref(parseSource(`sos://finding/${DOC}`)!)).toBe(`/findings/${DOC}`);
    expect(sourceHref(parseSource(`sos://change/${DOC}`)!)).toBe(`/change-requests/${DOC}`);
    expect(sourceHref(parseSource(`sos://verified/${DOC}`)!)).toBe("/ask/verified");
  });

  it("a student count source (numbers only) parses but has no screen to open", () => {
    const count = parseSource(`sos://count/${DOC}`);
    expect(count).toEqual({ kind: "count", id: DOC });
    expect(sourceHref(count!)).toBeNull();
    expect(parseSource("sos://count/not-a-uuid")).toBeNull();
  });

  it("a fee dues source from Tally opens the fee dues screen, never a ledger URL (FR-TALLY-008)", () => {
    const fee = parseSource(`sos://fee/${DOC}`);
    expect(fee).toEqual({ kind: "fee", id: DOC });
    expect(sourceHref(fee!)).toBe("/fees");
    expect(parseSource("sos://fee/not-a-uuid")).toBeNull();
    expect(parseSource(`sos://fee/${DOC}/ledger`)).toBeNull();
  });

  it("refuses anything that is not a well-formed sos:// URI", () => {
    for (const bad of [
      "https://evil.example/",
      "javascript:alert(1)",
      `sos://doc/${DOC}/../x`,
      `sos://doc/not-a-uuid/v1#p1`,
      `sos://student/${STUDENT}/field/x?src=<b>`,
      `sos://other/${DOC}`,
    ]) {
      expect(parseSource(bad), bad).toBeNull();
    }
  });

  it("quotes a snippet without its ellipsis", () => {
    expect(quoteFromSnippet("Exams begin on 22/09/2026 …")).toBe("Exams begin on 22/09/2026");
    expect(quoteFromSnippet("Whole text")).toBe("Whole text");
  });
});
