"use client";

import { useLocale, useTranslations } from "next-intl";
import { useId } from "react";
import type { Locale } from "@/i18n/routing";
import { cn } from "@/lib/cn";
import { formatCount } from "@/lib/format";
import { Icon } from "./Icon";

export interface PaginationProps {
  /** First and last row number on this page (1-based), for "1–50 of 1,240". */
  from: number;
  to: number;
  /** Total rows when the API returns it; cursor lists leave it out ("1–50"). */
  total?: number | null;
  hasPrevious: boolean;
  hasNext: boolean;
  onPrevious: () => void;
  onNext: () => void;
  /** Rows per page and its choices (25/50/100); omit both to hide the choice. */
  pageSize?: number;
  pageSizes?: readonly number[];
  onPageSizeChange?: (size: number) => void;
  className?: string;
}

/**
 * Server-side paging under a table (docs/17 §4.3): the range shown, rows per page and
 * Previous/Next. Works with cursor APIs (no page numbers needed). The range is a polite
 * live region, so a keyboard user hears where they landed after Next.
 */
export function Pagination({
  from,
  to,
  total = null,
  hasPrevious,
  hasNext,
  onPrevious,
  onNext,
  pageSize,
  pageSizes,
  onPageSizeChange,
  className,
}: PaginationProps) {
  const t = useTranslations("ui.pagination");
  const locale = useLocale() as Locale;
  const sizeId = useId();
  const number = (value: number) => formatCount(value, locale) ?? String(value);
  const range =
    to < from
      ? t("none")
      : total === null
        ? t("range", { from: number(from), to: number(to) })
        : t("rangeOf", { from: number(from), to: number(to), total: number(total) });
  const button =
    "pressable inline-flex min-h-9 items-center gap-1.5 rounded-md border border-border-soft bg-surface px-3 text-sm font-semibold text-ink hover:bg-surface-muted disabled:cursor-not-allowed disabled:opacity-50 pointer-coarse:min-h-11";
  return (
    <nav
      aria-label={t("label")}
      className={cn("flex flex-wrap items-center justify-between gap-3 text-sm", className)}
      data-print="hide"
    >
      <p aria-live="polite" className="num text-ink-muted">
        {range}
      </p>
      <div className="flex flex-wrap items-center gap-2">
        {pageSize && pageSizes && onPageSizeChange ? (
          <span className="inline-flex items-center gap-2">
            <label htmlFor={sizeId} className="text-ink-muted">
              {t("rowsPerPage")}
            </label>
            <select
              id={sizeId}
              value={pageSize}
              onChange={(event) => onPageSizeChange(Number(event.target.value))}
              className="select-chevron min-h-9 rounded-md border border-border-control bg-surface ps-2.5 pe-8 text-sm text-ink pointer-coarse:min-h-11 pointer-coarse:text-base"
            >
              {pageSizes.map((size) => (
                <option key={size} value={size}>
                  {size}
                </option>
              ))}
            </select>
          </span>
        ) : null}
        <button type="button" className={button} disabled={!hasPrevious} onClick={onPrevious}>
          <Icon name="chevronLeft" className="size-4" />
          {t("previous")}
        </button>
        <button type="button" className={button} disabled={!hasNext} onClick={onNext}>
          {t("next")}
          <Icon name="chevronRight" className="size-4" />
        </button>
      </div>
    </nav>
  );
}
