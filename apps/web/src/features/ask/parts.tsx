"use client";

import { useTranslations } from "next-intl";
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
                className="mx-0.5 rounded-sm px-0.5 align-super text-xs font-semibold text-primary underline"
                aria-label={t("sourceLink", {
                  index: part.index,
                  title: titles.get(part.index) || t("untitled"),
                })}
              >
                [{part.index}]
              </a>
            ),
          )}
        </p>
      ))}
    </div>
  );
}

type LinkedRef = Exclude<SourceRef, { kind: "count" }>;

function openLabel(ref: LinkedRef, t: ReturnType<typeof useTranslations<"ask.answer">>): string {
  switch (ref.kind) {
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
  const ref = parseSource(source);
  const href = ref ? sourceHref(ref) : null;
  const name = title?.trim() || t("untitled");
  return (
    <li
      id={anchorId}
      tabIndex={anchorId ? -1 : undefined}
      className="space-y-2 rounded-md border border-border p-3 focus:outline-2 focus:outline-primary"
    >
      <p className="flex flex-wrap items-baseline gap-x-2 text-sm">
        {index !== undefined ? (
          <span className="font-semibold text-ink-muted">[{index}]</span>
        ) : null}
        {ref && ref.kind !== "count" && href ? (
          <Link
            href={href}
            className="font-semibold text-primary underline"
            // The visible title starts the name (WCAG 2.5.3); the rest says what opens.
            aria-label={`${name} (${openLabel(ref, t)})`}
          >
            {name}
          </Link>
        ) : (
          <span className="font-semibold">{name}</span>
        )}
        {ref?.kind === "doc" && ref.page !== null ? (
          <span className="text-ink-muted">{t("page", { page: ref.page })}</span>
        ) : null}
        {ref?.kind === "count" ? <span className="text-ink-muted">{t("countNote")}</span> : null}
        {ref === null ? <span className="text-ink-muted">{t("noLink")}</span> : null}
      </p>
      {meta ? <p className="text-xs text-ink-muted">{meta}</p> : null}
      {quote ? (
        <blockquote className="border-l-4 border-border pl-3 text-sm whitespace-pre-line text-ink">
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
