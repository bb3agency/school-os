"use client";

import { useTranslations } from "next-intl";
import { useState } from "react";
import { Pill } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";

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
    <nav
      aria-label={label}
      className="flex flex-wrap items-center justify-end gap-3"
      data-print="hide"
    >
      <Button variant="secondary" size="sm" onClick={onPrevious} disabled={!onPrevious}>
        <Icon name="chevronLeft" className="size-4" />
        {t("previous")}
      </Button>
      <Pill variant="dark">{t("pageNumber", { page })}</Pill>
      <Button variant="secondary" size="sm" onClick={onNext} disabled={!onNext}>
        {t("next")}
        <Icon name="chevronRight" className="size-4" />
      </Button>
    </nav>
  );
}
