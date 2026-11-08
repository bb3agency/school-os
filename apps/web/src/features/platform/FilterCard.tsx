"use client";

import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { Button, ButtonLink } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { FilterChips } from "@/components/ui/FilterChips";
import { Icon } from "@/components/ui/Icon";
import { useRouter } from "@/i18n/navigation";

/** One filter set in the URL, shown as a removable chip (docs/17 §4.3). */
export interface ActiveFilter {
  /** The query parameter ("status"). */
  param: string;
  /** Its value in the URL ("past_due"). */
  raw: string;
  /** What the filter is ("Status"). */
  label: string;
  /** Its value as shown ("Past due"). */
  value: string;
}

/** `path` with the URL filters other than `drop` (all of them when `drop` is undefined). */
export function filterHref(path: string, active: readonly ActiveFilter[], drop?: string): string {
  const params = new URLSearchParams();
  for (const filter of active) {
    if (filter.param !== drop) params.append(filter.param, filter.raw);
  }
  const query = params.toString();
  return query ? `${path}?${query}` : path;
}

/** Builds the chip list, leaving out filters that are not set. */
export function activeFilters(
  entries: ReadonlyArray<ActiveFilter | false | null | undefined>,
): ActiveFilter[] {
  return entries.filter((entry): entry is ActiveFilter => Boolean(entry && entry.raw));
}

/** The translated label of a known value; a value typed into the URL is shown as it is. */
export function knownLabel<T extends string>(
  values: readonly T[],
  value: string,
  translate: (value: T) => string,
): string {
  return (values as readonly string[]).includes(value) ? translate(value as T) : value;
}

/**
 * The URL filters as chips: removing one opens the same list without it, "Clear all" opens
 * the unfiltered list. Its own component so the router is only asked for when a filter is set.
 */
export function UrlFilterChips({
  path,
  active,
}: {
  path: string;
  active: readonly ActiveFilter[];
}) {
  const router = useRouter();
  return (
    <FilterChips
      chips={active.map((filter) => ({
        id: filter.param,
        label: filter.label,
        value: filter.value,
      }))}
      onRemove={(param) => router.push(filterHref(path, active, param))}
      onClear={() => router.push(path)}
    />
  );
}

/**
 * Filter bar: GET form with the fields, "Apply filters" and, when filtered, "Clear filters".
 * `bare` drops the card, for the toolbar of a `DataTableV2` (docs/17 §4.3), which is already
 * inside the table's card; without it the filters sit in their own card above several tables.
 * With `path` and `active`, the filters set in the URL show under the form as `FilterChips`.
 */
export function FilterCard({
  children,
  clearHref,
  bare = false,
  path,
  active = [],
}: {
  children: ReactNode;
  /** Unfiltered URL; the "Clear filters" link shows only when a filter is set. */
  clearHref?: string | undefined;
  bare?: boolean;
  /** The list's own path ("/platform/invoices"), for the chips' links. */
  path?: string;
  /** The filters set in the URL, as chips. */
  active?: readonly ActiveFilter[];
}) {
  const tc = useTranslations("common");
  const t = useTranslations("platform.schools");
  const form = (
    // One column on phones (docs/17 §5.7): every filter takes the full width.
    <form method="get" className="flex flex-wrap items-end gap-3 max-sm:[&>*]:w-full">
      {children}
      <div className="relative flex flex-wrap gap-2">
        <Button type="submit" variant="secondary">
          <Icon name="filter" className="size-4" />
          {tc("applyFilters")}
        </Button>
        {clearHref ? (
          <ButtonLink href={clearHref} variant="ghost">
            {t("clearFilters")}
          </ButtonLink>
        ) : null}
      </div>
    </form>
  );
  const body =
    path && active.length > 0 ? (
      <div className="space-y-3">
        {form}
        <UrlFilterChips path={path} active={active} />
      </div>
    ) : (
      form
    );
  return bare ? body : <Card padding="sm">{body}</Card>;
}
