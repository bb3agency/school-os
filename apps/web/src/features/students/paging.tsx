"use client";

import { useTranslations } from "next-intl";
import { useState } from "react";
import { Button } from "@/components/ui/Button";

/**
 * Cursor paging (docs/09 §2: `?cursor=` with `next_cursor`): the cursors of the pages already
 * seen, so "Previous page" can go back without the API offering one.
 */
export function useCursorStack() {
  const [cursors, setCursors] = useState<string[]>([]);
  return {
    cursor: cursors.at(-1),
    page: cursors.length + 1,
    hasPrevious: cursors.length > 0,
    next: (cursor: string) => setCursors((list) => [...list, cursor]),
    previous: () => setCursors((list) => list.slice(0, -1)),
    reset: () => setCursors([]),
  };
}

/** Previous / Next buttons with the page number between them. Hidden when printing. */
export function Pager({
  label,
  page,
  onPrevious,
  onNext,
}: {
  label: string;
  page: number;
  onPrevious?: (() => void) | undefined;
  onNext?: (() => void) | undefined;
}) {
  const t = useTranslations("students.list");
  if (!onPrevious && !onNext) return null;
  return (
    <nav aria-label={label} className="flex flex-wrap items-center gap-3" data-print="hide">
      <Button variant="secondary" onClick={onPrevious} disabled={!onPrevious}>
        {t("previous")}
      </Button>
      <span className="text-sm text-ink-muted">{t("pageNumber", { page })}</span>
      <Button variant="secondary" onClick={onNext} disabled={!onNext}>
        {t("next")}
      </Button>
    </nav>
  );
}
