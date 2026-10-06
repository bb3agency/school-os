import { useTranslations } from "next-intl";
import type { ComponentProps, ReactNode } from "react";
import { cn } from "@/lib/cn";
import type { Loadable } from "@/lib/loadable";
import { Alert } from "./Alert";
import { EmptyState } from "./EmptyState";
import { LoadFade } from "./LoadFade";
import { LoadingState } from "./LoadingState";
import { NarrowSwitch } from "./NarrowSwitch";

/**
 * `comfortable` (default): rows about 56px high, like the reference tables.
 * `compact`: dense registers and long lists.
 */
export type TableDensity = "comfortable" | "compact";

export interface TableProps extends ComponentProps<"table"> {
  density?: TableDensity;
  /**
   * Keep the first column (the row's name: student, school, invoice number) in view while
   * a wide table scrolls sideways below md. Use it when the first cell identifies the row.
   */
  stickyFirstColumn?: boolean;
  /**
   * A record's fields (label, value, notes per row): below 640px each row becomes a block
   * (label line, then the value, then the rest) instead of a narrow table that scrolls
   * sideways; the header row stays for screen readers only (docs/17 §5.7).
   */
  reflow?: boolean;
}

/** `reflow` below 640px: rows as blocks, cells full width, header visually hidden. */
const reflowRows =
  "max-sm:block max-sm:[&_tbody]:block max-sm:[&_tr]:block max-sm:[&_tr]:py-2 " +
  "max-sm:[&_td]:block max-sm:[&_td]:py-1 max-sm:[&_tbody_th]:block max-sm:[&_tbody_th]:py-1 " +
  "max-sm:[&_thead]:sr-only max-sm:[&_tr>:first-child]:static max-sm:[&_tr>:first-child]:shadow-none";

/** Sticky first column below md: header cell on the header tint, body cells on white. */
const stickyFirst =
  "max-md:[&_tr>:first-child]:sticky max-md:[&_tr>:first-child]:left-0 max-md:[&_tr>:first-child]:z-[1] " +
  "max-md:[&_tbody_tr>:first-child]:bg-surface max-md:[&_thead_tr>:first-child]:bg-surface-muted " +
  "max-md:[&_tr>:first-child]:shadow-[1px_0_0_var(--color-border)]";

/**
 * Plain table: light header row, thin row dividers, row hover, no heavy grid. Cells read
 * the density from `data-density` (no React context, so it works in server components).
 * Put it in `TableScroll` (or use `DataTable`) so a wide table scrolls inside its card.
 */
export function Table({
  className,
  density = "comfortable",
  stickyFirstColumn = false,
  reflow = false,
  ...props
}: TableProps) {
  return (
    <table
      data-density={density}
      className={cn(
        // Tabular figures: digits line up in columns (counts, amounts, dates).
        "group/table w-full border-collapse text-left text-sm tabular-nums",
        stickyFirstColumn && stickyFirst,
        reflow && reflowRows,
        className,
      )}
      {...props}
    />
  );
}

export interface TableScrollProps extends Omit<ComponentProps<"div">, "role" | "tabIndex"> {
  /** Accessible name of the scroll region ("Students, table, scrolls sideways"). */
  label: string;
  /** Draw the white card frame (hairline border, radius, shadow) around the table. */
  framed?: boolean;
}

/**
 * Horizontal scroll box for a wide table: the table never widens the card or the page, and
 * keyboard users can focus the region and scroll it with the arrow keys (WCAG 2.1.1).
 * `position: relative` (`.table-scroll`) keeps sr-only text inside the box.
 */
export function TableScroll({ label, framed = false, className, ...props }: TableScrollProps) {
  return (
    <div
      role="region"
      aria-label={label}
      tabIndex={0}
      className={cn(
        "table-scroll rounded-xl border border-border print:overflow-visible print:border-0",
        framed && "bg-surface shadow-card print:shadow-none",
        className,
      )}
      {...props}
    />
  );
}

export function THead({ className, ...props }: ComponentProps<"thead">) {
  return (
    <thead
      className={cn("border-b border-border bg-surface-muted print:bg-white", className)}
      {...props}
    />
  );
}

