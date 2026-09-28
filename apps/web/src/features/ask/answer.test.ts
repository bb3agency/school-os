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
        ev("delta", { text: "ignored" }),
        ev("tool", { name: "search_documents" }),
        { event: "token", data: "not json" },
        ev("token", { text: 42 }),
        ev("citation", { index: "1", source: "x" }),
        ev("citation", { index: 0, source: "x" }),
        ev("meta", { query_id: 5, mode: "weird", language: "fr" }),
      ],
      before,
    );
    expect(after).toMatchObject({ queryId: "q1", mode: "full", language: "en", text: "" });
    expect(after.citations).toEqual([]);
  });

  it("maps message keys, unknown ones to 'unavailable'", () => {
    expect(kbMessage("kb.errors.disabled")).toBe("disabled");
    expect(kbMessage("kb.errors.rate_limited")).toBe("rate_limited");
    expect(kbMessage("kb.errors.something_new")).toBe("unavailable");
    expect(kbMessage(undefined)).toBe("unavailable");
  });

  it("joins segments with one space after punctuation or a marker, deltas as sent", () => {
    expect(joinToken("One. [1]", "Two.")).toBe("One. [1] Two.");
    expect(joinToken("One.", " Two.")).toBe("One. Two.");
    expect(joinToken("Exa", "ms")).toBe("Exams");
    expect(joinToken("", "Hi")).toBe("Hi");
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
