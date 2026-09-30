import { describe, expect, it } from "vitest";
import { createSseParser, parseAskEvent } from "./sse";

const msg = (event: string, data: unknown) => ({ event, data: JSON.stringify(data) });

describe("parseAskEvent: the Ask event payloads (docs/06 §5.1, FR-KB-008, FR-KB-012)", () => {
  it("reads every event exactly as the API sends it", () => {
    const events = [
      msg("meta", {
        query_id: "q1",
        language: "te",
        mode: "full",
        conversation_id: "c1",
        title: "పరీక్షలు",
        cached: true,
        cached_from: "q0",
        summarized: false,
      }),
      msg("status", { step: "searching_chats", tool: "search_chats", count: null }),
      msg("delta", { text: " Exams " }),
      msg("error", { type: "ai_budget_exhausted", message_key: "kb.errors.budget" }),
      msg("final", {
        text: "x",
        replaced: true,
        status: "search_only",
        mode: "search_only",
        summarized: true,
      }),
      msg("token", { text: "x" }),
      msg("citation", { index: 2, source: "sos://doc/a/v1#p1", title: "T", snippet: "S" }),
      msg("followups", { questions: ["A?"] }),
      msg("memory", { action: "suggested", item_id: "m1", text: "Prefers Telugu" }),
      msg("done", { latency_ms: 10, cited_sources: 1, status: "error", mode: "search_only" }),
    ].map(parseAskEvent);
    expect(events.map((e) => e?.event)).toEqual([
      "meta",
      "status",
      "delta",
      "error",
      "final",
      "token",
      "citation",
      "followups",
      "memory",
      "done",
    ]);
    expect(events[0]?.data).toMatchObject({ conversation_id: "c1", cached_from: "q0" });
    expect(events[1]?.data).toEqual({ step: "searching_chats", tool: "search_chats", count: null });
    expect(events[2]?.data).toEqual({ text: " Exams " });
  });

  it("drops unknown events and payloads without their defining fields", () => {
    expect(parseAskEvent(msg("tool", { name: "x" }))).toBeNull();
    expect(parseAskEvent(msg("toString", {}))).toBeNull();
    expect(parseAskEvent({ event: "meta", data: "not json" })).toBeNull();
    expect(parseAskEvent(msg("meta", { language: "en" }))).toBeNull();
    expect(parseAskEvent(msg("status", { step: "teleporting" }))).toBeNull();
    expect(parseAskEvent(msg("citation", { index: 0, source: "x" }))).toBeNull();
    expect(
      parseAskEvent(msg("memory", { action: "forgotten", item_id: "m", text: "" })),
    ).toBeNull();
  });

  it("ignores a field with an unexpected value, not the whole event", () => {
    expect(parseAskEvent(msg("done", { latency_ms: 5, status: "new" }))?.data).toEqual({
      latency_ms: 5,
      cited_sources: undefined,
      status: undefined,
      mode: undefined,
    });
    expect(
      parseAskEvent(msg("status", { step: "writing", tool: 3, count: -1 }))?.data,
    ).toMatchObject({ step: "writing", tool: undefined, count: undefined });
  });
});

describe("createSseParser edge cases (FR-KB-008)", () => {
  it("defaults the event name to message and strips one leading space only", () => {
    const parser = createSseParser();
    expect(parser.push("data:  two spaces\n\n")).toEqual([
      { event: "message", data: " two spaces" },
    ]);
  });

  it("ignores id and retry fields and events without data", () => {
    const parser = createSseParser();
    expect(parser.push("id: 1\nretry: 10\nevent: ping\n\n")).toEqual([]);
  });

  it("reads the new status, followups and memory events split anywhere, CRLF too (FR-KB-008)", () => {
    const text =
      'event: status\r\ndata: {"step":"searching_documents","count":4}\r\n\r\n' +
      'event: followups\ndata: {"questions":["A?","B?"]}\n\n' +
      'event: memory\rdata: {"action":"saved","item_id":"m1","text":"x"}\r\r';
    for (const size of [1, 3, 7, 40]) {
      const parser = createSseParser();
      const out = [];
      for (let i = 0; i < text.length; i += size) out.push(...parser.push(text.slice(i, i + size)));
      expect(out).toEqual([
        { event: "status", data: '{"step":"searching_documents","count":4}' },
        { event: "followups", data: '{"questions":["A?","B?"]}' },
        { event: "memory", data: '{"action":"saved","item_id":"m1","text":"x"}' },
      ]);
    }
  });

  it("drops an event cut off by the end of the stream", () => {
    const parser = createSseParser();
    expect(parser.push('event: final\ndata: {"text":"half')).toEqual([]);
    expect(parser.end()).toEqual([]);
    expect(parser.push("\n\n")).toEqual([]);
  });

  it("keeps UTF-8 Telugu text intact", () => {
    const parser = createSseParser();
    expect(parser.push('event: token\ndata: {"text":"పరీక్షలు"}\n\n')).toEqual([
      { event: "token", data: '{"text":"పరీక్షలు"}' },
    ]);
  });
});