export function TBody({ className, ...props }: ComponentProps<"tbody">) {
  return (
    <tbody
      className={cn(
        "divide-y divide-border [&>tr]:transition-colors [&>tr:hover]:bg-surface-hover",
        className,
      )}
      {...props}
    />
  );
}

export function Tr(props: ComponentProps<"tr">) {
  return <tr {...props} />;
}

/** Numbers line up: right-aligned (end), tabular figures, never wrapped mid-number. */
const NUMERIC = "text-end tabular-nums whitespace-nowrap";

export interface CellProps {
  /** A numeric column (counts, amounts): right-aligned with tabular figures. */
  numeric?: boolean;
}

export function Th({
  className,
  scope = "col",
  numeric = false,
  ...props
}: ComponentProps<"th"> & CellProps) {
  return (
    <th
      scope={scope}
      className={cn(
        "px-4 py-3 text-xs font-semibold whitespace-nowrap text-ink-muted",
        "group-data-[density=compact]/table:px-3 group-data-[density=compact]/table:py-2",
        numeric && NUMERIC,
        className,
      )}
      {...props}
    />
  );
}

export function Td({ className, numeric = false, ...props }: ComponentProps<"td"> & CellProps) {
  return (
    <td
      className={cn(
        "px-4 py-4 align-middle text-ink",
        "group-data-[density=compact]/table:px-3 group-data-[density=compact]/table:py-2 group-data-[density=compact]/table:align-top",
        numeric && NUMERIC,
        className,
      )}
      {...props}
    />
  );
}

export interface Column<T> {
  key: string;
  header: ReactNode;
  cell: (row: T) => ReactNode;
  className?: string;
  /** Counts and amounts: right-aligned, tabular figures (header and cells). */
  numeric?: boolean;
  /**
   * Its place in the stacked layout of a `stacked` table below 640px (docs/17 §5.7):
   * `title` (the row's name, the card's first line; the first column by default), `field`
   * (header and value as a label/value pair; the default for the others), `actions` (buttons
   * and links in a row at the end of the card; also any column with an empty header) or
   * `hidden` (repeats what the card already shows).
   */
  stack?: "title" | "field" | "actions" | "hidden";
}

type StackPlace = NonNullable<Column<unknown>["stack"]>;

/** Where a column goes in the stacked layout (see `Column.stack`). */
export function stackPlace<T>(column: Column<T>, index: number): StackPlace {
  if (column.stack) return column.stack;
  if (index === 0) return "title";
  const header: unknown = column.header;
  if (header === "" || header === null || header === undefined || header === false)
    return "actions";
  return "field";
}

export interface StackedRowsProps<T> {
  caption: string;
  captionHidden?: boolean;
  columns: ReadonlyArray<Column<T>>;
  rows: readonly T[];
  rowKey: (row: T) => string;
}

/**
 * The stacked layout of a table (docs/17 §5.7): one framed card, one list item per row. The
 * title column is the item's first line, the other columns are label/value pairs (`<dl>`, the
 * header as the muted label) and the actions sit in a row at the end. A list named by the
 * caption keeps "list, 12 items" for screen readers, and nothing scrolls sideways.
 */
