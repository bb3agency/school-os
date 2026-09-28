/**
 * Incremental Server-Sent Events parser for `POST /knowledge/ask` read through the BFF
 * (docs/06 §5.1). `EventSource` cannot send a POST body or the CSRF header, so the page reads
 * the fetch body stream and feeds decoded text chunks here.
 *
 * Follows the HTML "event stream" rules that matter for the API: lines end in LF, CRLF or CR;
 * a blank line dispatches; `event:` names the event (default `message`); several `data:` lines
 * are joined with LF; lines starting with `:` are comments; `id:`/`retry:` are ignored (the
 * page never reconnects: a question is asked once). A chunk may split a line anywhere.
 */

export interface SseMessage {
  event: string;
  data: string;
}

export interface SseParser {
  /** Feed decoded text; returns the events completed by it (possibly none). */
  push(chunk: string): SseMessage[];
  /** End of stream: an event without its final blank line is dropped (it may be cut off). */
  end(): SseMessage[];
}

export function createSseParser(): SseParser {
  let buffer = "";
  let event = "";
  let data: string[] = [];
  let pendingCr = false;

  function line(raw: string, out: SseMessage[]): void {
    if (raw === "") {
      if (data.length > 0) out.push({ event: event || "message", data: data.join("\n") });
      event = "";
      data = [];
      return;
    }
    if (raw.startsWith(":")) return;
    const colon = raw.indexOf(":");
    const field = colon === -1 ? raw : raw.slice(0, colon);
    let value = colon === -1 ? "" : raw.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") event = value;
    else if (field === "data") data.push(value);
  }

  return {
    push(chunk) {
      const out: SseMessage[] = [];
      let text = chunk;
      // A CR at the end of the previous chunk may be the first half of CRLF.
      if (pendingCr && text.startsWith("\n")) text = text.slice(1);
      pendingCr = false;
      buffer += text;
      for (;;) {
        const match = /\r\n|\r|\n/.exec(buffer);
        if (!match) break;
        if (match[0] === "\r" && match.index === buffer.length - 1) {
          pendingCr = true;
        }
        line(buffer.slice(0, match.index), out);
        buffer = buffer.slice(match.index + match[0].length);
      }
      return out;
    },
    end() {
      buffer = "";
      event = "";
      data = [];
      return [];
    },
  };
}
