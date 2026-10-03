"use client";

import { useTranslations } from "next-intl";
import { Fragment, useMemo, type ReactNode } from "react";
import { Link } from "@/i18n/navigation";
import { cn } from "@/lib/cn";
import { parseSource, sourceHref } from "./answer";
import { parseMarkdown, type Align, type Block, type Inline } from "./markdown-parse";

const ALIGN: Record<Exclude<Align, null>, string> = {
  left: "text-start",
  center: "text-center",
  right: "text-end",
};

interface RenderContext {
  cite: (index: number) => ReactNode;
  tableLabel: string;
}

function renderInline(nodes: readonly Inline[], ctx: RenderContext): ReactNode[] {
  return nodes.map((node, i) => {
    switch (node.type) {
      case "text":
        return <Fragment key={i}>{node.value}</Fragment>;
      case "br":
        return <br key={i} />;
      case "strong":
        return (
          <strong key={i} className="font-semibold text-ink">
            {renderInline(node.children, ctx)}
          </strong>
        );
      case "em":
        return <em key={i}>{renderInline(node.children, ctx)}</em>;
      case "del":
        return <del key={i}>{renderInline(node.children, ctx)}</del>;
      case "code":
        return (
          <code
            key={i}
            className="rounded-xs bg-surface-sunken px-1 py-0.5 font-mono text-[0.875em]"
          >
            {node.value}
          </code>
        );
      case "cite":
        return <Fragment key={i}>{ctx.cite(node.index)}</Fragment>;
      case "link": {
        // Only sos:// sources reach here; they open the existing screen (never a URL from prose).
        const ref = parseSource(node.href);
        const href = ref ? sourceHref(ref) : null;
        const label = renderInline(node.children, ctx);
        return href ? (
          <Link
            key={i}
            href={href}
            className="text-primary underline underline-offset-4 hover:no-underline"
          >
            {label}
          </Link>
        ) : (
          <Fragment key={i}>{label}</Fragment>
        );
      }
    }
  });
}

function renderBlock(block: Block, key: number, ctx: RenderContext): ReactNode {
  switch (block.type) {
    case "paragraph":
      return <p key={key}>{renderInline(block.children, ctx)}</p>;
    case "heading":
      // Shown as a bold line: the answer sits inside the page's heading outline.
      return (
        <p key={key} className="font-semibold text-ink">
          {renderInline(block.children, ctx)}
        </p>
      );
    case "rule":
      return <hr key={key} className="border-border" />;
    case "code":
      return (
        <pre
          key={key}
          className="overflow-x-auto rounded-md bg-surface-sunken p-3 font-mono text-sm whitespace-pre-wrap"
        >
          {block.value}
        </pre>
      );
    case "quote":
      return (
        <blockquote
          key={key}
          className="space-y-2 border-s-4 border-border-soft ps-3 text-ink-muted"
        >
          {block.children.map((child, i) => renderBlock(child, i, ctx))}
        </blockquote>
      );
    case "list": {
      const items = block.items.map((item, i) => (
        <li key={i} className="ps-1">
          {renderInline(item.children, ctx)}
          {item.sublist ? renderBlock(item.sublist, 0, ctx) : null}
        </li>
      ));
      return block.ordered ? (
        <ol key={key} start={block.start} className="list-decimal space-y-1 ps-6">
          {items}
        </ol>
      ) : (
        <ul key={key} className="list-disc space-y-1 ps-6">
          {items}
        </ul>
      );
    }
    case "table":
      return (
        // A wide table scrolls inside its own focusable region, never the page (docs/17 §5.1).
        <div
          key={key}
          className="table-scroll rounded-lg border border-border"
          tabIndex={0}
          role="region"
          aria-label={ctx.tableLabel}
        >
          <table className="w-full border-collapse text-sm">
            <thead className="bg-surface-muted text-ink-muted">
              <tr>
                {block.head.map((cell, c) => (
                  <th
                    key={c}
                    scope="col"
                    className={cn(
                      "border-b border-border px-3 py-2 text-start font-semibold",
                      ALIGN[block.align[c] ?? "left"],
                    )}
                  >
                    {renderInline(cell, ctx)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {block.rows.map((row, r) => (
                <tr key={r} className="border-b border-border last:border-b-0">
                  {row.map((cell, c) => (
                    <td
                      key={c}
                      className={cn("px-3 py-2 align-top", ALIGN[block.align[c] ?? "left"])}
                    >
                      {renderInline(cell, ctx)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
  }
}

/**
 * An answer's restricted markdown (markdown-parse.ts) as React elements: never HTML, links only to
 * the screens of `sos://` sources, `[n]` markers of known citations rendered by `cite`.
 */
export function Markdown({
  text,
  citations,
  cite,
  trailing,
  className,
}: {
  text: string;
  /** Indexes that are citations of this answer; other `[n]` stay text. */
  citations: ReadonlySet<number>;
  cite: (index: number) => ReactNode;
  /** Inline content after the last word (the streaming caret). */
  trailing?: ReactNode;
  className?: string;
}) {
  const t = useTranslations("ask.answer");
  const blocks = useMemo(() => parseMarkdown(text, citations), [text, citations]);
  const ctx: RenderContext = { cite, tableLabel: t("tableLabel") };
  const last = blocks[blocks.length - 1];
  const inlineTrailing = trailing !== undefined && last?.type === "paragraph";
  return (
    <div className={cn("chat-prose space-y-3 text-base leading-relaxed text-ink", className)}>
      {blocks.map((block, i) =>
        inlineTrailing && i === blocks.length - 1 && block.type === "paragraph" ? (
          <p key={i}>
            {renderInline(block.children, ctx)}
            {trailing}
          </p>
        ) : (
          renderBlock(block, i, ctx)
        ),
      )}
      {trailing !== undefined && !inlineTrailing ? <p>{trailing}</p> : null}
    </div>
  );
}

export default Markdown;
