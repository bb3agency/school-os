/**
 * The restricted markdown of Ask answers (docs/06 §9 rule 5: paragraphs, lists, bold, tables;
 * no HTML, no links except `sos://` citations), parsed into a tiny tree that `Markdown.tsx`
 * renders with React elements only. Nothing here produces HTML: raw tags are dropped as text,
 * link targets other than `sos://` are dropped (the label stays as text), and `[n]` markers
 * become citation nodes only when `n` is a citation of the answer.
 *
 * In-house on purpose (no react-markdown/remark): the subset is small and fixed, it must be
 * safe by construction, and it adds no dependency to review, update or ship (see docs/17 §5.3).
 *
 * Supported: paragraphs (single line breaks kept), `#` headings (shown as bold lines), `-`/`*`/
 * `+` and `1.` lists with one nested level, GFM tables (with alignment), `>` quotes, fenced code
 * (shown as plain preformatted text), `---` rules; inline `**bold**`/`__bold__`, `*italic*`/
 * `_italic_`, `~~strike~~`, `` `code` ``, `[label](sos://…)`, `[n]` citation markers.
 */

export type Inline =
  | { type: "text"; value: string }
  | { type: "strong"; children: Inline[] }
  | { type: "em"; children: Inline[] }
  | { type: "del"; children: Inline[] }
  | { type: "code"; value: string }
  /** `href` is the `sos://` source; the renderer turns it into a screen link or plain text. */
  | { type: "link"; href: string; children: Inline[] }
  | { type: "cite"; index: number }
  | { type: "br" };

export type Align = "left" | "center" | "right" | null;

export interface ListItem {
  children: Inline[];
  sublist: ListBlock | null;
}

export interface ListBlock {
  type: "list";
  ordered: boolean;
  start: number;
  items: ListItem[];
}

export type Block =
  | { type: "paragraph"; children: Inline[] }
  | { type: "heading"; children: Inline[] }
  | ListBlock
  | { type: "table"; align: Align[]; head: Inline[][]; rows: Inline[][][] }
  | { type: "quote"; children: Block[] }
  | { type: "code"; value: string }
  | { type: "rule" };

const HTML_TAG = /<\/?[A-Za-z][^<>]*>/g;
const HTML_COMMENT = /<!--[\s\S]*?-->/g;

// --- inline ----------------------------------------------------------------------------------

interface InlineRule {
  kind: "code" | "strong" | "em" | "del" | "link" | "cite";
  pattern: RegExp;
}

