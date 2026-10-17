"use client";

import type { QueryKey } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { asList, useApiQuery, type ApiQueryOptions } from "@/lib/bff/query";
import type { Loadable } from "@/lib/loadable";
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

/** Props for <Pager> from a paged list (spread with a label). */
export interface PagerState {
  page: number;
  onPrevious: (() => void) | undefined;
  onNext: (() => void) | undefined;
}

/**
 * One page of a cursor-paged list (docs/09 §2) with its pager state. `key` names the list and
 * its filters: when it changes, paging starts again at the first page. The rows are a Loadable
 * for the view; `pager` goes to <Pager>, which hides itself when there is only one page.
 */
export function usePagedList<T>(
  key: QueryKey,
  load: (cursor: string | undefined) => Promise<{ data?: T[] | null; next_cursor?: string | null }>,
  options: ApiQueryOptions = {},
): { rows: Loadable<T[]>; pager: PagerState } {
  const pages = useCursorStack();
  const keyText = JSON.stringify(key);
  const [listKey, setListKey] = useState(keyText);
  if (listKey !== keyText) {
    // New filters: back to the first page (state update during render, React's pattern for
    // adjusting state when a prop changes).
    setListKey(keyText);
    pages.reset();
  }
  const cursor = listKey === keyText ? pages.cursor : undefined;
  const result = useApiQuery([...key, "page", cursor ?? null], () => load(cursor), options);
  const rows: Loadable<T[]> =
    result.status === "ready" ? { status: "ready", data: asList(result.data.data) } : result;
  const next = result.status === "ready" ? (result.data.next_cursor ?? null) : null;
  return {
    rows,
    pager: {
      page: pages.page,
      onPrevious: pages.hasPrevious ? pages.previous : undefined,
      onNext: next ? () => pages.next(next) : undefined,
    },
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
