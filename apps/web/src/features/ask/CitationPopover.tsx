"use client";

import { useTranslations } from "next-intl";
import { useLayoutEffect, useRef, type PointerEvent, type RefObject } from "react";
import { Pill } from "@/components/ui/Badge";
import { Link } from "@/i18n/navigation";
import { parseSource, sourceHref, type AskCitation } from "./answer";
import { openLabel } from "./parts";

/** Space kept between the popover and the chip, and from the screen edges. */
const GAP = 8;

/**
 * The preview of one citation, shown beside its chip on hover and keyboard focus (lazy-loaded
 * with the chip's first use). Title, page and the quoted snippet; a link that opens the
 * source's own screen. It follows the chip in the DOM, so Tab from the chip reaches its link;
 * it stays while the pointer or focus is on it and Escape closes it (WCAG 1.4.13). Placed with
 * the CSSOM (fixed, flipped above the chip when there is no room below, kept on screen).
 */
export default function CitationPopover({
  citation,
  anchor,
  id,
  onPointerEnter,
  onPointerLeave,
}: {
  citation: AskCitation;
  anchor: RefObject<HTMLElement | null>;
  id: string;
  onPointerEnter: () => void;
  onPointerLeave: (event: PointerEvent<HTMLSpanElement>) => void;
}) {
  const t = useTranslations("ask.answer");
  const tt = useTranslations("tally");
  const box = useRef<HTMLSpanElement>(null);
  const ref = citation.withheld ? null : parseSource(citation.source);
  const href = ref ? sourceHref(ref) : null;
  const name = citation.withheld ? t("withheld") : citation.title.trim() || t("untitled");

  useLayoutEffect(() => {
    const node = box.current;
    const chip = anchor.current;
    if (!node || !chip) return;
    const rect = chip.getBoundingClientRect();
    const width = node.offsetWidth;
    const height = node.offsetHeight;
    const left = Math.min(
      Math.max(GAP, rect.left + rect.width / 2 - width / 2),
      window.innerWidth - width - GAP,
    );
    const below = rect.bottom + GAP;
    const top =
      below + height > window.innerHeight - GAP && rect.top - GAP - height > GAP
        ? rect.top - GAP - height
        : below;
    node.style.left = `${Math.max(GAP, left)}px`;
    node.style.top = `${top}px`;
  }, [anchor]);

  return (
    <span
      ref={box}
      id={id}
      className="chat-popover chat-pop block rounded-lg border border-border bg-surface p-3 text-start text-sm leading-normal font-normal text-ink shadow-popover"
      onPointerEnter={onPointerEnter}
      onPointerLeave={onPointerLeave}
    >
      <span className="flex items-start gap-2">
        <Pill variant="command" className="shrink-0">
          {citation.index}
        </Pill>
        <span className="min-w-0 flex-1 space-y-1">
          <span className="block font-semibold break-anywhere">{name}</span>
          {ref?.kind === "doc" && ref.page !== null ? (
            <span className="block text-xs text-ink-muted">{t("page", { page: ref.page })}</span>
          ) : null}
          {ref?.kind === "count" ? (
            <span className="block text-xs text-ink-muted">{t("countNote")}</span>
          ) : null}
          {ref?.kind === "fee" ? (
            <span className="block text-xs text-ink-muted">{tt("sourceNote")}</span>
          ) : null}
        </span>
      </span>
      {citation.withheld ? (
        <span className="mt-2 block text-xs text-ink-muted">{t("withheldNote")}</span>
      ) : citation.snippet ? (
        <span className="mt-2 block border-s-2 border-border-soft ps-2 text-ink-muted whitespace-pre-line">
          {citation.snippet}
        </span>
      ) : null}
      {ref && href && ref.kind !== "count" ? (
        <Link
          href={href}
          className="mt-2 inline-flex min-h-6 items-center font-semibold text-primary underline underline-offset-4 hover:no-underline"
          aria-label={`${t("openSource")}: ${name} (${ref.kind === "fee" ? tt("sourceOpen") : openLabel(ref, t)})`}
        >
          {t("openSource")}
        </Link>
      ) : null}
    </span>
  );
}