// Order breaks ties (same start): code, then links and citations, then emphasis.
const INLINE_RULES: InlineRule[] = [
  { kind: "code", pattern: /`([^`\n]+)`/g },
  { kind: "link", pattern: /\[([^\]\n]+)\]\(\s*([^)\s]*)(?:\s+"[^"\n]*")?\s*\)/g },
  { kind: "cite", pattern: /\[(\d{1,3})\]/g },
  { kind: "strong", pattern: /\*\*(?=\S)([\s\S]*?\S)\*\*|__(?=\S)([\s\S]*?\S)__/g },
  { kind: "del", pattern: /~~(?=\S)([\s\S]*?\S)~~/g },
  {
    kind: "em",
    pattern:
      /\*(?=[^\s*])([^*\n]*?[^\s*])\*|(?<![\p{L}\p{N}_])_(?=\S)([^_\n]*?\S)_(?![\p{L}\p{N}_])/gu,
  },
];

function pushText(out: Inline[], value: string): void {
  if (!value) return;
  const parts = value.split("\n");
  parts.forEach((part, i) => {
    if (i > 0) out.push({ type: "br" });
    if (!part) return;
    const last = out[out.length - 1];
    if (last && last.type === "text") last.value += part;
    else out.push({ type: "text", value: part });
  });
}

/** Parse inline markdown. `citations` are the indexes that may become citation chips. */
export function parseInline(text: string, citations: ReadonlySet<number>, depth = 0): Inline[] {
  const out: Inline[] = [];
  let pos = 0;
  while (pos < text.length) {
    let best: { rule: InlineRule; match: RegExpExecArray } | null = null;
    for (const rule of INLINE_RULES) {
      rule.pattern.lastIndex = pos;
      let match = rule.pattern.exec(text);
      // `[n]` that is not a citation of this answer stays text: look for the next one.
      while (match && rule.kind === "cite" && !citations.has(Number(match[1]))) {
        match = rule.pattern.exec(text);
      }
      if (match && (!best || match.index < best.match.index)) best = { rule, match };
    }
    if (!best || depth > 8) {
      pushText(out, text.slice(pos));
      break;
    }
    const { rule, match } = best;
    pushText(out, text.slice(pos, match.index));
    const inner = match[1] ?? match[2] ?? "";
    switch (rule.kind) {
      case "code":
        out.push({ type: "code", value: inner });
        break;
      case "cite":
        out.push({ type: "cite", index: Number(match[1]) });
        break;
      case "link": {
        const label = parseInline(inner, citations, depth + 1);
        const target = (match[2] ?? "").trim();
        if (/^sos:\/\//.test(target)) out.push({ type: "link", href: target, children: label });
        // Any other target is dropped; its label stays as text (docs/06 §9 rule 5).
        else out.push(...label);
        break;
      }
      case "strong":
      case "em":
      case "del":
        out.push({ type: rule.kind, children: parseInline(inner, citations, depth + 1) });
        break;
    }
    pos = match.index + match[0].length;
  }
  return out;
}

// --- blocks ----------------------------------------------------------------------------------

const BULLET = /^(\s*)([-*+])\s+(.*)$/;
const ORDERED = /^(\s*)(\d{1,9})[.)]\s+(.*)$/;
const HEADING = /^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$/;
const RULE = /^\s{0,3}([-*_])(\s*\1){2,}\s*$/;
const FENCE = /^\s{0,3}(```|~~~)/;
const QUOTE = /^\s{0,3}>\s?(.*)$/;
const TABLE_DELIMITER = /^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)*\|?\s*$/;

function splitRow(line: string): string[] {
  let row = line.trim();
  if (row.startsWith("|")) row = row.slice(1);
  if (row.endsWith("|") && !row.endsWith("\\|")) row = row.slice(0, -1);
  const cells: string[] = [];
  let cell = "";
  for (let i = 0; i < row.length; i += 1) {
    const char = row[i] as string;
    if (char === "\\" && row[i + 1] === "|") {
      cell += "|";
      i += 1;
    } else if (char === "|") {
      cells.push(cell.trim());
      cell = "";
    } else cell += char;
  }
  cells.push(cell.trim());
  return cells;
}

function alignOf(cell: string): Align {
  const left = cell.startsWith(":");
  const right = cell.endsWith(":");
  if (left && right) return "center";
  if (right) return "right";
  if (left) return "left";
  return null;
}

function isTableStart(lines: readonly string[], i: number): boolean {
  const head = lines[i];
  const delimiter = lines[i + 1];
  return (
    head !== undefined &&
    delimiter !== undefined &&
    head.includes("|") &&
    delimiter.includes("-") &&
    TABLE_DELIMITER.test(delimiter) &&
    splitRow(head).length === splitRow(delimiter).length
  );
}

function listMatch(
  line: string,
): { indent: number; ordered: boolean; start: number; text: string } | null {
  const bullet = BULLET.exec(line);
  if (bullet && !RULE.test(line)) {
    return { indent: (bullet[1] ?? "").length, ordered: false, start: 1, text: bullet[3] ?? "" };
  }
  const ordered = ORDERED.exec(line);
  if (ordered) {
    return {
      indent: (ordered[1] ?? "").length,
      ordered: true,
      start: Number(ordered[2]),
      text: ordered[3] ?? "",
    };
  }
  return null;
}

function startsBlock(lines: readonly string[], i: number): boolean {
  const line = lines[i] ?? "";
  return (
    HEADING.test(line) ||
    RULE.test(line) ||
    FENCE.test(line) ||
    QUOTE.test(line) ||
    listMatch(line) !== null ||
    isTableStart(lines, i)
  );
}

