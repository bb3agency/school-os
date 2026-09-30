import { describe, expect, it } from "vitest";
import { createSseParser } from "./sse";

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
