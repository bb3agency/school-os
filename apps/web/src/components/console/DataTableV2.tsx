"use client";

/*
 * DataTable v2 (docs/17 §4.3; research report §7.2). Written for SchoolOS after the Calevate
 * console dataTable reference (docs/design/reference/calevate/components/console); no code
 * from "Pixel Perfect".
 */

import { useTranslations } from "next-intl";
import {
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from "react";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { FilterChips, type FilterChip } from "@/components/ui/FilterChips";
import { Icon } from "@/components/ui/Icon";
import { LoadFade } from "@/components/ui/LoadFade";
import { LoadingState } from "@/components/ui/LoadingState";
import { NarrowSwitch } from "@/components/ui/NarrowSwitch";
import { Popover } from "@/components/ui/Popover";
import { StackedRows, type Column, type TableDensity } from "@/components/ui/Table";
import { useDensity } from "@/components/shell/density";
import { cn } from "@/lib/cn";
import type { Loadable } from "@/lib/loadable";
import { readPref, writePref } from "@/lib/ui-prefs";

export interface ColumnV2<T> extends Column<T> {
  /** A plain-text name for the column chooser (the header may be an element). */
  name?: string;
  /** The person may hide it (default: every column but the first). */
  hideable?: boolean;
  /** Hidden until the person shows it. */
  defaultHidden?: boolean;
}

export interface DataTableV2Props<T> {
  /** Names the table and its scroll region. */
  caption: string;
  captionHidden?: boolean;
  columns: ReadonlyArray<ColumnV2<T>>;
  state: Loadable<readonly T[]>;
  rowKey: (row: T) => string;
  /**
   * Remembers this table's hidden columns and density on this computer (UI preference only,
   * docs/13 §5). Use a stable id per screen ("students"); omit to remember nothing.
   */
  id?: string;
  /** Search box and filter controls, left of the column and density buttons. */
  toolbar?: ReactNode;
  /** Active filters as chips (with remove and "Clear all"). */
  chips?: readonly FilterChip[];
  onRemoveChip?: (id: string) => void;
  onClearFilters?: () => void;
  /** First use: what this list is and one action. */
  emptyTitle: ReactNode;
  emptyBody?: ReactNode;
  emptyAction?: ReactNode;
  /** Shown instead when filters are active and nothing matches. */
  noResultsTitle?: ReactNode;
  /** Error state: a "Try again" button. */
  onRetry?: () => void;
  /**
   * Row selection with a bulk bar, only for tables that have bulk actions. `actions` gets the
   * selected rows; record-changing actions must confirm through ActionDialog (count +
   * consequence). `rowLabel` names each checkbox ("Select Sample student A").
   */
  selection?: {
    rowLabel: (row: T) => string;
    actions: (selected: readonly T[], clear: () => void) => ReactNode;
  };
  /** Below the table: `Pagination`. */
  footer?: ReactNode;
  /** Below 640px show cards instead (record lists, docs/17 §5.7). */
  stacked?: boolean;
  /** Pins this table's density; otherwise the viewer's choice (and the table's own toggle). */
  density?: TableDensity;
}

const NUMERIC = "num text-end tabular-nums whitespace-nowrap";

/** Preferences change only through this table's own controls (state above), so nothing to
    subscribe to: the snapshot is read once after hydration. */
function subscribePrefs(): () => void {
  return () => undefined;
}

/**
 * A record list with a toolbar (search, filters, column chooser, density), filter chips,
 * a sticky header inside its own scroll box, optional row selection with a bulk bar, and
 * distinct loading, error, unavailable, first-use and no-results states. Keyboard: every
 * control is a native button, checkbox or link in reading order; the scroll region is
 * focusable and named. No motion on rows, sorting or paging (docs/17 §5.5).
 */
export function DataTableV2<T>({
  caption,
  captionHidden = false,
  columns,
  state,
  rowKey,
  id,
  toolbar,
  chips = [],
  onRemoveChip,
  onClearFilters,
  emptyTitle,
  emptyBody,
  emptyAction,
  noResultsTitle,
  onRetry,
  selection,
  footer,
  stacked = false,
  density: pinnedDensity,
}: DataTableV2Props<T>) {
  const t = useTranslations("ui.table");
  const tc = useTranslations("common");
  const te = useTranslations("errors");
  const captionId = useId();
  const headerCheckbox = useRef<HTMLInputElement>(null);

  const defaultHidden = useMemo(
    () => columns.filter((column) => column.defaultHidden).map((column) => column.key),
    [columns],
  );
  // Remembered choices (UI preferences) are read after hydration through an external store:
  // the server render and the first client render use the defaults, so nothing mismatches.
  const storedHidden = useSyncExternalStore(
    subscribePrefs,
    () => (id ? readPref(`table.${id}.hidden`) : null),
    () => null,
  );
  const storedDensity = useSyncExternalStore(
    subscribePrefs,
    () => (id ? readPref(`table.${id}.density`) : null),
    () => null,
  );
  const [hiddenChoice, setHiddenChoice] = useState<readonly string[] | null>(null);
  const [densityChoice, setDensityChoice] = useState<TableDensity | null>(null);
  const [selection$, setSelection] = useState<{ page: string; keys: ReadonlySet<string> }>({
    page: "",
    keys: new Set(),
  });
  const [viewerDensity] = useDensity();

  const known = useMemo(() => new Set(columns.map((column) => column.key)), [columns]);
  const hidden: readonly string[] =
    hiddenChoice ??
    (storedHidden !== null
      ? storedHidden.split(",").filter((key) => known.has(key))
      : defaultHidden);
  const ownDensity: TableDensity | null =
    densityChoice ??
    (storedDensity === "compact" || storedDensity === "comfortable" ? storedDensity : null);

  const rows = state.status === "ready" ? state.data : null;
  const rowKeys = useMemo(() => (rows ? rows.map(rowKey) : []), [rows, rowKey]);
  // Paging, sorting or filtering changes the rows on the page: that clears the selection
  // (a selection belongs to the page it was made on).
  const page = rowKeys.join("\u0000");
  const selected: ReadonlySet<string> = selection$.page === page ? selection$.keys : new Set();
  const setSelected = (next: ReadonlySet<string>) => setSelection({ page, keys: next });
  const visibleSelected = rowKeys.filter((key) => selected.has(key));
  const allSelected = rowKeys.length > 0 && visibleSelected.length === rowKeys.length;
  const someSelected = visibleSelected.length > 0 && !allSelected;

  useEffect(() => {
    if (headerCheckbox.current) headerCheckbox.current.indeterminate = someSelected;
  }, [someSelected]);

  const shown = columns.filter((column) => !hidden.includes(column.key));
  const density = pinnedDensity ?? ownDensity ?? undefined;

  const toggleColumn = (key: string) => {
    const next = hidden.includes(key) ? hidden.filter((item) => item !== key) : [...hidden, key];
    setHiddenChoice(next);
    if (id) writePref(`table.${id}.hidden`, next.join(","));
  };

  const chooseDensity = (next: TableDensity) => {
    setDensityChoice(next);
    if (id) writePref(`table.${id}.density`, next);
  };

  const clearSelection = () => setSelected(new Set());
  const selectedRows = rows ? rows.filter((row) => selected.has(rowKey(row))) : [];

  const columnChooser = (
    <Popover
      title={t("columns")}
      align="end"
      trigger={
        <>
          <Icon name="layers" className="size-4" />
          <span className="max-md:sr-only">{t("columns")}</span>
        </>
      }
    >
      <fieldset>
        <legend className="mb-2 text-sm font-semibold text-ink">{t("showColumns")}</legend>
        <div className="space-y-1">
          {columns.map((column, index) => {
            const locked =
              column.hideable === false || (column.hideable === undefined && index === 0);
            const name =
              column.name ?? (typeof column.header === "string" ? column.header : column.key);
            return (
              <label
                key={column.key}
                className="flex min-h-8 items-center gap-2.5 rounded-md px-1 text-sm text-ink hover:bg-surface-muted"
              >
                <input
                  type="checkbox"
                  className="size-4"
                  checked={!hidden.includes(column.key)}
                  disabled={locked}
                  onChange={() => toggleColumn(column.key)}
                />
                {name}
              </label>
            );
          })}
        </div>
      </fieldset>
      {pinnedDensity ? null : (
        <fieldset className="mt-3 border-t border-border pt-3">
          <legend className="mb-2 pt-3 text-sm font-semibold text-ink">{t("density")}</legend>
          <div className="space-y-1">
            {(["comfortable", "compact"] as const).map((value) => (
              <label
                key={value}
                className="flex min-h-8 items-center gap-2.5 rounded-md px-1 text-sm text-ink hover:bg-surface-muted"
              >
                <input
                  type="radio"
                  name={`${captionId}-density`}
                  className="size-4"
                  checked={(ownDensity ?? viewerDensity) === value}
                  onChange={() => chooseDensity(value)}
                />
                {t(value)}
              </label>
            ))}
          </div>
        </fieldset>
      )}
    </Popover>
  );

  let body: ReactNode;
  if (state.status === "loading") {
    body = <LoadingState label={tc("loading")} rows={5} className="p-4" />;
  } else if (state.status === "error") {
    body = (
      <div className="p-4">
        <Alert tone="danger" title={tc("loadErrorTitle")}>
          <p>{state.reason ? te(`load.${state.reason}`) : tc("loadErrorBody")}</p>
          {onRetry ? (
            <Button variant="secondary" size="sm" className="mt-3" onClick={onRetry}>
              <Icon name="refresh" className="size-4" />
              {tc("retry")}
            </Button>
          ) : null}
        </Alert>
      </div>
    );
  } else if (state.status === "unavailable") {
    body = (
      <EmptyState
        title={tc("notAvailableYetTitle")}
        body={tc("notAvailableYetBody")}
        className="m-4"
      />
    );
  } else if (state.data.length === 0) {
    body =
      chips.length > 0 ? (
        <EmptyState
          className="m-4"
          icon="search"
          title={noResultsTitle ?? t("noResults")}
          body={t("noResultsBody")}
          action={
            onClearFilters ? (
              <Button variant="secondary" size="sm" onClick={onClearFilters}>
                {t("clearFilters")}
              </Button>
            ) : undefined
          }
        />
      ) : (
        <EmptyState className="m-4" title={emptyTitle} body={emptyBody} action={emptyAction} />
      );
  } else {
    const data = state.data;
    const table = (
      <div
        role="region"
        aria-labelledby={captionId}
        tabIndex={0}
        className="table-scroll max-h-[70vh] overflow-y-auto max-h-[70dvh] focus-visible:outline-offset-[-3px]"
      >
        <table
          data-density={density}
          className="w-full border-separate border-spacing-0 text-left text-sm"
        >
          <caption
            id={captionId}
            className={cn("px-4 py-3 text-left font-semibold text-ink", captionHidden && "sr-only")}
          >
            {caption}
          </caption>
          <thead>
            <tr>
              {selection ? (
                <th
                  scope="col"
                  className="sticky top-0 z-[2] w-10 border-b border-border bg-surface-muted ps-4 pe-1"
                >
                  <input
                    ref={headerCheckbox}
                    type="checkbox"
                    className="size-4 align-middle"
                    aria-label={t("selectAll")}
                    checked={allSelected}
                    onChange={() => setSelected(allSelected ? new Set() : new Set(rowKeys))}
                  />
                </th>
              ) : null}
              {shown.map((column) => (
                <th
                  key={column.key}
                  scope="col"
                  className={cn(
                    "sticky top-0 z-[1] border-b border-border bg-surface-muted px-4 py-2.5 text-xs font-semibold whitespace-nowrap text-ink-muted",
                    "compact:px-3 compact:py-1.5",
                    column.numeric && NUMERIC,
                    column.className,
                  )}
                >
                  {column.header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.map((row) => {
              const key = rowKey(row);
              const isSelected = selected.has(key);
              return (
                <tr
                  key={key}
                  aria-selected={selection ? isSelected : undefined}
                  className={cn(
                    "hover:bg-surface-hover [&>*]:border-b [&>*]:border-border last:[&>*]:border-b-0",
                    isSelected && "bg-accent-soft hover:bg-accent-soft",
                  )}
                >
                  {selection ? (
                    <td className="w-10 ps-4 pe-1 align-middle">
                      <input
                        type="checkbox"
                        className="size-4 align-middle"
                        aria-label={selection.rowLabel(row)}
                        checked={isSelected}
                        onChange={() => {
                          const next = new Set(selected);
                          if (next.has(key)) next.delete(key);
                          else next.add(key);
                          setSelected(next);
                        }}
                      />
                    </td>
                  ) : null}
                  {shown.map((column) => (
                    <td
                      key={column.key}
                      className={cn(
                        "h-[var(--row-height)] px-4 py-2 align-middle text-ink",
                        "compact:px-3 compact:py-1 compact:text-[0.8125rem] compact:leading-5",
                        column.numeric && NUMERIC,
                        column.className,
                      )}
                    >
                      {column.cell(row)}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    );
    body = stacked ? (
      <NarrowSwitch
        wide={table}
        narrow={
          <StackedRows
            caption={caption}
            captionHidden
            columns={shown}
            rows={data}
            rowKey={rowKey}
          />
        }
      />
    ) : (
      table
    );
  }

  return (
    <div className="min-w-0 rounded-xl border border-border bg-surface" data-table-v2="">
      <div
        className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2.5 sm:px-4"
        data-print="hide"
      >
        <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">{toolbar}</div>
        {state.status === "ready" && state.data.length > 0 ? columnChooser : null}
      </div>
      {chips.length > 0 && onRemoveChip ? (
        <FilterChips
          chips={chips}
          onRemove={onRemoveChip}
          {...(onClearFilters ? { onClear: onClearFilters } : {})}
          className="border-b border-border px-3 py-2 sm:px-4"
        />
      ) : null}
      <LoadFade loading={state.status === "loading"}>{body}</LoadFade>
      {selection && visibleSelected.length > 0 ? (
        <div
          role="region"
          aria-label={t("bulkActions")}
          className="bar-in sticky bottom-0 z-[3] flex flex-wrap items-center gap-3 rounded-b-xl border-t border-border bg-surface px-4 py-2.5 shadow-popover"
          data-print="hide"
        >
          <p role="status" className="num text-sm font-semibold text-ink">
            {t("selected", { count: visibleSelected.length })}
          </p>
          <div className="flex flex-wrap items-center gap-2">
            {selection.actions(selectedRows, clearSelection)}
          </div>
          <button
            type="button"
            onClick={clearSelection}
            className="ms-auto min-h-8 rounded-md px-2 text-sm font-semibold text-accent hover:underline pointer-coarse:min-h-11"
          >
            {t("clearSelection")}
          </button>
        </div>
      ) : null}
      {footer ? <div className="border-t border-border px-3 py-2.5 sm:px-4">{footer}</div> : null}
    </div>
  );
}