function parseList(
  lines: readonly string[],
  start: number,
  citations: ReadonlySet<number>,
): { block: ListBlock; next: number } {
  const first = listMatch(lines[start] ?? "");
  if (!first) throw new Error("not a list");
  const block: ListBlock = { type: "list", ordered: first.ordered, start: first.start, items: [] };
  const baseIndent = first.indent;
  let i = start;
  let current: { text: string; sub: string[] } | null = null;
  const items: Array<{ text: string; sub: string[] }> = [];
  while (i < lines.length) {
    const line = lines[i] ?? "";
    if (line.trim() === "") {
      // A blank line ends the list unless the next line continues it.
      const next = lines[i + 1];
      const nextItem = next !== undefined ? listMatch(next) : null;
      if (nextItem && nextItem.indent >= baseIndent && nextItem.ordered === first.ordered) {
        i += 1;
        continue;
      }
      break;
    }
    const item = listMatch(line);
    if (item && item.indent <= baseIndent + 1) {
      if (item.ordered !== first.ordered) break;
      current = { text: item.text, sub: [] };
      items.push(current);
    } else if (item && current) {
      current.sub.push(line.slice(Math.min(item.indent, baseIndent + 2)));
    } else if (current && /^\s+\S/.test(line) && current.sub.length > 0) {
      current.sub.push(line.trim());
    } else if (current && !startsBlock(lines, i)) {
      current.text += `\n${line.trim()}`;
    } else break;
    i += 1;
  }
  block.items = items.map((item) => {
    const sub = item.sub.length > 0 ? listMatch(item.sub[0] ?? "") : null;
    return {
      children: parseInline(item.text, citations),
      sublist: sub ? parseList(item.sub, 0, citations).block : null,
    };
  });
  return { block, next: i };
}

/** Parse an answer into blocks (see the header for the subset). */
export function parseMarkdown(source: string, citations: ReadonlySet<number>): Block[] {
  const clean = source.replace(/\r\n?/g, "\n").replace(HTML_COMMENT, "").replace(HTML_TAG, "");
  const lines = clean.split("\n");
  const blocks: Block[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i] ?? "";
    if (line.trim() === "") {
      i += 1;
      continue;
    }
    if (FENCE.test(line)) {
      const fence = FENCE.exec(line)?.[1] ?? "```";
      const body: string[] = [];
      i += 1;
      while (i < lines.length && !(lines[i] ?? "").trim().startsWith(fence)) {
        body.push(lines[i] ?? "");
        i += 1;
      }
      i += 1;
      blocks.push({ type: "code", value: body.join("\n") });
      continue;
    }
    const heading = HEADING.exec(line);
    if (heading) {
      blocks.push({ type: "heading", children: parseInline(heading[1] ?? "", citations) });
      i += 1;
      continue;
    }
    if (RULE.test(line)) {
      blocks.push({ type: "rule" });
      i += 1;
      continue;
    }
    if (isTableStart(lines, i)) {
      const head = splitRow(line);
      const align = splitRow(lines[i + 1] ?? "").map(alignOf);
      const rows: Inline[][][] = [];
      i += 2;
      while (i < lines.length && (lines[i] ?? "").includes("|") && (lines[i] ?? "").trim()) {
        const cells = splitRow(lines[i] ?? "");
        rows.push(head.map((_, c) => parseInline(cells[c] ?? "", citations)));
        i += 1;
      }
      blocks.push({
        type: "table",
        align,
        head: head.map((cell) => parseInline(cell, citations)),
        rows,
      });
      continue;
    }
    if (QUOTE.test(line)) {
      const body: string[] = [];
      while (i < lines.length && QUOTE.test(lines[i] ?? "")) {
        body.push(QUOTE.exec(lines[i] ?? "")?.[1] ?? "");
        i += 1;
      }
      blocks.push({ type: "quote", children: parseMarkdown(body.join("\n"), citations) });
      continue;
    }
    if (listMatch(line)) {
      const { block, next } = parseList(lines, i, citations);
      blocks.push(block);
      i = next;
      continue;
    }
    const body: string[] = [line];
    i += 1;
    while (i < lines.length && (lines[i] ?? "").trim() !== "" && !startsBlock(lines, i)) {
      body.push(lines[i] ?? "");
      i += 1;
    }
    blocks.push({ type: "paragraph", children: parseInline(body.join("\n").trim(), citations) });
  }
  return blocks;
}

