"use client";

import { useTranslations } from "next-intl";
import { lazy, Suspense, useCallback, useEffect, useId, useRef, useState } from "react";
import { Pill } from "@/components/ui/Badge";
import { Icon } from "@/components/ui/Icon";
import { DownloadButton } from "@/features/documents/parts";
import { Link } from "@/i18n/navigation";
import { cn } from "@/lib/cn";
import { parseSource, sourceHref, type AskCitation } from "./answer";
import { openLabel } from "./parts";

// The popover's code loads with the first chip a reader points at (keeps the route lean).
const CitationPopover = lazy(() => import("./CitationPopover"));

/** Element id of source `index` in an answer's source cards. */
export function sourceCardId(prefix: string, index: number): string {
  return `${prefix}-source-${index}`;
}

const CLOSE_DELAY = 150;

/**
 * An inline `[n]` citation as a small superscript chip: a link to its source card below the
 * answer ("Source 1: <title>"), with a preview popover on hover and keyboard focus (title,
 * page, quote, "Open"). Escape closes the popover and leaves focus on the chip.
 */
export function CitationChip({
  citation,
  prefix,
}: {
  citation: AskCitation;
  /** Id prefix of this answer's source cards. */
  prefix: string;
}) {
  const t = useTranslations("ask.answer");
  const [open, setOpen] = useState(false);
  const chip = useRef<HTMLAnchorElement>(null);
  const wrapper = useRef<HTMLSpanElement>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const popoverId = useId();
  const name = citation.withheld ? t("withheld") : citation.title.trim() || t("untitled");

  const cancelClose = useCallback(() => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
  }, []);
  const show = useCallback(() => {
    cancelClose();
    setOpen(true);
  }, [cancelClose]);
  const hideSoon = useCallback(() => {
    cancelClose();
    timer.current = setTimeout(() => setOpen(false), CLOSE_DELAY);
  }, [cancelClose]);

  useEffect(() => cancelClose, [cancelClose]);

  // While open: scrolling (the chip moves away from the fixed popover) or focus leaving the
  // chip and its popover closes it; Escape closes it and leaves focus on the chip.
  useEffect(() => {
    if (!open) return;
    const close = () => setOpen(false);
    const onFocusIn = (event: FocusEvent) => {
      if (!wrapper.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      const inside = wrapper.current?.contains(document.activeElement) ?? false;
      setOpen(false);
      if (inside) {
        event.preventDefault();
        chip.current?.focus();
      }
    };
    window.addEventListener("scroll", close, { passive: true });
    window.addEventListener("resize", close);
    document.addEventListener("focusin", onFocusIn);
    document.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("scroll", close);
      window.removeEventListener("resize", close);
      document.removeEventListener("focusin", onFocusIn);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <span ref={wrapper} className="relative inline">
      <a
        ref={chip}
        href={`#${sourceCardId(prefix, citation.index)}`}
        aria-label={t("sourceLink", { index: citation.index, title: name })}
        aria-describedby={open ? popoverId : undefined}
        onPointerEnter={show}
        onPointerLeave={hideSoon}
        onFocus={show}
        className={cn(
          "relative -top-[0.4em] mx-0.5 inline-flex h-[1.35em] min-w-[1.35em] items-center justify-center rounded-full px-1",
          "border border-border-soft bg-surface-muted font-mono text-[0.7rem] leading-none font-semibold text-ink-muted no-underline",
          "hover:border-primary hover:bg-primary-soft hover:text-primary motion-safe:hover:transition-colors",
          open && "border-primary bg-primary-soft text-primary",
        )}
      >
        {citation.index}
      </a>
      {open ? (
        <Suspense fallback={null}>
          <CitationPopover
            citation={citation}
            anchor={chip}
            id={popoverId}
            onPointerEnter={cancelClose}
            onPointerLeave={hideSoon}
          />
        </Suspense>
      ) : null}
    </span>
  );
}

/**
 * One source under an answer: number, title (a link to the screen that opens it: document,
 * student record, finding, correction request, your earlier chat), page, the quoted snippet
 * and, for a document page, a download of exactly the cited version (US-801 AC4). A source the
 * member can no longer open is shown as such, without text. Not a `sos://` URI: no link.
 */
export function SourceCard({ citation, id }: { citation: AskCitation; id: string }) {
  const t = useTranslations("ask.answer");
  const tt = useTranslations("tally");
  const ref = citation.withheld ? null : parseSource(citation.source);
  const href = ref ? sourceHref(ref) : null;
  const name = citation.withheld ? t("withheld") : citation.title.trim() || t("untitled");
  return (
    <li
      id={id}
      tabIndex={-1}
      className="chat-enter flex min-w-0 gap-3 rounded-lg border border-border bg-surface p-3 focus:outline-2 focus:outline-primary"
    >
      <span
        aria-hidden="true"
        className="flex size-6 shrink-0 items-center justify-center rounded-full bg-surface-muted font-mono text-xs font-semibold text-ink-muted"
      >
        {citation.index}
      </span>
      <div className="min-w-0 flex-1 space-y-1.5">
        <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
          <span className="sr-only">{`${citation.index}.`}</span>
          {ref && ref.kind !== "count" && href ? (
            <Link
              href={href}
              className="font-medium text-primary underline underline-offset-4 break-anywhere hover:no-underline"
              // The visible title starts the name (WCAG 2.5.3); the rest says what opens.
              aria-label={`${name} (${ref.kind === "fee" ? tt("sourceOpen") : openLabel(ref, t)})`}
            >
              {name}
            </Link>
          ) : (
            <span className="font-medium break-anywhere text-ink">{name}</span>
          )}
          {ref?.kind === "doc" && ref.page !== null ? (
            <Pill variant="tag">{t("page", { page: ref.page })}</Pill>
          ) : null}
          {ref?.kind === "count" ? <Pill variant="tag">{t("countNote")}</Pill> : null}
          {ref?.kind === "fee" ? <Pill variant="tag">{tt("sourceNote")}</Pill> : null}
          {ref?.kind === "conversation" ? (
            <Pill variant="tag">
              <Icon name="message" className="size-3.5" />
              {t("chatSource")}
            </Pill>
          ) : null}
        </p>
        {citation.withheld ? (
          <p className="text-xs text-ink-muted">{t("withheldNote")}</p>
        ) : ref === null ? (
          <p className="text-xs text-ink-muted">{t("noLink")}</p>
        ) : null}
        {!citation.withheld && citation.snippet ? (
          <blockquote className="border-s-2 border-border-soft ps-2 text-sm whitespace-pre-line text-ink-muted">
            {citation.snippet}
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
      </div>
    </li>
  );
}
