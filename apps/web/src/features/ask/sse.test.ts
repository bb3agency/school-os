import { describe, expect, it } from "vitest";
import { createSseParser } from "./sse";

describe("createSseParser edge cases (FR-KB-008)", () => {
  it("defaults the event name to message and strips one leading space only", () => {
    const parser = createSseParser();
    expect(parser.push("data:  two spaces\n\n")).toEqual([{ event: "message", data: " two spaces" }]);
  });

  it("ignores id and retry fields and events without data", () => {
    const parser = createSseParser();
    expect(parser.push("id: 1\nretry: 10\nevent: ping\n\n")).toEqual([]);
  });

  it("keeps UTF-8 Telugu text intact", () => {
    const parser = createSseParser();
    expect(parser.push('event: token\ndata: {"text":"పరీక్షలు"}\n\n')).toEqual([
      { event: "token", data: '{"text":"పరీక్షలు"}' },
    ]);
  });
});