export function StackedRows<T>({
  caption,
  captionHidden = false,
  columns,
  rows,
  rowKey,
}: StackedRowsProps<T>) {
  const places = columns.map((column, index) => stackPlace(column, index));
  const titles = columns.filter((_, i) => places[i] === "title");
  const fields = columns.filter((_, i) => places[i] === "field");
  const actions = columns.filter((_, i) => places[i] === "actions");
  return (
    <div
      data-stacked-list=""
      className="min-w-0 rounded-xl border border-border bg-surface shadow-card print:shadow-none"
    >
      {captionHidden ? null : (
        <p className="border-b border-border px-4 py-3 font-semibold text-ink">{caption}</p>
      )}
      <ul aria-label={caption} className="divide-y divide-border">
        {rows.map((row) => (
          <li
            key={rowKey(row)}
            className="relative min-w-0 space-y-2 px-4 py-3.5 break-anywhere [&_*]:min-w-0 [&_*]:whitespace-normal"
          >
            {titles.map((column) => (
              <div
                key={column.key}
                className="min-w-0 text-ink pointer-coarse:[&_a]:inline-block pointer-coarse:[&_a]:py-3 pointer-coarse:[&_a]:-my-3"
              >
                {column.cell(row)}
              </div>
            ))}
            {fields.length > 0 ? (
              <dl className="grid grid-cols-label-value gap-x-4 gap-y-1.5 text-sm [&_a]:relative [&_a]:z-[1] [&_button]:relative [&_button]:z-[1]">
                {fields.map((column) => (
                  <div key={column.key} className="contents">
                    <dt className="text-ink-muted">{column.header}</dt>
                    <dd className={cn("text-ink", column.numeric && "tabular-nums")}>
                      {column.cell(row)}
                    </dd>
                  </div>
                ))}
              </dl>
            ) : null}
            {actions.length > 0 ? (
              <div className="relative z-[1] flex flex-wrap items-center gap-2 pt-1 [&_a]:inline-flex [&_a]:items-center pointer-coarse:[&_a]:min-h-11">
                {actions.map((column) => (
                  <div key={column.key} className="min-w-0">
                    {column.cell(row)}
                  </div>
                ))}
              </div>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}

export interface DataTableProps<T> {
  /** Visible caption; also names the scrollable region. */
  caption: string;
  columns: ReadonlyArray<Column<T>>;
  state: Loadable<readonly T[]>;
  rowKey: (row: T) => string;
  emptyTitle: ReactNode;
  emptyBody?: ReactNode;
  emptyAction?: ReactNode;
  /** Hide the caption visually when a heading right above already says the same. */
  captionHidden?: boolean;
  density?: TableDensity;
  /** Keep the first column in view while the table scrolls sideways below md. */
  stickyFirstColumn?: boolean;
  /**
   * Below 640px show the rows as a list of cards (`StackedRows`) instead of a table that
   * scrolls sideways: the key record lists use it (docs/17 §5.7).
   */
  stacked?: boolean;
}

/**
 * Table with loading, error and empty states. Wide tables scroll inside a focusable,
 * named region (`TableScroll`), so they never widen the page at any width.
 */
export function DataTable<T>({
  caption,
  columns,
  state,
  rowKey,
  emptyTitle,
  emptyBody,
  emptyAction,
  captionHidden = false,
  density = "comfortable",
  stickyFirstColumn = false,
  stacked = false,
}: DataTableProps<T>) {
  const t = useTranslations("common");
  const te = useTranslations("errors");

  let body: ReactNode;
  if (state.status === "loading") {
    body = <LoadingState label={t("loading")} />;
  } else if (state.status === "error") {
    body = (
      <Alert tone="danger" title={t("loadErrorTitle")}>
        {state.reason ? te(`load.${state.reason}`) : t("loadErrorBody")}
      </Alert>
    );
  } else if (state.status === "unavailable") {
    body = <EmptyState title={t("notAvailableYetTitle")} body={t("notAvailableYetBody")} />;
  } else if (state.data.length === 0) {
    body = <EmptyState title={emptyTitle} body={emptyBody} action={emptyAction} />;
  } else {
    const table = (
      <TableScroll label={t("scrollableTable", { caption })} framed>
        <Table density={density} stickyFirstColumn={stickyFirstColumn}>
          <caption
            className={cn("px-4 py-3 text-left font-semibold text-ink", captionHidden && "sr-only")}
          >
            {caption}
          </caption>
          <THead>
            <Tr>
              {columns.map((column) => (
                <Th key={column.key} className={column.className} numeric={column.numeric === true}>
                  {column.header}
                </Th>
              ))}
            </Tr>
          </THead>
          <TBody>
            {state.data.map((row) => (
              <Tr key={rowKey(row)}>
                {columns.map((column) => (
                  <Td
                    key={column.key}
                    className={column.className}
                    numeric={column.numeric === true}
                  >
                    {column.cell(row)}
                  </Td>
                ))}
              </Tr>
            ))}
          </TBody>
        </Table>
      </TableScroll>
    );
    body = stacked ? (
      <NarrowSwitch
        wide={table}
        narrow={
          <StackedRows
            caption={caption}
            captionHidden={captionHidden}
            columns={columns}
            rows={state.data}
            rowKey={rowKey}
          />
        }
      />
    ) : (
      table
    );
  }

  // Whatever replaces the skeleton fades in; a first render with data shows at once.
  return <LoadFade loading={state.status === "loading"}>{body}</LoadFade>;
}
