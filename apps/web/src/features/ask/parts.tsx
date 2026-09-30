"use client";

import { useTranslations } from "next-intl";
import { Pill } from "@/components/ui/Badge";
import { Icon } from "@/components/ui/Icon";
import { TabNav } from "@/components/ui/TabNav";
import { DownloadButton } from "@/features/documents/parts";
import { Link } from "@/i18n/navigation";
import { useStaffCan } from "@/lib/bff/staff-me";
import { displayText, parseSource, sourceHref, splitMarkers, type SourceRef } from "./answer";
import { ASK_PERM } from "./data";

export type AskTab = "ask" | "search" | "verified";

/** Ask / Search documents / Verified answers, each a URL (works without JS). */
export function AskTabs({ active }: { active: AskTab }) {
  const t = useTranslations("ask.tabs");
  const can = useStaffCan();
  const items = [
    ...(can(ASK_PERM.ask) ? [{ id: "ask", href: "/ask", label: t("ask") }] : []),
    ...(can(ASK_PERM.search) ? [{ id: "search", href: "/ask/search", label: t("search") }] : []),
    ...(can(ASK_PERM.ask) ? [{ id: "verified", href: "/ask/verified", label: t("verified") }] : []),
  ];
  if (items.length < 2) return null;
  return <TabNav label={t("label")} items={items} activeId={active} />;
}

/** Anchor id of source `n` in the source list below an answer. */
export function sourceAnchor(prefix: string, index: number): string {
  return `${prefix}-source-${index}`;
}

/**
 * Answer prose as TEXT (never HTML): paragraphs on blank lines, `[n]` markers that match a
 * citation become links to that source in the list below (built here, never from the prose).
 */
export function AnswerText({
  text,
  citations,
  anchorPrefix,
}: {
  text: string;
  citations: ReadonlyArray<{ index: number; title: string }>;
  anchorPrefix: string;
}) {
  const t = useTranslations("ask.answer");
  const known = new Set(citations.map((c) => c.index));
  const titles = new Map(citations.map((c) => [c.index, c.title]));
  const paragraphs = displayText(text)
    .split(/\n{2,}/)
    .map((p) => p.trim())
    .filter(Boolean);
  return (
    <div className="space-y-3 text-base leading-relaxed">
      {paragraphs.map((paragraph, i) => (
        <p key={i} className="whitespace-pre-line">
          {splitMarkers(paragraph, known).map((part, j) =>
            part.kind === "text" ? (
              <span key={j}>{part.value}</span>
            ) : (
              <a
                key={j}
                href={`#${sourceAnchor(anchorPrefix, part.index)}`}
                className="mx-0.5 inline-flex rounded-full align-text-bottom hover:opacity-80"
                aria-label={t("sourceLink", {
                  index: part.index,
                  title: titles.get(part.index) || t("untitled"),
                })}
              >
                {/* Numbered source chip; the link's name says which source it opens. */}
                <Pill variant="command">{part.index}</Pill>
              </a>
            ),
          )}
        </p>
      ))}
    </div>
  );
}

type LinkedRef = Exclude<SourceRef, { kind: "count" | "fee" }>;

/** What following a source's link opens ("open the document", "open the chat", …). */
export function openLabel(
  ref: LinkedRef,
  t: ReturnType<typeof useTranslations<"ask.answer">>,
): string {
  switch (ref.kind) {
    case "conversation":
      return t("openChat");
    case "doc":
      return t("openDocument");
    case "student":
      return t("openRecord");
    case "finding":
      return t("openFinding");
    case "change":
      return t("openChange");
    case "verified":
      return t("openVerified");
  }
}

/**
 * One source: its title (plain text from the API), where it is (page), a link to the existing
 * screen that opens it (document, student record, finding, correction request) and, for a
 * document page, a download of exactly that version (presigned, US-801 AC4). A source that
 * is not a well-formed `sos://` URI is shown without any link; so is a student count
 * (`sos://count/…`, numbers only), which has no screen to open.
 */
export function SourceChip({
  index,
  source,
  title,
  quote,
  anchorId,
  meta,
}: {
  index?: number;
  source: string;
  title?: string;
  quote?: string;
  anchorId?: string;
  /** Extra facts under the title (e.g. document type and date in search results). */
  meta?: string;
}) {
  const t = useTranslations("ask.answer");
  // Fee dues from Tally (M6) carry their labels in the tally namespace.
  const tt = useTranslations("tally");
  const ref = parseSource(source);
  const href = ref ? sourceHref(ref) : null;
  const name = title?.trim() || t("untitled");
  return (
    <li
      id={anchorId}
      tabIndex={anchorId ? -1 : undefined}
      className="space-y-3 rounded-lg border border-border bg-surface p-4 focus:outline-2 focus:outline-primary"
    >
      <div className="flex items-start gap-3">
        {index !== undefined ? (
          <Pill variant="command" className="mt-0.5 shrink-0">
            {index}
          </Pill>
        ) : (
          <span
            aria-hidden="true"
            className="flex size-7 shrink-0 items-center justify-center rounded-full bg-primary-soft text-primary"
          >
            <Icon name="file" className="size-4" />
          </span>
        )}
        <div className="min-w-0 flex-1 space-y-1">
          <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
            {ref && ref.kind !== "count" && href ? (
              <Link
                href={href}
                className="font-medium text-primary underline underline-offset-4 hover:no-underline"
                // The visible title starts the name (WCAG 2.5.3); the rest says what opens.
                aria-label={`${name} (${ref.kind === "fee" ? tt("sourceOpen") : openLabel(ref, t)})`}
              >
                {name}
              </Link>
            ) : (
              <span className="font-medium text-ink">{name}</span>
            )}
            {ref?.kind === "doc" && ref.page !== null ? (
              <Pill variant="tag">{t("page", { page: ref.page })}</Pill>
            ) : null}
            {ref?.kind === "count" ? <Pill variant="tag">{t("countNote")}</Pill> : null}
            {ref?.kind === "fee" ? <Pill variant="tag">{tt("sourceNote")}</Pill> : null}
          </p>
          {ref === null ? <p className="text-xs text-ink-muted">{t("noLink")}</p> : null}
          {meta ? <p className="text-xs text-ink-muted">{meta}</p> : null}
        </div>
      </div>
      {quote ? (
        <blockquote className="rounded-md border-l-4 border-border-soft bg-surface-muted px-3 py-2 text-sm whitespace-pre-line text-ink">
          {displayText(quote)}
        </blockquote>
      ) : null}
      {ref?.kind === "doc" ? (
        <DownloadButton
          documentId={ref.id}
          versionNo={ref.version}
          label={t("download", { version: ref.version })}
          description={t("downloadOf", { title: name })}
          variant="secondary"
        />
      ) : null}
    </li>
  );
}