// --- streaming ------------------------------------------------------------------------------

const TABLE_ROW_START = /^\s*\|/;
const BARE_MARKER = /^\s*(?:[-*+]|\d{1,9}[.)]|#{1,6}|>)?\s*$/;

function cutUnclosed(text: string, token: string): string {
  let count = 0;
  let at = -1;
  let from = 0;
  for (;;) {
    const found = text.indexOf(token, from);
    if (found === -1) break;
    count += 1;
    at = found;
    from = found + token.length;
  }
  return count % 2 === 1 && at >= 0 ? text.slice(0, at) : text;
}

/**
 * The stable part of a still-growing answer, so the layout does not flicker while it streams:
 * a half-typed last line that would change block type (a table row, a bare list marker or
 * heading mark) waits for its line to end, and unclosed `**`, `~~`, `` ` `` and `[`…`](…`
 * in the last paragraph wait until they close. The final answer is rendered in full.
 */
export function stableStreamingText(text: string): string {
  const cut = text.lastIndexOf("\n");
  const head = cut === -1 ? "" : text.slice(0, cut + 1);
  let tail = cut === -1 ? text : text.slice(cut + 1);
  if (TABLE_ROW_START.test(tail) || BARE_MARKER.test(tail)) return head;
  const paragraphStart = Math.max(head.lastIndexOf("\n\n"), -1);
  let paragraph = head.slice(paragraphStart + 1) + tail;
  const before = head.slice(0, paragraphStart + 1);
  for (const token of ["**", "~~", "`"]) paragraph = cutUnclosed(paragraph, token);
  const open = paragraph.lastIndexOf("[");
  if (open !== -1) {
    const rest = paragraph.slice(open);
    // `[label]` then `(` without `)`, or a `[` still waiting for its `]`.
    if (!rest.includes("]") || (/^\[[^\]]*\]\(/.test(rest) && !rest.includes(")"))) {
      paragraph = paragraph.slice(0, open);
    }
  }
  tail = before + paragraph;
  return tail;
}

// --- plain text (copy) -----------------------------------------------------------------------

function inlineText(nodes: readonly Inline[], cite: (index: number) => string): string {
  return nodes
    .map((node) => {
      switch (node.type) {
        case "text":
        case "code":
          return node.value;
        case "br":
          return "\n";
        case "cite":
          return cite(node.index);
        default:
          return inlineText(node.children, cite);
      }
    })
    .join("");
}

function blockText(block: Block, cite: (index: number) => string, indent = ""): string {
  switch (block.type) {
    case "paragraph":
    case "heading":
      return indent + inlineText(block.children, cite);
    case "code":
      return block.value;
    case "rule":
      return "";
    case "quote":
      return block.children.map((child) => blockText(child, cite, indent)).join("\n\n");
    case "table":
      return [block.head, ...block.rows]
        .map((row) => row.map((cell) => inlineText(cell, cite)).join("\t"))
        .join("\n");
    case "list":
      return block.items
        .map((item, i) => {
          const marker = block.ordered ? `${block.start + i}.` : "-";
          const line = `${indent}${marker} ${inlineText(item.children, cite)}`;
          return item.sublist ? `${line}\n${blockText(item.sublist, cite, `${indent}  `)}` : line;
        })
        .join("\n");
  }
}

/**
 * The answer as plain text for the clipboard: markdown syntax removed, citation markers either
 * kept as ` [n]` (with a numbered source list appended by the caller) or dropped.
 */
export function toPlainText(
  source: string,
  citations: ReadonlySet<number>,
  keepMarkers: boolean,
): string {
  const cite = (index: number) => (keepMarkers ? ` [${index}]` : "");
  return parseMarkdown(source, citations)
    .map((block) => blockText(block, cite))
    .filter((text) => text !== "")
    .join("\n\n")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/ {2,}\[/g, " [")
    .replace(/ +([.,;:!?])/g, (all, mark: string) => (keepMarkers ? all : mark))
    .trim();
}
